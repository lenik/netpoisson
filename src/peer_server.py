#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Server peer for ECHO/STATUS over TCP, UDP, or SSH stdio."""

from __future__ import annotations

import os
import queue
import select
import socket
import sys
import threading
import time

from protocol import (
    ECHO,
    STATUS,
    StatusSnapshot,
    decode_pdu,
    dispatch_binary,
    encode_status_body,
    encode_telemetry,
    pop_pdus,
    stdio_decode_data,
    stdio_echo_ack,
    stdio_hello_ack,
    stdio_parse_line,
    stdio_status_ack,
)
from traffic import (
    CounterSeries,
    build_notes,
    format_server_lines,
    newest_first,
    rtt_summary,
    slice_id_for,
)
from netio_sock import _READ_TIMEOUT, _Conn, _close_socket, accept_socket, listen_socket, open_datagram, seal_datagram


def _kernel_queue_bytes(sock: socket.socket | None) -> tuple[int | None, int | None]:
    if sock is None:
        return None, None
    send_q = recv_q = None
    try:
        # Linux: ioctl TIOCOUTQ / FIONREAD via getsockopt alternatives are awkward;
        # leave unknown unless SO_MEMINFO exists.
        if hasattr(socket, "SO_MEMINFO"):
            pass
    except OSError:
        pass
    return send_q, recv_q


