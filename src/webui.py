#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Live dashboard. The page is a hardcoded template, not a generated site."""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

# Generated from suite/worldman/themes via generate-theme-css.mjs
THEMES_DIR = Path(__file__).resolve().parent / "web_themes"
DEFAULT_THEME = "dark-x-files"

def _web_asset(*names: str) -> Path | None:
    here = Path(__file__).resolve().parent
    for base in (here, Path("src")):
        for name in names:
            candidate = base / name
            if candidate.is_file():
                return candidate
    return None


def _page_template() -> str:
    shell_path = _web_asset("web_page.html")
    if shell_path is None:
        return "<!DOCTYPE html><html><body>web_page.html missing</body></html>\n"
    style = (_web_asset("web_page.css").read_text(encoding="utf-8") if _web_asset("web_page.css") else "")
    dash = (_web_asset("web_dash.js").read_text(encoding="utf-8") if _web_asset("web_dash.js") else "")
    raw = (_web_asset("web_raw3d.js").read_text(encoding="utf-8") if _web_asset("web_raw3d.js") else "")
    return (
        shell_path.read_text(encoding="utf-8")
        .replace("/*__NP_STYLE__*/", style.rstrip("\n"))
        .replace("/*__NP_SCRIPT__*/", (dash + raw).rstrip("\n"))
    )


PAGE = _page_template()


def _app_version() -> str:
    here = Path(__file__).resolve().parent
    for candidate in (
        here.parent / "VERSION",
        here.parent.parent / "VERSION",
        Path("VERSION"),
    ):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or "0.0.0"
    return "0.0.0"


def _web_i18n_dir() -> Path | None:
    here = Path(__file__).resolve().parent
    for candidate in (here / "web_i18n", Path("src/web_i18n")):
        if candidate.is_dir():
            return candidate
    return None


def _load_web_i18n_raw() -> dict:
    """Load per-locale JSON packs from src/web_i18n/<locale>.json."""
    d = _web_i18n_dir()
    if d is None:
        return {}
    out: dict = {}
    for path in sorted(d.glob("*.json")):
        try:
            pack = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(pack, dict):
            out[path.stem] = pack
    return out


def _web_i18n() -> dict:
    """UI + note string packs for the dashboard (not gettext/po)."""
    raw = _load_web_i18n_raw()
    out: dict = {}
    for loc, pack in raw.items():
        out[loc] = {k: v for k, v in pack.items() if not str(k).startswith("_")}
    return out or {"en": {}}


def _lang_catalog() -> list[dict]:
    """Locales present as src/web_i18n/<locale>.json (dashboard-owned, not po/LINGUAS)."""
    raw = _load_web_i18n_raw()
    rows = []
    for loc, pack in raw.items():
        label = pack.get("_label") or loc
        rows.append({"id": loc, "label": label})
    rows.sort(key=lambda r: (0 if r["id"] == "en" else 1, r["label"].lower()))
    return rows or [{"id": "en", "label": "English"}]


def _page_html() -> str:
    langs = json.dumps(_lang_catalog(), ensure_ascii=False)
    i18n = json.dumps(_web_i18n(), ensure_ascii=False)
    return (
        PAGE.replace("__NP_VERSION__", _app_version())
        .replace("__NP_LANGS__", langs)
        .replace("__NP_I18N__", i18n)
    )


def _themes_root() -> Path:
    here = Path(__file__).resolve().parent
    for candidate in (here / "web_themes", THEMES_DIR):
        if candidate.is_dir():
            return candidate
    return THEMES_DIR


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        dash = self.server.dashboard  # type: ignore[attr-defined]
        if path in ("/", "/index.html"):
            body = _page_html().encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/snapshot":
            body = json.dumps(dash.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
            return
        if path.startswith("/themes/"):
            self._send_theme(path[len("/themes/") :])
            return
        if path == "/events":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()
            while not dash.stop.is_set():
                try:
                    payload = json.dumps(dash.snapshot(), ensure_ascii=False)
                    self.wfile.write(f"data: {payload}\n\n".encode("utf-8"))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    break
                if dash.stop.wait(0.1):
                    break
            return
        self._send(404, "text/plain; charset=utf-8", b"not found\n")

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        dash = self.server.dashboard  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        if path == "/api/window":
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, "application/json; charset=utf-8", b'{"error":"bad json"}\n')
                return
            if dash.set_window is None:
                self._send(501, "application/json; charset=utf-8", b'{"error":"window resize unsupported"}\n')
                return
            slices = body.get("slices")
            ms = body.get("ms")
            auto = body.get("auto")
            if slices is None and ms is not None:
                bucket = max(1, int(body.get("bucket_ms") or 100))
                slices = max(8, int(ms) // bucket)
            if slices is None:
                self._send(400, "application/json; charset=utf-8", b'{"error":"slices or ms required"}\n')
                return
            result = dash.set_window(int(slices), auto=None if auto is None else bool(auto))
            out = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", out)
            return
        if path == "/api/config":
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except json.JSONDecodeError:
                self._send(400, "application/json; charset=utf-8", b'{"error":"bad json"}\n')
                return
            if dash.set_config is None:
                self._send(501, "application/json; charset=utf-8", b'{"error":"config unsupported"}\n')
                return
            kwargs = {}
            if "lambda_rps" in body or "lam" in body:
                kwargs["lam"] = body.get("lambda_rps", body.get("lam"))
            if "bucket_ms" in body:
                kwargs["bucket_ms"] = body.get("bucket_ms")
            if "status_interval_ms" in body:
                kwargs["status_interval_ms"] = body.get("status_interval_ms")
            if not kwargs:
                self._send(400, "application/json; charset=utf-8", b'{"error":"no config fields"}\n')
                return
            result = dash.set_config(**kwargs)
            out = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", out)
            return
        self._send(404, "text/plain; charset=utf-8", b"not found\n")

    def _send_theme(self, name: str) -> None:
        if not name or ".." in name or name.startswith("/"):
            self._send(404, "text/plain; charset=utf-8", b"not found\n")
            return
        root = _themes_root()
        target = (root / name).resolve()
        try:
            target.relative_to(root.resolve())
        except ValueError:
            self._send(404, "text/plain; charset=utf-8", b"not found\n")
            return
        if not target.is_file():
            self._send(404, "text/plain; charset=utf-8", b"not found\n")
            return
        ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        if name.endswith(".css"):
            ctype = "text/css; charset=utf-8"
        elif name.endswith(".json"):
            ctype = "application/json; charset=utf-8"
        self._send(200, ctype, target.read_bytes())

    def _send(self, code: int, content_type: str, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        return


class Dashboard:
    def __init__(self, host: str, port: int, snapshot, *, set_window=None, set_config=None) -> None:
        self.snapshot = snapshot
        self.set_window = set_window
        self.set_config = set_config
        self.stop = threading.Event()
        self.httpd = ThreadingHTTPServer((host, port), _Handler)
        self.httpd.dashboard = self  # type: ignore[attr-defined]
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.2}, daemon=True)

    def start(self) -> str:
        self.thread.start()
        return self.url()

    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        if host in ("0.0.0.0", "::"):
            host = "127.0.0.1"
        return f"http://{host}:{port}/"

    def close(self) -> None:
        self.stop.set()
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2.0)


def open_browser(url: str) -> None:
    if os.environ.get("NETPOISSON_NO_BROWSER"):
        return
    try:
        webbrowser.open(url, new=2)
    except Exception:
        return
