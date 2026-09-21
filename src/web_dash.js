const DEFAULT_THEME = "dark-x-files";
const THEME_KEY = "netpoisson-theme";
const LANG_KEY = "netpoisson-lang";
const PANEL_KEY = "netpoisson-panel";
const LANG_CATALOG = __NP_LANGS__;
const GROUP_ORDER = ["Pride", "Vibe", "Countries"];
const GROUP_MAP = { minor: "Pride", vibe: "Vibe", country: "Countries" };

const I18N = __NP_I18N__;

let THEME_CATALOG = [];
let lang = "en";
let paused = false;
let viewMode = "running"; // running | raw
let panelMode = "capture"; // capture | analyse
let lastSnap = null;
let windowAuto = true;
let lastPostedSlices = 0;
let windowTimer = null;

const cam = { yaw: 30 * Math.PI / 180, pitch: 330 * Math.PI / 180, dist: 2.6, zoom: 2, zoomY: 2, panX: 0, panY: 0 };
function wrapRad(r) {
  const t = Math.PI * 2;
  r = r % t;
  if (r < 0) r += t;
  return r;
}
function wrapCamAngles() {
  cam.yaw = wrapRad(cam.yaw);
  cam.pitch = wrapRad(cam.pitch);
}
let drag = null;
let hoverPoints = [];
let hoverTip = null;
let spinRaf = 0;
let spinLastTs = 0;
let swingCenterYaw = null;
let swingPhase = 0;

function t(key) {
  const pack = I18N[lang] || I18N.en;
  return pack[key] != null ? pack[key] : (I18N.en[key] || key);
}
function noteParams(note) {
  const p = {};
  Object.keys(note || {}).forEach((k) => {
    const v = note[k];
    if (k === "code" || k === "level") return;
    if (k.endsWith("_us") || k === "queue_delay_p50_us") p[k] = us(v);
    else if (k === "lambda_rps" && (v === null || v === undefined)) p[k] = "—";
    else if (v === null || v === undefined) p[k] = "–";
    else p[k] = v;
  });
  if (note.loss_ratio != null) p.loss_pct = (Number(note.loss_ratio) * 100).toFixed(2);
  if (note.ratio != null) p.ratio_pct = Math.round(Number(note.ratio) * 100);
  if (p.threshold == null) p.threshold = "–";
  return p;
}
function formatNote(note) {
  const code = note && note.code;
  if (!code) return "";
  const tmpl = t("note_" + code);
  if (!tmpl || tmpl === "note_" + code) return code;
  const p = noteParams(note);
  return tmpl.replace(/\{(\w+)\}/g, (_, k) => (p[k] != null ? String(p[k]) : "–"));
}
function applyI18n() {
  document.documentElement.lang = (lang || "en").replace(/_/g, "-");
  const titleKey = document.querySelector("[data-i18n-title]");
  if (titleKey) {
    const tv = t(titleKey.getAttribute("data-i18n-title"));
    if (tv) document.title = tv;
  }
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    const val = t(key);
    if (val != null) el.textContent = val;
  });
  setPaused(paused); // refresh pause label
  fillThemeSelect();
  refreshCameraLabels();
  if (lastSnap) {
    const role = lastSnap.role || "client";
    document.getElementById("headline").textContent =
      role === "server" ? t("role_server") : t("role_client");
    renderNotes(lastSnap);
  }
}
function setLang(next) {
  const ids = LANG_CATALOG.map((row) => row.id);
  lang = ids.includes(next) ? next : "en";
  try { localStorage.setItem(LANG_KEY, lang); } catch (e) {}
  const sel = document.getElementById("lang-select");
  if (sel && sel.value !== lang) sel.value = lang;
  applyI18n();
}
function fillLangSelect() {
  const sel = document.getElementById("lang-select");
  if (!sel) return;
  const cur = sel.value || lang;
  sel.replaceChildren();
  LANG_CATALOG.forEach((row) => {
    const opt = document.createElement("option");
    opt.value = row.id;
    opt.textContent = row.label;
    sel.append(opt);
  });
  if (LANG_CATALOG.some((row) => row.id === cur)) sel.value = cur;
  else if (LANG_CATALOG.length) sel.value = LANG_CATALOG[0].id;
}

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
function groupInt(s) {
  return String(s).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}
