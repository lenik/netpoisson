#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Process entry: server, client, SSH, and web dashboard."""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from i18nutil import init_i18n
from netio import Client, Server, StdioServer
from npcli.args import (
    Args,
    UsageError,
    parse_args,
    parse_server,
    parse_web_addr,
    usage,
    version,
)
from peer_client import SshTransport
from traffic import TerminalStatus, cell_width
from webui import Dashboard, open_browser
from wiresec import UdpSeal, derive_psk, make_client_ssl_context, make_server_ssl_context


def _security(args: Args, *, server: bool):
    ssl_ctx = None
    udp_seal = None
    if args.tls and args.udp:
        udp_seal = UdpSeal(derive_psk(args.psk or ""))
    elif args.tls:
        if server:
            ssl_ctx = make_server_ssl_context(args.tls_cert, args.tls_key)
        else:
            insecure = args.tls_insecure or (args.tls_cert is None and args.tls_ca is None)
            ssl_ctx = make_client_ssl_context(insecure=insecure, cafile=args.tls_ca)
    elif args.psk and args.udp:
        udp_seal = UdpSeal(derive_psk(args.psk))
    return ssl_ctx, udp_seal

def _emit_report(report: dict, path: str | None) -> None:
    text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    sys.stdout.write(text)
    sys.stdout.flush()
    if path:
        Path(path).write_text(text, encoding="utf-8")


