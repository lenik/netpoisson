#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Binary TCP/UDP send and receive paths."""

from __future__ import annotations

import queue
import socket
import time

from netio_sock import open_datagram, seal_datagram
from protocol import (
    ECHO,
    STATUS,
    decode_pdu,
    decode_status_body,
    encode_status_request,
    pop_pdus,
)
from traffic import Pending, slice_id_for, update_jitter


class ClientBinaryMixin:
    def _send(self) -> None:
        rr = 0
        while not self.stopped.is_set() or not self.txq.empty():
            try:
                item = self.txq.get(timeout=0.05)
            except queue.Empty:
                if self.stopped.is_set():
                    break
                continue
            try:
                if self.stdio:
                    self._stdio_write(item.raw)
                else:
                    sock = self.socks[rr % len(self.socks)]
                    rr += 1
                    wire = seal_datagram(self.udp_seal, item.raw) if self.udp else item.raw
                    sock.sendall(wire)
            except OSError as exc:
                self.book.drop_unsent(item.seq)
                self.error = str(exc)
                self.stopped.set()
                break
            send_mono = time.monotonic_ns()
            self.book.mark_sent(item.seq, send_mono)
            self.req_series.add(slice_id_for(send_mono, self.bucket_ns))
            with self.state:
                self.wire_sent += 1
                self.session_tx_bytes += len(item.raw)
            self._note_depth()
            if self.stopped.is_set() and self.txq.empty():
                break

    def _probe(self) -> None:
        while not self.stopped.is_set():
            self._send_status()
            if self.stopped.wait(self.status_interval_ms / 1000):
                return

    def _send_status(self) -> None:
        sock = self.ctrl or (self.socks[0] if self.socks else None)
        if sock is None or self.stopped.is_set():
            return
        now_mono = time.monotonic_ns()
        seq = self._next_seq()
        raw = encode_status_request(seq, now_mono)
        self.book.add(Pending(seq=seq, mtype=STATUS, create_mono=now_mono, raw=raw, track_loss=False))
        try:
            with self.ctrl_lock:
                wire = seal_datagram(self.udp_seal, raw) if self.udp else raw
                sock.sendall(wire)
        except OSError:
            self.book.drop_unsent(seq)
            return
        self.book.mark_sent(seq, time.monotonic_ns())
        with self.state:
            self.status_sent += 1
            self.session_tx_bytes += len(raw)

    def _read_sock(self, sock: socket.socket) -> None:
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
            with self.state:
                self.session_rx_bytes += len(data)
            if self.udp:
                plain = open_datagram(self.udp_seal, data)
                if plain:
                    self._on_binary(plain)
                continue
            buf += data
            frames, buf = pop_pdus(buf)
            for raw in frames:
                self._on_binary(raw)

    def _on_binary(self, raw: bytes) -> None:
        now = time.monotonic_ns()
        frame = decode_pdu(raw)
        if frame is None or not frame.is_resp:
            with self.state:
                self.protocol_errors += 1
            return
        kind, pending = self.book.take(frame.seq)
        if kind == "duplicate":
            return
        if pending is None:
            return
        if pending.mtype == ECHO:
            req_frame = decode_pdu(pending.raw)
            if req_frame is None or frame.payload != req_frame.payload or frame.seq != req_frame.seq:
                with self.state:
                    self.mismatch += 1
            if frame.seq != pending.seq:
                with self.state:
                    self.seq_mismatch += 1
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
        if pending.mtype == ECHO:
            self.ack_series.add(slice_id_for(now, self.bucket_ns))
            if rtt_us is not None:
                self.samples.add_rtt(
                    rtt_us,
                    queue_delay,
                    send_mono=pending.send_mono,
                    bucket_ns=self.bucket_ns,
                    seq=pending.seq,
                )
            if frame.telemetry:
                self._apply_telemetry(frame.telemetry)
        if pending.mtype == STATUS:
            self._apply_status(frame.payload)

    def _apply_telemetry(self, tm: dict) -> None:
        with self.server_lock:
            self.read_pending = int(tm.get("read_pending") or 0)
            self.processing = int(tm.get("processing") or 0)
            self.response_pending = int(tm.get("response_pending") or 0)
            self.server_rx_bytes = int(tm.get("rx_bytes") or 0)
            self.server_tx_bytes = int(tm.get("tx_bytes") or 0)

    def _apply_status(self, payload: bytes) -> None:
        body = decode_status_body(payload)
        if body is None:
            with self.state:
                self.mismatch += 1
            return
        with self.server_lock:
            self.read_pending = body.read_pending
            self.processing = body.processing
            self.response_pending = body.response_pending
            self.server_rx_bytes = body.rx_bytes
            self.server_tx_bytes = body.tx_bytes
            sid = slice_id_for(bucket_ns=self.bucket_ns)
            self.recv_series.replace_window(sid, list(body.recv_slices))
            self.resp_series.replace_window(sid, list(body.resp_slices))