function fmtBytes(n) {
  if (n === null || n === undefined || !Number.isFinite(Number(n))) return "–";
  const sign = Number(n) < 0 ? "-" : "";
  let v = Math.abs(Number(n));
  const units = ["B", "kB", "MB", "GB", "TB"];
  let u = 0;
  while (v >= 1000 && u < units.length - 1) { v /= 1000; u++; }
  let body;
  if (u === 0) body = groupInt(Math.round(v));
  else if (v >= 100) body = groupInt(Math.round(v));
  else if (v >= 10) body = v.toFixed(1);
  else body = v.toFixed(2);
  if (u > 0 && body.indexOf(".") >= 0) {
    const parts = body.split(".");
    body = groupInt(parts[0]) + "." + parts[1];
  }
  return sign + body + " " + units[u];
}
function cssVar(name, fallback) {
  const v = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return v || fallback;
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
  const fill = cssVar("--ring", "#7dd3fc");
  rows.forEach((row, i) => {
    const bh = (row.n / max) * (h - 16);
    g.fillStyle = fill;
    g.fillRect(4 + i * bw + 1, h - 8 - bh, Math.max(1, bw - 2), bh);
  });
}

function denseFactor() {
  const id = viewMode === "raw" ? "opt-dense-raw" : "opt-dense-running";
  const el = document.getElementById(id);
  const d = el ? Number(el.value) : (viewMode === "raw" ? 1 : 0.5);
  if (viewMode === "raw") {
    return (d === 0.5 || d === 1 || d === 2) ? d : 1;
  }
  return (d === 0.25 || d === 0.5 || d === 1 || d === 2) ? d : 0.5;
}
function scatterRadius(nx, ny, w, h) {
  const cell = Math.min(w / Math.max(1, nx), h / Math.max(1, ny));
  const d = denseFactor();
  // 0.5× → former 1× size; 1× halved; 2× unchanged vs old 2×.
  return Math.max(0.7, Math.min(8, cell * 0.14 / Math.min(1, Math.max(0.25, d))));
}
function fitSlicesBase() {
  // default = how many base-size cells fit the timeline width (vw / board).
  const row = document.querySelector(".board.four .row");
  const cells = document.querySelector(".board.four .cells");
  const stage = document.querySelector(".raw-stage");
  const minCell = 10;
  let avail = window.innerWidth - 80;
  if (viewMode === "raw" && stage && stage.clientWidth > 40) {
    avail = stage.clientWidth - 16;
  } else if (row && row.clientWidth > 40) {
    const meta = row.querySelector(".meta");
    const lab = row.querySelector(".lab");
    avail = row.clientWidth - (meta ? meta.offsetWidth : 0) - (lab ? lab.offsetWidth : 48) - 24;
  } else if (cells && cells.parentElement) {
    avail = cells.parentElement.clientWidth - 56;
  }
  return Math.max(8, Math.min(512, Math.floor(Math.max(80, avail) / minCell)));
}
function fitSlices() {
  const base = fitSlicesBase();
  const d = denseFactor();
  // Runnings: linear. Raw/3D: dense².
  const scale = viewMode === "raw" ? d * d : d;
  return Math.max(8, Math.min(2048, Math.round(base * scale)));
}
async function maybeResizeWindow(snap, force) {
  windowAuto = snap.window_auto !== false;
  const desired = fitSlices();
  const d = denseFactor();
  document.getElementById("pill-window").textContent =
    "window " + (snap.window_slices || desired) + (windowAuto ? " · auto" : "") +
    " · " + d + "×";
  if (!windowAuto) return;
  if (!force && (desired === lastPostedSlices || desired === snap.window_slices)) {
    lastPostedSlices = desired;
    return;
  }
  lastPostedSlices = desired;
  try {
    await fetch("/api/window", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slices: desired, auto: true }),
    });
  } catch (e) {}
}
function syncConfigInputs(snap) {
  const lam = document.getElementById("cfg-lambda");
  const buck = document.getElementById("cfg-bucket");
  const st = document.getElementById("cfg-status");
  if (!lam || !buck || !st) return;
  const isServer = (snap.role || "client") === "server";
  const hasLam = snap.lambda_rps != null;
  const hasStatus = snap.status_interval_ms != null;
  lam.disabled = isServer || !hasLam;
  st.disabled = isServer || !hasStatus;
  if (document.activeElement !== lam) {
    lam.value = hasLam ? snap.lambda_rps : "";
    if (!hasLam) lam.placeholder = "—";
  }
  if (document.activeElement !== buck && snap.bucket_ms != null) buck.value = snap.bucket_ms;
  if (document.activeElement !== st) {
    st.value = hasStatus ? snap.status_interval_ms : "";
    if (!hasStatus) st.placeholder = "—";
  }
}
let configTimer = null;
function scheduleConfigPost() {
  clearTimeout(configTimer);
  configTimer = setTimeout(postConfig, 280);
}
async function postConfig() {
  const lam = document.getElementById("cfg-lambda");
  const buck = document.getElementById("cfg-bucket");
  const st = document.getElementById("cfg-status");
  const body = {};
  if (lam && !lam.disabled && lam.value !== "") {
    const v = Number(lam.value);
    if (Number.isFinite(v) && v > 0) body.lambda_rps = v;
  }
  if (buck && buck.value !== "") {
    const v = parseInt(buck.value, 10);
    if (Number.isFinite(v) && v >= 1) body.bucket_ms = v;
  }
  if (st && !st.disabled && st.value !== "") {
    const v = parseInt(st.value, 10);
    if (Number.isFinite(v) && v >= 1) body.status_interval_ms = v;
  }
  if (!Object.keys(body).length) return;
  try {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  } catch (e) {}
}
function scheduleWindowFit(force) {
  clearTimeout(windowTimer);
  windowTimer = setTimeout(() => {
    if (lastSnap) maybeResizeWindow(lastSnap, force);
  }, 120);
}

