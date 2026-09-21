function heatColor(t, spectrum) {
  return colorMapRGB(spectrum ? "spectrum" : "turbo", t);
}
function colorMapRGB(name, t) {
  t = Math.max(0, Math.min(1, t));
  if (name === "gray") {
    const g = Math.round(30 + t * 210);
    return "rgb(" + g + "," + g + "," + g + ")";
  }
  if (name === "spectrum") {
    const h = (1 - t) * 270;
    return "hsl(" + h + " 85% 55%)";
  }
  // piecewise stops for turbo / viridis / magma / inferno
  const stops = {
    turbo: [[0.0,[48,18,59]],[0.25,[33,144,140]],[0.5,[253,231,37]],[0.75,[244,109,67]],[1.0,[122,4,3]]],
    viridis: [[0.0,[68,1,84]],[0.33,[59,82,139]],[0.66,[33,145,140]],[1.0,[253,231,37]]],
    magma: [[0.0,[0,0,4]],[0.33,[74,12,107]],[0.66,[183,55,121]],[1.0,[252,253,191]]],
    inferno: [[0.0,[0,0,4]],[0.33,[87,16,110]],[0.66,[188,55,84]],[1.0,[252,255,164]]],
  }[name] || null;
  if (!stops) {
    const h = 200 - t * 160;
    const l = 28 + t * 32;
    return "hsl(" + h + " 70% " + l + "%)";
  }
  let i = 0;
  while (i < stops.length - 2 && t > stops[i + 1][0]) i++;
  const a = stops[i], b = stops[i + 1];
  const u = (t - a[0]) / Math.max(1e-9, b[0] - a[0]);
  const rgb = a[1].map((c, k) => Math.round(c + (b[1][k] - c) * u));
  return "rgb(" + rgb[0] + "," + rgb[1] + "," + rgb[2] + ")";
}
function colorMapByte(name, t) {
  const s = colorMapRGB(name, t);
  const m = /rgb\((\d+),\s*(\d+),\s*(\d+)\)/.exec(s);
  if (m) return [Number(m[1]), Number(m[2]), Number(m[3])];
  // hsl fallback → approximate via canvas-less parse: treat as turbo
  const m2 = /hsl\(([\d.]+)/.exec(s);
  const h = m2 ? Number(m2[1]) / 360 : 0.5;
  const rgb = hslToRgb(h, 0.85, 0.55);
  return rgb.map((x) => Math.round(x * 255));
}
function hslToRgb(h, s, l) {
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hk = ((x) => {
    if (x < 0) x += 1; if (x > 1) x -= 1;
    if (x < 1/6) return p + (q - p) * 6 * x;
    if (x < 1/2) return q;
    if (x < 2/3) return p + (q - p) * (2/3 - x) * 6;
    return p;
  });
  return [hk(h + 1/3), hk(h), hk(h - 1/3)];
}
function binGrid(events, nx, ny, bucketMs) {
  const grid = Array.from({ length: nx }, () => Array(ny).fill(null));
  const counts = Array.from({ length: nx }, () => Array(ny).fill(0));
  events.forEach((ev) => {
    const ix = Math.max(0, Math.min(nx - 1, Math.floor(ev.x)));
    const iy = Math.max(0, Math.min(ny - 1, Math.floor((ev.y / Math.max(1, bucketMs)) * ny)));
    counts[ix][iy] += 1;
    grid[ix][iy] = grid[ix][iy] == null ? ev.z : (grid[ix][iy] + ev.z);
  });
  for (let i = 0; i < nx; i++) {
    for (let j = 0; j < ny; j++) {
      if (counts[i][j] > 1) grid[i][j] /= counts[i][j];
      if (grid[i][j] == null) grid[i][j] = 0;
    }
  }
  return grid;
}
function strokeSmooth(g, pts) {
  if (pts.length < 2) return;
  g.beginPath();
  g.moveTo(pts[0].x, pts[0].y);
  for (let i = 1; i < pts.length - 1; i++) {
    const xc = (pts[i].x + pts[i + 1].x) / 2;
    const yc = (pts[i].y + pts[i + 1].y) / 2;
    g.quadraticCurveTo(pts[i].x, pts[i].y, xc, yc);
  }
  const last = pts[pts.length - 1];
  g.lineTo(last.x, last.y);
  g.stroke();
}
function slotLayout(nx, w, h, pad) {
  const left = pad;
  const right = w - pad;
  const top = pad;
  const bottom = h - pad - 18;
  const span = Math.max(1, right - left);
  const slotW = span / Math.max(1, nx);
  return { left, right, top, bottom, span, slotW, cell: slotW };
}
function squareLayout(nx, ny, w, h, pad) {
  const availW = Math.max(1, w - 2 * pad);
  const availH = Math.max(1, h - 2 * pad - 18);
  const cell = Math.max(1, Math.floor(Math.min(availW / Math.max(1, nx), availH / Math.max(1, ny))));
  const gridW = cell * nx;
  const gridH = cell * ny;
  const left = pad + Math.floor((availW - gridW) / 2);
  const top = pad + Math.floor((availH - gridH) / 2);
  return { left, top, right: left + gridW, bottom: top + gridH, cell, slotW: cell, gridW, gridH, ny };
}
function mapFront(ev, nx, zMax, layout) {
  const x = layout.left + (ev.x + 0.5) * layout.slotW;
  const y = layout.bottom - (ev.z / zMax) * (layout.bottom - layout.top);
  return { x, y, z: ev.z, slot: ev.x };
}
function mapTop(ev, nx, bucketMs, layout) {
  const x = layout.left + (ev.x + 0.5) * layout.slotW;
  const frac = Math.max(0, Math.min(1, ev.y / Math.max(1, bucketMs)));
  const y = layout.bottom - frac * (layout.bottom - layout.top);
  return { x, y, z: ev.z, slot: ev.x };
}
function nextPow2(n) {
  let p = 1;
  while (p < n) p <<= 1;
  return p;
}
function fft1d(re, im) {
  const n = re.length;
  for (let i = 1, j = 0; i < n; i++) {
    let bit = n >> 1;
    for (; j & bit; bit >>= 1) j ^= bit;
    j ^= bit;
    if (i < j) {
      let tr = re[i]; re[i] = re[j]; re[j] = tr;
      let ti = im[i]; im[i] = im[j]; im[j] = ti;
    }
  }
  for (let len = 2; len <= n; len <<= 1) {
    const ang = -2 * Math.PI / len;
    const wlenRe = Math.cos(ang), wlenIm = Math.sin(ang);
    for (let i = 0; i < n; i += len) {
      let wRe = 1, wIm = 0;
      for (let j = 0; j < len / 2; j++) {
        const uRe = re[i + j], uIm = im[i + j];
        const vRe = re[i + j + len / 2] * wRe - im[i + j + len / 2] * wIm;
        const vIm = re[i + j + len / 2] * wIm + im[i + j + len / 2] * wRe;
        re[i + j] = uRe + vRe; im[i + j] = uIm + vIm;
        re[i + j + len / 2] = uRe - vRe; im[i + j + len / 2] = uIm - vIm;
        const nRe = wRe * wlenRe - wIm * wlenIm;
        wIm = wRe * wlenIm + wIm * wlenRe;
        wRe = nRe;
      }
    }
  }
}
function spectrogramSTFT(grid, nx, ny, win) {
  // Sliding buckets along time (X). Each column = FFT of Y profile averaged in [t-win/2, t+win/2].
  const nfft = nextPow2(Math.max(ny, 16));
  const nFreq = Math.floor(nfft / 2);
  const mag = new Float32Array(nx * nFreq);
  const half = Math.max(0, Math.floor(win / 2));
  let max = 1e-9;
  for (let t = 0; t < nx; t++) {
    const re = new Float32Array(nfft);
    const im = new Float32Array(nfft);
    for (let y = 0; y < ny; y++) {
      let sum = 0, cnt = 0;
      for (let dt = -half; dt <= half; dt++) {
        const x = t + dt;
        if (x < 0 || x >= nx) continue;
        const v = grid[x][y] || 0;
        if (v) { sum += v; cnt++; }
      }
      const avg = cnt ? sum / cnt : 0;
      // Hann taper across the Y profile
      const hann = 0.5 - 0.5 * Math.cos((2 * Math.PI * y) / Math.max(1, ny - 1));
      re[y] = avg * hann;
    }
    fft1d(re, im);
    for (let f = 0; f < nFreq; f++) {
      const v = Math.hypot(re[f], im[f]);
      mag[t * nFreq + f] = v;
      if (v > max) max = v;
    }
  }
  for (let i = 0; i < mag.length; i++) mag[i] = Math.log1p(mag[i]) / Math.log1p(max);
  return { mag, nx, nFreq };
}

let glState = null;
function ensureSpectrumGL() {
  const canvas = document.getElementById("raw3d-gl");
  if (!canvas) return null;
  if (glState && glState.canvas === canvas && glState.rgb) return glState;
  const gl = canvas.getContext("webgl", { antialias: false, preserveDrawingBuffer: true });
  if (!gl) return null;
  const vsSrc = `
    attribute vec2 a_pos;
    varying vec2 v_uv;
    void main() {
      v_uv = a_pos * 0.5 + 0.5;
      gl_Position = vec4(a_pos, 0.0, 1.0);
    }
  `;
  const fsSrc = `
    precision mediump float;
    varying vec2 v_uv;
    uniform sampler2D u_tex;
    void main() {
      gl_FragColor = texture2D(u_tex, v_uv);
    }
  `;
  function compile(type, src) {
    const sh = gl.createShader(type);
    gl.shaderSource(sh, src);
    gl.compileShader(sh);
    if (!gl.getShaderParameter(sh, gl.COMPILE_STATUS)) {
      console.warn(gl.getShaderInfoLog(sh));
      return null;
    }
    return sh;
  }
  const vs = compile(gl.VERTEX_SHADER, vsSrc);
  const fs = compile(gl.FRAGMENT_SHADER, fsSrc);
  if (!vs || !fs) return null;
  const prog = gl.createProgram();
  gl.attachShader(prog, vs);
  gl.attachShader(prog, fs);
  gl.linkProgram(prog);
  if (!gl.getProgramParameter(prog, gl.LINK_STATUS)) {
    console.warn(gl.getProgramInfoLog(prog));
    return null;
  }
  const buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([
    -1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1,
  ]), gl.STATIC_DRAW);
  const tex = gl.createTexture();
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
  gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
  glState = {
    canvas, gl, prog, buf, tex, rgb: true,
    aPos: gl.getAttribLocation(prog, "a_pos"),
    uTex: gl.getUniformLocation(prog, "u_tex"),
  };
  return glState;
}
function drawSpectrumGL(spec, cmap, cssW, cssH) {
  const st = ensureSpectrumGL();
  const canvas2d = document.getElementById("raw3d");
  const canvasGl = document.getElementById("raw3d-gl");
  if (!st || !canvasGl) {
    if (canvas2d) canvas2d.classList.remove("hidden");
    if (canvasGl) canvasGl.classList.add("hidden");
    return false;
  }
  canvas2d.classList.add("hidden");
  canvasGl.classList.remove("hidden");
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(cssW * dpr);
  const h = Math.round(cssH * dpr);
  if (canvasGl.width !== w || canvasGl.height !== h) {
    canvasGl.width = w;
    canvasGl.height = h;
  }
  const { gl, prog, buf, tex, aPos, uTex } = st;
  const { mag, nx, nFreq } = spec;
  const pixels = new Uint8Array(nx * nFreq * 3);
  for (let t = 0; t < nx; t++) {
    for (let f = 0; f < nFreq; f++) {
      // texture row 0 at bottom → put low freq at bottom
      const src = mag[t * nFreq + f];
      const rgb = colorMapByte(cmap, src);
      const dst = ((nFreq - 1 - f) * nx + t) * 3;
      pixels[dst] = rgb[0];
      pixels[dst + 1] = rgb[1];
      pixels[dst + 2] = rgb[2];
    }
  }
  gl.viewport(0, 0, w, h);
  gl.useProgram(prog);
  gl.bindTexture(gl.TEXTURE_2D, tex);
  gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
  gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGB, nx, nFreq, 0, gl.RGB, gl.UNSIGNED_BYTE, pixels);
  gl.uniform1i(uTex, 0);
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.enableVertexAttribArray(aPos);
  gl.vertexAttribPointer(aPos, 2, gl.FLOAT, false, 0, 0);
  gl.drawArrays(gl.TRIANGLES, 0, 6);
  return true;
}
function showRaw2d() {
  const canvas2d = document.getElementById("raw3d");
  const canvasGl = document.getElementById("raw3d-gl");
  if (canvas2d) canvas2d.classList.remove("hidden");
  if (canvasGl) canvasGl.classList.add("hidden");
}
function worldExtents(nx, ny) {
  // Follow canvas aspect via slot counts (square cells) — not a unit cube.
  const maxD = Math.max(nx, ny, 1);
  return { xSpan: nx / maxD, ySpan: ny / maxD };
}
function drawRaw3d(snap) {
  const canvas = document.getElementById("raw3d");
  const canvasGl = document.getElementById("raw3d-gl");
  if ((!canvas && !canvasGl) || viewMode !== "raw") return;
  const stage = document.querySelector(".raw-stage") || canvas || canvasGl;
  const dpr = window.devicePixelRatio || 1;
  const w = stage.clientWidth || 640;
  // In capture layout the canvas is position:absolute; size comes from .raw-stage.
  const h = Math.max(160, stage.clientHeight || (canvas && canvas.clientHeight) || (canvasGl && canvasGl.clientHeight) || Math.min(520, window.innerHeight * 0.55));
  const events = snap.raw_events || [];
  const camera = document.getElementById("raw-camera").value;
  const style = document.getElementById("raw-style").value;
  const smooth = document.getElementById("opt-smooth").checked;
  const logscale = document.getElementById("opt-logscale").checked;
  const nx = Math.max(8, snap.window_slices || 32);
  const padGuess = 28;
  const cell = Math.max(1, (w - 2 * padGuess) / nx);
  const ny = Math.max(8, Math.min(256, Math.floor(Math.max(1, h - 2 * padGuess - 18) / cell)));
  const bucketMs = snap.bucket_ms || snap.slice_ms || 100;
  let zMax = 1;
  events.forEach((e) => { if (e.z > zMax) zMax = e.z; });
  const muted = cssVar("--muted-foreground", "#888");
  const border = cssVar("--border", "#666");
  const ring = cssVar("--ring", "#7dd3fc");
  const grid = binGrid(events, nx, ny, bucketMs);
  const dotR = scatterRadius(nx, ny, w, h);

  updateAngleHud();
  if (camera === "spectrum") {
    const win = Math.max(3, Math.round(8 / Math.max(0.5, denseFactor())));
    const spec = spectrogramSTFT(grid, nx, ny, win);
    const cmap = COLOR_MAPS.includes(style) ? style : "turbo";
    if (drawSpectrumGL(spec, cmap, w, h)) return;
    showRaw2d();
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    const g = canvas.getContext("2d");
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    g.clearRect(0, 0, w, h);
    const pad = 28;
    const left = pad, right = w - pad, top = pad, bottom = h - pad - 18;
    const cw = (right - left) / spec.nx;
    const ch = (bottom - top) / spec.nFreq;
    for (let t = 0; t < spec.nx; t++) {
      for (let f = 0; f < spec.nFreq; f++) {
        const v = spec.mag[t * spec.nFreq + f];
        g.globalAlpha = xFade(t, spec.nx);
        g.fillStyle = colorMapRGB(cmap, v);
        g.fillRect(left + t * cw, bottom - (f + 1) * ch, Math.max(1, cw + 0.5), Math.max(1, ch + 0.5));
      }
    }
    g.globalAlpha = 1;
    g.fillStyle = muted;
    g.font = "10px sans-serif";
    g.fillText(t("axis_x"), Math.min(right - 48, w - 56), bottom + 14);
    g.fillText(t("axis_freq"), left, Math.max(12, top - 4));
    return;
  }

  showRaw2d();
  canvas.width = Math.round(w * dpr);
  canvas.height = Math.round(h * dpr);
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);

  const { xSpan, ySpan } = worldExtents(nx, ny);
  // X restored (original). Y+: toward user.
  const toWorld = (xi, yi, z) => ({
    x: (xi / Math.max(1, nx - 1) - 0.5) * 2 * xSpan,
    y: zNorm(z, zMax, logscale) * 1.2,
    z: -((yi / Math.max(1, ny - 1) - 0.5) * 2 * ySpan),
  });
  const project3d = makeProjector(w, h, xSpan, ySpan, camera);
  hoverPoints = [];
  updateAngleHud();

  // Axes: always draw Z as a segment. In true top-down it foreshortens to a point.
  const o = project3d(-xSpan, 0, -ySpan);
  const ax = project3d(xSpan * 1.08, 0, -ySpan);
  const ay0 = project3d(-xSpan, 0, -ySpan);
  const ay1 = project3d(-xSpan, 1.2, -ySpan);
  const az = project3d(-xSpan, 0, ySpan * 1.08);
  g.strokeStyle = border;
  g.fillStyle = muted;
  g.globalAlpha = 1;
  g.font = "10px sans-serif";
  g.beginPath(); g.moveTo(o.x, o.y); g.lineTo(ax.x, ax.y); g.stroke();
  g.beginPath(); g.moveTo(o.x, o.y); g.lineTo(az.x, az.y); g.stroke();
  g.beginPath(); g.moveTo(ay0.x, ay0.y); g.lineTo(ay1.x, ay1.y); g.stroke();
  g.fillText(t("axis_x"), ax.x - 10, ax.y + 14);
  g.fillText(t("axis_y_offset"), az.x + 4, az.y);
  g.fillText(t("axis_z_latency"), ay1.x - 24, ay1.y - 4);

  const drawStyle = DRAW_STYLES.includes(style) ? style : "scatter";

  if (drawStyle === "scatter") {
    const pts = events.map((ev) => {
      const yy = Math.min(ny - 1, (ev.y / bucketMs) * ny);
      const p = toWorld(ev.x, yy, ev.z);
      const s = project3d(p.x, p.y, p.z);
      return {
        ...s,
        zval: ev.z,
        xi: ev.x,
        yi: ev.y,
        seq: ev.seq,
        r: Math.max(0.7, dotR / Math.max(0.5, s.z * 0.25)),
      };
    }).sort((a, b) => b.z - a.z);
    hoverPoints = pts;
    pts.forEach((p) => {
      g.globalAlpha = xFade(p.xi, nx);
      g.fillStyle = heatColor(zNorm(p.zval, zMax, logscale), false);
      g.beginPath();
      g.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      g.fill();
    });
    g.globalAlpha = 1;
    if (smooth && pts.length > 1) {
      const sorted = pts.slice().sort((a, b) => a.x - b.x);
      g.strokeStyle = ring;
      strokeSmooth(g, sorted);
    }
    return;
  }

  // Downsample for grid/surface when dense.
  const budget = drawStyle === "surface" ? 900 : 1600;
  let stepX = 1, stepY = 1;
  while (Math.ceil(nx / stepX) * Math.ceil(ny / stepY) > budget) {
    if (stepX <= stepY) stepX++; else stepY++;
  }
  const gx = Math.ceil(nx / stepX);
  const gy = Math.ceil(ny / stepY);
  const g2 = Array.from({ length: gx }, () => Array(gy).fill(0));
  const g2c = Array.from({ length: gx }, () => Array(gy).fill(0));
  for (let i = 0; i < nx; i++) {
    const ii = Math.min(gx - 1, Math.floor(i / stepX));
    for (let j = 0; j < ny; j++) {
      const jj = Math.min(gy - 1, Math.floor(j / stepY));
      const v = grid[i][j];
      if (!v) continue;
      g2[ii][jj] += v;
      g2c[ii][jj] += 1;
    }
  }
  for (let i = 0; i < gx; i++) {
    for (let j = 0; j < gy; j++) {
      if (g2c[i][j]) g2[i][j] /= g2c[i][j];
    }
  }
  const xiOf = (i) => Math.min(nx - 1, i * stepX + (stepX - 1) / 2);
  const yiOf = (j) => Math.min(ny - 1, j * stepY + (stepY - 1) / 2);

  if (drawStyle === "grid") {
    g.lineWidth = 1;
    for (let i = 0; i < gx; i++) {
      const pts = [];
      for (let j = 0; j < gy; j++) {
        const a = toWorld(xiOf(i), yiOf(j), g2[i][j]);
        pts.push(project3d(a.x, a.y, a.z));
      }
      g.globalAlpha = xFade(xiOf(i), nx);
      g.strokeStyle = ring;
      if (smooth) strokeSmooth(g, pts); else {
        g.beginPath();
        pts.forEach((p, k) => { if (k === 0) g.moveTo(p.x, p.y); else g.lineTo(p.x, p.y); });
        g.stroke();
      }
    }
    for (let j = 0; j < gy; j++) {
      const pts = [];
      for (let i = 0; i < gx; i++) {
        const a = toWorld(xiOf(i), yiOf(j), g2[i][j]);
        pts.push({ ...project3d(a.x, a.y, a.z), xi: xiOf(i) });
      }
      g.beginPath();
      pts.forEach((p, k) => {
        g.globalAlpha = xFade(p.xi, nx);
        if (k === 0) g.moveTo(p.x, p.y); else g.lineTo(p.x, p.y);
      });
      g.strokeStyle = ring;
      g.stroke();
    }
    g.globalAlpha = 1;
    return;
  }

  // surface — fill-only, skip empty, depth-sort, no per-quad stroke
  const quads = [];
  for (let i = 0; i < gx - 1; i++) {
    for (let j = 0; j < gy - 1; j++) {
      const z0 = g2[i][j], z1 = g2[i + 1][j], z2 = g2[i + 1][j + 1], z3 = g2[i][j + 1];
      if (!(z0 || z1 || z2 || z3)) continue;
      const c = [
        toWorld(xiOf(i), yiOf(j), z0),
        toWorld(xiOf(i + 1), yiOf(j), z1),
        toWorld(xiOf(i + 1), yiOf(j + 1), z2),
        toWorld(xiOf(i), yiOf(j + 1), z3),
      ].map((p) => project3d(p.x, p.y, p.z));
      const depth = (c[0].z + c[1].z + c[2].z + c[3].z) * 0.25;
      const zAvg = (z0 + z1 + z2 + z3) * 0.25;
      quads.push({ c, depth, zAvg, xi: xiOf(i) });
    }
  }
  quads.sort((a, b) => b.depth - a.depth);
  for (let q = 0; q < quads.length; q++) {
    const item = quads[q];
    g.globalAlpha = xFade(item.xi, nx);
    g.fillStyle = heatColor(zNorm(item.zAvg, zMax, logscale), false);
    g.beginPath();
    g.moveTo(item.c[0].x, item.c[0].y);
    g.lineTo(item.c[1].x, item.c[1].y);
    g.lineTo(item.c[2].x, item.c[2].y);
    g.lineTo(item.c[3].x, item.c[3].y);
    g.closePath();
    g.fill();
  }
  g.globalAlpha = 1;
}

