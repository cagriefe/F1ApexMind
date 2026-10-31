// SPDX-License-Identifier: AGPL-3.0-or-later
"use strict";
/* ApexMind web UI: no dependencies, no build step. */

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

function h(tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (v === true) e.setAttribute(k, "");
    else if (v !== false && v != null) e.setAttribute(k, v);
  }
  for (const kid of kids.flat()) {
    if (kid == null || kid === false) continue;
    e.append(kid.nodeType ? kid : document.createTextNode(kid));
  }
  return e;
}
const SVGNS = "http://www.w3.org/2000/svg";
function s(tag, attrs, ...kids) {
  const e = document.createElementNS(SVGNS, tag);
  for (const [k, v] of Object.entries(attrs || {})) e.setAttribute(k, v);
  for (const kid of kids.flat()) if (kid != null) e.append(kid.nodeType ? kid : document.createTextNode(kid));
  return e;
}

const fmtLap = (ms) => {
  if (ms == null) return "–";
  const sign = ms < 0 ? "-" : "";
  ms = Math.abs(Math.round(ms));
  return sign + Math.floor(ms / 60000) + ":" + ((ms % 60000) / 1000).toFixed(3).padStart(6, "0");
};
const fmtSector = (ms) => (ms == null ? "–" : (ms / 1000).toFixed(3));
const fmtGap = (ms) => (ms >= 0 ? "+" : "−") + (Math.abs(ms) / 1000).toFixed(3);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

/* ------------------------------------------------------------------ views */

let liveAvailable = false;
function showView(name) {
  if (name === "live" && !liveAvailable) name = "analyze";
  for (const v of ["analyze", "live"]) {
    $("#view-" + v).hidden = v !== name;
    $("#tab-" + v).setAttribute("aria-selected", String(v === name));
  }
  if (location.hash !== "#" + name) history.replaceState(null, "", "#" + name);
  if (name === "analyze") requestAnimationFrame(() => state.report && drawChart());
}
$$(".tabs button").forEach((b) => b.addEventListener("click", () => showView(b.dataset.view)));

/* --------------------------------------------------------------- analysis */

const worker = new Worker("worker.js");
let reqId = 0;
let waiter = null;
worker.onmessage = (e) => {
  const m = e.data;
  if (!waiter || m.id !== reqId) return;
  if (m.type === "status") setStatus(m.text, "busy");
  else if (m.type === "result") { const w = waiter; waiter = null; w.resolve(JSON.parse(m.json)); }
  else if (m.type === "error") { const w = waiter; waiter = null; w.reject(new Error(m.message)); }
};
worker.onerror = (e) => {
  if (waiter) { const w = waiter; waiter = null; w.reject(new Error(e.message || "The analysis worker failed to start.")); }
};

function setStatus(text, kind) {
  const el = $("#status");
  el.hidden = !text;
  el.className = "status" + (kind === "error" ? " error" : "");
  el.replaceChildren(...(kind === "busy" ? [h("span", { class: "spin" })] : []), text || "");
}

async function analyze(text, label) {
  setStatus("Preparing…", "busy");
  try {
    const report = await new Promise((resolve, reject) => {
      reqId += 1;
      waiter = { resolve, reject };
      worker.postMessage({ id: reqId, type: "analyze", text });
    });
    setStatus("");
    state.report = report;
    state.label = label;
    initSelection();
    renderResults();
  } catch (err) {
    setStatus(err.message || String(err), "error");
  }
}

async function loadFile(file) {
  if (!file) return;
  if (file.size > 60e6) return setStatus("That file is larger than 60 MB; is it a session CSV?", "error");
  analyze(await file.text(), file.name);
}

const drop = $("#drop");
drop.addEventListener("click", (e) => { if (!e.target.closest("button, label")) $("#file").click(); });
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file").click(); } });
for (const t of ["dragenter", "dragover"]) drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); });
for (const t of ["dragleave", "drop"]) drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); });
drop.addEventListener("drop", (e) => loadFile(e.dataTransfer.files[0]));
$("#file").addEventListener("change", (e) => { loadFile(e.target.files[0]); e.target.value = ""; });
$("#sample").addEventListener("click", async () => {
  setStatus("Fetching the sample session…", "busy");
  try {
    const res = await fetch("sample.csv");
    if (!res.ok) throw new Error("Could not load the sample (" + res.status + ").");
    analyze(await res.text(), "Sample session");
  } catch (err) { setStatus(err.message, "error"); }
});

