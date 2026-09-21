#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Client peer: Poisson ECHO load plus STATUS probes."""

from __future__ import annotations

import queue
import socket
import threading
import time
from dataclasses import dataclass

from protocol import (
    ECHO,
    ECHO_PAYLOAD,
    STATUS,
    decode_frame,
    decode_status_payload,
    encode_request,
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
    format_status_lines,
    histogram,
    newest_first,
    rtt_summary,
    slice_id,
    update_jitter,
)
from netio_sock import _close_socket, connect_socket

@dataclass
class Outgoing:
    seq: int
    mtype: int
    raw: bytes
    create_mono: int


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

