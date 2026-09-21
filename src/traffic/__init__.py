#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Poisson timing, timelines, status lines, and jitter analysis."""

from traffic.analysis import (
    HISTOGRAM_EDGES_US,
    SampleWindow,
    best_lag,
    build_notes,
    histogram,
    mean_stdev,
    percentile,
    rtt_summary,
    update_jitter,
)
from traffic.book import InflightBook, Pending
from traffic.display import TerminalStatus, format_server_lines, format_status_lines
from traffic.timing import (
    SLICE_MS,
    SLICE_NS,
    VISIBLE,
    CounterSeries,
    cell_width,
    choose_half,
    expovariate,
    newest_first,
    pairs_oldest_first,
    slice_id,
    slice_id_for,
)

__all__ = [
    "SLICE_MS", "SLICE_NS", "VISIBLE",
    "slice_id", "slice_id_for", "expovariate", "CounterSeries",
    "newest_first", "pairs_oldest_first", "cell_width", "choose_half",
    "format_status_lines", "format_server_lines", "TerminalStatus",
    "Pending", "InflightBook",
    "mean_stdev", "percentile", "rtt_summary", "update_jitter", "best_lag",
    "HISTOGRAM_EDGES_US", "histogram", "build_notes", "SampleWindow",
]