/* ---------------------------------------------------------------- results */

const state = { report: null, label: "", a: null, b: null, focus: null };

function initSelection() {
  const r = state.report;
  const traced = r.laps.filter((l) => r.traces[l.lap]);
  const best = r.laps.find((l) => l.lap === r.summary.best_lap);
  state.a = best ? best.lap : (traced[0] && traced[0].lap) ?? null;
  state.b = null;
}

function renderResults() {
  const r = state.report;
  const root = $("#results");
  $("#intro").hidden = true;
  root.hidden = false;
  root.replaceChildren(
    h("div", { class: "results-head" },
      h("h1", { text: state.label }),
      h("button", { class: "btn small", type: "button", onclick: resetView }, "Analyze another file")),
    tiles(r), lapsBlock(r), mapBlock(r), chartBlock(r), eventsBlock(r),
  );
  drawChart();
  window.scrollTo({ top: 0 });
}

function resetView() {
  state.report = null;
  $("#results").hidden = true;
  $("#results").replaceChildren();
  $("#intro").hidden = false;
  setStatus("");
}

function tile(k, v, sub) {
  return h("div", { class: "tile" }, h("div", { class: "k", text: k }), h("div", { class: "v", text: v }),
    sub ? h("div", { class: "s", text: sub }) : null);
}

function tiles(r) {
  const m = r.summary;
  const ideal = m.ideal_ms != null && m.best_ms != null ? fmtGap(m.ideal_ms - m.best_ms) + " s vs best" : "needs 2+ valid laps";
  return h("div", { class: "tiles" },
    tile("Best lap", m.best_ms != null ? fmtLap(m.best_ms) : "–", m.best_lap != null ? "Lap " + m.best_lap : "no valid complete lap"),
    tile("Ideal lap", m.ideal_ms != null ? fmtLap(m.ideal_ms) : "–", ideal),
    tile("Valid laps", m.valid + " / " + m.complete, m.laps - m.complete ? (m.laps - m.complete) + " partial" : "all complete"),
    tile("Track limits", String(m.violations), m.violations === 1 ? "lap deleted" : "laps deleted"),
    tile("Lock-ups", String(m.lockups), "flagged by the coach"),
    tile("Calls", String(m.calls), "spoken pedal calls"),
  );
}

function lapsBlock(r) {
  const good = r.laps.filter((l) => l.complete && l.valid);
  const bestMs = r.summary.best_ms;
  const bestSec = [0, 1, 2].map((i) => Math.min(...good.filter((l) => l.sectors).map((l) => l.sectors[i]), Infinity));
  const rows = r.laps.map((l) => {
    const tags = [];
    if (!l.valid) tags.push(h("span", { class: "tag bad", text: "Invalid" }));
    if (!l.complete) tags.push(h("span", { class: "tag warn", text: "Partial" }));
    if (l.lockups) tags.push(h("span", { class: "tag warn", text: l.lockups + (l.lockups === 1 ? " lock-up" : " lock-ups") }));
    if (l.lap === r.summary.best_lap) tags.push(h("span", { class: "tag good", text: "Best" }));
    const sec = (i) => h("td", { class: l.valid && l.sectors && l.sectors[i] === bestSec[i] ? "best-sector" : "", text: l.sectors ? fmtSector(l.sectors[i]) : "–" });
    const tr = h("tr", {
      "data-lap": l.lap, tabindex: r.traces[l.lap] ? "0" : null,
      class: [l.lap === r.summary.best_lap ? "is-best" : "", !l.valid || !l.complete ? "is-bad" : ""].join(" ").trim(),
      title: r.traces[l.lap] ? "Click to compare this lap in the chart" : "",
    },
      h("td", { text: String(l.lap) }),
      h("td", { class: "time", text: l.complete ? fmtLap(l.ms) : "–" }),
      h("td", { text: l.complete && bestMs != null && l.ms !== bestMs ? fmtGap(l.ms - bestMs) : "" }),
      sec(0), sec(1), sec(2),
      h("td", { class: "flags" }, tags));
    const pick = () => { if (r.traces[l.lap] && l.lap !== state.a) { state.b = state.b === l.lap ? null : l.lap; syncSelection(); } };
    tr.addEventListener("click", pick);
    tr.addEventListener("keydown", (e) => { if (e.key === "Enter") pick(); });
    return tr;
  });
  return h("section", { class: "block" }, h("h2", { text: "Laps" }),
    h("div", { class: "table-wrap" }, h("table", null,
      h("thead", null, h("tr", null, ["Lap", "Time", "Gap", "S1", "S2", "S3", "Notes"].map((t) => h("th", { text: t })))),
      h("tbody", null, rows))),
    h("p", { class: "hint", text: "Click a lap to compare it with the primary lap in the chart. Green sectors are the fastest valid ones." }));
}

