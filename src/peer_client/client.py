#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TCP/UDP/SSH client peer."""

from __future__ import annotations

import queue
import random
import socket
import threading
import time

from netio_sock import _close_socket, connect_socket, open_datagram, seal_datagram
from peer_client.binary_io import ClientBinaryMixin
from peer_client.reporting import ClientReportingMixin
from peer_client.schedule import ClientScheduleMixin
from peer_client.stdio_io import ClientStdioMixin
from peer_client.types import Outgoing, SshTransport
from traffic import CounterSeries, InflightBook, SampleWindow

class Client(ClientReportingMixin, ClientScheduleMixin, ClientBinaryMixin, ClientStdioMixin):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        udp: bool = False,
        lam: float = 100.0,
        timeout_s: float = 2.0,
        payload_size: int = 64,
        connections: int = 1,
        max_pending: int | None = None,
        status_interval_ms: int = 100,
        bucket_ms: int = 100,
        window_slices: int = 120,
        seed: int | None = None,
        ssh: SshTransport | None = None,
        payload_min: int | None = None,
        payload_max: int | None = None,
        ssl_ctx=None,
        udp_seal=None,
    ) -> None:
        self.host = host
        self.port = port
        self.udp = udp
        self.ssl_ctx = ssl_ctx
        self.udp_seal = udp_seal
        self.lam = lam
        self.payload_size = max(0, payload_size)
        self.payload_min = payload_min
        self.payload_max = payload_max
        self.connections = max(1, connections)
        self.max_pending = max_pending
        self.status_interval_ms = max(1, status_interval_ms)
        self.bucket_ms = max(1, bucket_ms)
        self.bucket_ns = self.bucket_ms * 1_000_000
        self.window_slices = max(8, window_slices)
        self.rng = random.Random(seed)
        self.ssh = ssh
        self.stdio = ssh is not None
        self.stopped = threading.Event()
        self.sched_stop = threading.Event()
        self.socks: list[socket.socket] = []
        self.ctrl: socket.socket | None = None
        self.ctrl_lock = threading.Lock()
        self.txq: queue.Queue[Outgoing] = queue.Queue()
        self.book = InflightBook(timeout_s=timeout_s)
        self.req_series = CounterSeries(keep=self.window_slices * 2)
        self.recv_series = CounterSeries(keep=self.window_slices * 2)  # server received (from STATUS)
        self.resp_series = CounterSeries(keep=self.window_slices * 2)  # server transmitted
        self.ack_series = CounterSeries(keep=self.window_slices * 2)
        self.server_lock = threading.Lock()
        self.read_pending: int | None = None
        self.processing: int | None = None
        self.response_pending: int | None = None
        self.server_rx_bytes = 0
        self.server_tx_bytes = 0
        self.samples = SampleWindow()
        self.state = threading.Lock()
        self.seq = 0
        self.offered = 0
        self.wire_sent = 0
        self.received = 0
        self.late = 0
        self.mismatch = 0
        self.seq_mismatch = 0
        self.status_sent = 0
        self.status_received = 0
        self.protocol_errors = 0
        self.remote_noise = 0
        self.jitter = 0.0
        self.prev_rtt: float | None = None
        self.tx_max = 0
        self.skew_us: list[float] = []
        self.last_offer: int | None = None
        self.started = time.monotonic()
        self.threads: list[threading.Thread] = []
        self.error: str | None = None
        self.session_rx_bytes = 0
        self.session_tx_bytes = 0
        self._stdio_buf = b""
        self._stdio_lock = threading.Lock()

    def endpoint(self) -> str:
        if self.ssh:
            return f"ssh:{self.host}"
        return f"{self.host}:{self.port}"

    def start(self) -> None:
        if self.ssh:
            self._wait_hello()
        else:
            deadline = time.monotonic() + 2.0
            while True:
                try:
                    n = 1 if self.udp else self.connections
                    self.socks = [
                        connect_socket(self.host, self.port, self.udp, ssl_ctx=None if self.udp else self.ssl_ctx)
                        for _ in range(n)
                    ]
                    if not self.udp:
                        self.ctrl = connect_socket(self.host, self.port, self.udp, ssl_ctx=self.ssl_ctx)
                    break
                except OSError:
                    for sock in self.socks:
                        _close_socket(sock)
                    self.socks = []
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
        self.started = time.monotonic()
        self._spawn(self._schedule, "schedule")
        self._spawn(self._send, "send")
        if not self.stdio:
            self._spawn(self._probe, "probe")
            for i, sock in enumerate(self.socks):
                self._spawn(self._read_sock, f"read-{i}", sock)
            if self.ctrl is not None:
                self._spawn(self._read_sock, "read-ctrl", self.ctrl)
        else:
            self._spawn(self._probe_stdio, "probe")
            self._spawn(self._read_stdio, "read-stdio")
            self._spawn(self._read_ssh_err, "ssh-err")
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
        for sock in self.socks:
            _close_socket(sock)
        _close_socket(self.ctrl)
        if self.ssh is not None:
            try:
                if self.ssh.proc.stdin:
                    self.ssh.proc.stdin.close()
            except OSError:
                pass
            try:
                self.ssh.proc.terminate()
            except OSError:
                pass
            try:
                self.ssh.proc.wait(timeout=2)
            except Exception:
                try:
                    self.ssh.proc.kill()
                except OSError:
                    pass
            for stream in (self.ssh.proc.stdout, self.ssh.proc.stderr):
                try:
                    if stream is not None:
                        stream.close()
                except OSError:
                    pass
        for thread in self.threads:
            thread.join(timeout=1.0)
        self.book.reap(time.monotonic_ns(), force=True)

    def _spawn(self, target, name: str, *args) -> None:
        thread = threading.Thread(target=target, name=name, args=args, daemon=True)
        thread.start()
        self.threads.append(thread)

    def _next_seq(self) -> int:
        with self.state:
            self.seq += 1
            return self.seq

    def _payload(self) -> bytes:
        if self.payload_min is not None and self.payload_max is not None:
            n = self.rng.randint(self.payload_min, self.payload_max)
        else:
            n = self.payload_size
        if n <= 0:
            return b""
        # Reproducible pseudo-content.
        return bytes(self.rng.getrandbits(8) for _ in range(n))

    def _note_depth(self) -> None:
        depth = self.txq.qsize()
        if depth > self.tx_max:
            self.tx_max = depth

    def _reap(self) -> None:
        while not self.stopped.is_set():
            self.book.reap(time.monotonic_ns())
            if self.stopped.wait(0.2):
                return

    def _sample(self) -> None:
        while not self.stopped.is_set():
            with self.server_lock:
                rx = self.read_pending
                tx = self.response_pending
            self.samples.add_queues(self.txq.qsize(), rx, tx)
            if self.stopped.wait(0.05):
                return
