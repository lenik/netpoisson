#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Wire formats for TCP/UDP (binary) and SSH stdio (NDJSON).

TCP/UDP
-------
Each PDU is length-prefixed for stream safety::

    uint32_be total_len   # bytes after this field
    magic "NP" (2)
    version = 2 (u8)
    type: ECHO=1 STATUS=2 (u8)
    flags (u8): REQ=0x01 RESP=0x02 TELEMETRY=0x04
    reserved (u8)
    seq (u64)
    client_ns (u64)       # client monotonic send time when known
    body_len (u32)
    body...

ECHO request body is the opaque payload. An ECHO response without TELEMETRY
is byte-identical to the request PDU (including header). With TELEMETRY, the
response keeps the same header fields and payload, then appends a fixed
telemetry trailer so the observer need not send separate STATUS probes.

STATUS body is a structured snapshot (see encode_status_body).

SSH stdio
---------
Each line is UTF-8 NDJSON prefixed with ``@nPoi `` and terminated by ``\\n``.
Only lines with that prefix are protocol; everything else is remote_noise.
"""

from __future__ import annotations

import base64
import json
import struct
from dataclasses import dataclass
from collections.abc import Callable
from typing import Any

MAGIC = b"NP"
VERSION = 2
ECHO = 1
STATUS = 2

FLAG_REQ = 0x01
FLAG_RESP = 0x02
FLAG_TELEMETRY = 0x04

# total_len is outside; inner starts at magic
INNER_HDR = struct.Struct("!2sBBBBQQI")  # magic ver type flags reserved seq client_ns body_len
INNER_HDR_SIZE = INNER_HDR.size
LEN_SIZE = 4
MAX_BODY = 1024 * 1024
MAX_STDIO_LINE = 1024 * 1024

# Compact telemetry on ECHO responses (fixed size).
# mono, req_total, resp_total, read_pending, processing, response_pending,
# rx_bytes, tx_bytes, interval_rx, interval_tx, kernel_send, kernel_recv
TELEMETRY = struct.Struct("!QQQIIIQQIIII")
TELEMETRY_SIZE = TELEMETRY.size

# STATUS body head + newest-first slice counts.
# mono, req_total, resp_total, read_pending, processing, response_pending,
# rx_bytes, tx_bytes, interval_rx, interval_tx, bucket_ms,
# kernel_send, kernel_recv, n_recv, n_resp
STATUS_HEAD = struct.Struct("!QQQIIIQQIIIIIHH")
STATUS_COUNT = struct.Struct("!H")

STDIO_VERSION = 1
STDIO_PREFIX = "@nPoi "
KERNEL_UNKNOWN = 0xFFFFFFFF


@dataclass(frozen=True)
class Frame:
    mtype: int
    flags: int
    seq: int
    client_ns: int
    payload: bytes
    telemetry: dict | None
    raw: bytes  # full on-wire PDU including length prefix

    @property
    def is_req(self) -> bool:
        return bool(self.flags & FLAG_REQ)

    @property
    def is_resp(self) -> bool:
        return bool(self.flags & FLAG_RESP)


@dataclass(frozen=True)
class StatusSnapshot:
    server_monotonic_ns: int
    request_received_total: int
    response_completed_total: int
    read_pending: int
    processing: int
    response_pending: int
    rx_bytes: int
    tx_bytes: int
    interval_rx_requests: int
    interval_tx_responses: int
    bucket_ms: int
    kernel_send_queue_bytes: int | None
    kernel_recv_queue_bytes: int | None
    recv_slices: list[int]  # newest first
    resp_slices: list[int]  # newest first


def _pack_kernel(value: int | None) -> int:
    if value is None or value < 0:
        return KERNEL_UNKNOWN
    return min(int(value), KERNEL_UNKNOWN - 1)


def _unpack_kernel(value: int) -> int | None:
    if value == KERNEL_UNKNOWN:
        return None
    return int(value)


def encode_telemetry(
    *,
    server_mono_ns: int,
    request_received_total: int,
    response_completed_total: int,
    read_pending: int,
    processing: int,
    response_pending: int,
    rx_bytes: int,
    tx_bytes: int,
    interval_rx_requests: int,
    interval_tx_responses: int,
    kernel_send_queue_bytes: int | None = None,
    kernel_recv_queue_bytes: int | None = None,
) -> bytes:
    return TELEMETRY.pack(
        server_mono_ns,
        request_received_total,
        response_completed_total,
        max(0, read_pending),
        max(0, processing),
        max(0, response_pending),
        max(0, rx_bytes),
        max(0, tx_bytes),
        max(0, interval_rx_requests),
        max(0, interval_tx_responses),
        _pack_kernel(kernel_send_queue_bytes),
        _pack_kernel(kernel_recv_queue_bytes),
    )


def decode_telemetry(blob: bytes) -> dict | None:
    if len(blob) < TELEMETRY_SIZE:
        return None
    vals = TELEMETRY.unpack_from(blob)
    return {
        "server_monotonic_ns": vals[0],
        "request_received_total": vals[1],
        "response_completed_total": vals[2],
        "read_pending": vals[3],
        "processing": vals[4],
        "response_pending": vals[5],
        "rx_bytes": vals[6],
        "tx_bytes": vals[7],
        "interval_rx_requests": vals[8],
        "interval_tx_responses": vals[9],
        "kernel_send_queue_bytes": _unpack_kernel(vals[10]),
        "kernel_recv_queue_bytes": _unpack_kernel(vals[11]),
    }


def encode_status_body(snap: StatusSnapshot, max_slices: int = 120) -> bytes:
    recv = list(snap.recv_slices[:max_slices])
    resp = list(snap.resp_slices[:max_slices])
    head = STATUS_HEAD.pack(
        snap.server_monotonic_ns,
        snap.request_received_total,
        snap.response_completed_total,
        max(0, snap.read_pending),
        max(0, snap.processing),
        max(0, snap.response_pending),
        max(0, snap.rx_bytes),
        max(0, snap.tx_bytes),
        max(0, snap.interval_rx_requests),
        max(0, snap.interval_tx_responses),
        max(1, snap.bucket_ms),
        _pack_kernel(snap.kernel_send_queue_bytes),
        _pack_kernel(snap.kernel_recv_queue_bytes),
        len(recv),
        len(resp),
    )
    counts = b"".join(STATUS_COUNT.pack(min(c, 0xFFFF)) for c in recv + resp)
    return head + counts


def decode_status_body(payload: bytes) -> StatusSnapshot | None:
    if len(payload) < STATUS_HEAD.size:
        return None
    vals = STATUS_HEAD.unpack_from(payload)
    n_recv, n_resp = vals[13], vals[14]
    need = STATUS_HEAD.size + (n_recv + n_resp) * STATUS_COUNT.size
    if len(payload) < need or vals[10] <= 0:
        return None
    off = STATUS_HEAD.size
    recv: list[int] = []
    for _ in range(n_recv):
        recv.append(STATUS_COUNT.unpack_from(payload, off)[0])
        off += STATUS_COUNT.size
    resp: list[int] = []
    for _ in range(n_resp):
        resp.append(STATUS_COUNT.unpack_from(payload, off)[0])
        off += STATUS_COUNT.size
    return StatusSnapshot(
        server_monotonic_ns=vals[0],
        request_received_total=vals[1],
        response_completed_total=vals[2],
        read_pending=vals[3],
        processing=vals[4],
        response_pending=vals[5],
        rx_bytes=vals[6],
        tx_bytes=vals[7],
        interval_rx_requests=vals[8],
        interval_tx_responses=vals[9],
        bucket_ms=vals[10],
        kernel_send_queue_bytes=_unpack_kernel(vals[11]),
        kernel_recv_queue_bytes=_unpack_kernel(vals[12]),
        recv_slices=recv,
        resp_slices=resp,
    )


def _inner(mtype: int, flags: int, seq: int, client_ns: int, body: bytes) -> bytes:
    return INNER_HDR.pack(MAGIC, VERSION, mtype, flags, 0, seq, client_ns, len(body)) + body


def wrap_pdu(inner: bytes) -> bytes:
    return struct.pack("!I", len(inner)) + inner


def encode_echo_request(seq: int, client_ns: int, payload: bytes) -> bytes:
    return wrap_pdu(_inner(ECHO, FLAG_REQ, seq, client_ns, payload))


def encode_echo_response(
    seq: int,
    client_ns: int,
    payload: bytes,
    telemetry: bytes | None = None,
) -> bytes:
    if telemetry:
        body = payload + telemetry
        flags = FLAG_RESP | FLAG_TELEMETRY
    else:
        body = payload
        flags = FLAG_RESP
    return wrap_pdu(_inner(ECHO, flags, seq, client_ns, body))


def encode_status_request(seq: int, client_ns: int) -> bytes:
    return wrap_pdu(_inner(STATUS, FLAG_REQ, seq, client_ns, b""))


def encode_status_response(seq: int, client_ns: int, body: bytes) -> bytes:
    return wrap_pdu(_inner(STATUS, FLAG_RESP, seq, client_ns, body))


def decode_inner(inner: bytes) -> Frame | None:
    if len(inner) < INNER_HDR_SIZE:
        return None
    magic, ver, mtype, flags, _res, seq, client_ns, body_len = INNER_HDR.unpack_from(inner)
    if magic != MAGIC or ver != VERSION:
        return None
    if mtype not in (ECHO, STATUS):
        return None
    if body_len > MAX_BODY or body_len != len(inner) - INNER_HDR_SIZE:
        return None
    body = inner[INNER_HDR_SIZE:]
    telemetry = None
    payload = body
    if mtype == ECHO and (flags & FLAG_TELEMETRY):
        if len(body) < TELEMETRY_SIZE:
            return None
        payload = body[:-TELEMETRY_SIZE]
        telemetry = decode_telemetry(body[-TELEMETRY_SIZE:])
        if telemetry is None:
            return None
    raw = wrap_pdu(inner)
    return Frame(mtype, flags, seq, client_ns, payload, telemetry, raw)


def decode_pdu(raw: bytes) -> Frame | None:
    if len(raw) < LEN_SIZE + INNER_HDR_SIZE:
        return None
    total = struct.unpack_from("!I", raw, 0)[0]
    if total != len(raw) - LEN_SIZE or total > MAX_BODY + INNER_HDR_SIZE:
        return None
    return decode_inner(raw[LEN_SIZE:])


def pop_pdus(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a TCP byte stream into full PDUs."""
    out: list[bytes] = []
    while True:
        if len(buf) < LEN_SIZE:
            break
        total = struct.unpack_from("!I", buf, 0)[0]
        if total > MAX_BODY + INNER_HDR_SIZE:
            # resync: drop one byte
            buf = buf[1:]
            continue
        need = LEN_SIZE + total
        if len(buf) < need:
            break
        out.append(buf[:need])
        buf = buf[need:]
    return out, buf