class Server:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        udp: bool = False,
        bucket_ms: int = 100,
        window_slices: int = 120,
        telemetry: bool = True,
        ssl_ctx=None,
        udp_seal=None,
    ) -> None:
        self.host = host
        self.port = port
        self.udp = udp
        self.ssl_ctx = ssl_ctx
        self.udp_seal = udp_seal
        self.bucket_ms = max(1, bucket_ms)
        self.bucket_ns = self.bucket_ms * 1_000_000
        self.window_slices = max(8, window_slices)
        self.window_auto = False
        self.telemetry = telemetry
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.stat_lock = threading.Lock()
        self.conns: set[_Conn] = set()
        self.lsock: socket.socket | None = None
        self.udp_rx: queue.Queue = queue.Queue()
        self.udp_tx: queue.Queue = queue.Queue()
        self.recv_series = CounterSeries(keep=self.window_slices * 2)
        self.resp_series = CounterSeries(keep=self.window_slices * 2)
        self.echo_rx = 0
        self.echo_tx = 0
        self.status_rx = 0
        self.rx_bytes = 0
        self.tx_bytes = 0
        self.interval_rx = 0
        self.interval_tx = 0
        self.interval_reset = time.monotonic()
        self.started = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.rx_max = 0
        self.tx_max = 0
        self.processing = 0

    def start(self) -> None:
        self.lsock = listen_socket(self.host, self.port, self.udp)
        self.host, self.port = self.lsock.getsockname()[:2]
        self.started = time.monotonic()
        if self.udp:
            self._spawn(self._udp_read, "udp-read")
            self._spawn(self._udp_work, "udp-work")
            self._spawn(self._udp_write, "udp-write")
        else:
            self._spawn(self._accept, "accept")

    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    def set_window_slices(self, n: int, *, auto: bool | None = None) -> dict:
        n = max(8, min(2048, int(n)))
        self.window_slices = n
        keep = max(16, n * 2)
        self.recv_series.keep = keep
        self.resp_series.keep = keep
        if auto is not None:
            self.window_auto = bool(auto)
        return {
            "window_slices": self.window_slices,
            "window_auto": self.window_auto,
            "bucket_ms": self.bucket_ms,
            "window_ms": self.window_slices * self.bucket_ms,
        }

    def set_runtime_config(
        self,
        *,
        lam: float | None = None,
        bucket_ms: int | None = None,
        status_interval_ms: int | None = None,
    ) -> dict:
        # Server has no λ / STATUS probe interval; bucket still applies to timelines.
        if bucket_ms is not None:
            self.bucket_ms = max(1, int(bucket_ms))
            self.bucket_ns = self.bucket_ms * 1_000_000
        return {
            "lambda_rps": None,
            "bucket_ms": self.bucket_ms,
            "status_interval_ms": None,
            "window_slices": self.window_slices,
            "window_auto": self.window_auto,
        }

    def stop(self) -> None:
        self.stopped.set()
        with self.lock:
            conns = list(self.conns)
        for conn in conns:
            conn.closed.set()
            _close_socket(conn.sock)
        _close_socket(self.lsock)
        for thread in self.threads:
            thread.join(timeout=1.0)

    def queue_depths(self) -> tuple[int, int, int]:
        """read_pending, processing, response_pending."""
        if self.udp:
            read_pending = self.udp_rx.qsize()
            response_pending = self.udp_tx.qsize()
        else:
            with self.lock:
                conns = list(self.conns)
            read_pending = sum(conn.rxq.qsize() for conn in conns)
            response_pending = sum(conn.txq.qsize() for conn in conns)
        with self.stat_lock:
            processing = self.processing
        if read_pending > self.rx_max:
            self.rx_max = read_pending
        if response_pending > self.tx_max:
            self.tx_max = response_pending
        return read_pending, processing, response_pending

    def _interval_counts(self) -> tuple[int, int]:
        with self.stat_lock:
            now = time.monotonic()
            if now - self.interval_reset >= 0.1:
                ir, it = self.interval_rx, self.interval_tx
                self.interval_rx = 0
                self.interval_tx = 0
                self.interval_reset = now
                return ir, it
            return self.interval_rx, self.interval_tx

    def make_snapshot(self) -> StatusSnapshot:
        sid = slice_id_for(bucket_ns=self.bucket_ns)
        recv = newest_first(self.recv_series.snapshot(), sid, self.window_slices)
        resp = newest_first(self.resp_series.snapshot(), sid, self.window_slices)
        read_pending, processing, response_pending = self.queue_depths()
        ir, it = self._interval_counts()
        with self.stat_lock:
            echo_rx = self.echo_rx
            echo_tx = self.echo_tx
            rx_bytes = self.rx_bytes
            tx_bytes = self.tx_bytes
        ks, kr = _kernel_queue_bytes(self.lsock)
        return StatusSnapshot(
            server_monotonic_ns=time.monotonic_ns(),
            request_received_total=echo_rx,
            response_completed_total=echo_tx,
            read_pending=read_pending,
            processing=processing,
            response_pending=response_pending,
            rx_bytes=rx_bytes,
            tx_bytes=tx_bytes,
            interval_rx_requests=ir,
            interval_tx_responses=it,
            bucket_ms=self.bucket_ms,
            kernel_send_queue_bytes=ks,
            kernel_recv_queue_bytes=kr,
            recv_slices=recv,
            resp_slices=resp,
        )

    def make_telemetry_bytes(self) -> bytes:
        snap = self.make_snapshot()
        return encode_telemetry(
            server_mono_ns=snap.server_monotonic_ns,
            request_received_total=snap.request_received_total,
            response_completed_total=snap.response_completed_total,
            read_pending=snap.read_pending,
            processing=snap.processing,
            response_pending=snap.response_pending,
            rx_bytes=snap.rx_bytes,
            tx_bytes=snap.tx_bytes,
            interval_rx_requests=snap.interval_rx_requests,
            interval_tx_responses=snap.interval_tx_responses,
            kernel_send_queue_bytes=snap.kernel_send_queue_bytes,
            kernel_recv_queue_bytes=snap.kernel_recv_queue_bytes,
        )

    def snapshot(self) -> dict:
        snap = self.make_snapshot()
        duration = max(0.0, time.monotonic() - self.started)
        ascii_lines = format_server_lines(
            snap.recv_slices,
            snap.resp_slices,
            snap.read_pending,
            snap.processing,
            snap.response_pending,
            width=2,
            half=min(12, len(snap.recv_slices) // 2 or 12),
        )
        view = {
            "role": "server",
            "proto": ("udp+psk" if self.udp_seal else "udp") if self.udp else ("tcp+tls" if self.ssl_ctx else "tcp"),
            "endpoint": self.endpoint(),
            "lambda_rps": None,
            "slice_ms": self.bucket_ms,
            "bucket_ms": self.bucket_ms,
            "status_interval_ms": None,
            "window_slices": self.window_slices,
            "window_auto": getattr(self, "window_auto", False),
            "duration_s": round(duration, 3),
            "uptime_s": round(duration, 3),
            "req": list(snap.recv_slices),
            "recv": list(snap.recv_slices),
            "resp": list(snap.resp_slices),
            "ack": [],
            "raw_events": [],
            "queues": {
                "client_pending": None,
                "read_pending": snap.read_pending,
                "processing": snap.processing,
                "response_pending": snap.response_pending,
            },
            "client_tx": None,
            "client_pending": None,
            "server_rx": snap.read_pending,
            "server_tx": snap.response_pending,
            "processing": snap.processing,
            "wire_sent": snap.response_completed_total,
            "received": snap.request_received_total,
            "offered": snap.request_received_total,
            "lost": 0,
            "echo": {"received": snap.request_received_total, "sent": snap.response_completed_total},
            "status_received": self.status_rx,
            "rx_bytes": snap.rx_bytes,
            "tx_bytes": snap.tx_bytes,
            "bandwidth_bps": {
                "rx": round(snap.rx_bytes * 8 / duration, 1) if duration else 0,
                "tx": round(snap.tx_bytes * 8 / duration, 1) if duration else 0,
            },
            "rx_queue_max": self.rx_max,
            "tx_queue_max": self.tx_max,
            "rtt_us": rtt_summary([]),
            "jitter_rfc3550_us": None,
            "samples": {"rtt_us": [], "client_tx": [], "server_rx": [], "server_tx": []},
            "histogram": [],
            "achieved_rps": round(snap.request_received_total / duration, 2) if duration else 0,
            "loss_ratio": 0,
            "ascii": list(ascii_lines),
        }
        view["notes"] = build_notes(view)
        return view

    def report(self) -> dict:
        snap = self.snapshot()
        return {
            "role": "server",
            "proto": snap["proto"],
            "listen": snap["endpoint"],
            "slice_ms": snap["slice_ms"],
            "duration_s": snap["duration_s"],
            "requests": snap["echo"]["received"],
            "responses": snap["echo"]["sent"],
            "status_requests": snap["status_received"],
            "rx_bytes": snap["rx_bytes"],
            "tx_bytes": snap["tx_bytes"],
            "rx_queue_max": snap["rx_queue_max"],
            "tx_queue_max": snap["tx_queue_max"],
            "recv_timeline": snap["recv"],
            "resp_timeline": snap["resp"],
            "notes": snap["notes"],
        }

    def _spawn(self, target, name: str, *args) -> None:
        thread = threading.Thread(target=target, name=name, args=args, daemon=True)
        thread.start()
        self.threads.append(thread)

    def _accept(self) -> None:
        assert self.lsock is not None
        while not self.stopped.is_set():
            try:
                sock, addr = accept_socket(self.lsock, self.ssl_ctx)
            except TimeoutError:
                continue
            except OSError:
                break
            conn = _Conn(sock, addr)
            with self.lock:
                self.conns.add(conn)
            self._spawn(self._read_conn, "read", conn)
            self._spawn(self._work_conn, "work", conn)
            self._spawn(self._write_conn, "write", conn)

    def _read_conn(self, conn: _Conn) -> None:
        buf = b""
        try:
            while not self.stopped.is_set():
                try:
                    data = conn.sock.recv(65536)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not data:
                    break
                with self.stat_lock:
                    self.rx_bytes += len(data)
                buf += data
                if len(buf) > 2_000_000:
                    break
                frames, buf = pop_pdus(buf)
                now = time.monotonic_ns()
                for raw in frames:
                    conn.rxq.put((raw, now))
        finally:
            conn.closed.set()
            _close_socket(conn.sock)
            with self.lock:
                self.conns.discard(conn)

    def _work_conn(self, conn: _Conn) -> None:
        while not self.stopped.is_set() and not conn.closed.is_set():
            try:
                raw, recv_ns = conn.rxq.get(timeout=0.1)
            except queue.Empty:
                continue
            with self.stat_lock:
                self.processing += 1
            try:
                resp = self._handle(raw, recv_ns)
            finally:
                with self.stat_lock:
                    self.processing = max(0, self.processing - 1)
            if resp:
                conn.txq.put(resp)
        while True:
            try:
                raw, recv_ns = conn.rxq.get_nowait()
            except queue.Empty:
                break
            resp = self._handle(raw, recv_ns)
            if resp:
                conn.txq.put(resp)

    def _write_conn(self, conn: _Conn) -> None:
        while not self.stopped.is_set() or not conn.txq.empty():
            try:
                resp = conn.txq.get(timeout=0.1)
            except queue.Empty:
                if conn.closed.is_set() or self.stopped.is_set():
                    break
                continue
            try:
                conn.sock.sendall(resp)
            except OSError:
                break
            with self.stat_lock:
                self.tx_bytes += len(resp)
            self._note_sent(resp)
            if self.stopped.is_set() and conn.txq.empty():
                break

    def _udp_read(self) -> None:
        assert self.lsock is not None
        while not self.stopped.is_set():
            try:
                data, addr = self.lsock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            plain = open_datagram(self.udp_seal, data)
            if plain:
                with self.stat_lock:
                    self.rx_bytes += len(data)
                self.udp_rx.put((plain, addr, time.monotonic_ns()))

    def _udp_work(self) -> None:
        while not self.stopped.is_set():
            try:
                raw, addr, recv_ns = self.udp_rx.get(timeout=0.1)
            except queue.Empty:
                continue
            with self.stat_lock:
                self.processing += 1
            try:
                resp = self._handle(raw, recv_ns)
            finally:
                with self.stat_lock:
                    self.processing = max(0, self.processing - 1)
            if resp:
                self.udp_tx.put((resp, addr))

    def _udp_write(self) -> None:
        assert self.lsock is not None
        while not self.stopped.is_set() or not self.udp_tx.empty():
            try:
                resp, addr = self.udp_tx.get(timeout=0.1)
            except queue.Empty:
                if self.stopped.is_set():
                    break
                continue
            wire = seal_datagram(self.udp_seal, resp)
            try:
                self.lsock.sendto(wire, addr)
            except OSError:
                if self.stopped.is_set():
                    break
                continue
            with self.stat_lock:
                self.tx_bytes += len(wire)
            self._note_sent(resp)

    def _handle(self, raw: bytes, recv_ns: int) -> bytes | None:
        frame = decode_pdu(raw)
        if frame is None or not frame.is_req:
            # UDP datagram may be inner-only without length prefix in old clients;
            # try length-wrapped only.
            if len(raw) >= 4:
                return None
            return None
        if frame.mtype == ECHO:
            with self.stat_lock:
                self.echo_rx += 1
                self.interval_rx += 1
            self.recv_series.add(slice_id_for(recv_ns, self.bucket_ns))
        elif frame.mtype == STATUS:
            with self.stat_lock:
                self.status_rx += 1

        return dispatch_binary(
            raw,
            make_telemetry=(self.make_telemetry_bytes if self.telemetry else None),
            make_status=lambda: encode_status_body(self.make_snapshot(), self.window_slices),
        )

    def _note_sent(self, resp: bytes) -> None:
        frame = decode_pdu(resp)
        if frame is None or frame.mtype != ECHO:
            return
        with self.stat_lock:
            self.echo_tx += 1
            self.interval_tx += 1
        self.resp_series.add(slice_id_for(bucket_ns=self.bucket_ns))


class StdioServer:
    """NDJSON `@nPoi` protocol on stdin/stdout (SSH stdio transport)."""

    def __init__(self, *, bucket_ms: int = 100, window_slices: int = 120) -> None:
        self.inner = Server("stdio", 0, bucket_ms=bucket_ms, window_slices=window_slices, telemetry=True)
        self.stopped = threading.Event()
        self._out_lock = threading.Lock()

    def run(self) -> int:
        # Unbuffered stdout for protocol; logs go to stderr only.
        try:
            sys.stdout.reconfigure(write_through=True)  # type: ignore[attr-defined]
        except Exception:
            pass
        self.inner.started = time.monotonic()
        buf = b""
        while not self.stopped.is_set():
            try:
                ready, _, _ = select.select([sys.stdin.buffer], [], [], 0.2)
            except (ValueError, OSError):
                break
            if not ready:
                continue
            chunk = os.read(sys.stdin.buffer.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > 1_000_000:
                    print("netpoisson: stdio line too long", file=sys.stderr)
                    return 2
                self._on_line(line)
        return 0

    def stop(self) -> None:
        self.stopped.set()

    def _write(self, data: bytes) -> None:
        with self._out_lock:
            os.write(sys.stdout.buffer.fileno(), data)

    def _on_line(self, line: bytes) -> None:
        text_line = line if line.endswith(b"\n") else line + b"\n"
        kind, obj = stdio_parse_line(text_line)
        if kind == "noise":
            return
        if kind != "protocol" or obj is None:
            print("netpoisson: protocol error on stdio line", file=sys.stderr)
            return
        t = obj.get("t")
        seq = int(obj.get("i", 0))
        client_ns = int(obj.get("cs", 0))
        now = time.monotonic_ns()
        if t == "hello":
            self._write(stdio_hello_ack(seq, now))
            return
        if t == "e":
            data = stdio_decode_data(obj)
            if data is None:
                print("netpoisson: bad echo data", file=sys.stderr)
                return
            with self.inner.stat_lock:
                self.inner.echo_rx += 1
                self.inner.interval_rx += 1
                self.inner.rx_bytes += len(line)
            self.inner.recv_series.add(slice_id_for(now, self.inner.bucket_ns))
            depths = self.inner.queue_depths()
            telem = {"rq": depths[0], "pc": depths[1], "sq": depths[2]}
            tx_ns = time.monotonic_ns()
            out = stdio_echo_ack(
                seq,
                client_ns,
                data,
                server_rx_ns=now,
                server_tx_ns=tx_ns,
                telemetry=telem,
                b64=obj.get("enc") == "b64",
            )
            self._write(out)
            with self.inner.stat_lock:
                self.inner.echo_tx += 1
                self.inner.interval_tx += 1
                self.inner.tx_bytes += len(out)
            self.inner.resp_series.add(slice_id_for(tx_ns, self.inner.bucket_ns))
            return
        if t == "s":
            with self.inner.stat_lock:
                self.inner.status_rx += 1
            snap = self.inner.make_snapshot()
            out = stdio_status_ack(seq, client_ns, snap)
            self._write(out)
            with self.inner.stat_lock:
                self.inner.tx_bytes += len(out)
            return