/* ---- track map + corners */

function speedColor(t) {
  const a = [255, 106, 43], b = [79, 156, 255];
  return "rgb(" + a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",") + ")";
}

function pointAt(points, d) {
  let best = points[0], bd = Infinity;
  for (const p of points) { const e = Math.abs(p[0] - d); if (e < bd) { bd = e; best = p; } }
  return best;
}

function mapBlock(r) {
  const pts = r.map && r.map.points;
  const corners = r.corners || [];
  const cards = corners.map((c) => cornerCard(c));
  const left = pts && pts.length > 20 ? mapCard(r, pts) : null;
  return h("section", { class: "block" }, h("h2", { text: "Track and corners" }),
    h("div", { class: "map-grid" }, left, h("div", { class: "corners" },
      cards.length ? cards : h("p", { class: "muted", text: "No corners were learned yet. Brake hard at least twice at the same spot on valid laps." }))));
}

function mapCard(r, pts) {
  const xs = pts.map((p) => p[1]), ys = pts.map((p) => p[2]);
  const minx = Math.min(...xs), maxx = Math.max(...xs), miny = Math.min(...ys), maxy = Math.max(...ys);
  const size = Math.max(maxx - minx, maxy - miny) || 1;
  const pad = size * 0.07, lw = size / 150;
  const sv = s("svg", { viewBox: [minx - pad, miny - pad, maxx - minx + 2 * pad, maxy - miny + 2 * pad].join(" "), role: "img",
    "aria-label": "Racing line coloured by speed, with numbered braking points" });
  const vmin = Math.min(...pts.map((p) => p[3])), vmax = Math.max(...pts.map((p) => p[3]));
  const halo = s("g", { stroke: css("--bad"), "stroke-width": lw * 2.4, "stroke-linecap": "round", opacity: ".55" });
  const line = s("g", { "stroke-width": lw, "stroke-linecap": "round", fill: "none" });
  for (let i = 1; i < pts.length; i++) {
    const a = pts[i - 1], b = pts[i];
    if (b[4] > 10) halo.append(s("line", { x1: a[1], y1: a[2], x2: b[1], y2: b[2] }));
    line.append(s("line", { x1: a[1], y1: a[2], x2: b[1], y2: b[2], stroke: speedColor((b[3] - vmin) / ((vmax - vmin) || 1)) }));
  }
  sv.append(halo, line);
  for (const spot of r.limit_spots || []) {
    const p = pointAt(pts, spot.d), k = lw * 2.2;
    sv.append(s("path", { d: "M" + (p[1] - k) + " " + (p[2] - k) + "l" + 2 * k + " " + 2 * k + "m0 " + -2 * k + "l" + -2 * k + " " + 2 * k,
      stroke: css("--warn"), "stroke-width": lw * 1.2, fill: "none", "stroke-linecap": "round" }, s("title", {}, "Track-limit spot at " + spot.d + " m")));
  }
  const f = pts[0];
  sv.append(s("circle", { cx: f[1], cy: f[2], r: lw * 2.2, fill: css("--text") }, s("title", {}, "Start / finish")));
  for (const c of r.corners || []) {
    const p = pointAt(pts, c.brake_at), rad = size / 38;
    const g = s("g", { style: "cursor:pointer", tabindex: "0", role: "button", "aria-label": "Corner " + c.id });
    g.append(s("circle", { cx: p[1], cy: p[2], r: rad, fill: css("--bg"), stroke: css("--text"), "stroke-width": lw * .9 }),
      s("text", { x: p[1], y: p[2], "text-anchor": "middle", "dominant-baseline": "central", "font-size": rad * 1.05,
        "font-weight": 650, fill: css("--text"), style: "font-family:var(--sans)" }, String(c.id)));
    const go = () => focusCorner(c.id);
    g.addEventListener("click", go);
    g.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); });
    sv.append(g);
  }
  return h("div", { class: "card map-card" }, sv,
    h("div", { class: "legend" }, "slow", h("i"), "fast", h("span", { style: "margin-left:auto" }, "red halo = braking · ✕ = track-limit spot")));
}