def echo_request_identity(raw: bytes) -> bytes | None:
    """Return the request PDU that an ECHO response without telemetry should match.

    For telemetry responses, identity is header+payload with REQ flag and no trailer.
    """
    frame = decode_pdu(raw)
    if frame is None or frame.mtype != ECHO:
        return None
    return encode_echo_request(frame.seq, frame.client_ns, frame.payload)


def dispatch_binary(
    raw: bytes,
    *,
    make_telemetry: Callable[[], bytes] | None,
    make_status: Callable[[], bytes] | None,
) -> bytes | None:
    frame = decode_pdu(raw)
    if frame is None or not frame.is_req:
        return None
    if frame.mtype == ECHO:
        telem = make_telemetry() if make_telemetry else None
        if telem:
            return encode_echo_response(frame.seq, frame.client_ns, frame.payload, telem)
        # Byte-identical to the request.
        return raw
    if frame.mtype == STATUS:
        if make_status is None:
            return None
        body = make_status()
        return encode_status_response(frame.seq, frame.client_ns, body)
    return None


# ----- SSH stdio NDJSON -----


def stdio_pack(obj: dict[str, Any]) -> bytes:
    line = STDIO_PREFIX + json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
    data = line.encode("utf-8")
    if len(data) > MAX_STDIO_LINE:
        raise ValueError("stdio line too long")
    return data


