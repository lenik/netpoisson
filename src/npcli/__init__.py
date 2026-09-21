#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""netpoisson CLI package."""

from npcli.args import Args, UsageError, app_version, parse_args, parse_server, usage, version
from npcli.main import main

__all__ = [
    "Args",
    "UsageError",
    "app_version",
    "main",
    "parse_args",
    "parse_server",
    "usage",
    "version",
]