function focusCorner(id) {
  $$(".corner").forEach((c) => c.classList.toggle("focus", c.id === "corner-" + id));
  const el = $("#corner-" + id);
  if (el) el.scrollIntoView({ block: "center", behavior: "smooth" });
}

function cornerCard(c) {
  const chips = [];
  c.script.forEach((step, i) => {
    if (i) chips.push(h("span", { class: "arrow", text: "›" }));
    chips.push(h("span", { class: "chip-call " + step.kind }, step.text, i ? h("small", { text: "+" + step.d + " m" }) : null));
  });
  const facts = [
    ["Entry", c.entry_kmh + " km/h"], ["Minimum", c.min_kmh + " km/h"], ["Peak brake", c.peak + "%"],
    c.spread_m != null ? ["Brake point", "±" + c.spread_m + " m"] : null,
    c.lock_frac ? ["Lock-ups", Math.round(c.lock_frac * 100) + "% of laps"] : null,
  ].filter(Boolean);
  return h("article", { class: "corner", id: "corner-" + c.id },
    h("div", { class: "corner-head" }, h("span", { class: "corner-id", text: "T" + c.id }),
      h("span", { class: "corner-meta", text: "brake at " + c.brake_at + " m · " + c.laps + " reference laps" })),
    h("div", { class: "facts" }, facts.map(([k, v]) => h("span", null, k + " ", h("b", { text: v })))),
    chips.length ? h("div", { class: "script" }, chips) : h("div", { class: "muted", text: "No call script yet." }));
}

/* ---- trace chart */

const LAP_A = () => css("--accent");
const LAP_B = () => css("--blue");

function chartBlock(r) {
  const options = r.laps.filter((l) => r.traces[l.lap]);
  if (!options.length) return h("div");
  const mk = (id, withNone) => {
    const sel = h("select", { id, "aria-label": id === "selA" ? "Primary lap" : "Compare lap" },
      withNone ? h("option", { value: "", text: "None" }) : null,
      options.map((l) => h("option", { value: l.lap, text: "Lap " + l.lap + (l.complete ? " · " + fmtLap(l.ms) : " · partial") })));
    sel.addEventListener("change", () => {
      state.a = Number($("#selA").value) || null;
      state.b = Number($("#selB").value) || null;
      if (state.b === state.a) state.b = null;
      syncSelection();
    });
    return sel;
  };
  const wrap = h("div", { class: "canvas-wrap", id: "chart-wrap" }, h("canvas", { id: "chart" }), h("div", { class: "tooltip", id: "tip", hidden: true }));
  const sec = h("section", { class: "block" }, h("h2", { text: "Speed and pedals" }),
    h("div", { class: "card chart-card" },
      h("div", { class: "chart-tools" },
        h("span", { class: "legend-dot" }, h("i", { style: "background:var(--accent)" }), mk("selA", false)),
        h("span", { class: "legend-dot" }, h("i", { style: "background:var(--blue)" }), mk("selB", true)),
        h("span", { class: "muted", text: "Hover or drag across the chart to read values." })),
      wrap));
  queueMicrotask(() => { $("#selA").value = state.a ?? ""; $("#selB").value = state.b ?? ""; });
  bindChart(wrap);
  return sec;
}

