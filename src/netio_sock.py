#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Socket helpers shared by the TCP and UDP peers."""

from __future__ import annotations

import queue
import socket
import ssl
import threading

from wiresec import UdpSeal, wrap_client_socket, wrap_server_socket

_READ_TIMEOUT = 0.3


def _close_socket(sock: socket.socket | None) -> None:
    if sock is None:
        return
    try:
        sock.shutdown(socket.SHUT_RDWR)
    except OSError:
        pass
    try:
        sock.close()
    except OSError:
        pass


def listen_socket(host: str, port: int, udp: bool) -> socket.socket:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    infos = socket.getaddrinfo(host, port, type=kind, flags=socket.AI_PASSIVE)
    if not infos:
        raise OSError(f"cannot resolve {host}")
    af, socktype, proto, _canon, sa = infos[0]
    sock = socket.socket(af, socktype, proto)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(sa)
    if not udp:
        sock.listen(128)
    sock.settimeout(_READ_TIMEOUT)
    return sock


def connect_socket(
    host: str,
    port: int,
    udp: bool,
    timeout: float = 2.0,
    *,
    ssl_ctx: ssl.SSLContext | None = None,
) -> socket.socket:
    kind = socket.SOCK_DGRAM if udp else socket.SOCK_STREAM
    infos = socket.getaddrinfo(host, port, type=kind)
    last: Exception | None = None
    for af, socktype, proto, _canon, sa in infos:
        sock = socket.socket(af, socktype, proto)
        sock.settimeout(timeout)
        try:
            sock.connect(sa)
        except OSError as exc:
            last = exc
            sock.close()
            continue
        if not udp:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            if ssl_ctx is not None:
                sock = wrap_client_socket(sock, ssl_ctx, server_hostname=host)
        sock.settimeout(_READ_TIMEOUT)
        return sock
    raise OSError(last or f"cannot connect to {host}:{port}")


def accept_socket(lsock: socket.socket, ssl_ctx: ssl.SSLContext | None = None) -> tuple[socket.socket, tuple]:
    sock, addr = lsock.accept()
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    if ssl_ctx is not None:
        sock = wrap_server_socket(sock, ssl_ctx)
    sock.settimeout(_READ_TIMEOUT)
    return sock, addr


class _Conn:
    def __init__(self, sock: socket.socket, addr) -> None:
        self.sock = sock
        self.addr = addr
        self.rxq: queue.Queue = queue.Queue()
        self.txq: queue.Queue = queue.Queue()
        self.closed = threading.Event()


def seal_datagram(seal: UdpSeal | None, data: bytes) -> bytes:
    return data if seal is None else seal.seal(data)


def open_datagram(seal: UdpSeal | None, data: bytes) -> bytes | None:
    if seal is None:
        return data
    return seal.open(data)
