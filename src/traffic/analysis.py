#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""RTT summaries, notes, and sample windows."""

from __future__ import annotations

import math
import threading
from collections import deque

from traffic.timing import SLICE_MS

def mean_stdev(xs: list[float]) -> tuple[float | None, float | None]:
    n = len(xs)
    if n == 0:
        return None, None
    mean = sum(xs) / n
    if n == 1:
        return mean, 0.0
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    return mean, math.sqrt(var)


def percentile(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ordered = sorted(xs)
    if len(ordered) == 1:
        return ordered[0]
    k = (len(ordered) - 1) * (p / 100.0)
    lo = math.floor(k)
    hi = math.ceil(k)
    if lo == hi:
        return ordered[lo]
    return ordered[lo] * (hi - k) + ordered[hi] * (k - lo)


def rtt_summary(xs: list[float]) -> dict:
    mean, stdev = mean_stdev(xs)
    return {
        "n": len(xs),
        "min": _round(min(xs) if xs else None),
        "mean": _round(mean),
        "stdev": _round(stdev),
        "p50": _round(percentile(xs, 50)),
        "p90": _round(percentile(xs, 90)),
        "p95": _round(percentile(xs, 95)),
        "p99": _round(percentile(xs, 99)),
        "max": _round(max(xs) if xs else None),
    }


def _round(value: float | None, digits: int = 1) -> float | None:
    if value is None:
        return None
    return round(value, digits)


def update_jitter(jitter: float, prev: float | None, rtt: float) -> tuple[float, float]:
    """RFC 3550 smoothed jitter, using consecutive RTT samples (microseconds)."""
    if prev is None:
        return jitter, rtt
    delta = abs(rtt - prev)
    return jitter + (delta - jitter) / 16.0, rtt


def best_lag(req: list[int], resp: list[int], max_lag: int = 8) -> int | None:
    """How many slices the response series lags the request series.

    Both lists are oldest-first and share a time axis.
    """
    n = min(len(req), len(resp))
    if n == 0 or sum(req[:n]) == 0 or sum(resp[:n]) == 0:
        return None
    best_l = 0
    best_s: float | None = None
    for lag in range(0, max_lag + 1):
        score = 0
        count = 0
        for i in range(lag, n):
            score += req[i - lag] * resp[i]
            count += 1
        if count == 0:
            continue
        norm = score / count
        if best_s is None or norm > best_s:
            best_s = norm
            best_l = lag
    return best_l


HISTOGRAM_EDGES_US = [50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000, 50000, 100000]


def histogram(xs: list[float], edges: list[int] | None = None) -> list[dict]:
    bounds = edges or HISTOGRAM_EDGES_US
    bins = [{"le": edge, "n": 0} for edge in bounds]
    bins.append({"le": None, "n": 0})
    for value in xs:
        placed = False
        for bucket in bins:
            limit = bucket["le"]
            if limit is None or value <= limit:
                bucket["n"] += 1
                placed = True
                break
        if not placed:
            bins[-1]["n"] += 1
    return bins


def _level_for_ratio(ratio: float, warn: float, bad: float) -> str:
    if ratio >= bad:
        return "bad"
    if ratio >= warn:
        return "warn"
    return "info"


def build_notes(view: dict) -> list[dict]:
    """Machine-readable analysis. The dashboard turns ``code`` into prose."""
    notes: list[dict] = []
    role = view.get("role")
    lam = view.get("lambda_rps") or 0
    sent = int(view.get("wire_sent") or 0)
    received = int(view.get("received") or 0)
    lost = int(view.get("lost") or 0)
    offered = int(view.get("offered") or 0)
    duration = float(view.get("duration_s") or 0)
    achieved = (sent / duration) if duration > 0 else 0.0
    loss_ratio = (lost / sent) if sent else 0.0

    notes.append(
        {
            "code": "overview",
            "level": "info",
            "offered": offered,
            "sent": sent,
            "received": received,
            "lost": lost,
            "achieved_rps": round(achieved, 2),
            "loss_ratio": round(loss_ratio, 4),
            "lambda_rps": lam,
        }
    )

    if role == "server":
        rx = view.get("server_rx") or 0
        tx = view.get("server_tx") or 0
        if received == 0 and sent == 0:
            notes.append({"code": "server_idle", "level": "info"})
        else:
            notes.append({"code": "server_busy", "level": "info", "received": received, "sent": sent})
        if rx >= 8:
            notes.append({"code": "server_rx_backlog", "level": "warn", "server_rx": rx})
        if tx >= 8:
            notes.append({"code": "server_tx_backlog", "level": "warn", "server_tx": tx})
        return notes

    if sent < 10:
        notes.append({"code": "warming_up", "level": "info", "sent": sent})
    elif lam > 0:
        ratio = achieved / lam
        level = "info" if ratio >= 0.85 else "warn"
        notes.append(
            {
                "code": "rate_ok" if level == "info" else "rate_shortfall",
                "level": level,
                "achieved_rps": round(achieved, 2),
                "lambda_rps": lam,
                "ratio": round(ratio, 3),
            }
        )

    if sent >= 10 and loss_ratio > 0:
        notes.append(
            {
                "code": "loss",
                "level": _level_for_ratio(loss_ratio, 0.01, 0.05),
                "loss_ratio": round(loss_ratio, 4),
                "lost": lost,
                "sent": sent,
            }
        )

    rtt = view.get("rtt_us") or {}
    mean_rtt = rtt.get("mean")
    jitter = view.get("jitter_rfc3550_us")
    if mean_rtt and jitter is not None and rtt.get("n", 0) >= 8:
        cv = jitter / mean_rtt if mean_rtt else 0
        p99 = rtt.get("p99") or 0
        # A long tail counts only when it is both wide and not just a few
        # hundred microseconds of scheduler noise.
        spread = p99 - mean_rtt
        high = cv >= 0.5 or (spread > max(2000.0, mean_rtt * 2) and p99 > mean_rtt * 4)
        notes.append(
            {
                "code": "jitter_high" if high else "jitter_low",
                "level": "warn" if high else "info",
                "jitter_us": round(jitter, 1),
                "mean_rtt_us": mean_rtt,
                "p50_us": rtt.get("p50"),
                "p99_us": rtt.get("p99"),
                "cv": round(cv, 3),
            }
        )

    threshold = max(8, int((lam or 1) * (SLICE_MS / 1000) * 3))
    client_tx = view.get("client_tx")
    server_rx = view.get("server_rx")
    server_tx = view.get("server_tx")
    if client_tx is not None and client_tx >= threshold:
        notes.append({"code": "client_tx_backlog", "level": "warn", "client_tx": client_tx, "threshold": threshold})
    if server_rx is not None and server_rx >= threshold:
        notes.append({"code": "server_rx_backlog", "level": "warn", "server_rx": server_rx, "threshold": threshold})
    if server_tx is not None and server_tx >= threshold:
        notes.append({"code": "server_tx_backlog", "level": "warn", "server_tx": server_tx, "threshold": threshold})
    if (
        sent >= 10
        and (client_tx or 0) < threshold
        and (server_rx or 0) < threshold
        and (server_tx or 0) < threshold
        and loss_ratio < 0.01
    ):
        notes.append({"code": "keeping_up", "level": "info"})

    gaps = view.get("gaps_us") or []
    if len(gaps) >= 30 and lam > 0:
        gmean, gstdev = mean_stdev(gaps)
        cv = (gstdev / gmean) if gmean else None
        expected = 1_000_000 / lam
        ok = cv is not None and 0.7 <= cv <= 1.35
        notes.append(
            {
                "code": "poisson_ok" if ok else "poisson_off",
                "level": "info" if ok else "warn",
                "cv": _round(cv, 3),
                "mean_us": _round(gmean),
                "expected_us": round(expected, 1),
            }
        )

    lag = view.get("timeline_lag_slices")
    if lag is not None:
        notes.append(
            {
                "code": "timeline_lag",
                "level": "info",
                "slices": lag,
                "ms": lag * SLICE_MS,
            }
        )

    hold = view.get("server_hold_us") or {}
    if hold.get("n", 0) >= 3 and hold.get("p50") is not None:
        notes.append(
            {
                "code": "server_hold",
                "level": "info",
                "p50_us": hold.get("p50"),
                "p95_us": hold.get("p95"),
            }
        )

    qdelay = view.get("queue_delay_us") or {}
    if (
        qdelay.get("p50") is not None
        and mean_rtt
        and qdelay["p50"] > mean_rtt
        and (client_tx or 0) >= 2
    ):
        notes.append(
            {
                "code": "local_queue_dominates",
                "level": "warn",
                "queue_delay_p50_us": qdelay["p50"],
                "mean_rtt_us": mean_rtt,
            }
        )

    skew = view.get("clock_skew_slices")
    if skew is not None and abs(skew) > 2:
        notes.append({"code": "clock_skew", "level": "warn", "slices": skew, "ms": skew * SLICE_MS})

    if sent >= 5 and view.get("status_received", 0) == 0:
        notes.append({"code": "no_server_status", "level": "warn"})

    mismatches = int(view.get("mismatch") or 0)
    if mismatches:
        notes.append({"code": "echo_mismatch", "level": "bad", "mismatch": mismatches})

    return notes


class SampleWindow:
    """Bounded recent samples for the live dashboard."""

    def __init__(self, rtt_keep: int = 2000, spark_keep: int = 240, queue_keep: int = 180) -> None:
        self.rtt_us: deque[float] = deque(maxlen=rtt_keep)
        self.spark_us: deque[float] = deque(maxlen=spark_keep)
        self.gaps_us: deque[float] = deque(maxlen=rtt_keep)
        self.hold_us: deque[float] = deque(maxlen=rtt_keep)
        self.queue_delay_us: deque[float] = deque(maxlen=rtt_keep)
        self.client_tx: deque[int] = deque(maxlen=queue_keep)
        self.server_rx: deque[int] = deque(maxlen=queue_keep)
        self.server_tx: deque[int] = deque(maxlen=queue_keep)
        self.all_rtt: deque[float] = deque(maxlen=100000)
        self.lock = threading.Lock()

    def add_rtt(self, rtt_us: float, queue_delay_us: float | None = None) -> None:
        with self.lock:
            self.rtt_us.append(rtt_us)
            self.spark_us.append(rtt_us)
            self.all_rtt.append(rtt_us)
            if queue_delay_us is not None:
                self.queue_delay_us.append(queue_delay_us)

    def add_gap(self, gap_us: float) -> None:
        with self.lock:
            self.gaps_us.append(gap_us)

    def add_hold(self, hold_us: float) -> None:
        with self.lock:
            self.hold_us.append(hold_us)

    def add_queues(self, client_tx: int, server_rx: int | None, server_tx: int | None) -> None:
        with self.lock:
            self.client_tx.append(client_tx)
            if server_rx is not None:
                self.server_rx.append(server_rx)
            if server_tx is not None:
                self.server_tx.append(server_tx)

    def copy(self) -> dict:
        with self.lock:
            return {
                "rtt_us": list(self.rtt_us),
                "spark_us": list(self.spark_us),
                "gaps_us": list(self.gaps_us),
                "hold_us": list(self.hold_us),
                "queue_delay_us": list(self.queue_delay_us),
                "all_rtt": list(self.all_rtt),
                "client_tx": list(self.client_tx),
                "server_rx": list(self.server_rx),
                "server_tx": list(self.server_tx),
            }