function syncSelection() {
  $("#selA").value = state.a ?? "";
  $("#selB").value = state.b ?? "";
  $$("#results tbody tr").forEach((tr) => tr.classList.toggle("is-compare", Number(tr.dataset.lap) === state.b));
  drawChart();
}

let chartGeo = null;
function drawChart() {
  const r = state.report, canvas = $("#chart");
  if (!r || !canvas || $("#view-analyze").hidden) return;
  const A = r.traces[state.a], B = state.b != null ? r.traces[state.b] : null;
  if (!A) return;
  const wrap = canvas.parentElement, W = wrap.clientWidth, H = W < 560 ? 300 : 340;
  const dpr = window.devicePixelRatio || 1;
  canvas.width = W * dpr; canvas.height = H * dpr; canvas.style.height = H + "px";
  const g = canvas.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, W, H);
  const L = 38, R = 8, T = 8, gap = 22, bottomH = 78, topH = H - T - gap - bottomH - 18;
  const len = r.summary.track_length_m;
  const X = (d) => L + (d / len) * (W - L - R);
  const vmax = Math.ceil(Math.max(...A.v, ...(B ? B.v : [0]), 100) / 50) * 50;
  const Y1 = (v) => T + topH - (v / vmax) * topH;
  const Y2 = (p) => T + topH + gap + bottomH - (p / 100) * bottomH;
  const muted = css("--muted"), border = css("--border"), text = css("--text");
  g.font = "11px " + css("--sans");
  g.textBaseline = "middle";

  // grid + labels
  g.strokeStyle = border; g.lineWidth = 1; g.fillStyle = muted; g.textAlign = "right";
  for (let v = 0; v <= vmax; v += 100) { g.beginPath(); g.moveTo(L, Y1(v) + .5); g.lineTo(W - R, Y1(v) + .5); g.stroke(); g.fillText(String(v), L - 6, Y1(v)); }
  for (const p of [0, 50, 100]) { g.beginPath(); g.moveTo(L, Y2(p) + .5); g.lineTo(W - R, Y2(p) + .5); g.stroke(); g.fillText(p + "%", L - 6, Y2(p)); }
  g.textAlign = "center"; g.textBaseline = "top";
  const step = len > 4000 ? 1000 : 500;
  for (let d = 0; d <= len; d += step) g.fillText(d >= 1000 ? (d / 1000) + " km" : d + " m", Math.min(Math.max(X(d), L + 6), W - R - 14), T + topH + gap + bottomH + 4);

  // corner markers
  for (const c of r.corners || []) {
    const x = X(c.brake_at);
    g.strokeStyle = border; g.setLineDash([3, 4]);
    g.beginPath(); g.moveTo(x + .5, T); g.lineTo(x + .5, T + topH + gap + bottomH); g.stroke(); g.setLineDash([]);
    g.fillStyle = muted; g.textAlign = "center"; g.textBaseline = "top"; g.fillText("T" + c.id, x, T + 1);
  }

  const line = (tr, key, scale, color, width, dash) => {
    g.beginPath(); g.strokeStyle = color; g.lineWidth = width; g.setLineDash(dash || []);
    tr.d.forEach((d, i) => (i ? g.lineTo(X(d), scale(tr[key][i])) : g.moveTo(X(d), scale(tr[key][i]))));
    g.stroke(); g.setLineDash([]);
  };
  const area = (tr, key, color) => {
    g.beginPath(); g.moveTo(X(tr.d[0]), Y2(0));
    tr.d.forEach((d, i) => g.lineTo(X(d), Y2(tr[key][i])));
    g.lineTo(X(tr.d[tr.d.length - 1]), Y2(0)); g.closePath(); g.fillStyle = color; g.fill();
  };
  g.lineJoin = "round";
  const good = css("--good"), bad = css("--bad");
  const withAlpha = (c, a) => `color-mix(in srgb, ${c} ${a}%, transparent)`;
  if (B) line(B, "v", Y1, LAP_B(), 1.5);
  line(A, "v", Y1, LAP_A(), 2);
  area(A, "th", withAlpha(good, 32)); area(A, "br", withAlpha(bad, 42));
  line(A, "th", Y2, good, 1.4); line(A, "br", Y2, bad, 1.4);
  if (B) { line(B, "th", Y2, good, 1, [4, 3]); line(B, "br", Y2, bad, 1, [4, 3]); }

  g.fillStyle = muted; g.textAlign = "left"; g.textBaseline = "top";
  g.fillText("km/h", L + 4, T + topH - 14);
  g.fillText("throttle (green) · brake (red)", L + 4, T + topH + gap + 2);
  chartGeo = { L, R, W, H, T, topH, gap, bottomH, len, A, B, X, text };
}

