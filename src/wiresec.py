#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TLS for TCP and PSK datagram sealing for UDP (stdlib only)."""

from __future__ import annotations

import hashlib
import hmac
import os
import ssl
import tempfile
from dataclasses import dataclass
from pathlib import Path


def derive_psk(text: str) -> bytes:
    return hashlib.sha256(b"netpoisson-udp-v1\0" + text.encode("utf-8")).digest()


@dataclass
class UdpSeal:
    """Encrypt/authenticate UDP datagrams with a PSK.

    Layout: nonce(12) || ciphertext || tag(16). Ciphertext is XOR with a
    SHAKE256 keystream; tag is truncated HMAC-SHA256. Stdlib-only AEAD-alike
    suitable for traffic tests (not a replacement for TLS on TCP).
    """

    key: bytes
    _OVERHEAD = 12 + 16

    def seal(self, plain: bytes) -> bytes:
        nonce = os.urandom(12)
        stream = hashlib.shake_256(self.key + nonce).digest(len(plain))
        cipher = bytes(a ^ b for a, b in zip(plain, stream, strict=True))
        tag = hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()[:16]
        return nonce + cipher + tag

    def open(self, blob: bytes) -> bytes | None:
        if len(blob) < self._OVERHEAD:
            return None
        nonce, rest = blob[:12], blob[12:]
        cipher, tag = rest[:-16], rest[-16:]
        expect = hmac.new(self.key, nonce + cipher, hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(tag, expect):
            return None
        stream = hashlib.shake_256(self.key + nonce).digest(len(cipher))
        return bytes(a ^ b for a, b in zip(cipher, stream, strict=True))


def make_server_ssl_context(certfile: str | None, keyfile: str | None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if certfile and keyfile:
        ctx.load_cert_chain(certfile, keyfile)
        return ctx
    # Ephemeral self-signed cert for localhost tests / ad-hoc servers.
    cert_path, key_path = _ensure_self_signed()
    ctx.load_cert_chain(cert_path, key_path)
    return ctx


def make_client_ssl_context(*, insecure: bool = False, cafile: str | None = None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    if insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    if cafile:
        ctx.load_verify_locations(cafile)
    else:
        ctx.load_default_certs()
    return ctx


def wrap_server_socket(sock, ctx: ssl.SSLContext):
    return ctx.wrap_socket(sock, server_side=True)


def wrap_client_socket(sock, ctx: ssl.SSLContext, server_hostname: str | None):
    return ctx.wrap_socket(sock, server_side=False, server_hostname=server_hostname)


_SELF_SIGNED: tuple[str, str] | None = None


def _ensure_self_signed() -> tuple[str, str]:
    global _SELF_SIGNED
    if _SELF_SIGNED is not None and Path(_SELF_SIGNED[0]).is_file():
        return _SELF_SIGNED
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.x509.oid import NameOID
        import datetime

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "netpoisson")])
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1))
            .not_valid_after(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
            .sign(key, hashes.SHA256())
        )
        td = tempfile.mkdtemp(prefix="netpoisson-tls-")
        cert_path = str(Path(td) / "cert.pem")
        key_path = str(Path(td) / "key.pem")
        Path(cert_path).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        Path(key_path).write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.TraditionalOpenSSL,
                serialization.NoEncryption(),
            )
        )
        _SELF_SIGNED = (cert_path, key_path)
        return _SELF_SIGNED
    except ImportError:
        pass
    # Fallback: openssl CLI
    td = tempfile.mkdtemp(prefix="netpoisson-tls-")
    cert_path = str(Path(td) / "cert.pem")
    key_path = str(Path(td) / "key.pem")
    import subprocess

    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-keyout",
            key_path,
            "-out",
            cert_path,
            "-days",
            "3650",
            "-nodes",
            "-subj",
            "/CN=netpoisson",
            "-addext",
            "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        capture_output=True,
    )
    _SELF_SIGNED = (cert_path, key_path)
    return _SELF_SIGNED
