#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Binary frames shared by the TCP and UDP peers.

A request and its ECHO response are the same bytes. STATUS responses carry
the server receive queue, send queue, and per-slice reply counts.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = b"NP"
VERSION = 1
ECHO = 1
STATUS = 2
REQ = 1
RESP = 2

# magic, version, mtype, role, reserved, seq, client_ns, payload_len
HDR = struct.Struct("!2sBBBBQQI")
HDR_SIZE = HDR.size
MAX_PAYLOAD = 65536

# recv_ns, send_ns, rx_queued, tx_queued, slice_ms, n_slices
STATUS_HEAD = struct.Struct("!QQIIIH")
# slice_id, response count
STATUS_ENTRY = struct.Struct("!QI")

ECHO_PAYLOAD = b"netpoisson"


@dataclass(frozen=True)
class Frame:
    mtype: int
    role: int
    seq: int
    client_ns: int
    payload: bytes
    raw: bytes


def encode_frame(mtype: int, role: int, seq: int, client_ns: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD:
        raise ValueError("payload too large")
    return HDR.pack(MAGIC, VERSION, mtype, role, 0, seq, client_ns, len(payload)) + payload


def encode_request(mtype: int, seq: int, client_ns: int, payload: bytes = b"") -> bytes:
    return encode_frame(mtype, REQ, seq, client_ns, payload)


def decode_frame(raw: bytes) -> Frame | None:
    if len(raw) < HDR_SIZE:
        return None
    magic, ver, mtype, role, _reserved, seq, client_ns, length = HDR.unpack_from(raw)
    if magic != MAGIC or ver != VERSION:
        return None
    if length > MAX_PAYLOAD or length != len(raw) - HDR_SIZE:
        return None
    if mtype not in (ECHO, STATUS) or role not in (REQ, RESP):
        return None
    return Frame(mtype, role, seq, client_ns, raw[HDR_SIZE:], raw)


def pop_frames(buf: bytes) -> tuple[list[bytes], bytes]:
    """Split a TCP byte stream. Incomplete data stays in the returned buffer."""
    frames: list[bytes] = []
    while True:
        if len(buf) < HDR_SIZE:
            break
        if buf[:2] != MAGIC:
            buf = buf[1:]
            continue
        length = struct.unpack_from("!I", buf, 22)[0]
        if length > MAX_PAYLOAD:
            buf = buf[2:]
            continue
        total = HDR_SIZE + length
        if len(buf) < total:
            break
        frames.append(buf[:total])
        buf = buf[total:]
    return frames, buf


def encode_status_payload(
    recv_ns: int,
    send_ns: int,
    rx_queued: int,
    tx_queued: int,
    slice_ms: int,
    slices: list[tuple[int, int]],
) -> bytes:
    rows = slices[-48:]
    body = STATUS_HEAD.pack(
        recv_ns,
        send_ns,
        max(0, rx_queued),
        max(0, tx_queued),
        slice_ms,
        len(rows),
    )
    body += b"".join(STATUS_ENTRY.pack(sid, min(count, 0xFFFFFFFF)) for sid, count in rows)
    return body


def decode_status_payload(payload: bytes) -> dict | None:
    if len(payload) < STATUS_HEAD.size:
        return None
    recv_ns, send_ns, rxq, txq, slice_ms, n = STATUS_HEAD.unpack_from(payload)
    need = STATUS_HEAD.size + n * STATUS_ENTRY.size
    if slice_ms <= 0 or len(payload) < need:
        return None
    slices: list[tuple[int, int]] = []
    off = STATUS_HEAD.size
    for _ in range(n):
        sid, count = STATUS_ENTRY.unpack_from(payload, off)
        slices.append((sid, count))
        off += STATUS_ENTRY.size
    return {
        "recv_ns": recv_ns,
        "send_ns": send_ns,
        "rx_queued": rxq,
        "tx_queued": txq,
        "slice_ms": slice_ms,
        "slices": slices,
    }


def dispatch_request(raw: bytes, recv_ns: int, status_payload) -> bytes | None:
    """Build one response. ECHO returns the request bytes unchanged."""
    frame = decode_frame(raw)
    if frame is None or frame.role != REQ:
        return None
    if frame.mtype == ECHO:
        return raw
    if frame.mtype == STATUS:
        payload = status_payload(recv_ns)
        return encode_frame(STATUS, RESP, frame.seq, frame.client_ns, payload)
    return None