def stdio_parse_line(line: bytes) -> tuple[str, dict | None]:
    """Return (kind, obj). kind is protocol|noise|error."""
    try:
        text = line.decode("utf-8")
    except UnicodeDecodeError:
        return "error", None
    if not text.startswith(STDIO_PREFIX):
        return "noise", None
    payload = text[len(STDIO_PREFIX) :].strip("\r\n")
    try:
        obj = json.loads(payload)
    except json.JSONDecodeError:
        return "error", None
    if not isinstance(obj, dict) or int(obj.get("v", -1)) not in (STDIO_VERSION, VERSION):
        return "error", None
    return "protocol", obj


def stdio_echo_req(seq: int, client_ns: int, data: bytes, *, b64: bool = False) -> bytes:
    use_b64 = b64 or any(b < 32 and b not in (9,) for b in data) or b"\n" in data or b"\r" in data
    if not use_b64:
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            use_b64 = True
        else:
            return stdio_pack({"v": STDIO_VERSION, "t": "e", "i": seq, "cs": client_ns, "d": text})
    return stdio_pack(
        {
            "v": STDIO_VERSION,
            "t": "e",
            "i": seq,
            "cs": client_ns,
            "enc": "b64",
            "d": base64.b64encode(data).decode("ascii"),
        }
    )