def _emit_ndjson(event: dict) -> None:
    sys.stdout.write(json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _install_signals(stop: threading.Event) -> None:
    def handle(_signum, _frame) -> None:
        stop.set()

    signal.signal(signal.SIGINT, handle)
    signal.signal(signal.SIGTERM, handle)


def _systemd_unit_path() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "systemd" / "user" / "netpoisson.service"


def _do_install(prog: str) -> int:
    path = _systemd_unit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    exe = shutil.which("netpoisson") or sys.argv[0]
    unit = f"""[Unit]
Description=netpoisson echo/status server
After=network.target

[Service]
ExecStart={exe} --daemon
Restart=on-failure

[Install]
WantedBy=default.target
"""
    path.write_text(unit, encoding="utf-8")
    print(_("{prog}: wrote {path}").format(prog=prog, path=path), file=sys.stderr)
    print(_("{prog}: run: systemctl --user daemon-reload && systemctl --user enable --now netpoisson").format(prog=prog), file=sys.stderr)
    return 0


def _do_remove(prog: str) -> int:
    path = _systemd_unit_path()
    if path.exists():
        path.unlink()
        print(_("{prog}: removed {path}").format(prog=prog, path=path), file=sys.stderr)
    else:
        print(_("{prog}: no unit at {path}").format(prog=prog, path=path), file=sys.stderr)
    return 0


def _start_ssh(args: Args, prog: str) -> SshTransport:
    assert args.ssh_target is not None
    cmd = [args.ssh_command, "-T"]
    defaults = [
        "BatchMode=yes",
        "Compression=no",
        "ControlMaster=no",
        "ControlPath=none",
        "LogLevel=ERROR",
        "ServerAliveInterval=0",
    ]
    seen = {opt.split("=", 1)[0] for opt in args.ssh_options if "=" in opt}
    for item in defaults:
        key = item.split("=", 1)[0]
        if key not in seen:
            cmd.extend(["-o", item])
    for opt in args.ssh_options:
        cmd.extend(["-o", opt])
    cmd.append(args.ssh_target)
    # remote command as a single argv so the remote shell does not re-parse oddly
    cmd.append(args.remote_command)
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
    except OSError as exc:
        raise UsageError(_("{prog}: {message}\n").format(prog=prog, message=str(exc))) from exc
    setup_ms = (time.monotonic() - t0) * 1000
    # Remote startup is measured until hello_ack inside Client.start.
    return SshTransport(proc=proc, setup_ms=setup_ms, remote_start_ms=0.0)


def _start_web(prog: str, args: Args, snapshot) -> Dashboard | None:
    if not args.web:
        return None
    host, port = parse_web_addr(args.web_addr, prog)
    try:
        dash = Dashboard(host, port, snapshot)
    except OSError as exc:
        raise UsageError(_("{prog}: {message}\n").format(prog=prog, message=str(exc))) from exc
    url = dash.start()
    print(_("{prog}: dashboard {url}").format(prog=prog, url=url), file=sys.stderr)
    if not args.no_open:
        open_browser(url)
    return dash


def _pump(stop: threading.Event, duration: float | None, painter: TerminalStatus, lines, *, ndjson: bool, snapshot) -> None:
    end = None if duration is None else time.monotonic() + duration
    try:
        while not stop.is_set():
            if end is not None and time.monotonic() >= end:
                return
            if ndjson:
                _emit_ndjson({"v": 1, "t": "tick", "snap": snapshot()})
            elif painter.enabled:
                columns = shutil.get_terminal_size(fallback=(100, 24)).columns
                painter.update(list(lines(columns)))
            if stop.wait(0.1):
                return
    except KeyboardInterrupt:
        stop.set()


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
    if args.install:
        return _do_install(prog)
    if args.remove:
        return _do_remove(prog)

    if args.stdio_server:
        server = StdioServer(bucket_ms=args.bucket_ms, window_slices=max(8, args.window_ms // args.bucket_ms))
        _install_signals(server.stopped)
        return server.run()

    daemon = args.daemon or (args.server is None and args.ssh_target is None)
    stop = threading.Event()
    _install_signals(stop)
    use_ndjson = args.json_out or (not sys.stdout.isatty() and args.stats is None)
    # Live UI on stderr only when stderr is a TTY and not quiet / not forced json.
    painter = TerminalStatus(args.verbose >= 0 and sys.stderr.isatty() and not args.json_out)
    dash: Dashboard | None = None
    window_slices = max(8, args.window_ms // max(1, args.bucket_ms))

    try:
        if daemon:
            if args.lam_set:
                print(
                    _("{prog}: --lambda applies to the client; ignoring it").format(prog=prog),
                    file=sys.stderr,
                )
            ssl_ctx, udp_seal = _security(args, server=True)
            server = Server(
                args.host,
                args.port,
                udp=args.udp,
                bucket_ms=args.bucket_ms,
                window_slices=window_slices,
                ssl_ctx=ssl_ctx,
                udp_seal=udp_seal,
            )
            try:
                server.start()
            except OSError as exc:
                print(_("{prog}: {message}").format(prog=prog, message=exc), file=sys.stderr)
                return 1
            if args.udp:
                proto = "udp+psk" if udp_seal else "udp"
            else:
                proto = "tcp+tls" if ssl_ctx else "tcp"
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
            _pump(
                stop,
                args.stats,
                painter,
                lambda cols: server_lines(server, cols),
                ndjson=use_ndjson,
                snapshot=server.snapshot,
            )
            server.stop()
            if args.stats is not None:
                _emit_report(server.report(), args.report_file)
            return 0

        ssh_transport = None
        host = "ssh"
        port = 0
        if args.ssh_target:
            t0 = time.monotonic()
            ssh_transport = _start_ssh(args, prog)
            # remote_start filled after hello
            host = args.ssh_target
        else:
            assert args.server is not None
            if args.host_set and args.verbose >= 0:
                print(
                    _("{prog}: --host is the listen address; the target is SERVER").format(prog=prog),
                    file=sys.stderr,
                )
            host, port = parse_server(args.server, args.port, prog)
            if args.port_set:
                port = args.port

        ssl_ctx, udp_seal = _security(args, server=False)
        client = Client(
            host,
            port,
            udp=args.udp,
            lam=args.lam,
            timeout_s=args.timeout_s,
            payload_size=args.payload_size,
            connections=args.connections,
            max_pending=args.max_pending,
            status_interval_ms=args.status_interval_ms,
            bucket_ms=args.bucket_ms,
            window_slices=window_slices,
            seed=args.seed,
            ssh=ssh_transport,
            payload_min=args.payload_min,
            payload_max=args.payload_max,
            ssl_ctx=ssl_ctx,
            udp_seal=udp_seal,
        )
        try:
            hello_t0 = time.monotonic()
            client.start()
            if ssh_transport is not None:
                ssh_transport.remote_start_ms = (time.monotonic() - hello_t0) * 1000
        except OSError as exc:
            print(_("{prog}: {message}").format(prog=prog, message=exc), file=sys.stderr)
            client.stop(grace=0.1)
            return 1
        if args.verbose > 0:
            print(
                _("{prog}: connected {proto} {endpoint} at {lam} req/s").format(
                    prog=prog,
                    proto=client.snapshot()["proto"],
                    endpoint=client.endpoint(),
                    lam=args.lam,
                ),
                file=sys.stderr,
            )
        try:
            dash = _start_web(prog, args, client.snapshot)
        except UsageError as exc:
            client.stop(grace=0.1)
            sys.stderr.write(exc.message)
            return 1
        width = cell_width(args.lam, args.bucket_ms / 1000)
        _pump(
            stop,
            args.stats,
            painter,
            lambda cols: client.status_lines(cols, width=width),
            ndjson=use_ndjson,
            snapshot=client.snapshot,
        )
        client.stop(grace=1.0)
        report = client.report()
        if args.stats is not None or args.report_file:
            _emit_report(report, args.report_file)
        elif use_ndjson:
            _emit_ndjson({"v": 1, "t": "report", "report": report})
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


def server_lines(server: Server, columns: int) -> tuple[str, ...]:
    from traffic import choose_half

    snap = server.snapshot()
    half = min(12, choose_half(columns, 2))
    from traffic import format_server_lines

    return format_server_lines(
        snap["recv"],
        snap["resp"],
        snap["queues"]["read_pending"],
        snap["queues"]["processing"],
        snap["queues"]["response_pending"],
        width=2,
        half=half,
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
