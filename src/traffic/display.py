#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Terminal timeline formatting."""

from __future__ import annotations

import sys

def format_status_lines(
    req: list[int],
    resp: list[int],
    client_pending: int,
    processing: int | None,
    response_pending: int | None,
    *,
    width: int = 2,
    half: int = 12,
    qwidth: int = 4,
    recv: list[int] | None = None,
    ack: list[int] | None = None,
    compact: bool = True,
) -> tuple[str, ...]:
    """Timeline lines. Newest slices are on the left (index 0).

    Compact (TTY default) shows Req/Resp. Full mode also shows Recv/Ack.
    """

    def cell(n: int, blank_zero: bool = False) -> str:
        if blank_zero and n == 0:
            return " " * width
        text = str(n)
        if len(text) < width:
            return text.rjust(width)
        return text

    def join(vals: list[int], blank_zero: bool = False) -> str:
        padded = list(vals[:half])
        if len(padded) < half:
            padded.extend([0] * (half - len(padded)))
        return " ".join(cell(v, blank_zero) for v in padded)

    def q(n: int | None) -> str:
        if n is None:
            return "?"
        return str(n)

    # Prefer the simpler right-side pending labels from the redesign sketch.
    lines = [
        f"Req.  [{join(req)}]  pending {q(client_pending)}",
        f"Resp. [{join(resp, blank_zero=True)}]  processing {q(processing)}  pending {q(response_pending)}",
    ]
    if not compact:
        if recv is not None:
            lines.insert(1, f"Recv. [{join(recv)}]")
        if ack is not None:
            lines.append(f"Ack.  [{join(ack)}]")
    return tuple(lines)


def format_server_lines(
    received: list[int],
    sent: list[int],
    read_pending: int,
    processing: int,
    response_pending: int,
    *,
    width: int = 2,
    half: int = 12,
) -> tuple[str, str]:
    def join(vals: list[int]) -> str:
        padded = list(vals[:half])
        if len(padded) < half:
            padded.extend([0] * (half - len(padded)))
        return " ".join(str(v).rjust(width) for v in padded)

    return (
        f"Recv. [{join(received)}]  read {read_pending}  processing {processing}",
        f"Resp. [{join(sent)}]  pending {response_pending}",
    )


class TerminalStatus:
    """Redraw a few status lines in place. A cursor-up rewrite, not a pager."""

    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled and sys.stderr.isatty()
        self.n = 0

    def update(self, lines: list[str]) -> None:
        if not self.enabled:
            return
        if self.n:
            sys.stderr.write(f"\033[{self.n}A")
        sys.stderr.write("\n".join("\033[2K" + line for line in lines) + "\n")
        sys.stderr.flush()
        self.n = len(lines)

    def interrupt(self) -> None:
        self.n = 0


