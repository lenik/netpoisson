#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Server peer for ECHO and STATUS."""

from __future__ import annotations

import queue
import socket
import threading
import time

from protocol import (
    ECHO,
    REQ,
    STATUS,
    decode_frame,
    dispatch_request,
    encode_status_payload,
    pop_frames,
)
from traffic import (
    SLICE_MS,
    VISIBLE,
    CounterSeries,
    build_notes,
    format_server_lines,
    newest_first,
    pairs_oldest_first,
    rtt_summary,
    slice_id,
)
from netio_sock import _READ_TIMEOUT, _Conn, _close_socket, listen_socket

class Server:
    def __init__(self, host: str, port: int, udp: bool = False) -> None:
        self.host = host
        self.port = port
        self.udp = udp
        self.stopped = threading.Event()
        self.lock = threading.Lock()
        self.stat_lock = threading.Lock()
        self.conns: set[_Conn] = set()
        self.lsock: socket.socket | None = None
        self.udp_rx: queue.Queue = queue.Queue()
        self.udp_tx: queue.Queue = queue.Queue()
        self.req_series = CounterSeries()
        self.resp_series = CounterSeries()
        self.echo_rx = 0
        self.echo_tx = 0
        self.status_rx = 0
        self.started = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.rx_max = 0
        self.tx_max = 0

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

    def queue_depths(self) -> tuple[int, int]:
        if self.udp:
            rx = self.udp_rx.qsize()
            tx = self.udp_tx.qsize()
        else:
            with self.lock:
                conns = list(self.conns)
            rx = sum(conn.rxq.qsize() for conn in conns)
            tx = sum(conn.txq.qsize() for conn in conns)
        if rx > self.rx_max:
            self.rx_max = rx
        if tx > self.tx_max:
            self.tx_max = tx
        return rx, tx

    def snapshot(self) -> dict:
        sid = slice_id()
        req = newest_first(self.req_series.snapshot(), sid, VISIBLE)
        resp = newest_first(self.resp_series.snapshot(), sid, VISIBLE)
        rx, tx = self.queue_depths()
        with self.stat_lock:
            echo_rx = self.echo_rx
            echo_tx = self.echo_tx
            status_rx = self.status_rx
        duration = max(0.0, time.monotonic() - self.started)
        width = 2
        ascii_lines = format_server_lines(req, resp, rx, tx, width=width, half=12)
        view = {
            "role": "server",
            "proto": "udp" if self.udp else "tcp",
            "endpoint": self.endpoint(),
            "lambda_rps": None,
            "slice_ms": SLICE_MS,
            "duration_s": round(duration, 3),
            "uptime_s": round(duration, 3),
            "req": req,
            "resp": resp,
            "queues": {"client_tx": None, "server_rx": rx, "server_tx": tx},
            "client_tx": None,
            "server_rx": rx,
            "server_tx": tx,
            "wire_sent": echo_tx,
            "received": echo_rx,
            "offered": echo_rx,
            "lost": 0,
            "echo": {"received": echo_rx, "sent": echo_tx},
            "status_received": status_rx,
            "rx_queue_max": self.rx_max,
            "tx_queue_max": self.tx_max,
            "rtt_us": rtt_summary([]),
            "jitter_rfc3550_us": None,
            "samples": {"rtt_us": [], "client_tx": [], "server_rx": [], "server_tx": []},
            "histogram": [],
            "achieved_rps": round(echo_rx / duration, 2) if duration else 0,
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
            "slice_ms": SLICE_MS,
            "duration_s": snap["duration_s"],
            "requests": snap["echo"]["received"],
            "responses": snap["echo"]["sent"],
            "status_requests": snap["status_received"],
            "rx_queue_max": snap["rx_queue_max"],
            "tx_queue_max": snap["tx_queue_max"],
            "req_timeline": snap["req"],
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
                sock, addr = self.lsock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(_READ_TIMEOUT)
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
                buf += data
                if len(buf) > 1_000_000:
                    break
                frames, buf = pop_frames(buf)
                now = time.time_ns()
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
            resp = self._handle(raw, recv_ns)
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
            if data:
                self.udp_rx.put((data, addr, time.time_ns()))

    def _udp_work(self) -> None:
        while not self.stopped.is_set():
            try:
                raw, addr, recv_ns = self.udp_rx.get(timeout=0.1)
            except queue.Empty:
                continue
            resp = self._handle(raw, recv_ns)
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
            try:
                self.lsock.sendto(resp, addr)
            except OSError:
                if self.stopped.is_set():
                    break
                continue
            self._note_sent(resp)

    def _handle(self, raw: bytes, recv_ns: int) -> bytes | None:
        frame = decode_frame(raw)
        if frame is None or frame.role != REQ:
            return None
        if frame.mtype == ECHO:
            with self.stat_lock:
                self.echo_rx += 1
            self.req_series.add(slice_id(recv_ns))
        elif frame.mtype == STATUS:
            with self.stat_lock:
                self.status_rx += 1

        def payload(got_ns: int) -> bytes:
            rx, tx = self.queue_depths()
            sid = slice_id()
            pairs = pairs_oldest_first(self.resp_series.snapshot(), sid, 48)
            return encode_status_payload(got_ns, time.time_ns(), rx, tx, SLICE_MS, pairs)

        return dispatch_request(raw, recv_ns, payload)

    def _note_sent(self, resp: bytes) -> None:
        frame = decode_frame(resp)
        if frame is None or frame.mtype != ECHO:
            return
        with self.stat_lock:
            self.echo_tx += 1
        self.resp_series.add(slice_id())


