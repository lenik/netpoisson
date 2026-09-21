#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Mixin."""

from __future__ import annotations

import time

from protocol import ECHO, encode_echo_request, stdio_echo_req
from traffic import Pending, expovariate
from peer_client.types import Outgoing



class ClientScheduleMixin:
    def _schedule(self) -> None:
        # Absolute Poisson schedule on the monotonic clock.
        next_due = time.monotonic()
        while not self.sched_stop.is_set() and not self.stopped.is_set():
            next_due += expovariate(self.lam, self.rng)
            while True:
                wait = next_due - time.monotonic()
                if wait <= 0:
                    break
                if self.sched_stop.wait(min(wait, 0.05)):
                    return
            if self.sched_stop.is_set() or self.stopped.is_set():
                return
            # Keep generating even if the writer is blocked (client_pending grows).
            if self.max_pending is not None and self.txq.qsize() >= self.max_pending:
                # Still count as offered into pending by waiting briefly for room.
                while self.txq.qsize() >= self.max_pending and not self.sched_stop.is_set():
                    if self.sched_stop.wait(0.001):
                        return
            planned = int(next_due * 1_000_000_000)
            self._offer_echo(planned)

    def _offer_echo(self, planned_mono: int) -> None:
        now_mono = time.monotonic_ns()
        skew = (now_mono - planned_mono) / 1000.0
        with self.state:
            previous = self.last_offer
            self.last_offer = now_mono
            self.offered += 1
            self.skew_us.append(skew)
            if len(self.skew_us) > 20000:
                self.skew_us = self.skew_us[-10000:]
        if previous is not None:
            self.samples.add_gap((now_mono - previous) / 1000.0)
        seq = self._next_seq()
        payload = self._payload()
        if self.stdio:
            raw = stdio_echo_req(seq, now_mono, payload)
        else:
            raw = encode_echo_request(seq, now_mono, payload)
        self.book.add(
            Pending(seq=seq, mtype=ECHO, create_mono=now_mono, raw=raw, track_loss=True)
        )
        # Req. counts successful socket submissions happen in _send; pending grows here.
        self.txq.put(
            Outgoing(
                seq=seq,
                mtype=ECHO,
                raw=raw,
                create_mono=now_mono,
                planned_mono=planned_mono,
                payload=payload,
            )
        )
        self._note_depth()

