#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared client transport types."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

@dataclass
class Outgoing:
    seq: int
    mtype: int
    raw: bytes
    create_mono: int
    planned_mono: int
    payload: bytes = b""


@dataclass
class SshTransport:
    proc: subprocess.Popen
    setup_ms: float
    remote_start_ms: float


