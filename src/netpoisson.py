#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Poisson load generator and the server that answers it."""

from __future__ import annotations

import json
import shutil
import signal
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO

from commons import init_i18n
from netio import Client, Server
from traffic import TerminalStatus, cell_width
from webui import Dashboard, open_browser

DEFAULT_PORT = 1871
DEFAULT_WEB_PORT = 8711


class UsageError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


@dataclass
class Args:
    daemon: bool = False
    host: str = "0.0.0.0"
    host_set: bool = False
    port: int = DEFAULT_PORT
    port_set: bool = False
    udp: bool = False
    lam: float = 100.0
    lam_set: bool = False
    stats: float | None = None
    web: bool = False
    web_host: str = "127.0.0.1"
    web_port: int = DEFAULT_WEB_PORT
    verbose: int = 0
    server: str | None = None
    help: bool = False
    show_version: bool = False


def app_version() -> str:
    here = Path(__file__).resolve()
    for candidate in (here.parent.parent / "VERSION", here.parent / "VERSION"):
        try:
            text = candidate.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if text:
            return text[1:] if text.startswith("v") else text
    return "dev"


def usage(out: TextIO) -> None:
    out.write(_("Usage: netpoisson [OPTION]... [SERVER]\n"))
    out.write(
        _(
            "Offer a Poisson stream of requests to SERVER, or listen for them when\n"
            "SERVER is omitted.\n"
        )
    )
    out.write("\n")
    out.write(_("  -d, --daemon       listen (also the default when SERVER is omitted)\n"))
    out.write(_("  -H, --host HOST    address to bind (default 0.0.0.0)\n"))
    out.write(_("  -p, --port PORT    port to bind or connect to (default 1871)\n"))
    out.write(_("  -u, --udp          use UDP (default is TCP)\n"))
    out.write(_("  -l, --lambda N     offered requests per second (default 100)\n"))
    out.write(_("  -s, --stats SECS   run for SECS seconds, then print a JSON report\n"))
    out.write(_("  -w, --web          serve a live dashboard and open a browser\n"))
    out.write(_("      --web-host H   dashboard address (default 127.0.0.1)\n"))
    out.write(_("      --web-port N   dashboard port (default 8711)\n"))
    out.write("  -v, --verbose      ")
    out.write(_("repeat for more verbose loggings\n"))
    out.write("  -q, --quiet        ")
    out.write(_("show less logging messages\n"))
    out.write("  -h, --help         ")
    out.write(_("display this help and exit\n"))
    out.write("      --version      ")
    out.write(_("output version information and exit\n"))
    out.write("\n")
    out.write(
        _(
            "ECHO responses are the request bytes. STATUS responses carry the server\n"
            "timeline and queues. Req/Resp lines scroll right: newest slices on the left.\n"
        )
    )
    out.write("\n")
    out.write(_("Report bugs to: <{email}>\n").format(email="netpoisson@bodz.net"))


def version(out: TextIO) -> None:
    out.write(f"netpoisson {app_version()}\n")
    out.write(_("Copyright (C) {year} {author}\n").format(year=2026, author="Lenik"))
    out.write(_("License AGPL-3.0-or-later: <https://www.gnu.org/licenses/agpl-3.0.html>\n"))
    out.write(_("This is free software: you are free to change and redistribute it.\n"))
    out.write(_("This project opposes AI exploitation and AI hegemony.\n"))
    out.write(
        _(
            "This project rejects mindless MIT-style licensing and politically naive "
            "BSD-style licensing.\n"
        )
    )
    out.write(_("There is NO WARRANTY, to the extent permitted by law.\n"))


_VALUE = {
    "-H": "host",
    "--host": "host",
    "-p": "port",
    "--port": "port",
    "-l": "lam",
    "--lambda": "lam",
    "-s": "stats",
    "--stats": "stats",
    "--web-host": "web_host",
    "--web-port": "web_port",
}


def _need(prog: str, opt: str, args: list[str], index: int) -> tuple[str, int]:
    if index >= len(args):
        raise UsageError(_("{prog}: missing argument for {opt}\n").format(prog=prog, opt=opt))
    return args[index], index + 1


