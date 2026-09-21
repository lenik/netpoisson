from __future__ import annotations

import json
import os
import random
import socket
import subprocess
import sys
import time
import unittest
import urllib.request
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from i18nutil import init_i18n  # noqa: E402
from netio import Client, Server  # noqa: E402
from npcli import parse_args  # noqa: E402
from protocol import (  # noqa: E402
    ECHO,
    FLAG_RESP,
    STATUS,
    StatusSnapshot,
    decode_pdu,
    decode_status_body,
    dispatch_binary,
    encode_echo_request,
    encode_status_body,
    encode_telemetry,
    pop_pdus,
    stdio_echo_ack,
    stdio_echo_req,
    stdio_parse_line,
    stdio_status_ack,
)
from traffic import (  # noqa: E402
    InflightBook,
    Pending,
    best_lag,
    build_notes,
    expovariate,
    format_status_lines,
    newest_first,
    update_jitter,
)
from webui import PAGE, Dashboard  # noqa: E402

init_i18n("netpoisson")


class ProtocolTests(unittest.TestCase):
    def test_echo_with_telemetry(self) -> None:
        raw = encode_echo_request(3, 99, b"payload")
        telem = encode_telemetry(
            server_mono_ns=1,
            request_received_total=2,
            response_completed_total=3,
            read_pending=4,
            processing=5,
            response_pending=6,
            rx_bytes=7,
            tx_bytes=8,
            interval_rx_requests=9,
            interval_tx_responses=10,
        )
        out = dispatch_binary(raw, make_telemetry=lambda: telem, make_status=None)
        assert out is not None
        frame = decode_pdu(out)
        assert frame is not None
        self.assertTrue(frame.is_resp)
        self.assertEqual(frame.payload, b"payload")
        self.assertIsNotNone(frame.telemetry)
        self.assertEqual(frame.telemetry["read_pending"], 4)

    def test_echo_identical_without_telemetry(self) -> None:
        raw = encode_echo_request(3, 99, b"payload")
        out = dispatch_binary(raw, make_telemetry=None, make_status=None)
        self.assertEqual(out, raw)

    def test_status_roundtrip(self) -> None:
        snap = StatusSnapshot(
            server_monotonic_ns=10,
            request_received_total=1,
            response_completed_total=2,
            read_pending=16,
            processing=3,
            response_pending=20,
            rx_bytes=100,
            tx_bytes=200,
            interval_rx_requests=5,
            interval_tx_responses=4,
            bucket_ms=100,
            kernel_send_queue_bytes=None,
            kernel_recv_queue_bytes=None,
            recv_slices=[1, 0, 4],
            resp_slices=[2, 3],
        )
        body = encode_status_body(snap)
        raw = encode_echo_request(5, 7, b"")  # placeholder seq path via status
        from protocol import encode_status_request

        raw = encode_status_request(5, 7)
        out = dispatch_binary(raw, make_telemetry=None, make_status=lambda: body)
        assert out is not None
        frame = decode_pdu(out)
        assert frame is not None
        self.assertEqual(frame.flags & FLAG_RESP, FLAG_RESP)
        self.assertEqual(frame.seq, 5)
        decoded = decode_status_body(frame.payload)
        assert decoded is not None
        self.assertEqual(decoded.read_pending, 16)
        self.assertEqual(decoded.response_pending, 20)
        self.assertEqual(decoded.recv_slices, [1, 0, 4])
        self.assertEqual(decoded.resp_slices, [2, 3])

    def test_partial_pdus(self) -> None:
        raw = encode_echo_request(1, 2, b"hi")
        frames, rest = pop_pdus(raw[:10])
        self.assertEqual(frames, [])
        self.assertEqual(rest, raw[:10])
        frames, rest = pop_pdus(rest + raw[10:] + raw[:4])
        self.assertEqual(frames, [raw])
        self.assertEqual(rest, raw[:4])

    def test_stdio_prefix(self) -> None:
        line = stdio_echo_req(1, 100, b"hello")
        self.assertTrue(line.startswith(b"@nPoi "))
        kind, obj = stdio_parse_line(line)
        self.assertEqual(kind, "protocol")
        assert obj is not None
        self.assertEqual(obj["t"], "e")
        kind, _ = stdio_parse_line(b"Welcome to Ubuntu\n")
        self.assertEqual(kind, "noise")


