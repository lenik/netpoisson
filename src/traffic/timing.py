#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Slice clocks, Poisson draws, and counter series."""

from __future__ import annotations

import random
import threading
import time

SLICE_MS = 100
SLICE_NS = SLICE_MS * 1_000_000
VISIBLE = 32


def slice_id(when_ns: int | None = None, slice_ns: int = SLICE_NS) -> int:
    if when_ns is None:
        when_ns = time.monotonic_ns()
    return when_ns // slice_ns


def slice_id_for(when_ns: int | None = None, bucket_ns: int = SLICE_NS) -> int:
    return slice_id(when_ns, bucket_ns)


def expovariate(lam: float, rng: random.Random | None = None) -> float:
    """Inter-arrival seconds for a Poisson process of rate ``lam``."""
    if lam <= 0:
        raise ValueError("lambda must be positive")
    if rng is None:
        return random.expovariate(lam)
    return rng.expovariate(lam)


class CounterSeries:
    """Per-slice counters keyed by :func:`slice_id`."""

    def __init__(self, keep: int = 256) -> None:
        self.keep = keep
        self.counts: dict[int, int] = {}
        self.lock = threading.Lock()

    def add(self, sid: int, n: int = 1) -> None:
        with self.lock:
            self.counts[sid] = self.counts.get(sid, 0) + n
            if len(self.counts) > self.keep:
                cutoff = sid - self.keep
                for key in list(self.counts):
                    if key < cutoff:
                        del self.counts[key]

    def replace_window(self, newest_sid: int, values: list[int]) -> None:
        """Replace counts for newest_sid, newest_sid-1, ... from a newest-first list."""
        with self.lock:
            for i, count in enumerate(values):
                self.counts[newest_sid - i] = int(count)
            if len(self.counts) > self.keep:
                cutoff = newest_sid - self.keep
                for key in list(self.counts):
                    if key < cutoff:
                        del self.counts[key]

    def snapshot(self) -> dict[int, int]:
        with self.lock:
            return dict(self.counts)


def newest_first(counts: dict[int, int], sid: int, n: int) -> list[int]:
    """Index 0 is slice ``sid`` (newest). The next index is one slice older."""
    return [int(counts.get(sid - i, 0)) for i in range(n)]


def pairs_oldest_first(counts: dict[int, int], sid: int, n: int) -> list[tuple[int, int]]:
    start = sid - (n - 1)
    return [(start + i, int(counts.get(start + i, 0))) for i in range(n)]


def cell_width(lam: float, slice_s: float = SLICE_MS / 1000) -> int:
    typical = max(1, int(lam * slice_s * 8))
    return max(2, len(str(typical)))


def choose_half(columns: int, width: int, qwidth: int = 4) -> int:
    overhead = 6 + 1 + (qwidth * 2 + 10) + 1 + 1
    unit = width + 1
    room = max(0, columns - overhead)
    total = max(8, room // max(1, unit))
    if total % 2:
        total -= 1
    half = max(4, min(24, total // 2))
    return half