def stdio_echo_ack(
    seq: int,
    client_ns: int,
    data: bytes,
    *,
    server_rx_ns: int,
    server_tx_ns: int,
    telemetry: dict | None = None,
    b64: bool = False,
) -> bytes:
    if b64 or "enc" in (telemetry or {}):
        d: Any = base64.b64encode(data).decode("ascii")
        enc = "b64"
    else:
        try:
            d = data.decode("utf-8")
            enc = None
        except UnicodeDecodeError:
            d = base64.b64encode(data).decode("ascii")
            enc = "b64"
    obj: dict[str, Any] = {
        "v": STDIO_VERSION,
        "t": "er",
        "i": seq,
        "cs": client_ns,
        "sr": server_rx_ns,
        "ss": server_tx_ns,
        "d": d,
    }
    if enc:
        obj["enc"] = enc
    if telemetry:
        obj["tm"] = telemetry
    return stdio_pack(obj)


def stdio_status_req(seq: int, client_ns: int) -> bytes:
    return stdio_pack({"v": STDIO_VERSION, "t": "s", "i": seq, "cs": client_ns})


def stdio_status_ack(seq: int, client_ns: int, snap: StatusSnapshot) -> bytes:
    return stdio_pack(
        {
            "v": STDIO_VERSION,
            "t": "sr",
            "i": seq,
            "cs": client_ns,
            "sm": snap.server_monotonic_ns,
            "rr": snap.request_received_total,
            "rc": snap.response_completed_total,
            "rq": snap.read_pending,
            "pc": snap.processing,
            "sq": snap.response_pending,
            "rx": snap.rx_bytes,
            "tx": snap.tx_bytes,
            "ir": snap.interval_rx_requests,
            "it": snap.interval_tx_responses,
            "bm": snap.bucket_ms,
            "ks": snap.kernel_send_queue_bytes,
            "kr": snap.kernel_recv_queue_bytes,
            "rv": snap.recv_slices,
            "rp": snap.resp_slices,
        }
    )


def stdio_hello(seq: int, client_ns: int) -> bytes:
    return stdio_pack({"v": STDIO_VERSION, "t": "hello", "i": seq, "cs": client_ns})


def stdio_hello_ack(seq: int, server_ns: int) -> bytes:
    return stdio_pack({"v": STDIO_VERSION, "t": "hello_ack", "i": seq, "ss": server_ns})


def stdio_decode_data(obj: dict) -> bytes | None:
    d = obj.get("d")
    if d is None:
        return b""
    if obj.get("enc") == "b64":
        try:
            return base64.b64decode(d, validate=True)
        except Exception:
            return None
    if isinstance(d, str):
        return d.encode("utf-8")
    return None


def parse_duration_ms(text: str) -> int:
    """Parse ``100ms``, ``12s``, ``1.5s``, or bare milliseconds."""
    s = text.strip().lower()
    if s.endswith("ms"):
        return max(1, int(float(s[:-2])))
    if s.endswith("s"):
        return max(1, int(float(s[:-1]) * 1000))
    return max(1, int(float(s)))


def parse_size(text: str) -> int:
    s = text.strip().lower()
    units = {"k": 1024, "kb": 1024, "kib": 1024, "m": 1024 * 1024, "mb": 1024 * 1024, "mib": 1024 * 1024, "b": 1}
    for suffix, mul in sorted(units.items(), key=lambda x: -len(x[0])):
        if s.endswith(suffix):
            return max(0, int(float(s[: -len(suffix)]) * mul))
    return max(0, int(s))
