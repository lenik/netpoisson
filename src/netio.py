#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TCP and UDP peers.

The client offers ECHO requests as a Poisson process on one socket. A second
socket carries STATUS probes so the dashboard can still see server queues
when the data socket is backed up.
"""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass

from protocol import (
    ECHO,
    ECHO_PAYLOAD,
    REQ,
    STATUS,
    decode_frame,
    decode_status_payload,
    dispatch_request,
    encode_request,
    encode_status_payload,
    pop_frames,
)
from traffic import (
    SLICE_MS,
    VISIBLE,
    CounterSeries,
    InflightBook,
    Pending,
    SampleWindow,
    best_lag,
    build_notes,
    cell_width,
    choose_half,
    expovariate,
    format_server_lines,
    format_status_lines,
    histogram,
    newest_first,
    pairs_oldest_first,
    rtt_summary,
    slice_id,
    update_jitter,
)

_READ_TIMEOUT = 0.3


@dataclass
class Outgoing:
    seq: int
    mtype: int
    raw: bytes
    create_mono: int


def _close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def listen_socket(host: str, port: int, udp: bool) -> socket.socket:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    infos = socket.getaddrinfo(host, port, type=kind, flags=socket.AI_PASSIVE)
    if not infos:
        raise OSError(f"cannot resolve {host}")
    af, socktype, proto, _canon, sa = infos[0]
    sock = socket.socket(af, socktype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(sa)
    if not udp:
        sock.listen(128)
    sock.settimeout(_READ_TIMEOUT)
    return sock


def connect_socket(host: str, port: int, udp: bool, timeout: float = 2.0) -> socket.socket:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    infos = socket.getaddrinfo(host, port, type=kind)
    last: Exception | None = None
    for af, socktype, proto, _canon, sa in infos:
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sa)
        except OSError as exc:
            last = exc
            sock.close()
            continue
        if not udp:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.settimeout(_READ_TIMEOUT)
        return sock
    raise OSError(last or f"cannot connect to {host}:{port}")


class _Conn:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.rxq: queue.Queue = queue.Queue()
        self.txq: queue.Queue = queue.Queue()
        self.closed = threading.Event()


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


class Client:
    def __init__(
        self,
        host: str,
        port: int,
        *,
        udp: bool = False,
        lam: float = 100.0,
        timeout_s: float = 2.0,
    ) -> None:
        self.host = host
        self.port = port
        self.udp = udp
        self.lam = lam
        self.stopped = threading.Event()
        self.sched_stop = threading.Event()
        self.data: socket.socket | None = None
        self.ctrl: socket.socket | None = None
        self.ctrl_lock = threading.Lock()
        self.txq: queue.Queue[Outgoing] = queue.Queue()
        self.book = InflightBook(timeout_s=timeout_s)
        self.req_series = CounterSeries()
        self.server_resp: dict[int, int] = {}
        self.server_lock = threading.Lock()
        self.server_rx: int | None = None
        self.server_tx: int | None = None
        self.server_latest_sid: int | None = None
        self.samples = SampleWindow()
        self.state = threading.Lock()
        self.seq = 0
        self.offered = 0
        self.wire_sent = 0
        self.received = 0
        self.late = 0
        self.mismatch = 0
        self.status_sent = 0
        self.status_received = 0
        self.jitter = 0.0
        self.prev_rtt: float | None = None
        self.tx_max = 0
        self.last_offer: int | None = None
        self.started = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.error: str | None = None

    def endpoint(self) -> str:
        return f"{self.host}:{self.port}"

    def start(self) -> None:
        deadline = time.monotonic() + 2.0
        while True:
            try:
                self.data = connect_socket(self.host, self.port, self.udp)
                try:
                    self.ctrl = connect_socket(self.host, self.port, self.udp)
                except OSError:
                    _close_socket(self.data)
                    self.data = None
                    raise
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self.started = time.monotonic()
        self._spawn(self._schedule, "schedule")
        self._spawn(self._send, "send")
        self._spawn(self._probe, "probe")
        self._spawn(self._read, "read-data", self.data)
        self._spawn(self._read, "read-ctrl", self.ctrl)
        self._spawn(self._reap, "reap")
        self._spawn(self._sample, "sample")

    def stop(self, grace: float = 1.0) -> None:
        self.sched_stop.set()
        deadline = time.monotonic() + grace
        while time.monotonic() < deadline:
            if self.txq.empty() and self.book.waiting() == 0:
                break
            time.sleep(0.02)
        self.stopped.set()
        _close_socket(self.data)
        _close_socket(self.ctrl)
        for thread in self.threads:
            thread.join(timeout=1.0)
        self.book.reap(time.monotonic_ns(), force=True)

    def status_lines(self, columns: int = 100, width: int | None = None, half: int | None = None) -> tuple[str, str]:
        sid = slice_id()
        req = newest_first(self.req_series.snapshot(), sid, VISIBLE)
        with self.server_lock:
            resp_map = dict(self.server_resp)
            rx = self.server_rx
            tx = self.server_tx
        resp = newest_first(resp_map, sid, VISIBLE)
        if width is None:
            width = cell_width(self.lam)
        if half is None:
            half = choose_half(columns, width)
        half = min(half, VISIBLE // 2)
        return format_status_lines(
            req,
            resp,
            self.txq.qsize(),
            rx,
            tx,
            width=width,
            half=half,
        )

    def snapshot(self) -> dict:
        view = self._view(final=False)
        copied = self.samples.copy()
        view["samples"] = {
            "rtt_us": [round(v, 1) for v in copied["spark_us"]],
            "client_tx": copied["client_tx"],
            "server_rx": copied["server_rx"],
            "server_tx": copied["server_tx"],
        }
        view["histogram"] = histogram(copied["rtt_us"])
        view["notes"] = build_notes(view)
        view.pop("gaps_us", None)
        width = cell_width(self.lam)
        view["ascii"] = list(self.status_lines(120, width=width, half=12))
        view["queues"] = {
            "client_tx": view["client_tx"],
            "server_rx": view["server_rx"],
            "server_tx": view["server_tx"],
        }
        return view

    def report(self) -> dict:
        view = self._view(final=True)
        view["notes"] = build_notes(view)
        view.pop("gaps_us", None)
        rtt = view["rtt_us"]
        return {
            "role": "client",
            "proto": view["proto"],
            "target": view["endpoint"],
            "lambda_rps": self.lam,
            "slice_ms": SLICE_MS,
            "duration_s": view["duration_s"],
            "offered": view["offered"],
            "sent": view["wire_sent"],
            "received": view["received"],
            "lost": view["lost"],
            "late": view["late"],
            "unsent": view["unsent"],
            "mismatch": view["mismatch"],
            "loss_ratio": view["loss_ratio"],
            "achieved_rps": view["achieved_rps"],
            "offered_rps": view["offered_rps"],
            "rtt_us": rtt,
            "jitter_rfc3550_us": view["jitter_rfc3550_us"],
            "queue_delay_us": view["queue_delay_us"],
            "server_hold_us": view["server_hold_us"],
            "interarrival_us": view["interarrival_us"],
            "queues": {
                "client_tx": {"last": view["client_tx"], "max": self.tx_max},
                "server_rx": {"last": view["server_rx"]},
                "server_tx": {"last": view["server_tx"]},
            },
            "timeline_lag_slices": view["timeline_lag_slices"],
            "timeline_lag_ms": None if view["timeline_lag_slices"] is None else view["timeline_lag_slices"] * SLICE_MS,
            "clock_skew_slices": view["clock_skew_slices"],
            "req_timeline": view["req"],
            "resp_timeline": view["resp"],
            "status": {"sent": view["status_sent"], "received": view["status_received"]},
            "notes": view["notes"],
        }

    def _view(self, final: bool) -> dict:
        copied = self.samples.copy()
        rtt_src = copied["all_rtt"] if final else copied["rtt_us"]
        sid = slice_id()
        req = newest_first(self.req_series.snapshot(), sid, VISIBLE)
        with self.server_lock:
            resp_map = dict(self.server_resp)
            server_rx = self.server_rx
            server_tx = self.server_tx
            latest = self.server_latest_sid
        resp = newest_first(resp_map, sid, VISIBLE)
        # Correlation wants oldest-first.
        req_old = list(reversed(req))
        resp_old = list(reversed(resp))
        lag = best_lag(req_old, resp_old)
        skew = None if latest is None else sid - latest
        duration = max(0.0, time.monotonic() - self.started)
        with self.state:
            offered = self.offered
            wire_sent = self.wire_sent
            received = self.received
            late = self.late
            mismatch = self.mismatch
            status_sent = self.status_sent
            status_received = self.status_received
            jitter = self.jitter
        lost = self.book.lost
        unsent = self.book.unsent
        loss_ratio = (lost / wire_sent) if wire_sent else 0.0
        gaps = copied["gaps_us"]
        gmean = sum(gaps) / len(gaps) if gaps else None
        return {
            "role": "client",
            "proto": "udp" if self.udp else "tcp",
            "endpoint": self.endpoint(),
            "lambda_rps": self.lam,
            "slice_ms": SLICE_MS,
            "duration_s": round(duration, 3),
            "uptime_s": round(duration, 3),
            "req": req,
            "resp": resp,
            "client_tx": self.txq.qsize(),
            "server_rx": server_rx,
            "server_tx": server_tx,
            "offered": offered,
            "wire_sent": wire_sent,
            "received": received,
            "lost": lost,
            "late": late,
            "unsent": unsent,
            "mismatch": mismatch,
            "loss_ratio": round(loss_ratio, 4),
            "achieved_rps": round(wire_sent / duration, 2) if duration else 0,
            "offered_rps": round(offered / duration, 2) if duration else 0,
            "rtt_us": rtt_summary(rtt_src),
            "jitter_rfc3550_us": round(jitter, 1) if received else None,
            "queue_delay_us": rtt_summary(copied["queue_delay_us"]),
            "server_hold_us": rtt_summary(copied["hold_us"]),
            "interarrival_us": {
                "n": len(gaps),
                "mean": None if gmean is None else round(gmean, 1),
                "expected": round(1_000_000 / self.lam, 1) if self.lam else None,
            },
            "gaps_us": gaps,
            "timeline_lag_slices": lag,
            "clock_skew_slices": skew,
            "status_sent": status_sent,
            "status_received": status_received,
        }

    def _spawn(self, target, name: str, *args) -> None:
        thread = threading.Thread(target=target, name=name, args=args, daemon=True)
        thread.start()
        self.threads.append(thread)

    def _next_seq(self) -> int:
        with self.state:
            self.seq += 1
            return self.seq

    def _note_depth(self) -> None:
        depth = self.txq.qsize()
        if depth > self.tx_max:
            self.tx_max = depth

    def _schedule(self) -> None:
        next_t = time.monotonic()
        while not self.sched_stop.is_set() and not self.stopped.is_set():
            next_t += expovariate(self.lam)
            wait = next_t - time.monotonic()
            if wait > 0:
                if self.sched_stop.wait(wait):
                    return
            elif time.monotonic() - next_t > 1.0:
                next_t = time.monotonic()
            if self.sched_stop.is_set() or self.stopped.is_set():
                return
            self._offer_echo()

    def _offer_echo(self) -> None:
        now_mono = time.monotonic_ns()
        with self.state:
            previous = self.last_offer
            self.last_offer = now_mono
            self.offered += 1
        if previous is not None:
            self.samples.add_gap((now_mono - previous) / 1000.0)
        seq = self._next_seq()
        raw = encode_request(ECHO, seq, time.time_ns(), ECHO_PAYLOAD)
        self.book.add(Pending(seq=seq, mtype=ECHO, create_mono=now_mono, raw=raw))
        self.req_series.add(slice_id())
        self.txq.put(Outgoing(seq=seq, mtype=ECHO, raw=raw, create_mono=now_mono))
        self._note_depth()

    def _send(self) -> None:
        assert self.data is not None
        while not self.stopped.is_set() or not self.txq.empty():
            try:
                item = self.txq.get(timeout=0.05)
            except queue.Empty:
                if self.stopped.is_set():
                    break
                continue
            try:
                self.data.sendall(item.raw)
            except OSError as exc:
                self.book.drop_unsent(item.seq)
                self.error = str(exc)
                self.stopped.set()
                break
            self.book.mark_sent(item.seq, time.monotonic_ns())
            with self.state:
                self.wire_sent += 1
            self._note_depth()
            if self.stopped.is_set() and self.txq.empty():
                break

    def _probe(self) -> None:
        while not self.stopped.is_set():
            self._send_status()
            if self.stopped.wait(SLICE_MS / 1000):
                return

    def _send_status(self) -> None:
        if self.ctrl is None or self.stopped.is_set():
            return
        now_mono = time.monotonic_ns()
        seq = self._next_seq()
        raw = encode_request(STATUS, seq, time.time_ns(), b"")
        self.book.add(Pending(seq=seq, mtype=STATUS, create_mono=now_mono, raw=raw, track_loss=False))
        try:
            with self.ctrl_lock:
                self.ctrl.sendall(raw)
        except OSError:
            self.book.drop_unsent(seq)
            return
        self.book.mark_sent(seq, time.monotonic_ns())
        with self.state:
            self.status_sent += 1

    def _read(self, sock: socket.socket | None) -> None:
        if sock is None:
            return
        buf = b""
        while not self.stopped.is_set():
            try:
                data = sock.recv(65536)
            except TimeoutError:
                continue
            except OSError:
                break
            if not data:
                break
            if self.udp:
                self._on_datagram(data)
                continue
            buf += data
            frames, buf = pop_frames(buf)
            for raw in frames:
                self._on_frame(raw)
        if buf and not self.udp:
            frames, _rest = pop_frames(buf)
            for raw in frames:
                self._on_frame(raw)

    def _on_datagram(self, data: bytes) -> None:
        frame = decode_frame(data)
        if frame is not None:
            self._on_frame(data)

    def _on_frame(self, raw: bytes) -> None:
        now = time.monotonic_ns()
        frame = decode_frame(raw)
        if frame is None:
            return
        kind, pending = self.book.take(frame.seq)
        if pending is None or kind == "unknown":
            return
        if pending.mtype == ECHO and raw != pending.raw:
            with self.state:
                self.mismatch += 1
        rtt_us = None
        queue_delay = None
        if pending.send_mono is not None:
            rtt_us = (now - pending.send_mono) / 1000.0
            queue_delay = (pending.send_mono - pending.create_mono) / 1000.0
        with self.state:
            if kind == "late":
                self.late += 1
            if pending.mtype == ECHO:
                self.received += 1
                if rtt_us is not None:
                    self.jitter, self.prev_rtt = update_jitter(self.jitter, self.prev_rtt, rtt_us)
            elif pending.mtype == STATUS:
                self.status_received += 1
        if pending.mtype == ECHO and rtt_us is not None:
            self.samples.add_rtt(rtt_us, queue_delay)
        if pending.mtype == STATUS:
            self._apply_status(frame, rtt_us)

    def _apply_status(self, frame, rtt_us: float | None) -> None:
        body = decode_status_payload(frame.payload)
        if body is None:
            with self.state:
                self.mismatch += 1
            return
        hold = (body["send_ns"] - body["recv_ns"]) / 1000.0
        if hold >= 0:
            self.samples.add_hold(hold)
        with self.server_lock:
            self.server_rx = int(body["rx_queued"])
            self.server_tx = int(body["tx_queued"])
            for sid, count in body["slices"]:
                self.server_resp[sid] = count
            if body["slices"]:
                self.server_latest_sid = body["slices"][-1][0]
            cutoff = slice_id() - 200
            for key in list(self.server_resp):
                if key < cutoff:
                    del self.server_resp[key]
        del rtt_us

    def _reap(self) -> None:
        while not self.stopped.is_set():
            self.book.reap(time.monotonic_ns())
            if self.stopped.wait(0.2):
                return

    def _sample(self) -> None:
        while not self.stopped.is_set():
            with self.server_lock:
                rx = self.server_rx
                tx = self.server_tx
            self.samples.add_queues(self.txq.qsize(), rx, tx)
            if self.stopped.wait(0.05):
                return