function bindRawPointer(canvas) {
  if (!canvas || canvas._npBound) return;
  canvas._npBound = true;
  canvas.addEventListener("pointerdown", (ev) => {
    if (document.getElementById("raw-camera").value === "spectrum") return;
    hideRawTip();
    canvas.setPointerCapture(ev.pointerId);
    drag = { x: ev.clientX, y: ev.clientY, btn: ev.button, middle: ev.button === 1 || (ev.buttons & 4) };
    canvas.classList.add("dragging");
    ev.preventDefault();
  });
  canvas.addEventListener("pointermove", (ev) => {
    if (!drag) {
      const hit = hitHoverPoint(canvas, ev.clientX, ev.clientY);
      if (hit) {
        const lines = [];
        if (hit.seq != null) lines.push("seq " + hit.seq);
        lines.push("slot " + hit.xi);
        lines.push("offset " + (hit.yi != null ? hit.yi + " ms" : "—"));
        lines.push("latency " + us(hit.zval));
        showRawTip(ev, lines.join("\n"), canvas);
      } else hideRawTip();
      return;
    }
    const dx = ev.clientX - drag.x;
    const dy = ev.clientY - drag.y;
    drag.x = ev.clientX;
    drag.y = ev.clientY;
    if (drag.btn === 1 || drag.middle) {
      cam.panX += dx * 0.004;
      cam.panY -= dy * 0.004;
    } else {
      cam.yaw += dx * 0.01;
      cam.pitch += dy * 0.01;
      wrapCamAngles();
      swingCenterYaw = cam.yaw;
      swingPhase = 0;
    }
    updateAngleHud();
    if (lastSnap) drawRaw3d(lastSnap);
  });
  const end = () => { drag = null; canvas.classList.remove("dragging"); };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("pointerleave", () => hideRawTip());
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (ev) => {
    if (document.getElementById("raw-camera").value === "spectrum") {
      ev.preventDefault();
      return;
    }
    const zf = ev.deltaY > 0 ? 0.92 : 1.08;
    cam.zoom = Math.max(0.35, Math.min(4, cam.zoom * zf));
    cam.zoomY = Math.max(0.35, Math.min(4, (cam.zoomY != null ? cam.zoomY : cam.zoom) * zf));
    cam.dist = Math.max(1.2, Math.min(8, cam.dist * (ev.deltaY > 0 ? 1.08 : 0.92)));
    updateAngleHud();
    if (lastSnap) drawRaw3d(lastSnap);
    ev.preventDefault();
  }, { passive: false });
}
function swingAmpDeg() {
  const el = document.getElementById("opt-swing");
  const v = el ? Number(el.value) : 0;
  return (v === 5 || v === 10 || v === 15 || v === 20) ? v : 0;
}
function resetSwingCenter() {
  swingCenterYaw = cam.yaw;
  swingPhase = 0;
}
function spinLoop(ts) {
  spinRaf = requestAnimationFrame(spinLoop);
  const amp = swingAmpDeg();
  if (!amp || viewMode !== "raw" || document.getElementById("raw-camera").value === "spectrum") {
    swingCenterYaw = null;
    spinLastTs = ts;
    return;
  }
  if (swingCenterYaw == null) resetSwingCenter();
  if (!spinLastTs) spinLastTs = ts;
  const dt = Math.min(0.05, (ts - spinLastTs) / 1000);
  spinLastTs = ts;
  // Oscillate yaw within ±amp (full range = 2×amp). Period ~4s.
  const omega = (Math.PI * 2) / 4;
  swingPhase += omega * dt;
  cam.yaw = swingCenterYaw + (amp * Math.PI / 180) * Math.sin(swingPhase);
  updateAngleHud();
  if (lastSnap) drawRaw3d(lastSnap);
}
function ensureSpinLoop() {
  if (!spinRaf) {
    spinLastTs = 0;
    spinRaf = requestAnimationFrame(spinLoop);
  }
}
function bindRaw3d() {
  bindRawPointer(document.getElementById("raw3d"));
  bindRawPointer(document.getElementById("raw3d-gl"));
  ensureSpinLoop();
  const stage = document.querySelector(".raw-stage");
  if (stage && typeof ResizeObserver !== "undefined" && !stage._npRo) {
    stage._npRo = new ResizeObserver(() => {
      if (viewMode === "raw" && panelMode === "capture" && lastSnap) drawRaw3d(lastSnap);
    });
    stage._npRo.observe(stage);
  }
}