class TimelineTests(unittest.TestCase):
    def test_slices_scroll_right(self) -> None:
        counts = {10: 4, 9: 7}
        self.assertEqual(newest_first(counts, 10, 4), [4, 7, 0, 0])
        self.assertEqual(newest_first(counts, 11, 4), [0, 4, 7, 0])

    def test_status_lines_show_pending(self) -> None:
        req = [8, 11, 9, 13, 7, 12, 10, 8, 15, 9, 11, 7]
        resp = [8, 10, 10, 12, 8, 11, 10, 8, 14, 10, 10, 6]
        lines = format_status_lines(req, resp, 17, 16, 20, width=2, half=12)
        self.assertTrue(lines[0].startswith("Req."))
        self.assertTrue(lines[1].startswith("Resp."))
        self.assertIn("pending 17", lines[0])
        self.assertIn("processing 16", lines[1])
        self.assertIn("pending 20", lines[1])

    def test_jitter_smooths(self) -> None:
        jitter, prev = update_jitter(0, None, 100)
        jitter, prev = update_jitter(jitter, prev, 180)
        self.assertAlmostEqual(jitter, 5.0)
        self.assertEqual(prev, 180)

    def test_lag_finds_the_shift(self) -> None:
        req = [0, 0, 5, 0, 1, 0, 4]
        resp = [0, 0, 0, 0, 5, 0, 1]
        self.assertEqual(best_lag(req, resp), 2)

    def test_poisson_mean(self) -> None:
        rng = random.Random(1)
        samples = [expovariate(100, rng) for _ in range(4000)]
        mean = sum(samples) / len(samples)
        self.assertGreater(mean, 0.008)
        self.assertLess(mean, 0.012)

    def test_notes_flag_backlog_and_loss(self) -> None:
        notes = build_notes(
            {
                "role": "client",
                "lambda_rps": 100,
                "wire_sent": 100,
                "received": 90,
                "lost": 10,
                "offered": 100,
                "duration_s": 1,
                "client_tx": 40,
                "server_rx": 0,
                "server_tx": 0,
                "rtt_us": {"n": 20, "mean": 100, "p50": 90, "p99": 800},
                "jitter_rfc3550_us": 80,
                "gaps_us": [10000] * 40,
                "status_received": 3,
                "server_hold_us": {"n": 0},
                "queue_delay_us": {"p50": None},
                "timeline_lag_slices": 1,
                "clock_skew_slices": 0,
                "mismatch": 0,
            }
        )
        codes = {note["code"] for note in notes}
        self.assertIn("loss", codes)
        self.assertIn("client_tx_backlog", codes)
        self.assertIn("jitter_high", codes)


class BookTests(unittest.TestCase):
    def test_timeout_then_late_reply(self) -> None:
        book = InflightBook(timeout_s=0.05)
        book.add(Pending(seq=1, mtype=1, create_mono=0, send_mono=1000))
        losses, unsent = book.reap(1000 + book.timeout_ns)
        self.assertEqual(losses, 1)
        self.assertEqual(book.lost, 1)
        kind, pending = book.take(1)
        self.assertEqual(kind, "late")
        self.assertIsNotNone(pending)
        self.assertEqual(book.lost, 0)

    def test_duplicate_response(self) -> None:
        book = InflightBook(timeout_s=2)
        book.add(Pending(seq=1, mtype=1, create_mono=0, send_mono=1))
        kind, _ = book.take(1)
        self.assertEqual(kind, "ok")
        kind, _ = book.take(1)
        self.assertEqual(kind, "duplicate")
        self.assertEqual(book.duplicates, 1)


class ArgTests(unittest.TestCase):
    def test_client_target(self) -> None:
        args = parse_args(["-l", "50", "127.0.0.1:1871"])
        self.assertEqual(args.lam, 50)
        self.assertEqual(args.server, "127.0.0.1:1871")
        self.assertFalse(args.daemon)

    def test_payload_and_bucket(self) -> None:
        args = parse_args(["--payload", "1k", "--bucket", "50ms", "--window", "6s", "h"])
        self.assertEqual(args.payload_size, 1024)
        self.assertEqual(args.bucket_ms, 50)
        self.assertEqual(args.window_ms, 6000)

    def test_ssh_profile(self) -> None:
        args = parse_args(["--ssh", "user@host", "--profile", "ssh-interactive"])
        self.assertEqual(args.ssh_target, "user@host")
        self.assertEqual(args.lam, 20.0)
        self.assertEqual(args.payload_min, 1)
        self.assertEqual(args.payload_max, 128)