function applyTheme(id) {
  const entry = THEME_CATALOG.find((theme) => theme.id === id) || { id: DEFAULT_THEME, type: "dark" };
  document.documentElement.setAttribute("data-theme", entry.id);
  document.documentElement.classList.toggle("dark", entry.type === "dark");
  try { localStorage.setItem(THEME_KEY, entry.id); } catch (e) {}
  const sel = document.getElementById("theme-select");
  if (sel && sel.value !== entry.id) sel.value = entry.id;
}
function themeGroupName(row) {
  return GROUP_MAP[row.group] || (row.group ? (row.group.charAt(0).toUpperCase() + row.group.slice(1)) : "Vibe");
}
function fillThemeSelect() {
  const sel = document.getElementById("theme-select");
  if (!sel) return;
  const current = sel.value;
  const groups = new Map();
  GROUP_ORDER.forEach((g) => groups.set(g, []));
  THEME_CATALOG.forEach((theme) => {
    const key = themeGroupName(theme);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(theme);
  });
  sel.replaceChildren();
  for (const [group, items] of groups) {
    if (!items.length) continue;
    items.sort((a, b) => {
      const ta = a.type === "dark" ? 1 : 0;
      const tb = b.type === "dark" ? 1 : 0;
      if (ta !== tb) return ta - tb;
      return (a.family || "").localeCompare(b.family || "") || a.label.localeCompare(b.label);
    });
    const og = document.createElement("optgroup");
    og.label = t("group_" + group) || group;
    items.forEach((theme) => {
      const opt = document.createElement("option");
      opt.value = theme.id;
      opt.textContent = theme.label + (theme.type === "dark" ? " · dark" : " · light");
      og.append(opt);
    });
    sel.append(og);
  }
  if (current && THEME_CATALOG.some((x) => x.id === current)) sel.value = current;
}
async function initThemes() {
  const sel = document.getElementById("theme-select");
  try {
    THEME_CATALOG = await fetch("/themes/catalog.json").then((r) => r.json());
  } catch (e) {
    THEME_CATALOG = [{ id: DEFAULT_THEME, label: "Dark X-Files", type: "dark", group: "vibe", family: "X-Files" }];
  }
  fillThemeSelect();
  let saved = DEFAULT_THEME;
  try { saved = localStorage.getItem(THEME_KEY) || DEFAULT_THEME; } catch (e) {}
  if (!THEME_CATALOG.some((theme) => theme.id === saved)) saved = DEFAULT_THEME;
  applyTheme(saved);
  sel.addEventListener("change", () => applyTheme(sel.value));
}
function initLang() {
  fillLangSelect();
  let saved = "en";
  try { saved = localStorage.getItem(LANG_KEY) || "en"; } catch (e) {}
  const ids = LANG_CATALOG.map((row) => row.id);
  if (!ids.includes(saved)) saved = "en";
  document.getElementById("lang-select").addEventListener("change", (e) => {
    setLang(e.target.value);
    try { localStorage.setItem(LANG_KEY, e.target.value); } catch (err) {}
  });
  setLang(saved);
}
function setPaused(on) {
  paused = on;
  document.body.classList.toggle("is-paused", on);
  const btn = document.getElementById("btn-pause");
  btn.textContent = on ? t("resume") : t("pause");
  btn.setAttribute("aria-pressed", on ? "true" : "false");
  btn.classList.toggle("active", on);
}
function setPanelMode(mode) {
  panelMode = mode === "analyse" ? "analyse" : "capture";
  try { localStorage.setItem(PANEL_KEY, panelMode); } catch (e) {}
  document.body.classList.toggle("panel-capture", panelMode === "capture");
  document.body.classList.toggle("panel-analyse", panelMode === "analyse");
  const cap = document.getElementById("panel-capture");
  const ana = document.getElementById("panel-analyse");
  if (cap) cap.classList.toggle("hidden", panelMode !== "capture");
  if (ana) ana.classList.toggle("hidden", panelMode !== "analyse");
  const bc = document.getElementById("btn-capture");
  const ba = document.getElementById("btn-analyse");
  if (bc) {
    bc.classList.toggle("active", panelMode === "capture");
    bc.setAttribute("aria-pressed", panelMode === "capture" ? "true" : "false");
  }
  if (ba) {
    ba.classList.toggle("active", panelMode === "analyse");
    ba.setAttribute("aria-pressed", panelMode === "analyse" ? "true" : "false");
  }
  if (panelMode === "capture" && viewMode === "raw" && lastSnap) {
    requestAnimationFrame(() => drawRaw3d(lastSnap));
  }
}
function initPanelMode() {
  let saved = "capture";
  try { saved = localStorage.getItem(PANEL_KEY) || "capture"; } catch (e) {}
  setPanelMode(saved === "analyse" ? "analyse" : "capture");
  document.getElementById("btn-capture").addEventListener("click", () => setPanelMode("capture"));
  document.getElementById("btn-analyse").addEventListener("click", () => setPanelMode("analyse"));
}
function renderNotes(snap) {
  const box = document.getElementById("notes");
  box.replaceChildren();
  (snap.notes || []).forEach((note) => {
    const row = document.createElement("div");
    row.className = "note " + (note.level || "info");
    row.textContent = formatNote(note);
    box.append(row);
  });
}
function setViewMode(mode) {
  viewMode = mode;
  document.getElementById("sec-running").classList.toggle("hidden", mode !== "running");
  document.getElementById("sec-raw").classList.toggle("hidden", mode !== "raw");
  document.getElementById("raw-opts-row").classList.toggle("hidden", mode !== "raw");
  document.getElementById("dense-running-wrap").classList.toggle("hidden", mode !== "running");
  document.getElementById("dense-raw-wrap").classList.toggle("hidden", mode !== "raw");
  document.getElementById("btn-running").classList.toggle("active", mode === "running");
  document.getElementById("btn-raw").classList.toggle("active", mode === "raw");
  document.getElementById("btn-running").setAttribute("aria-pressed", mode === "running" ? "true" : "false");
  document.getElementById("btn-raw").setAttribute("aria-pressed", mode === "raw" ? "true" : "false");
  lastPostedSlices = 0;
  scheduleWindowFit(true);
  if (mode === "raw") {
    syncStyleDropdown();
    fitOnceCurrentView();
    if (lastSnap) drawRaw3d(lastSnap);
  }
}
const VIEW_PRESETS = {
  front: { yaw: 0.0, pitch: 0.08, zoom: 1 },
  // Look from +Y (latency +Inf) toward 0 — not from below.
  top: { yaw: 0.0, pitch: -Math.PI / 2, zoom: 1 },
  free: { yaw: 30 * Math.PI / 180, pitch: 330 * Math.PI / 180, zoom: 2 },
};
function applyViewPreset(name) {
  const p = VIEW_PRESETS[name];
  if (!p) return;
  cam.yaw = wrapRad(p.yaw);
  cam.pitch = wrapRad(p.pitch);
  cam.panX = 0;
  cam.panY = 0;
  cam.zoom = p.zoom != null ? p.zoom : 1;
  cam.zoomY = cam.zoom;
  cam.dist = 2.6;
  resetSwingCenter();
}
const DRAW_STYLES = ["scatter", "grid", "surface"];
const COLOR_MAPS = ["turbo", "viridis", "magma", "inferno", "gray", "spectrum"];
function syncStyleDropdown() {
  const camSel = document.getElementById("raw-camera");
  const styleSel = document.getElementById("raw-style");
  const label = document.getElementById("style-label");
  if (!camSel || !styleSel || !label) return;
  const spectrum = camSel.value === "spectrum";
  const prev = styleSel.value;
  label.setAttribute("data-i18n", spectrum ? "color_theme" : "style");
  label.textContent = t(spectrum ? "color_theme" : "style");
  const opts = spectrum ? COLOR_MAPS : DRAW_STYLES;
  const prefix = spectrum ? "cmap_" : "style_";
  styleSel.replaceChildren();
  opts.forEach((id) => {
    const opt = document.createElement("option");
    opt.value = id;
    opt.textContent = t(prefix + id);
    styleSel.append(opt);
  });
  if (opts.includes(prev)) styleSel.value = prev;
  else styleSel.value = spectrum ? "turbo" : "scatter";
}
function refreshCameraLabels() {
  const sel = document.getElementById("raw-camera");
  if (!sel) return;
  const cur = sel.value;
  const map = [
    ["front", "view_front"],
    ["top", "view_top"],
    ["spectrum", "view_spectrum"],
    ["free", "view_free"],
  ];
  [...sel.options].forEach((opt, i) => {
    if (map[i]) opt.textContent = t(map[i][1]);
  });
  sel.value = cur;
  syncStyleDropdown();
}

