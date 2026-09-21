#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""CLI argument parsing and help text."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TextIO

from protocol import parse_duration_ms, parse_size

DEFAULT_PORT = 1871
DEFAULT_WEB_PORT = 3871

PROFILES = {
    "ssh-interactive": {
        "lam": 20.0,
        "payload_min": 1,
        "payload_max": 128,
        "payload_size": 64,
        "status_interval_ms": 200,
        "timeout_s": 5.0,
        "connections": 1,
    },
    "ssh-bulk": {
        "lam": 100.0,
        "payload_min": 4 * 1024,
        "payload_max": 64 * 1024,
        "payload_size": 16 * 1024,
        "status_interval_ms": 100,
        "timeout_s": 10.0,
        "connections": 1,
    },
}


class UsageError(Exception):
    def __init__(self, message: str) -> None:
        self.message = message


@dataclass
class Args:
    daemon: bool = False
    stdio_server: bool = False
    host: str = "0.0.0.0"
    host_set: bool = False
    port: int = DEFAULT_PORT
    port_set: bool = False
    udp: bool = False
    tls: bool = False
    psk: str | None = None
    tls_cert: str | None = None
    tls_key: str | None = None
    tls_ca: str | None = None
    tls_insecure: bool = False
    lam: float = 100.0
    lam_set: bool = False
    payload_size: int = 64
    payload_min: int | None = None
    payload_max: int | None = None
    connections: int = 1
    max_pending: int | None = None
    status_interval_ms: int = 100
    bucket_ms: int = 100
    window_ms: int = 12000
    seed: int | None = None
    timeout_s: float = 2.0
    stats: float | None = None
    web: bool = False
    web_addr: str = "127.0.0.1:3871"
    no_open: bool = False
    json_out: bool = False
    report_file: str | None = None
    ssh_target: str | None = None
    ssh_command: str = "ssh"
    remote_command: str = "netpoisson --stdio-server"
    ssh_options: list[str] = field(default_factory=list)
    profile: str | None = None
    install: bool = False
    remove: bool = False
    verbose: int = 0
    server: str | None = None
    help: bool = False
    show_version: bool = False


