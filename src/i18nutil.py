#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared helpers: locale and gettext."""

from __future__ import annotations

import gettext
import locale
import os
from pathlib import Path

TEXT_DOMAIN = "netpoisson"


def init_i18n(argv0: str) -> gettext.NullTranslations:
    locale.setlocale(locale.LC_ALL, "")

    localedir = os.environ.get("NETPOISSON_LOCALEDIR")
    if not localedir and "/" in argv0:
        here = Path(argv0).resolve().parent
        for candidate in (here / "po", here.parent / "po"):
            if candidate.is_dir():
                localedir = str(candidate)
                break

    trans = gettext.translation(TEXT_DOMAIN, localedir=localedir, fallback=True)
    trans.install()
    return trans
