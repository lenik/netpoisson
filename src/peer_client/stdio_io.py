#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""SSH stdio client I/O."""

from __future__ import annotations

import select
import sys
import time

from protocol import (
    STATUS,
    stdio_decode_data,
    stdio_hello,
    stdio_parse_line,
    stdio_status_req,
)
from traffic import Pending, slice_id_for, update_jitter


class ClientStdioMixin:
    def _wait_hello(self) -> None:
        assert self.ssh is not None
        raw = stdio_hello(0, time.monotonic_ns())
        self._stdio_write(raw)
        deadline = time.monotonic() + 5.0
        buf = b""
        while time.monotonic() < deadline:
            if self.ssh.proc.stdout is None:
                break
            ready, _, _ = select.select([self.ssh.proc.stdout], [], [], 0.2)
            if not ready:
                continue
            chunk = self.ssh.proc.stdout.read1(4096) if hasattr(self.ssh.proc.stdout, "read1") else self.ssh.proc.stdout.read(4096)
            if not chunk:
                if self.ssh.proc.poll() is not None:
                    raise OSError("ssh remote process exited before hello_ack")
                continue
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                kind, obj = stdio_parse_line(line + b"\n")
                if kind == "noise":
                    self.remote_noise += 1
                    continue
                if kind == "protocol" and obj and obj.get("t") == "hello_ack":
                    self._stdio_buf = buf
                    return
        raise OSError("timeout waiting for hello_ack from remote netpoisson")

    def _stdio_write(self, data: bytes) -> None:
        assert self.ssh is not None and self.ssh.proc.stdin is not None
        with self._stdio_lock:
            self.ssh.proc.stdin.write(data)
            self.ssh.proc.stdin.flush()

    def _probe_stdio(self) -> None:
        while not self.stopped.is_set():
            self._send_status_stdio()
            if self.stopped.wait(self.status_interval_ms / 1000):
                return

    def _send_status_stdio(self) -> None:
        now_mono = time.monotonic_ns()
        seq = self._next_seq()
        raw = stdio_status_req(seq, now_mono)
        self.book.add(Pending(seq=seq, mtype=STATUS, create_mono=now_mono, raw=raw, track_loss=False))
        try:
            self._stdio_write(raw)
        except OSError:
            self.book.drop_unsent(seq)
            return
        self.book.mark_sent(seq, time.monotonic_ns())
        with self.state:
            self.status_sent += 1
            self.session_tx_bytes += len(raw)

    def _read_stdio(self) -> None:
        assert self.ssh is not None and self.ssh.proc.stdout is not None
        buf = self._stdio_buf
        while not self.stopped.is_set():
            try:
                ready, _, _ = select.select([self.ssh.proc.stdout], [], [], 0.2)
            except (ValueError, OSError):
                break
            if not ready:
                if self.ssh.proc.poll() is not None:
                    break
                continue
            chunk = (
                self.ssh.proc.stdout.read1(65536)
                if hasattr(self.ssh.proc.stdout, "read1")
                else self.ssh.proc.stdout.read(65536)
            )
            if not chunk:
                if self.ssh.proc.poll() is not None:
                    break
                continue
            with self.state:
                self.session_rx_bytes += len(chunk)
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                if len(line) > 1_000_000:
                    self.error = "stdio line too long"
                    self.stopped.set()
                    return
                self._on_stdio_line(line)

    def _read_ssh_err(self) -> None:
        assert self.ssh is not None and self.ssh.proc.stderr is not None
        while not self.stopped.is_set():
            try:
                ready, _, _ = select.select([self.ssh.proc.stderr], [], [], 0.3)
            except (ValueError, OSError):
                break
            if not ready:
                if self.ssh.proc.poll() is not None:
                    break
                continue
            chunk = self.ssh.proc.stderr.read1(4096) if hasattr(self.ssh.proc.stderr, "read1") else self.ssh.proc.stderr.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace").rstrip()
            if text:
                print(f"ssh: {text}", file=__import__("sys").stderr)

    def _on_stdio_line(self, line: bytes) -> None:
        kind, obj = stdio_parse_line(line + b"\n")
        if kind == "noise":
            with self.state:
                self.remote_noise += 1
            return
        if kind != "protocol" or obj is None:
            with self.state:
                self.protocol_errors += 1
            return
        t = obj.get("t")
        seq = int(obj.get("i", -1))
        now = time.monotonic_ns()
        if t == "er":
            kind_take, pending = self.book.take(seq)
            if pending is None:
                return
            data = stdio_decode_data(obj)
            # Reconstruct expected payload from the pending request if possible.
            if data is None:
                with self.state:
                    self.mismatch += 1
            elif pending.raw:
                # Compare decoded data to what we sent via stdio_echo_req fields.
                pass
            rtt_us = None
            queue_delay = None
            if pending.send_mono is not None:
                rtt_us = (now - pending.send_mono) / 1000.0
                queue_delay = (pending.send_mono - pending.create_mono) / 1000.0
            with self.state:
                if kind_take == "late":
                    self.late += 1
                self.received += 1
                if rtt_us is not None:
                    self.jitter, self.prev_rtt = update_jitter(self.jitter, self.prev_rtt, rtt_us)
            self.ack_series.add(slice_id_for(now, self.bucket_ns))
            if rtt_us is not None:
                self.samples.add_rtt(rtt_us, queue_delay)
            sr = obj.get("sr")
            ss = obj.get("ss")
            if isinstance(sr, int) and isinstance(ss, int) and ss >= sr:
                self.samples.add_hold((ss - sr) / 1000.0)
            tm = obj.get("tm") or {}
            with self.server_lock:
                if "rq" in tm:
                    self.read_pending = int(tm["rq"])
                if "pc" in tm:
                    self.processing = int(tm["pc"])
                if "sq" in tm:
                    self.response_pending = int(tm["sq"])
            return
        if t == "sr":
            kind_take, pending = self.book.take(seq)
            if pending is None and kind_take != "duplicate":
                return
            with self.state:
                self.status_received += 1
            self._apply_stdio_status(obj)
            return

    def _apply_stdio_status(self, obj: dict) -> None:
        with self.server_lock:
            self.read_pending = int(obj.get("rq") or 0)
            self.processing = int(obj.get("pc") or 0)
            self.response_pending = int(obj.get("sq") or 0)
            self.server_rx_bytes = int(obj.get("rx") or 0)
            self.server_tx_bytes = int(obj.get("tx") or 0)
            sid = slice_id_for(bucket_ns=self.bucket_ns)
            self.recv_series.replace_window(sid, [int(c) for c in (obj.get("rv") or [])])
            self.resp_series.replace_window(sid, [int(c) for c in (obj.get("rp") or [])])