def app_version() -> str:
    here = Path(__file__).resolve()
    for candidate in (
        here.parent.parent.parent / "VERSION",
        here.parent.parent / "VERSION",
        here.parent / "VERSION",
    ):
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
            "Offer a Poisson stream of requests to SERVER, or listen when SERVER is omitted.\n"
        )
    )
    out.write("\n")
    out.write(_("Modes:\n"))
    out.write(_("  -d, --daemon              listen (also the default when SERVER is omitted)\n"))
    out.write(_("      --stdio-server        serve NDJSON @nPoi protocol on stdin/stdout\n"))
    out.write(_("      --ssh TARGET          run client over SSH stdio to TARGET\n"))
    out.write(_("      --install/--remove    manage a systemd user service unit\n"))
    out.write("\n")
    out.write(_("Transport:\n"))
    out.write(_("  -H, --host HOST           bind host (default 0.0.0.0)\n"))
    out.write(_("  -p, --port PORT           port (default 1871)\n"))
    out.write(_("  -u, --udp                 UDP; default TCP\n"))
    out.write(_("      --tls                 TLS on TCP; PSK seal on UDP (needs --psk)\n"))
    out.write(_("      --psk TEXT            pre-shared key for UDP seal\n"))
    out.write(_("      --cert/--key PATH     TLS server certificate and key (PEM)\n"))
    out.write(_("      --tls-ca PATH         TLS client CA file\n"))
    out.write(_("      --tls-insecure        TLS client: skip certificate verify\n"))
    out.write(_("      --ssh-command PATH    ssh binary (default ssh)\n"))
    out.write(_("      --remote-command CMD  remote stdio server command\n"))
    out.write(_("      --ssh-option KEY=VAL  extra -o option (repeatable)\n"))
    out.write("\n")
    out.write(_("Traffic:\n"))
    out.write(_("  -l, --lambda RATE         mean requests/s (default 100)\n"))
    out.write(_("      --payload SIZE        payload size (default 64B)\n"))
    out.write(_("      --connections N       parallel TCP connections (default 1)\n"))
    out.write(_("      --max-pending N       client pending queue limit\n"))
    out.write(_("      --status-interval D   STATUS interval (default 100ms)\n"))
    out.write(_("      --bucket D            timeline bucket (default 100ms)\n"))
    out.write(_("      --window D            visible window (default 12s)\n"))
    out.write(_("      --seed N              reproducible RNG seed\n"))
    out.write(_("      --profile NAME        ssh-interactive | ssh-bulk\n"))
    out.write("\n")
    out.write(_("Execution:\n"))
    out.write(_("  -s, --stats D             run for D then print a final report\n"))
    out.write(_("  -w, --web[=ADDR]          start Web UI (default 127.0.0.1:3871)\n"))
    out.write(_("      --no-open             do not open a browser\n"))
    out.write(_("      --json                force NDJSON event output\n"))
    out.write(_("      --report FILE         save final JSON report\n"))
    out.write("  -v, --verbose             ")
    out.write(_("repeat for more verbose loggings\n"))
    out.write("  -q, --quiet               ")
    out.write(_("show less logging messages\n"))
    out.write("  -h, --help                ")
    out.write(_("display this help and exit\n"))
    out.write("  -V, --version             ")
    out.write(_("output version information and exit\n"))
    out.write("\n")
    out.write(
        _(
            "TCP/UDP use a length-prefixed binary PDU. SSH stdio uses @nPoi NDJSON lines.\n"
            "When stdout is not a TTY, live UI is suppressed and NDJSON events are emitted.\n"
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
    "--payload": "payload",
    "--connections": "connections",
    "--max-pending": "max_pending",
    "--status-interval": "status_interval",
    "--bucket": "bucket",
    "--window": "window",
    "--seed": "seed",
    "--ssh": "ssh",
    "--ssh-command": "ssh_command",
    "--remote-command": "remote_command",
    "--ssh-option": "ssh_option",
    "--profile": "profile",
    "--report": "report",
    "--timeout": "timeout",
    "--psk": "psk",
    "--cert": "tls_cert",
    "--key": "tls_key",
    "--tls-ca": "tls_ca",
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


def _apply_profile(args: Args, name: str, prog: str) -> None:
    profile = PROFILES.get(name)
    if profile is None:
        raise UsageError(_("{prog}: unknown profile {name}\n").format(prog=prog, name=name))
    if not args.lam_set:
        args.lam = float(profile["lam"])
    args.payload_size = int(profile["payload_size"])
    args.payload_min = int(profile["payload_min"])
    args.payload_max = int(profile["payload_max"])
    args.status_interval_ms = int(profile["status_interval_ms"])
    args.timeout_s = float(profile["timeout_s"])
    args.connections = int(profile["connections"])


def parse_args(argv: list[str], prog: str = "netpoisson") -> Args:
    args = Args()
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token in ("-h", "--help"):
            args.help = True
            return args
        if token in ("-V", "--version"):
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
        if token == "--stdio-server":
            args.stdio_server = True
            i += 1
            continue
        if token in ("-u", "--udp"):
            args.udp = True
            i += 1
            continue
        if token == "--tls":
            args.tls = True
            i += 1
            continue
        if token == "--tls-insecure":
            args.tls_insecure = True
            i += 1
            continue
        if token == "--no-open":
            args.no_open = True
            i += 1
            continue
        if token == "--json":
            args.json_out = True
            i += 1
            continue
        if token == "--install":
            args.install = True
            i += 1
            continue
        if token == "--remove":
            args.remove = True
            i += 1
            continue
        if token == "-w" or token == "--web":
            args.web = True
            i += 1
            continue
        if token.startswith("--web="):
            args.web = True
            args.web_addr = token.split("=", 1)[1]
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
            # Accept bare seconds or duration strings.
            text = raw or ""
            if text[-1:].lower() in "smh" or text.endswith("ms"):
                args.stats = parse_duration_ms(text) / 1000.0
            else:
                args.stats = _positive(prog, text, "duration")
        elif key == "payload":
            args.payload_size = parse_size(raw or "0")
        elif key == "connections":
            args.connections = max(1, int(raw or "1"))
        elif key == "max_pending":
            args.max_pending = max(1, int(raw or "1"))
        elif key == "status_interval":
            args.status_interval_ms = parse_duration_ms(raw or "100ms")
        elif key == "bucket":
            args.bucket_ms = parse_duration_ms(raw or "100ms")
        elif key == "window":
            args.window_ms = parse_duration_ms(raw or "12s")
        elif key == "seed":
            args.seed = int(raw or "0")
        elif key == "ssh":
            args.ssh_target = raw
        elif key == "ssh_command":
            args.ssh_command = raw or "ssh"
        elif key == "remote_command":
            args.remote_command = raw or "netpoisson --stdio-server"
        elif key == "ssh_option":
            args.ssh_options.append(raw or "")
        elif key == "profile":
            args.profile = raw
        elif key == "report":
            args.report_file = raw
        elif key == "timeout":
            args.timeout_s = _positive(prog, raw or "2", "duration")
        elif key == "psk":
            args.psk = raw
        elif key == "tls_cert":
            args.tls_cert = raw
        elif key == "tls_key":
            args.tls_key = raw
        elif key == "tls_ca":
            args.tls_ca = raw
    if len(positionals) > 1:
        raise UsageError(_("{prog}: unexpected argument {text}\n").format(prog=prog, text=positionals[1]))
    if positionals:
        args.server = positionals[0]
    if args.profile:
        _apply_profile(args, args.profile, prog)
    if args.daemon and args.server:
        raise UsageError(_("{prog}: do not combine --daemon with SERVER\n").format(prog=prog))
    if args.stdio_server and (args.server or args.ssh_target or args.daemon):
        raise UsageError(_("{prog}: --stdio-server is exclusive\n").format(prog=prog))
    if args.udp and args.ssh_target:
        raise UsageError(_("{prog}: --udp cannot combine with --ssh\n").format(prog=prog))
    if args.tls and args.udp and not args.psk:
        raise UsageError(_("{prog}: UDP --tls requires --psk\n").format(prog=prog))
    if (args.tls_cert and not args.tls_key) or (args.tls_key and not args.tls_cert):
        raise UsageError(_("{prog}: --cert and --key must be used together\n").format(prog=prog))
    if args.install and args.remove:
        raise UsageError(_("{prog}: choose --install or --remove\n").format(prog=prog))
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


def parse_web_addr(text: str, prog: str) -> tuple[str, int]:
    if ":" in text:
        host, port_s = text.rsplit(":", 1)
        return host or "127.0.0.1", _port(prog, port_s)
    return text, DEFAULT_WEB_PORT