function project3dCam(x, y, z, ignorePan) {
  const perspective = document.getElementById("opt-perspective").checked;
  const cy = Math.cos(cam.yaw), sy = Math.sin(cam.yaw);
  const cp = Math.cos(cam.pitch), sp = Math.sin(cam.pitch);
  let X = x * cy - z * sy;
  let Z = x * sy + z * cy;
  let Y = y * cp - Z * sp;
  Z = y * sp + Z * cp;
  const panX = ignorePan ? 0 : cam.panX;
  const panY = ignorePan ? 0 : cam.panY;
  if (perspective) {
    Z += cam.dist;
    if (Z < 0.2) Z = 0.2;
    const f = 1 / Z;
    return { x: (X + panX) * f, y: (Y + panY) * f, z: Z };
  }
  return { x: X + panX, y: Y + panY, z: Z + cam.dist };
}
function camAABBOriented(xSpan, ySpan) {
  // AABB of the oriented unit volume (rotation only; pan ignored).
  const corners = [];
  const ys = [0, 1.2];
  for (const xv of [-xSpan, xSpan]) {
    for (const yv of ys) {
      for (const zv of [-ySpan, ySpan]) corners.push(project3dCam(xv, yv, zv, true));
    }
  }
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  corners.forEach((p) => {
    if (p.x < minX) minX = p.x; if (p.x > maxX) maxX = p.x;
    if (p.y < minY) minY = p.y; if (p.y > maxY) maxY = p.y;
  });
  return { minX, maxX, minY, maxY, bw: Math.max(1e-6, maxX - minX), bh: Math.max(1e-6, maxY - minY) };
}
function fitPadPx() {
  // ~1em canvas padding (enough for axis labels, no huge letterbox).
  const root = getComputedStyle(document.documentElement).fontSize;
  const em = parseFloat(root) || 16;
  return Math.max(12, Math.round(em));
}
function keepAspectOn() {
  const el = document.getElementById("opt-keep-aspect");
  return !!(el && el.checked);
}
function fitScaleForBox(w, h, box) {
  const pad = fitPadPx();
  const availW = Math.max(1, w - 2 * pad);
  const availH = Math.max(1, h - 2 * pad);
  if (keepAspectOn()) {
    const scale = Math.min(availW / box.bw, availH / box.bh);
    return { scaleX: scale, scaleY: scale, pad };
  }
  // Default: fill both axes independently (no large empty bands).
  return { scaleX: availW / box.bw, scaleY: availH / box.bh, pad };
}
function classicBaseScale(w, h) {
  const scale = Math.min(w, h) * 0.42;
  return { scaleX: scale, scaleY: scale };
}
function bakeFitIntoCam(w, h, xSpan, ySpan, mode) {
  // Capture current Fit framing into zoom/zoomY/pan so unchecking Fit does not jump.
  const box = camAABBOriented(xSpan, ySpan);
  const fitted = fitScaleForBox(w, h, box);
  const scaleX = fitted.scaleX * cam.zoom;
  const scaleY = fitted.scaleY * cam.zoom;
  const pad = fitted.pad;
  const base = classicBaseScale(w, h);
  cam.zoom = Math.max(0.35, Math.min(4, scaleX / Math.max(1e-9, base.scaleX)));
  cam.zoomY = Math.max(0.35, Math.min(4, scaleY / Math.max(1e-9, base.scaleY)));
  // Match edge-padded Fit with classic origin at canvas center.
  cam.panX = (pad - box.minX * scaleX - w * 0.5) / scaleX;
  cam.panY = (h * 0.5 - pad) / scaleY - box.maxY;
}
function fitOnceFromLayout(w, h, xSpan, ySpan, mode) {
  cam.panX = 0; cam.panY = 0; cam.zoom = 1; cam.zoomY = 1;
  bakeFitIntoCam(w, h, xSpan, ySpan, mode);
}
function rawLayoutSize() {
  const stage = document.querySelector(".raw-stage");
  const canvas = document.getElementById("raw3d") || document.getElementById("raw3d-gl");
  const w = (stage && stage.clientWidth) || 640;
  const h = Math.max(240, (canvas && canvas.clientHeight) || 320);
  const nx = Math.max(8, (lastSnap && lastSnap.window_slices) || 32);
  const padGuess = 28;
  const cell = Math.max(1, (w - 2 * padGuess) / nx);
  const ny = Math.max(8, Math.min(256, Math.floor(Math.max(1, h - 2 * padGuess - 18) / cell)));
  const maxD = Math.max(nx, ny, 1);
  return { w, h, xSpan: nx / maxD, ySpan: ny / maxD };
}
function fitOnceCurrentView() {
  if (!lastSnap || viewMode !== "raw") return;
  const mode = document.getElementById("raw-camera").value;
  if (mode === "spectrum") return;
  const { w, h, xSpan, ySpan } = rawLayoutSize();
  fitOnceFromLayout(w, h, xSpan, ySpan, mode);
}
function makeProjector(w, h, xSpan, ySpan, mode) {
  const fit = document.getElementById("opt-fit") && document.getElementById("opt-fit").checked;
  if (fit) {
    // Fit on: edge-pad ~1em and fill canvas (anisotropic unless Keep aspect).
    const box = camAABBOriented(xSpan, ySpan);
    const fitted = fitScaleForBox(w, h, box);
    const scaleX = fitted.scaleX * cam.zoom;
    const scaleY = fitted.scaleY * cam.zoom;
    const pad = fitted.pad;
    const ox = pad - box.minX * scaleX;
    const oy = pad + box.maxY * scaleY;
    return function project3d(x, y, z) {
      const p = project3dCam(x, y, z, true);
      return { x: ox + p.x * scaleX, y: oy - p.y * scaleY, z: p.z };
    };
  }
  // Fit off: classic framing (pan / zoom / zoomY). Origin at canvas center.
  const base = classicBaseScale(w, h);
  const scaleX = cam.zoom * base.scaleX;
  const scaleY = (cam.zoomY != null ? cam.zoomY : cam.zoom) * base.scaleY;
  const ox = w * 0.5;
  const oy = h * 0.5;
  return function project3d(x, y, z) {
    const p = project3dCam(x, y, z);
    return { x: ox + p.x * scaleX, y: oy - p.y * scaleY, z: p.z };
  };
}
function zNorm(z, zMax, logscale) {
  if (zMax <= 0) return 0;
  z = Math.max(0, Number(z) || 0);
  if (logscale) {
    // log1p: 0→0, zMax→1. Spreads low latencies; avoids a dead band at the floor.
    return Math.log1p(z) / Math.log1p(zMax);
  }
  return z / zMax;
}
function updateAngleHud() {
  const el = document.getElementById("raw-angle");
  if (!el) return;
  // Display wrapped degrees without mutating cam while Swing is running.
  const twopi = Math.PI * 2;
  const yawDeg = ((cam.yaw % twopi) + twopi) % twopi * 180 / Math.PI;
  const pitchDeg = ((cam.pitch % twopi) + twopi) % twopi * 180 / Math.PI;
  el.textContent = "yaw " + yawDeg.toFixed(1) + "° · pitch " + pitchDeg.toFixed(1) + "° · zoom " + cam.zoom.toFixed(2);
}
function showRawTip(ev, info, canvas) {
  const tip = document.getElementById("raw-tip");
  if (!tip || !info) { if (tip) tip.classList.add("hidden"); return; }
  const stage = document.querySelector(".raw-stage");
  const rect = stage.getBoundingClientRect();
  tip.textContent = info;
  tip.classList.remove("hidden");
  let left = ev.clientX - rect.left + 12;
  let top = ev.clientY - rect.top + 12;
  tip.style.left = left + "px";
  tip.style.top = top + "px";
  const tw = tip.offsetWidth, th = tip.offsetHeight;
  if (left + tw > rect.width - 4) tip.style.left = Math.max(4, rect.width - tw - 4) + "px";
  if (top + th > rect.height - 4) tip.style.top = Math.max(4, rect.height - th - 4) + "px";
}
function hideRawTip() {
  const tip = document.getElementById("raw-tip");
  if (tip) tip.classList.add("hidden");
}
function hitHoverPoint(canvas, clientX, clientY) {
  if (!hoverPoints.length) return null;
  const rect = canvas.getBoundingClientRect();
  // Drawing uses CSS pixels via setTransform(dpr).
  const sx = clientX - rect.left;
  const sy = clientY - rect.top;
  let best = null, bestD = 10;
  hoverPoints.forEach((p) => {
    const d = Math.hypot(p.x - sx, p.y - sy);
    if (d < bestD) { bestD = d; best = p; }
  });
  return best;
}
function xFade(xi, nx) {
  // X+ fades toward 30% opacity.
  const t = Math.max(0, Math.min(1, xi / Math.max(1, nx - 1)));
  return 1 - 0.7 * t;
}
function withAlpha(color, a) {
  const m = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(color);
  if (m) return "rgba(" + m[1] + "," + m[2] + "," + m[3] + "," + a + ")";
  const m2 = /hsl\(([^)]+)\)/.exec(color);
  if (m2) return "hsl(" + m2[1] + " / " + a + ")";
  return color;
}