function nearest(tr, d) {
  let lo = 0, hi = tr.d.length - 1;
  while (hi - lo > 1) { const mid = (lo + hi) >> 1; if (tr.d[mid] < d) lo = mid; else hi = mid; }
  return Math.abs(tr.d[lo] - d) <= Math.abs(tr.d[hi] - d) ? lo : hi;
}

function bindChart(wrap) {
  const move = (ev) => {
    if (!chartGeo) return;
    const rect = wrap.getBoundingClientRect(), x = ev.clientX - rect.left;
    const { L, R, W, len, A, B } = chartGeo;
    const d = Math.min(Math.max(((x - L) / (W - L - R)) * len, 0), len);
    const i = nearest(A, d);
    drawChart();
    const gx = chartGeo.X(A.d[i]), g = $("#chart").getContext("2d");
    g.strokeStyle = chartGeo.text; g.globalAlpha = .55; g.lineWidth = 1;
    g.beginPath(); g.moveTo(gx + .5, chartGeo.T); g.lineTo(gx + .5, chartGeo.T + chartGeo.topH + chartGeo.gap + chartGeo.bottomH); g.stroke(); g.globalAlpha = 1;
    const tip = $("#tip");
    const row = (name, tr, color) => {
      const j = nearest(tr, A.d[i]);
      return h("div", null, h("span", { style: "color:" + color }, "● " + name + " "), tr.v[j] + " km/h · T " + tr.th[j] + "% · B " + tr.br[j] + "% · G" + tr.g[j]);
    };
    tip.replaceChildren(h("div", { class: "t-h", text: A.d[i] + " m" }), row("A", A, LAP_A()), B ? row("B", B, LAP_B()) : null);
    tip.hidden = false;
    const tw = tip.offsetWidth;
    tip.style.left = Math.min(Math.max(gx + 12, 4), W - tw - 4) + "px";
    tip.style.top = (chartGeo.T + 22) + "px";
  };
  wrap.addEventListener("pointermove", move);
  wrap.addEventListener("pointerdown", move);
  wrap.addEventListener("pointerleave", () => { $("#tip").hidden = true; drawChart(); });
}
new ResizeObserver(() => state.report && drawChart()).observe(document.body);

/* ---- events */

const GROUPS = [
  ["all", "All", () => true],
  ["calls", "Calls", (k) => k.startsWith("call_") && !["call_limits", "call_traction"].includes(k)],
  ["lock", "Lock-ups", (k) => k === "lockup"],
  ["limits", "Track limits", (k) => ["wide", "lap_invalid", "call_limits"].includes(k)],
  ["slides", "Slides", (k) => k.startsWith("spin") || ["slide", "recover", "call_traction", "throttle"].includes(k)],
  ["laps", "Laps", (k) => ["debrief", "delta"].includes(k)],
];

function eventsBlock(r) {
  let active = "all";
  const list = h("div", { class: "events" });
  const filters = h("div", { class: "filters" });
  const paint = () => {
    const test = GROUPS.find((x) => x[0] === active)[2];
    const rows = r.events.filter((e) => test(e.kind));
    list.replaceChildren(...(rows.length ? rows.map((e) => h("div", { class: "ev" },
      h("span", { class: "where", text: "L" + e.lap + " · " + e.d + " m" }),
      h("span", { class: "kind", text: e.kind.replace(/_/g, " ") }),
      h("span", { class: "text", text: e.text }))) : [h("div", { class: "ev" }, h("span"), h("span"), h("span", { class: "muted", text: "Nothing here." }))]));
    $$("button", filters).forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.g === active)));
  };
  for (const [id, label, fn] of GROUPS) {
    const n = r.events.filter((e) => fn(e.kind)).length;
    if (id !== "all" && !n) continue;
    filters.append(h("button", { type: "button", "data-g": id, "aria-pressed": "false", onclick: () => { active = id; paint(); } }, label + " " + n));
  }
  paint();
  return h("section", { class: "block" }, h("h2", { text: "Event log" }), filters, list);
}

