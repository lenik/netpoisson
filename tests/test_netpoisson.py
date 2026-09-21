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

from commons import init_i18n  # noqa: E402
from netio import Client, Server, connect_socket  # noqa: E402
from netpoisson import parse_args  # noqa: E402
from protocol import (  # noqa: E402
    ECHO,
    ECHO_PAYLOAD,
    RESP,
    STATUS,
    decode_frame,
    decode_status_payload,
    dispatch_request,
    encode_request,
    encode_status_payload,
    pop_frames,
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
    def test_echo_response_is_the_request(self) -> None:
        raw = encode_request(ECHO, 3, 99, ECHO_PAYLOAD)
        out = dispatch_request(raw, 100, lambda _recv: b"")
        self.assertEqual(out, raw)

    def test_status_roundtrip(self) -> None:
        payload = encode_status_payload(10, 40, 16, 20, 100, [(8, 1), (9, 0), (10, 4)])
        raw = encode_request(STATUS, 5, 7, b"")
        out = dispatch_request(raw, 10, lambda _recv: payload)
        assert out is not None
        frame = decode_frame(out)
        assert frame is not None
        self.assertEqual(frame.role, RESP)
        self.assertEqual(frame.seq, 5)
        body = decode_status_payload(frame.payload)
        assert body is not None
        self.assertEqual(body["rx_queued"], 16)
        self.assertEqual(body["tx_queued"], 20)
        self.assertEqual(body["slices"], [(8, 1), (9, 0), (10, 4)])
        self.assertEqual(body["send_ns"] - body["recv_ns"], 30)

    def test_partial_frames(self) -> None:
        raw = encode_request(ECHO, 1, 2, ECHO_PAYLOAD)
        frames, rest = pop_frames(raw[:10])
        self.assertEqual(frames, [])
        self.assertEqual(rest, raw[:10])
        frames, rest = pop_frames(rest + raw[10:] + raw[:4])
        self.assertEqual(frames, [raw])
        self.assertEqual(rest, raw[:4])


class TimelineTests(unittest.TestCase):
    def test_slices_scroll_right(self) -> None:
        counts = {10: 4, 9: 7}
        self.assertEqual(newest_first(counts, 10, 4), [4, 7, 0, 0])
        self.assertEqual(newest_first(counts, 11, 4), [0, 4, 7, 0])

    def test_status_lines_align_like_the_sketch(self) -> None:
        req = [1, 2, 0, 1, 1, 3, 1, 0, 4, 0, 2, 1, 9, 8, 7, 6]
        resp = [0, 0, 0, 0, 0, 0, 0, 0, 4, 0, 2, 1, 9, 8, 7, 6]
        top, bottom = format_status_lines(req, resp, 17, 16, 20, width=1, half=8, qwidth=2)
        self.assertTrue(top.startswith("Req. ["))
        self.assertTrue(bottom.startswith("Resp ["))
        self.assertIn("-> 17 ->", top)
        self.assertIn("16 -> 20 ->", bottom)
        self.assertEqual(top.rfind("->"), bottom.rfind("->"))
        self.assertEqual(top[top.rfind("->") :], bottom[bottom.rfind("->") :])
        self.assertNotIn("0", bottom.split("16")[0])

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
        self.assertEqual((losses, unsent, book.lost), (1, 0, 1))
        kind, item = book.take(1)
        self.assertEqual(kind, "late")
        self.assertIsNotNone(item)
        self.assertEqual(book.lost, 0)

    def test_status_timeout_is_not_echo_loss(self) -> None:
        book = InflightBook(timeout_s=0.05)
        book.add(Pending(seq=2, mtype=2, create_mono=0, send_mono=0, track_loss=False))
        book.reap(book.timeout_ns + 1)
        self.assertEqual(book.lost, 0)

    def test_unsent_is_not_loss(self) -> None:
        book = InflightBook(timeout_s=1)
        book.add(Pending(seq=3, mtype=1, create_mono=0))
        _losses, unsent = book.reap(10**18, force=True)
        self.assertEqual(unsent, 1)
        self.assertEqual(book.lost, 0)


class CliTests(unittest.TestCase):
    def test_parse_client_and_server_flags(self) -> None:
        args = parse_args(["-d", "-u", "-p", "0", "-H", "127.0.0.1"], "netpoisson")
        self.assertTrue(args.daemon)
        self.assertTrue(args.udp)
        self.assertEqual(args.port, 0)
        self.assertEqual(args.host, "127.0.0.1")

        args = parse_args(["-l", "50", "-s", "1.5", "-w", "--web-port", "9", "127.0.0.1"], "netpoisson")
        self.assertEqual(args.lam, 50)
        self.assertEqual(args.stats, 1.5)
        self.assertTrue(args.web)
        self.assertEqual(args.web_port, 9)
        self.assertEqual(args.server, "127.0.0.1")

    def test_reject_daemon_with_server(self) -> None:
        from netpoisson import UsageError

        with self.assertRaises(UsageError):
            parse_args(["-d", "127.0.0.1"], "netpoisson")

    def test_glued_short_option(self) -> None:
        args = parse_args(["-l12.5", "-s2"], "netpoisson")
        self.assertEqual(args.lam, 12.5)
        self.assertEqual(args.stats, 2)


class PeerTests(unittest.TestCase):
    def test_tcp_echo_bytes_match(self) -> None:
        server = Server("127.0.0.1", 0, udp=False)
        server.start()
        self.addCleanup(server.stop)
        sock = connect_socket("127.0.0.1", server.port, udp=False)
        self.addCleanup(sock.close)
        raw = encode_request(ECHO, 7, 99, ECHO_PAYLOAD)
        sock.sendall(raw)
        sock.settimeout(2)
        buf = b""
        while len(buf) < len(raw):
            buf += sock.recv(4096)
        self.assertEqual(buf[: len(raw)], raw)

    def test_tcp_status_reports_queues(self) -> None:
        server = Server("127.0.0.1", 0, udp=False)
        server.start()
        self.addCleanup(server.stop)
        sock = connect_socket("127.0.0.1", server.port, udp=False)
        self.addCleanup(sock.close)
        sock.sendall(encode_request(STATUS, 1, 5, b""))
        sock.settimeout(2)
        buf = b""
        frame = None
        deadline = time.time() + 2
        while time.time() < deadline and frame is None:
            buf += sock.recv(4096)
            frames, buf = pop_frames(buf)
            if frames:
                frame = decode_frame(frames[0])
        assert frame is not None
        body = decode_status_payload(frame.payload)
        assert body is not None
        self.assertGreaterEqual(body["rx_queued"], 0)
        self.assertEqual(body["slice_ms"], 100)
        self.assertGreaterEqual(len(body["slices"]), 1)

    def test_client_echo_and_status(self) -> None:
        server = Server("127.0.0.1", 0, udp=False)
        server.start()
        self.addCleanup(server.stop)
        client = Client("127.0.0.1", server.port, udp=False, lam=80)
        client.start()
        self.addCleanup(lambda: client.stop(grace=0.3))
        time.sleep(0.7)
        client.stop(grace=0.8)
        report = client.report()
        self.assertGreater(report["received"], 10)
        self.assertEqual(report["mismatch"], 0)
        self.assertGreater(report["status"]["received"], 0)
        self.assertIsNotNone(report["rtt_us"]["p50"])
        self.assertIn("notes", report)

    def test_udp_client(self) -> None:
        server = Server("127.0.0.1", 0, udp=True)
        server.start()
        self.addCleanup(server.stop)
        client = Client("127.0.0.1", server.port, udp=True, lam=60)
        client.start()
        time.sleep(0.6)
        client.stop(grace=0.8)
        report = client.report()
        self.assertGreater(report["received"], 5)
        self.assertEqual(report["mismatch"], 0)
        self.assertEqual(report["proto"], "udp")


class WebTests(unittest.TestCase):
    def test_template_and_live_snapshot(self) -> None:
        self.assertIn("cdn.tailwindcss.com", PAGE)
        self.assertIn("q-client-tx", PAGE)
        self.assertIn("id=\"notes\"", PAGE)
        server = Server("127.0.0.1", 0, udp=False)
        server.start()
        self.addCleanup(server.stop)
        client = Client("127.0.0.1", server.port, udp=False, lam=40)
        client.start()
        self.addCleanup(lambda: client.stop(grace=0.2))
        dash = Dashboard("127.0.0.1", 0, client.snapshot)
        url = dash.start()
        self.addCleanup(dash.close)
        with urllib.request.urlopen(url, timeout=2) as res:
            html = res.read().decode("utf-8")
        self.assertIn("q-server-rx", html)
        with urllib.request.urlopen(url + "api/snapshot", timeout=2) as res:
            snap = json.load(res)
        self.assertEqual(snap["role"], "client")
        self.assertIn("ascii", snap)
        self.assertTrue(snap["ascii"][0].startswith("Req."))
        self.assertTrue(any(note["code"] == "overview" for note in snap["notes"]))
        host, port = dash.httpd.server_address[:2]
        probe = socket.create_connection((host, port), timeout=2)
        probe.sendall(b"GET /events HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
        data = b""
        probe.settimeout(2)
        while b"data:" not in data:
            chunk = probe.recv(4096)
            if not chunk:
                break
            data += chunk
        probe.close()
        self.assertIn(b"data:", data)


class EndToEndCliTests(unittest.TestCase):
    def test_stats_json(self) -> None:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(SRC)
        env["PYTHONUNBUFFERED"] = "1"
        env["NETPOISSON_NO_BROWSER"] = "1"
        env["LC_ALL"] = "C"
        env["LANGUAGE"] = "C"
        script = str(SRC / "netpoisson.py")
        server = subprocess.Popen(
            [sys.executable, script, "-d", "-q", "-H", "127.0.0.1", "-p", "0"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            assert server.stderr is not None
            line = ""
            deadline = time.time() + 5
            while time.time() < deadline:
                line = server.stderr.readline()
                if "listening on" in line:
                    break
            self.assertIn("listening on", line)
            port = line.rsplit(":", 1)[-1].strip()
            proc = subprocess.run(
                [sys.executable, script, "-q", "-l", "80", "-s", "0.8", f"127.0.0.1:{port}"],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(proc.stdout)
            self.assertEqual(report["role"], "client")
            self.assertGreater(report["received"], 5)
            self.assertEqual(report["mismatch"], 0)
            self.assertIn("jitter_rfc3550_us", report)
        finally:
            server.terminate()
            try:
                server.wait(timeout=3)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=3)
            if server.stdout is not None:
                server.stdout.close()
            if server.stderr is not None:
                server.stderr.close()


if __name__ == "__main__":
    unittest.main()