def _port(prog: str, text: str) -> int:
    try:
        value = int(text, 10)
    except ValueError:
        raise UsageError(_("{prog}: invalid number: {text}\n").format(prog=prog, text=text)) from None
    if value < 0 or value > 65535:
        raise UsageError(_("{prog}: port out of range\n").format(prog=prog))
    return value


def _positive(prog: str, text: str, kind: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise UsageError(_("{prog}: invalid number: {text}\n").format(prog=prog, text=text)) from None
    if not value > 0:
        if kind == "lambda":
            raise UsageError(_("{prog}: lambda must be positive\n").format(prog=prog))
        raise UsageError(_("{prog}: duration must be positive\n").format(prog=prog))
    return value


def parse_args(argv: list[str], prog: str = "netpoisson") -> Args:
    args = Args()
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("-h", "--help"):
            args.help = True
            return args
        if token == "--version":
            args.show_version = True
            return args
        if token in ("-v", "--verbose"):
            args.verbose += 1
            i += 1
            continue
        if token in ("-q", "--quiet"):
            args.verbose = -1
            i += 1
            continue
        if token in ("-d", "--daemon"):
            args.daemon = True
            i += 1
            continue
        if token in ("-u", "--udp"):
            args.udp = True
            i += 1
            continue
        if token in ("-w", "--web"):
            args.web = True
            i += 1
            continue
        key = None
        raw = None
        if token in _VALUE:
            raw, i = _need(prog, token, argv, i + 1)
            key = _VALUE[token]
        elif token.startswith("--") and "=" in token:
            name, raw = token.split("=", 1)
            if name not in _VALUE:
                raise UsageError(_("{prog}: unknown option {opt}\n").format(prog=prog, opt=name))
            key = _VALUE[name]
            i += 1
        elif token.startswith("-") and not token.startswith("--") and len(token) > 2 and token[:2] in _VALUE:
            key = _VALUE[token[:2]]
            raw = token[2:]
            i += 1
        elif token.startswith("-"):
            raise UsageError(_("{prog}: unknown option {opt}\n").format(prog=prog, opt=token))
        else:
            positionals.append(token)
            i += 1
            continue
        if key == "host":
            args.host = raw or ""
            args.host_set = True
        elif key == "port":
            args.port = _port(prog, raw or "")
            args.port_set = True
        elif key == "lam":
            args.lam = _positive(prog, raw or "", "lambda")
            args.lam_set = True
        elif key == "stats":
            args.stats = _positive(prog, raw or "", "duration")
        elif key == "web_host":
            args.web_host = raw or ""
        elif key == "web_port":
            args.web_port = _port(prog, raw or "")
            if args.web_port == 0:
                raise UsageError(_("{prog}: port out of range\n").format(prog=prog))
    if len(positionals) > 1:
        raise UsageError(_("{prog}: unexpected argument {text}\n").format(prog=prog, text=positionals[1]))
    if positionals:
        args.server = positionals[0]
    if args.daemon and args.server:
        raise UsageError(_("{prog}: do not combine --daemon with SERVER\n").format(prog=prog))
    return args


def parse_server(text: str, default_port: int, prog: str) -> tuple[str, int]:
    if text.startswith("["):
        end = text.find("]")
        if end < 0:
            raise UsageError(_("{prog}: invalid server address\n").format(prog=prog))
        host = text[1:end]
        rest = text[end + 1 :]
        if not rest:
            return host, default_port
        if rest.startswith(":"):
            return host, _port(prog, rest[1:])
        raise UsageError(_("{prog}: invalid server address\n").format(prog=prog))
    if text.count(":") == 1:
        host, maybe = text.rsplit(":", 1)
        if maybe.isdigit():
            return host, _port(prog, maybe)
    return text, default_port


def _pump(stop: threading.Event, duration: float | None, painter: TerminalStatus, lines) -> None:
    end = None if duration is None else time.monotonic() + duration
    try:
        while not stop.is_set():
            if end is not None and time.monotonic() >= end:
                return
            if painter.enabled:
                columns = shutil.get_terminal_size(fallback=(100, 24)).columns
                painter.update(list(lines(columns)))
            if stop.wait(0.1):
                return
    except KeyboardInterrupt:
        stop.set()


def _emit_report(report: dict) -> None:
    json.dump(report, sys.stdout, indent=2, sort_keys=True, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()


def _install_signals(stop: threading.Event) -> None:
    def handle(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def _start_web(prog: str, args: Args, snapshot) -> Dashboard | None:
    if not args.web:
        return None
    try:
        dash = Dashboard(args.web_host, args.web_port, snapshot)
    except OSError as exc:
        raise UsageError(_("{prog}: {message}\n").format(prog=prog, message=str(exc))) from exc
    url = dash.start()
    print(_("{prog}: dashboard {url}").format(prog=prog, url=url), file=sys.stderr)
    open_browser(url)
    return dash


def main(argv: list[str]) -> int:
    init_i18n(argv[0])
    prog = Path(argv[0]).name
    try:
        args = parse_args(argv[1:], prog)
    except UsageError as exc:
        sys.stderr.write(exc.message if exc.message.endswith("\n") else exc.message + "\n")
        return 1
    if args.help:
        usage(sys.stdout)
        return 0
    if args.show_version:
        version(sys.stdout)
        return 0

    daemon = args.daemon or args.server is None
    stop = threading.Event()
    _install_signals(stop)
    painter = TerminalStatus(args.verbose >= 0)
    dash: Dashboard | None = None

    try:
        if daemon:
            if args.lam_set:
                print(
                    _("{prog}: --lambda applies to the client; ignoring it").format(prog=prog),
                    file=sys.stderr,
                )
            if args.host_set is False and args.server is None:
                pass
            server = Server(args.host, args.port, udp=args.udp)
            try:
                server.start()
            except OSError as exc:
                print(_("{prog}: {message}").format(prog=prog, message=exc), file=sys.stderr)
                return 1
            proto = "udp" if args.udp else "tcp"
            print(
                _("{prog}: listening on {proto}://{endpoint}").format(
                    prog=prog, proto=proto, endpoint=server.endpoint()
                ),
                file=sys.stderr,
            )
            try:
                dash = _start_web(prog, args, server.snapshot)
            except UsageError as exc:
                server.stop()
                sys.stderr.write(exc.message)
                return 1
            _pump(stop, args.stats, painter, lambda cols: server_lines(server, cols))
            server.stop()
            if args.stats is not None:
                _emit_report(server.report())
            return 0

        assert args.server is not None
        if args.host_set and args.verbose >= 0:
            print(
                _("{prog}: --host is the listen address; the target is SERVER").format(prog=prog),
                file=sys.stderr,
            )
        host, port = parse_server(args.server, args.port, prog)
        if args.port_set:
            port = args.port
        client = Client(host, port, udp=args.udp, lam=args.lam)
        try:
            client.start()
        except OSError as exc:
            print(_("{prog}: {message}").format(prog=prog, message=exc), file=sys.stderr)
            return 1
        if args.verbose > 0:
            print(
                _("{prog}: connected {proto} {endpoint} at {lam} req/s").format(
                    prog=prog,
                    proto="udp" if args.udp else "tcp",
                    endpoint=client.endpoint(),
                    lam=args.lam,
                ),
                file=sys.stderr,
            )
        if args.verbose >= 0:
            print(
                _(
                    "Req counts local arrivals per slice (newest on the left). "
                    "The center number has not left this host."
                ),
                file=sys.stderr,
            )
            print(
                _(
                    "Resp counts server replies per slice. "
                    "Left of the arrow: requests not yet read. Next: replies not yet sent."
                ),
                file=sys.stderr,
            )
        try:
            dash = _start_web(prog, args, client.snapshot)
        except UsageError as exc:
            client.stop(grace=0.1)
            sys.stderr.write(exc.message)
            return 1
        width = cell_width(args.lam)
        _pump(
            stop,
            args.stats,
            painter,
            lambda cols: client.status_lines(cols, width=width),
        )
        client.stop(grace=1.0)
        report = client.report()
        if args.stats is not None:
            _emit_report(report)
        if report["sent"] > 0 and report["received"] == 0:
            return 1
        return 0
    except UsageError as exc:
        sys.stderr.write(exc.message if exc.message.endswith("\n") else exc.message + "\n")
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        if dash is not None:
            dash.close()


def server_lines(server: Server, columns: int) -> tuple[str, str]:
    from traffic import choose_half, format_server_lines

    snap = server.snapshot()
    half = min(12, choose_half(columns, 2))
    return format_server_lines(snap["req"], snap["resp"], snap["server_rx"], snap["server_tx"], width=2, half=half)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
