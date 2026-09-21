#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Client snapshot and report assembly."""

from __future__ import annotations

import time

from traffic import (
    best_lag,
    build_notes,
    cell_width,
    choose_half,
    format_status_lines,
    histogram,
    newest_first,
    rtt_summary,
    slice_id_for,
)


class ClientReportingMixin:
    def status_lines(self, columns: int = 100, width: int | None = None, half: int | None = None) -> tuple[str, ...]:
        sid = slice_id_for(bucket_ns=self.bucket_ns)
        req = newest_first(self.req_series.snapshot(), sid, self.window_slices)
        recv = newest_first(self.recv_series.snapshot(), sid, self.window_slices)
        resp = newest_first(self.resp_series.snapshot(), sid, self.window_slices)
        ack = newest_first(self.ack_series.snapshot(), sid, self.window_slices)
        if width is None:
            width = cell_width(self.lam, self.bucket_ms / 1000)
        if half is None:
            half = choose_half(columns, width)
        half = min(half, max(4, self.window_slices // 2))
        with self.server_lock:
            processing = self.processing
            response_pending = self.response_pending
        return format_status_lines(
            req,
            resp,
            self.txq.qsize(),
            processing,
            response_pending,
            width=width,
            half=half,
            recv=recv,
            ack=ack,
            compact=True,
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
        sid = slice_id_for(bucket_ns=self.bucket_ns)
        view["raw_events"] = self.samples.raw_in_window(sid, self.window_slices)
        view["histogram"] = histogram(copied["rtt_us"])
        view["notes"] = build_notes(view)
        view.pop("gaps_us", None)
        width = cell_width(self.lam, self.bucket_ms / 1000)
        view["ascii"] = list(self.status_lines(120, width=width, half=min(12, max(4, self.window_slices // 2))))
        view["queues"] = {
            "client_pending": view["client_pending"],
            "read_pending": view.get("read_pending"),
            "processing": view["processing"],
            "response_pending": view["response_pending"],
        }
        view["window_auto"] = getattr(self, "window_auto", False)
        return view

    def report(self) -> dict:
        view = self._view(final=True)
        view["notes"] = build_notes(view)
        view.pop("gaps_us", None)
        rtt = view["rtt_us"]
        out = {
            "role": "client",
            "proto": view["proto"],
            "target": view["endpoint"],
            "lambda_rps": self.lam,
            "payload_bytes": self.payload_size,
            "slice_ms": self.bucket_ms,
            "duration_s": view["duration_s"],
            "offered": view["offered"],
            "sent": view["wire_sent"],
            "received": view["received"],
            "lost": view["lost"],
            "late": view["late"],
            "unsent": view["unsent"],
            "mismatch": view["mismatch"],
            "duplicates": view["duplicates"],
            "reordered": view["reordered"],
            "loss_ratio": view["loss_ratio"],
            "achieved_rps": view["achieved_rps"],
            "offered_rps": view["offered_rps"],
            "rtt_us": rtt,
            "jitter_rfc3550_us": view["jitter_rfc3550_us"],
            "schedule_skew_us": view["schedule_skew_us"],
            "queue_delay_us": view["queue_delay_us"],
            "server_hold_us": view["server_hold_us"],
            "interarrival_us": view["interarrival_us"],
            "rx_bytes": view["session_rx_bytes"],
            "tx_bytes": view["session_tx_bytes"],
            "bandwidth_bps": view["bandwidth_bps"],
            "queues": {
                "client_pending": {"last": view["client_pending"], "max": self.tx_max},
                "processing": {"last": view["processing"]},
                "response_pending": {"last": view["response_pending"]},
            },
            "timeline_lag_slices": view["timeline_lag_slices"],
            "req_timeline": view["req"],
            "recv_timeline": view["recv"],
            "resp_timeline": view["resp"],
            "ack_timeline": view["ack"],
            "status": {"sent": view["status_sent"], "received": view["status_received"]},
            "notes": view["notes"],
        }
        if self.ssh is not None:
            out["ssh"] = {
                "connection_setup_ms": round(self.ssh.setup_ms, 1),
                "remote_process_startup_ms": round(self.ssh.remote_start_ms, 1),
            }
        return out

    def _view(self, final: bool) -> dict:
        copied = self.samples.copy()
        rtt_src = copied["all_rtt"] if final else copied["rtt_us"]
        sid = slice_id_for(bucket_ns=self.bucket_ns)
        req = newest_first(self.req_series.snapshot(), sid, self.window_slices)
        recv = newest_first(self.recv_series.snapshot(), sid, self.window_slices)
        resp = newest_first(self.resp_series.snapshot(), sid, self.window_slices)
        ack = newest_first(self.ack_series.snapshot(), sid, self.window_slices)
        lag = best_lag(list(reversed(req)), list(reversed(resp)))
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
            rx_b = self.session_rx_bytes
            tx_b = self.session_tx_bytes
        with self.server_lock:
            read_pending = self.read_pending
            processing = self.processing
            response_pending = self.response_pending
        lost = self.book.lost
        unsent = self.book.unsent
        loss_ratio = (lost / wire_sent) if wire_sent else 0.0
        gaps = copied["gaps_us"]
        gmean = sum(gaps) / len(gaps) if gaps else None
        skew = list(self.skew_us[-5000:])
        return {
            "role": "client",
            "proto": (
                "ssh-stdio"
                if self.stdio
                else (
                    ("udp+psk" if self.udp_seal else "udp")
                    if self.udp
                    else ("tcp+tls" if self.ssl_ctx else "tcp")
                )
            ),
            "endpoint": self.endpoint(),
            "lambda_rps": self.lam,
            "slice_ms": self.bucket_ms,
            "bucket_ms": self.bucket_ms,
            "status_interval_ms": self.status_interval_ms,
            "window_slices": self.window_slices,
            "duration_s": round(duration, 3),
            "uptime_s": round(duration, 3),
            "req": req,
            "recv": recv,
            "resp": resp,
            "ack": ack,
            "client_tx": self.txq.qsize(),
            "client_pending": self.txq.qsize(),
            "server_rx": read_pending,
            "read_pending": read_pending,
            "processing": processing,
            "server_tx": response_pending,
            "response_pending": response_pending,
            "offered": offered,
            "wire_sent": wire_sent,
            "received": received,
            "lost": lost,
            "late": late,
            "unsent": unsent,
            "mismatch": mismatch,
            "duplicates": self.book.duplicates,
            "reordered": self.book.reorder,
            "loss_ratio": round(loss_ratio, 4),
            "achieved_rps": round(wire_sent / duration, 2) if duration else 0,
            "offered_rps": round(offered / duration, 2) if duration else 0,
            "rtt_us": rtt_summary(rtt_src),
            "jitter_rfc3550_us": round(jitter, 1) if received else None,
            "schedule_skew_us": rtt_summary(skew),
            "queue_delay_us": rtt_summary(copied["queue_delay_us"]),
            "server_hold_us": rtt_summary(copied["hold_us"]),
            "interarrival_us": {
                "n": len(gaps),
                "mean": None if gmean is None else round(gmean, 1),
                "expected": round(1_000_000 / self.lam, 1) if self.lam else None,
            },
            "gaps_us": gaps,
            "timeline_lag_slices": lag,
            "clock_skew_slices": None,
            "status_sent": status_sent,
            "status_received": status_received,
            "session_rx_bytes": rx_b,
            "session_tx_bytes": tx_b,
            "bandwidth_bps": {
                "rx": round(rx_b * 8 / duration, 1) if duration else 0,
                "tx": round(tx_b * 8 / duration, 1) if duration else 0,
            },
            "server_rx_bytes": self.server_rx_bytes,
            "server_tx_bytes": self.server_tx_bytes,
        }