/* ------------------------------------------------------------------- live */

let lastStateAt = 0;
function setLive(on) {
  const chip = $("#live-chip");
  chip.className = "chip " + (on ? "on" : "");
  chip.textContent = on ? "Live" : "Waiting for telemetry";
}

function updateLive(st) {
  lastStateAt = performance.now();
  setLive(true);
  $("#live-speed").textContent = st.speed;
  $("#live-gear").textContent = st.gear < 0 ? "R" : st.gear === 0 ? "N" : st.gear;
  $("#live-rpm").textContent = st.rpm ? st.rpm + " rpm" : "";
  $("#live-thr").style.height = st.throttle * 100 + "%";
  $("#live-brk").style.height = st.brake * 100 + "%";
  $("#live-thr-v").textContent = Math.round(st.throttle * 100);
  $("#live-brk-v").textContent = Math.round(st.brake * 100);
  $("#live-steer").style.left = 50 + Math.max(-1, Math.min(1, st.steer)) * 50 + "%";
  $("#live-lap").textContent = st.lap ?? "–";
  $("#live-sector").textContent = st.sector == null ? "–" : st.sector + 1;
  $("#live-laptime").textContent = st.lap_ms != null ? fmtLap(st.lap_ms) : "0:00.000";
  $("#live-last").textContent = st.last_ms ? fmtLap(st.last_ms) : "–";
  const flags = [];
  if (st.invalid) flags.push(h("span", { class: "tag bad", text: "Lap invalid" }));
  if (st.spin) flags.push(h("span", { class: "tag bad", text: "Spin" }));
  if (st.pit) flags.push(h("span", { class: "tag warn", text: "Pit" }));
  $("#live-flags").replaceChildren(...flags);
  $("#live-track").textContent = st.track_length ? st.track_length + " m track · " + st.corners + " corners learned" : "";
}

let callTimer = 0;
function addAdvice(ev, quiet) {
  const label = ev.kind.replace(/^call_/, "").replace(/_/g, " ");
  const where = ev.lap != null ? "L" + ev.lap + (ev.d != null ? " · " + ev.d + " m" : "") : "";
  const li = h("li", null, h("span", { class: "k", text: label }), h("span", { text: ev.text }));
  const log = $("#live-log");
  log.prepend(li);
  while (log.children.length > 40) log.lastChild.remove();
  if (quiet) return;
  const call = $("#live-call");
  call.textContent = ev.text;
  call.classList.add("fresh");
  $("#live-call-meta").textContent = label + (where ? " · " + where : "");
  clearTimeout(callTimer);
  callTimer = setTimeout(() => call.classList.remove("fresh"), 1500);
}

async function initLive(initialHash) {
  let snap = null;
  try {
    const res = await fetch("api/live", { cache: "no-store" });
    if (res.ok) snap = await res.json();
  } catch { /* hosted statically: no live endpoint */ }
  if (!snap || !snap.live) return;
  liveAvailable = true;
  $("#tab-live").hidden = false;
  $("#live-hint").hidden = true;
  [...snap.events].forEach((e) => addAdvice(e, true));
  if (snap.state && snap.state.speed != null) updateLive(snap.state);
  const es = new EventSource("events");
  es.addEventListener("state", (e) => updateLive(JSON.parse(e.data)));
  es.addEventListener("advice", (e) => addAdvice(JSON.parse(e.data)));
  es.onerror = () => setLive(false);
  setInterval(() => { if (performance.now() - lastStateAt > 3000) setLive(false); }, 1000);
  if (!initialHash || initialHash === "#live") showView("live");
}

const initialHash = location.hash;
showView(initialHash === "#live" ? "live" : "analyze");
initLive(initialHash);
