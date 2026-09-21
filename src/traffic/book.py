#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""In-flight request bookkeeping."""

from __future__ import annotations

import threading
from dataclasses import dataclass

@dataclass
class Pending:
    seq: int
    mtype: int
    create_mono: int
    send_mono: int | None = None
    raw: bytes = b""
    track_loss: bool = True


class InflightBook:
    """Match responses to requests and time out ones that never return."""

    def __init__(self, timeout_s: float = 2.0, remember: int = 20000) -> None:
        self.timeout_ns = int(timeout_s * 1_000_000_000)
        self.remember = remember
        self.open: dict[int, Pending] = {}
        self.lost_seqs: dict[int, Pending] = {}
        self.seen_acks: dict[int, int] = {}
        self.lock = threading.Lock()
        self.lost = 0
        self.unsent = 0
        self.duplicates = 0
        self.reorder = 0
        self._expect_next = 1

    def add(self, item: Pending) -> None:
        with self.lock:
            self.open[item.seq] = item

    def mark_sent(self, seq: int, send_mono: int) -> None:
        with self.lock:
            item = self.open.get(seq)
            if item is not None:
                item.send_mono = send_mono

    def drop_unsent(self, seq: int) -> None:
        with self.lock:
            item = self.open.get(seq)
            if item is not None and item.send_mono is None:
                self.open.pop(seq, None)
                self.unsent += 1

    def take(self, seq: int) -> tuple[str, Pending | None]:
        with self.lock:
            if seq in self.seen_acks:
                self.duplicates += 1
                return "duplicate", None
            item = self.open.pop(seq, None)
            if item is not None:
                self.seen_acks[seq] = 1
                if item.mtype == 1 and seq > self._expect_next:
                    self.reorder += 1
                if item.mtype == 1:
                    self._expect_next = max(self._expect_next, seq + 1)
                if len(self.seen_acks) > self.remember:
                    for key in list(self.seen_acks)[: len(self.seen_acks) - self.remember]:
                        del self.seen_acks[key]
                return "ok", item
            late = self.lost_seqs.pop(seq, None)
            if late is not None:
                self.seen_acks[seq] = 1
                if late.track_loss:
                    self.lost = max(0, self.lost - 1)
                return "late", late
            return "unknown", None

    def waiting(self) -> int:
        with self.lock:
            return sum(1 for item in self.open.values() if item.send_mono is not None)

    def queued(self) -> int:
        with self.lock:
            return sum(1 for item in self.open.values() if item.send_mono is None)

    def reap(self, now: int, force: bool = False) -> tuple[int, int]:
        with self.lock:
            losses = 0
            unsent = 0
            for seq, item in list(self.open.items()):
                if item.send_mono is None:
                    if force:
                        self.open.pop(seq, None)
                        self.unsent += 1
                        unsent += 1
                    continue
                if force or now - item.send_mono >= self.timeout_ns:
                    self.open.pop(seq, None)
                    self.lost_seqs[seq] = item
                    if item.track_loss:
                        self.lost += 1
                        losses += 1
            extra = len(self.lost_seqs) - self.remember
            if extra > 0:
                for key in list(self.lost_seqs)[:extra]:
                    del self.lost_seqs[key]
            return losses, unsent