function render(snap) {
  if (paused && lastSnap) return;
  lastSnap = snap;
  maybeResizeWindow(snap);
  syncConfigInputs(snap);
  const role = snap.role || "client";
  document.getElementById("headline").textContent = role === "server" ? t("role_server") : t("role_client");
  document.getElementById("pill-proto").textContent = snap.proto || "–";
  document.getElementById("pill-endpoint").textContent = snap.endpoint || "–";
  document.getElementById("pill-lambda").textContent = snap.lambda_rps == null ? "λ —" : ("λ " + snap.lambda_rps + "/s");
  document.getElementById("pill-uptime").textContent = (snap.uptime_s || 0).toFixed(1) + " s";
  const n = snap.window_slices || 32;
  paintCells(document.getElementById("req-cells"), (snap.req || []).slice(0, n), false);
  paintCells(document.getElementById("recv-cells"), (snap.recv || []).slice(0, n), false);
  paintCells(document.getElementById("resp-cells"), (snap.resp || []).slice(0, n), true);
  paintCells(document.getElementById("ack-cells"), (snap.ack || []).slice(0, n), false);
  const q = snap.queues || {};
  document.getElementById("q-client-tx").textContent = show(q.client_pending != null ? q.client_pending : q.client_tx);
  document.getElementById("q-read").textContent = show(q.read_pending != null ? q.read_pending : q.server_rx);
  document.getElementById("q-proc").textContent = show(q.processing);
  document.getElementById("q-server-tx").textContent = show(q.response_pending != null ? q.response_pending : q.server_tx);
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
  document.getElementById("bytes").textContent = (rxb == null && txb == null) ? "–" : ("↓" + fmtBytes(rxb) + " ↑" + fmtBytes(txb));
  const lag = snap.timeline_lag_slices;
  document.getElementById("lag").textContent = lag == null ? "–" : (lag + " × " + (snap.slice_ms || 100) + " ms");
  const samples = snap.samples || {};
  line(document.getElementById("rtt-chart"), [{data: samples.rtt_us || [], color: cssVar("--ring", "#7dd3fc")}]);
  line(document.getElementById("queue-chart"), [
    {data: samples.client_tx || [], color: cssVar("--primary", "#5eead4")},
    {data: samples.server_rx || [], color: cssVar("--ring", "#7dd3fc")},
    {data: samples.server_tx || [], color: cssVar("--warning", "#fbbf24")}
  ]);
  bars(document.getElementById("hist-chart"), snap.histogram || []);
  renderNotes(snap);
  if (viewMode === "raw") drawRaw3d(snap);
}
function onMessage(snap) {
  if (paused) return;
  render(snap);
}
function connect() {
  if (!window.EventSource) {
    setInterval(() => { if (!paused) poll(); }, 500);
    poll();
    return;
  }
  const source = new EventSource("/events");
  source.onmessage = (event) => onMessage(JSON.parse(event.data));
  source.onerror = () => {
    source.close();
    setInterval(() => { if (!paused) poll(); }, 500);
    poll();
  };
}
function poll() {
  fetch("/api/snapshot").then((res) => res.json()).then(onMessage).catch(() => {});
}

