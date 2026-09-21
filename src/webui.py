#!/usr/bin/env python3
# Copyright (C) 2026 Lenik <netpoisson@bodz.net>
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Live dashboard. The page is a hardcoded template, not a generated site."""

from __future__ import annotations

import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>netpoisson</title>
<script src="https://cdn.tailwindcss.com" defer></script>
<style>
  :root {
    --bg: #0b1020;
    --card: #141a2e;
    --line: #2a3654;
    --text: #e8eefc;
    --muted: #93a0bd;
    --req: #5eead4;
    --resp: #fbbf24;
    --bad: #fb7185;
    --warn: #fbbf24;
    --ok: #4ade80;
    --info: #7dd3fc;
  }
  * { box-sizing: border-box; }
  body.np {
    margin: 0;
    background: radial-gradient(1200px 500px at 10% -10%, #1a2444 0%, var(--bg) 55%);
    color: var(--text);
    font: 15px/1.45 ui-sans-serif, system-ui, "Noto Sans SC", sans-serif;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 24px 20px 48px; }
  header.top { display: flex; justify-content: space-between; gap: 16px; align-items: flex-end; flex-wrap: wrap; }
  h1 { font-size: 28px; margin: 0 0 4px; letter-spacing: -0.03em; }
  .sub { color: var(--muted); margin: 0; }
  .pills { display: flex; gap: 8px; flex-wrap: wrap; }
  .pill { background: #10182e; border: 1px solid var(--line); border-radius: 999px; padding: 4px 10px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .card { background: rgba(20, 26, 46, 0.92); border: 1px solid var(--line); border-radius: 16px; padding: 16px; margin-top: 16px; }
  h2 { margin: 0 0 8px; font-size: 16px; }
  .hint { color: var(--muted); margin: 0 0 12px; font-size: 13px; }
  .board.four { display: flex; flex-direction: column; gap: 10px; }
  .board.four .row { display: grid; grid-template-columns: 48px 1fr auto auto auto auto; gap: 8px; align-items: end; }
  .board.four .lab { color: var(--muted); font-size: 12px; padding-bottom: 14px; }
  .cells.recv .cell i { background: #7dd3fc; }
  .cells.ack .cell i { background: #c4b5fd; }
  .board {
    display: grid;
    grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
    grid-template-rows: auto auto;
    gap: 10px 14px;
    align-items: stretch;
  }
  .cells { display: flex; gap: 3px; min-height: 78px; align-items: flex-end; }
  .cells.right { justify-content: flex-start; }
  .cells.left { justify-content: flex-end; }
  .cell { width: 18px; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; gap: 4px; }
  .cell i { display: block; width: 10px; border-radius: 3px 3px 1px 1px; background: var(--req); min-height: 0; }
  .resp .cell i { background: var(--resp); }
  .cell b { font-size: 10px; font-weight: 600; color: var(--muted); font-variant-numeric: tabular-nums; min-height: 12px; }
  .mark {
    grid-column: 2;
    grid-row: 1 / span 2;
    min-width: 168px;
    border: 1px dashed var(--line);
    border-radius: 14px;
    padding: 10px 12px;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 4px;
    background: #10182c;
  }
  .mark strong { font-size: 28px; font-variant-numeric: tabular-nums; line-height: 1; }
  .mark .rx { color: var(--info); }
  .mark .tx { color: var(--resp); }
  .mark .local { color: var(--req); }
  .arrow { color: var(--muted); font-size: 20px; line-height: 1; }
  .mark small { color: var(--muted); font-size: 11px; }
  .pair { display: flex; gap: 14px; align-items: flex-end; }
  pre.ascii {
    margin: 12px 0 0;
    padding: 10px 12px;
    overflow: auto;
    background: #0c1224;
    border-radius: 10px;
    color: #d5deef;
    font: 12px/1.45 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
  }
  .stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; }
  .stat { background: #10182c; border-radius: 12px; padding: 10px 12px; }
  .stat b { display: block; font-size: 20px; font-variant-numeric: tabular-nums; }
  .stat span { color: var(--muted); font-size: 12px; }
  .charts { display: grid; grid-template-columns: 1.2fr 1fr 0.8fr; gap: 12px; }
  canvas { width: 100%; height: 140px; background: #0c1224; border-radius: 10px; display: block; }
  .note { border-left: 3px solid var(--info); padding: 8px 10px; margin: 8px 0; background: #10182c; border-radius: 8px; }
  .note.warn { border-color: var(--warn); }
  .note.bad { border-color: var(--bad); }
  .note.info { border-color: var(--ok); }
  .legend { display: flex; gap: 16px; color: var(--muted); font-size: 12px; margin-top: 8px; }
  .legend i { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
  @media (max-width: 800px) {
    .charts { grid-template-columns: 1fr; }
    .board { grid-template-columns: 1fr; }
    .mark { grid-column: 1; grid-row: auto; }
    .cells.left, .cells.right { justify-content: flex-start; overflow-x: auto; }
  }
</style>
</head>
<body class="np bg-slate-950 text-slate-100">
<div class="wrap">
  <header class="top">
    <div>
      <h1>netpoisson</h1>
      <p class="sub" id="headline">连接中…</p>
    </div>
    <div class="pills">
      <span class="pill" id="pill-proto">tcp</span>
      <span class="pill" id="pill-endpoint">–</span>
      <span class="pill" id="pill-lambda">λ –</span>
      <span class="pill" id="pill-uptime">0.0 s</span>
    </div>
  </header>

  <section class="card">
    <h2>四条时间线</h2>
    <p class="hint">Newest bucket on the left. Req=client transmitted, Recv=server received, Resp=server transmitted, Ack=client received.</p>
    <div class="board four">
      <div class="row"><span class="lab">Req.</span><div class="cells req" id="req-cells"></div><strong class="local" id="q-client-tx">–</strong><small>pending</small></div>
      <div class="row"><span class="lab">Recv.</span><div class="cells recv" id="recv-cells"></div><strong class="rx" id="q-read">–</strong><small>read</small></div>
      <div class="row"><span class="lab">Resp.</span><div class="cells resp" id="resp-cells"></div><strong class="tx" id="q-proc">–</strong><small>processing</small> <strong class="tx" id="q-server-tx">–</strong><small>pending</small></div>
      <div class="row"><span class="lab">Ack.</span><div class="cells ack" id="ack-cells"></div></div>
    </div>
    <div class="legend">
      <span><i style="background:#5eead4"></i>Req</span>
      <span><i style="background:#7dd3fc"></i>Recv</span>
      <span><i style="background:#fbbf24"></i>Resp</span>
      <span><i style="background:#c4b5fd"></i>Ack</span>
    </div>
    <pre class="ascii" id="ascii"></pre>
  </section>

  <section class="card">
    <h2>实时统计</h2>
    <div class="stats">
      <div class="stat"><b id="achieved">–</b><span>实际 req/s</span></div>
      <div class="stat"><b id="loss">–</b><span>丢失</span></div>
      <div class="stat"><b id="rtt-p50">–</b><span>RTT P50</span></div>
      <div class="stat"><b id="rtt-p99">–</b><span>RTT P99</span></div>
      <div class="stat"><b id="jitter">–</b><span>RFC 3550 抖动</span></div>
      <div class="stat"><b id="bw">–</b><span>带宽 (netpoisson)</span></div>
      <div class="stat"><b id="bytes">–</b><span>会话收发 bytes</span></div>
      <div class="stat"><b id="lag">–</b><span>时间线滞后</span></div>
    </div>
  </section>

  <section class="card">
    <h2>抖动与排队</h2>
    <div class="charts">
      <div><p class="hint">RTT</p><canvas id="rtt-chart"></canvas></div>
      <div><p class="hint">队列深度</p><canvas id="queue-chart"></canvas></div>
      <div><p class="hint">RTT 分布</p><canvas id="hist-chart"></canvas></div>
    </div>
  </section>

  <section class="card">
    <h2>分析</h2>
    <div id="notes"></div>
  </section>
</div>
<script>
const TEXT = {
  overview: (n) => "已产生 " + n.offered + " 个请求，发出 " + n.sent + "，收到 " + n.received + "，丢失 " + n.lost + "。实际 " + n.achieved_rps + " req/s，目标 " + (n.lambda_rps == null ? "—" : n.lambda_rps) + " req/s。",
  warming_up: (n) => "样本还少（" + n.sent + " 个已发出），统计会在几秒内稳定下来。",
  rate_ok: (n) => "发送速率跟上了目标（" + n.achieved_rps + " / " + n.lambda_rps + " req/s，" + Math.round(n.ratio * 100) + "%）。",
  rate_shortfall: (n) => "实际发出 " + n.achieved_rps + " req/s，低于目标 " + n.lambda_rps + "（" + Math.round(n.ratio * 100) + "%）。本机发送队列或路径在限速。",
  loss: (n) => "丢失 " + (n.loss_ratio * 100).toFixed(2) + "%（" + n.lost + "/" + n.sent + "）。超时未返回会计入这里。",
  jitter_low: (n) => "抖动较低。RFC 3550 抖动 " + us(n.jitter_us) + "，平均 RTT " + us(n.mean_rtt_us) + "，P99 " + us(n.p99_us) + "。",
  jitter_high: (n) => "抖动偏大。RFC 3550 抖动 " + us(n.jitter_us) + "，平均 RTT " + us(n.mean_rtt_us) + "（CV " + n.cv + "），P50 " + us(n.p50_us) + "，P99 " + us(n.p99_us) + "。延迟在抖，不只是稳定排队。",
  client_tx_backlog: (n) => "本机还有 " + n.client_tx + " 个请求在排队，没有离开网口（阈值 " + n.threshold + "）。发送比泊松到达慢。",
  server_rx_backlog: (n) => "服务器还有 " + n.server_rx + " 个请求没被读取或分析（阈值 " + n.threshold + "）。服务端处理慢于到达。",
  server_tx_backlog: (n) => "服务器还有 " + n.server_tx + " 个响应没离开网口（阈值 " + n.threshold + "）。发送侧或对端窗口在堆积。",
  keeping_up: () => "队列很浅，丢失可以忽略。这条路径目前跟得上负载。",
  poisson_ok: (n) => "到达间隔的变异系数 " + n.cv + "，接近指数分布的 1（均值 " + us(n.mean_us) + "，理论 " + us(n.expected_us) + "）。负载形状像泊松过程。",
  poisson_off: (n) => "到达间隔 CV " + n.cv + "，偏离指数分布（理论 CV 约 1，均值 " + us(n.mean_us) + "，理论 " + us(n.expected_us) + "）。调度把流量抹平或打成了突发。",
  timeline_lag: (n) => "响应时间线相对请求大约落后 " + n.slices + " 个时间片（" + n.ms + " ms）。其中包含路径时延，也可能含两端时钟差。",
  server_hold: (n) => "服务器从读到请求到组好响应，P50 " + us(n.p50_us) + "，P95 " + us(n.p95_us) + "。这段时间包含接收队列里的等待。",
  local_queue_dominates: (n) => "本机排队 P50 " + us(n.queue_delay_p50_us) + " 已经大于在途 RTT 均值 " + us(n.mean_rtt_us) + "。变慢主要发生在离开网口之前。",
  clock_skew: (n) => "两端时间片相差 " + n.slices + "（" + n.ms + " ms）。时间线对齐里混有时钟偏差。",
  no_server_status: () => "还没有收到 STATUS。服务器队列和时间线暂时是空的。",
  echo_mismatch: (n) => "有 " + n.mismatch + " 个 ECHO 响应和请求字节不一致。",
  server_idle: () => "还没有请求进来。",
  server_busy: (n) => "已接收 " + n.received + " 个 ECHO，已发出 " + n.sent + " 个响应。"
};

function us(v) {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "–";
  v = Number(v);
  if (Math.abs(v) >= 1000) return (v / 1000).toFixed(2) + " ms";
  return Math.round(v) + " µs";
}
function show(v) {
  if (v === null || v === undefined) return "–";
  return String(v);
}
function split(arr) {
  const values = arr || [];
  const half = Math.floor(values.length / 2);
  return [values.slice(0, half), values.slice(half)];
}
function paintCells(el, values, blankZero) {
  const max = Math.max(1, ...values, 1);
  el.replaceChildren();
  values.forEach((value) => {
    const cell = document.createElement("div");
    cell.className = "cell";
    const bar = document.createElement("i");
    const height = value === 0 ? 0 : Math.max(4, Math.round((value / max) * 46));
    bar.style.height = height + "px";
    const label = document.createElement("b");
    label.textContent = blankZero && value === 0 ? "" : String(value);
    cell.append(bar, label);
    el.append(cell);
  });
}
function line(canvas, seriesList) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 300;
  const h = canvas.clientHeight || 140;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const flat = seriesList.flatMap((item) => item.data);
  if (!flat.length) return;
  const max = Math.max(1, ...flat);
  seriesList.forEach((item) => {
    if (!item.data.length) return;
    g.beginPath();
    item.data.forEach((value, i) => {
      const x = 4 + (i / Math.max(1, item.data.length - 1)) * (w - 8);
      const y = h - 8 - (value / max) * (h - 16);
      if (i === 0) g.moveTo(x, y); else g.lineTo(x, y);
    });
    g.strokeStyle = item.color;
    g.lineWidth = 1.6;
    g.stroke();
  });
}
function bars(canvas, rows) {
  const dpr = window.devicePixelRatio || 1;
  const w = canvas.clientWidth || 220;
  const h = canvas.clientHeight || 140;
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  if (!rows.length) return;
  const max = Math.max(1, ...rows.map((row) => row.n));
  const bw = (w - 8) / rows.length;
  rows.forEach((row, i) => {
    const bh = (row.n / max) * (h - 16);
    g.fillStyle = "#7dd3fc";
    g.fillRect(4 + i * bw + 1, h - 8 - bh, Math.max(1, bw - 2), bh);
  });
}
function render(snap) {
  const role = snap.role || "client";
  document.getElementById("headline").textContent = role === "server" ? "服务端" : "客户端";
  document.getElementById("pill-proto").textContent = snap.proto || "–";
  document.getElementById("pill-endpoint").textContent = snap.endpoint || "–";
  document.getElementById("pill-lambda").textContent = snap.lambda_rps == null ? "λ —" : ("λ " + snap.lambda_rps + "/s");
  document.getElementById("pill-uptime").textContent = (snap.uptime_s || 0).toFixed(1) + " s";
  paintCells(document.getElementById("req-cells"), (snap.req || []).slice(0, 24), false);
  paintCells(document.getElementById("recv-cells"), (snap.recv || []).slice(0, 24), false);
  paintCells(document.getElementById("resp-cells"), (snap.resp || []).slice(0, 24), true);
  paintCells(document.getElementById("ack-cells"), (snap.ack || []).slice(0, 24), false);
  const q = snap.queues || {};
  document.getElementById("q-client-tx").textContent = show(q.client_pending != null ? q.client_pending : q.client_tx);
  document.getElementById("q-read").textContent = show(q.read_pending != null ? q.read_pending : q.server_rx);
  document.getElementById("q-proc").textContent = show(q.processing);
  document.getElementById("q-server-tx").textContent = show(q.response_pending != null ? q.response_pending : q.server_tx);
  document.getElementById("ascii").textContent = (snap.ascii || []).join("\\n");
  document.getElementById("achieved").textContent = show(snap.achieved_rps);
  const lost = snap.lost == null ? "–" : snap.lost;
  const ratio = snap.loss_ratio == null ? "" : " (" + (snap.loss_ratio * 100).toFixed(2) + "%)";
  document.getElementById("loss").textContent = lost + ratio;
  const rtt = snap.rtt_us || {};
  document.getElementById("rtt-p50").textContent = us(rtt.p50);
  document.getElementById("rtt-p99").textContent = us(rtt.p99);
  document.getElementById("jitter").textContent = us(snap.jitter_rfc3550_us);
  const bw = snap.bandwidth_bps || {};
  const bps = (bw.rx || 0) + (bw.tx || 0);
  document.getElementById("bw").textContent = bps ? (bps >= 1e6 ? (bps/1e6).toFixed(2) + " Mb/s" : (bps/1e3).toFixed(1) + " kb/s") : "–";
  const rxb = snap.session_rx_bytes != null ? snap.session_rx_bytes : snap.rx_bytes;
  const txb = snap.session_tx_bytes != null ? snap.session_tx_bytes : snap.tx_bytes;
  document.getElementById("bytes").textContent = (rxb == null && txb == null) ? "–" : ("↓" + show(rxb) + " ↑" + show(txb));
  const lag = snap.timeline_lag_slices;
  document.getElementById("lag").textContent = lag == null ? "–" : (lag + " × " + (snap.slice_ms || 100) + " ms");
  const samples = snap.samples || {};
  line(document.getElementById("rtt-chart"), [{data: samples.rtt_us || [], color: "#7dd3fc"}]);
  line(document.getElementById("queue-chart"), [
    {data: samples.client_tx || [], color: "#5eead4"},
    {data: samples.server_rx || [], color: "#7dd3fc"},
    {data: samples.server_tx || [], color: "#fbbf24"}
  ]);
  bars(document.getElementById("hist-chart"), snap.histogram || []);
  const box = document.getElementById("notes");
  box.replaceChildren();
  (snap.notes || []).forEach((note) => {
    const row = document.createElement("div");
    row.className = "note " + (note.level || "info");
    const fn = TEXT[note.code];
    row.textContent = fn ? fn(note) : note.code;
    box.append(row);
  });
}
function connect() {
  if (!window.EventSource) {
    setInterval(poll, 500);
    poll();
    return;
  }
  const source = new EventSource("/events");
  source.onmessage = (event) => render(JSON.parse(event.data));
  source.onerror = () => {
    source.close();
    setInterval(poll, 500);
    poll();
  };
}
function poll() {
  fetch("/api/snapshot").then((res) => res.json()).then(render).catch(() => {});
}
connect();
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        dash = self.server.dashboard  # type: ignore[attr-defined]
        if path in ("/", "/index.html"):
            body = PAGE.encode("utf-8")
            self._send(200, "text/html; charset=utf-8", body)
            return
        if path == "/api/snapshot":
            body = json.dumps(dash.snapshot(), ensure_ascii=False).encode("utf-8")
            self._send(200, "application/json; charset=utf-8", body)
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
    def __init__(self, host: str, port: int, snapshot) -> None:
        self.snapshot = snapshot
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