class IntegrationTests(unittest.TestCase):
    def test_tcp_echo_roundtrip(self) -> None:
        server = Server("127.0.0.1", 0, udp=False)
        server.start()
        try:
            client = Client("127.0.0.1", server.port, udp=False, lam=80, payload_size=32, seed=1)
            client.start()
            time.sleep(0.8)
            client.stop(grace=0.5)
            report = client.report()
            self.assertGreater(report["sent"], 10)
            self.assertGreater(report["received"], 5)
            self.assertEqual(report["mismatch"], 0)
            self.assertIn("rx_bytes", report)
            self.assertIn("tx_bytes", report)
        finally:
            server.stop()

    def test_udp_echo(self) -> None:
        server = Server("127.0.0.1", 0, udp=True)
        server.start()
        try:
            client = Client("127.0.0.1", server.port, udp=True, lam=40, payload_size=16, seed=2, timeout_s=1.0)
            client.start()
            time.sleep(0.6)
            client.stop(grace=0.4)
            report = client.report()
            self.assertGreater(report["sent"], 5)
            self.assertGreaterEqual(report["received"], 1)
        finally:
            server.stop()

    def test_cli_stats(self) -> None:
        server = Server("127.0.0.1", 0)
        server.start()
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = str(SRC)
            env["NETPOISSON_NO_BROWSER"] = "1"
            proc = subprocess.run(
                [
                    sys.executable,
                    str(SRC / "netpoisson.py"),
                    "-l",
                    "60",
                    "-s",
                    "0.5",
                    "--payload",
                    "16",
                    f"127.0.0.1:{server.port}",
                ],
                capture_output=True,
                text=True,
                env=env,
                timeout=10,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["role"], "client")
            self.assertGreater(report["sent"], 0)
        finally:
            server.stop()

    def test_stdio_local_pipe(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(SRC)}
        srv = subprocess.Popen(
            [sys.executable, str(SRC / "netpoisson.py"), "--stdio-server"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
            env=env,
        )
        from peer_client import Client, SshTransport

        client = Client(
            "local",
            0,
            lam=50,
            payload_size=12,
            seed=9,
            ssh=SshTransport(proc=srv, setup_ms=0.0, remote_start_ms=0.0),
            status_interval_ms=150,
        )
        try:
            client.start()
            time.sleep(0.6)
            client.stop(grace=0.4)
            report = client.report()
            self.assertGreater(report["sent"], 5)
            self.assertGreater(report["received"], 3)
            self.assertEqual(report["mismatch"], 0)
            self.assertEqual(report["proto"], "ssh-stdio")
        finally:
            client.stop(grace=0.1)

    def test_tls_tcp_localhost(self) -> None:
        from wiresec import make_client_ssl_context, make_server_ssl_context

        server = Server("127.0.0.1", 0, ssl_ctx=make_server_ssl_context(None, None))
        server.start()
        try:
            client = Client(
                "127.0.0.1",
                server.port,
                lam=50,
                payload_size=16,
                seed=11,
                ssl_ctx=make_client_ssl_context(insecure=True),
            )
            client.start()
            time.sleep(0.5)
            client.stop(grace=0.4)
            report = client.report()
            self.assertEqual(report["proto"], "tcp+tls")
            self.assertGreater(report["sent"], 5)
            self.assertGreater(report["received"], 3)
            self.assertEqual(report["mismatch"], 0)
        finally:
            server.stop()

    def test_udp_psk_localhost(self) -> None:
        from wiresec import UdpSeal, derive_psk

        seal = UdpSeal(derive_psk("unit-test-psk"))
        server = Server("127.0.0.1", 0, udp=True, udp_seal=seal)
        server.start()
        try:
            client = Client(
                "127.0.0.1",
                server.port,
                udp=True,
                lam=40,
                payload_size=16,
                seed=12,
                timeout_s=1.0,
                udp_seal=seal,
            )
            client.start()
            time.sleep(0.5)
            client.stop(grace=0.4)
            report = client.report()
            self.assertEqual(report["proto"], "udp+psk")
            self.assertGreater(report["sent"], 5)
            self.assertGreaterEqual(report["received"], 1)
        finally:
            server.stop()

    def test_web_default_port_constant(self) -> None:
        from npcli.args import DEFAULT_WEB_PORT

        self.assertEqual(DEFAULT_WEB_PORT, 3871)
        args = parse_args([])
        self.assertEqual(args.web_addr, "127.0.0.1:3871")

    def test_web_snapshot_has_bandwidth(self) -> None:
        server = Server("127.0.0.1", 0)
        server.start()
        try:
            dash = Dashboard("127.0.0.1", 0, server.snapshot)
            url = dash.start()
            try:
                with urllib.request.urlopen(url + "api/snapshot", timeout=2) as resp:
                    body = json.loads(resp.read().decode())
                self.assertIn("bandwidth_bps", body)
                self.assertIn("rx_bytes", body)
                self.assertIn("recv", body)
                self.assertIn("netpoisson", PAGE)
                self.assertIn("带宽", PAGE)
            finally:
                dash.close()
        finally:
            server.stop()


if __name__ == "__main__":
    unittest.main()
