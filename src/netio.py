#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TCP and UDP peers (re-export)."""

from __future__ import annotations

from netio_sock import connect_socket, listen_socket
from peer_client import Client
from peer_server import Server, StdioServer

__all__ = ["Client", "Server", "StdioServer", "connect_socket", "listen_socket"]