document.getElementById("btn-pause").addEventListener("click", () => setPaused(!paused));
document.getElementById("btn-running").addEventListener("click", () => setViewMode("running"));
document.getElementById("btn-raw").addEventListener("click", () => setViewMode("raw"));
["opt-dense-running", "opt-dense-raw"].forEach((id) => {
  document.getElementById(id).addEventListener("change", () => {
    lastPostedSlices = 0;
    scheduleWindowFit(true);
    if (viewMode === "raw" && lastSnap) drawRaw3d(lastSnap);
  });
});
["cfg-lambda", "cfg-bucket", "cfg-status"].forEach((id) => {
  const el = document.getElementById(id);
  el.addEventListener("change", scheduleConfigPost);
  el.addEventListener("keydown", (ev) => { if (ev.key === "Enter") { ev.preventDefault(); postConfig(); } });
});
document.getElementById("raw-camera").addEventListener("change", () => {
  const v = document.getElementById("raw-camera").value;
  syncStyleDropdown();
  if (v !== "spectrum") {
    applyViewPreset(v === "free" ? "free" : v);
    resetSwingCenter();
    // View change: one-shot Fit into zoom/pan (does not toggle the Fit checkbox).
    fitOnceCurrentView();
  }
  if (lastSnap) drawRaw3d(lastSnap);
});
["raw-style", "opt-perspective", "opt-smooth", "opt-logscale", "opt-swing", "opt-keep-aspect"].forEach((id) => {
  const el = document.getElementById(id);
  if (!el) return;
  el.addEventListener("change", () => {
    if (id === "opt-swing") {
      resetSwingCenter();
      ensureSpinLoop();
    }
    if (id === "opt-keep-aspect") {
      // Re-fit framing when aspect mode changes (checkbox Fit or one-shot bake).
      const fitEl = document.getElementById("opt-fit");
      if (fitEl && fitEl.checked) {
        cam.zoom = 1; cam.zoomY = 1; cam.panX = 0; cam.panY = 0;
      } else {
        fitOnceCurrentView();
      }
    }
    if (lastSnap) drawRaw3d(lastSnap);
  });
});
(function bindFit() {
  const el = document.getElementById("opt-fit");
  if (!el) return;
  let wasOn = el.checked;
  el.addEventListener("change", () => {
    const on = el.checked;
    if (on) {
      // Start live Fit from a clean zoom; rotate will keep filling the canvas.
      cam.zoom = 1;
      cam.zoomY = 1;
      cam.panX = 0;
      cam.panY = 0;
    } else if (wasOn && lastSnap && viewMode === "raw") {
      const mode = document.getElementById("raw-camera").value;
      if (mode !== "spectrum") {
        const { w, h, xSpan, ySpan } = rawLayoutSize();
        // zoom still 1 (or user-wheel while Fit on); bake live framing.
        bakeFitIntoCam(w, h, xSpan, ySpan, mode);
      }
    }
    wasOn = on;
    if (lastSnap) drawRaw3d(lastSnap);
  });
})();
window.addEventListener("resize", () => {
  scheduleWindowFit();
  if (viewMode === "raw" && lastSnap) drawRaw3d(lastSnap);
});
bindRaw3d();
applyViewPreset("front");
initLang();
initPanelMode();
initThemes().then(connect);
