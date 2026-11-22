// Web page of the tour insertion tool.
//
// The server (Python, src/tour_insertion/server.py) does all planning.
// This page uploads the files, shows the result on a Leaflet map and lets
// the dispatcher drag stops between tours. After every move the new stop
// order is sent to /api/schedule, which returns times and late stops.
// The page holds the current plan; the server keeps no state.
"use strict";

// Same 12 colours as the Python map (mapview.py). No pure red: red = not placed.
const COLOURS = ["#1f77b4", "#ff7f0e", "#2ca02c", "#9467bd", "#17becf", "#8c564b",
                 "#e377c2", "#1b2a6b", "#bcbd22", "#555555", "#00695c", "#f5b800"];
const SNAP_PX = 40;           // how close to a tour line a stop must be dropped

const state = {
  profiles: {}, examples: [], plan: null, settings: null,
  tours: [], unplaced: [], skipped: [], geocoding: [], changes: [], csv: {},
  deltas: {}, manual: new Set(), hidden: new Set(), history: []
};
let map = null, layer = null, highlight = null, legend = null;

const $ = id => document.getElementById(id);

function hhmm(m) {
  if (m === null || m === undefined) return "";
  return String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function colour(i) { return COLOURS[i % COLOURS.length]; }
function store(key, value) { try { localStorage.setItem(key, value); } catch (e) { /* ignore */ } }
function load(key) { try { return localStorage.getItem(key); } catch (e) { return null; } }

// Small DOM helper: h("td", {class: "late"}, "text", childNode, ...)
function h(tag, attrs, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === false || v === null || v === undefined) continue;
    if (k === "class") node.className = v; else node.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined) continue;
    node.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return node;
}
function table(headers, rows) {
  return h("table", {}, h("thead", {}, h("tr", {}, headers.map(x => h("th", {}, x)))),
    h("tbody", {}, rows));
}

// --------------------------------------------------------------------------
// Start, settings, data
// --------------------------------------------------------------------------

async function init() {
  LANG = load("lang") || (navigator.language || "de").slice(0, 2);
  if (!["de", "fr", "en"].includes(LANG)) LANG = "de";
  $("language").value = LANG;
  applyTexts();

  $("language").addEventListener("change", () => {
    LANG = $("language").value; store("lang", LANG);
    applyTexts(); fillSources(); $("optimize-help").title = t("optimize_help");
    if (state.plan) render();
  });
  $("optimize-help").title = t("optimize_help");
  initMap();                        // the map is there from the start
  $("profile").addEventListener("change", () => applyProfile($("profile").value));
  $("run").addEventListener("click", runPlan);
  $("undo").addEventListener("click", undo);

  try {
    state.profiles = await (await fetch("api/profiles")).json();
    state.examples = await (await fetch("api/examples")).json();
  } catch (e) {
    showError("Server not reachable: " + e);
  }
  $("profile").replaceChildren(...Object.keys(state.profiles).map(n => h("option", { value: n }, n)));
  applyProfile(Object.keys(state.profiles)[0]);
  fillSources();
}

function selectedSource() {
  const checked = document.querySelector("input[name=source]:checked");
  return checked ? checked.value : "upload";
}

// Radio buttons: own files, then one per example data set
function fillSources() {
  const current = selectedSource();
  const options = [["upload", t("source_upload")], ...state.examples.map(n => [n, t("ex_" + n)])];
  $("sources").replaceChildren(...options.map(([value, text]) => {
    const radio = h("input", { type: "radio", name: "source", value });
    radio.checked = value === current;
    radio.addEventListener("change", () => { $("upload-box").hidden = selectedSource() !== "upload"; });
    return h("label", {}, radio, text);
  }));
  if (!document.querySelector("input[name=source]:checked"))
    document.querySelector("input[name=source]").checked = true;
  $("upload-box").hidden = selectedSource() !== "upload";
}

function applyProfile(name) {
  const p = state.profiles[name];
  if (!p) return;
  $("speed").value = (p.speed_m_per_min * 60 / 1000).toFixed(1);
  $("detour").value = p.detour_factor;
  $("service").value = p.service_min;
  $("tolerance").value = p.tolerance_min;
  $("optimize").checked = !!p.optimize;
  $("open-start").checked = !!p.open_start;
  $("open-end").checked = !!p.open_end;
  $("min-score").value = p.min_score;
}

function readSettings() {
  return {
    speed_m_per_min: Number($("speed").value) * 1000 / 60,
    detour_factor: Number($("detour").value),
    service_min: Math.round(Number($("service").value)),
    tolerance_min: Math.round(Number($("tolerance").value)),
    optimize: $("optimize").checked,
    open_start: $("open-start").checked,
    open_end: $("open-end").checked,
    min_score: Number($("min-score").value)
  };
}

function showError(text) { $("error").textContent = text; $("error").hidden = !text; }

async function runPlan() {
  showError("");
  const form = new FormData();
  const source = selectedSource();
  try {
    if (source !== "upload") {
      const name = source;
      for (const kind of ["tours", "orders"]) {
        const text = await (await fetch(`api/examples/${name}/${kind}`)).text();
        form.append(kind, new Blob([text], { type: "text/csv" }), `${name}_${kind}.csv`);
      }
    } else {
      const tours = $("tours-file").files[0], orders = $("orders-file").files[0];
      if (!tours || !orders) { showError(t("need_files")); return; }
      form.append("tours", tours); form.append("orders", orders);
    }
    form.append("settings", JSON.stringify(readSettings()));
    $("run").disabled = true; $("run").textContent = t("running");
    const response = await fetch("api/plan", { method: "POST", body: form });
    const data = await response.json().catch(() => ({ detail: response.statusText }));
    if (!response.ok) { showError(t("error") + "\n" + (data.detail || response.status)); return; }
    setPlan(data);
  } catch (e) {
    showError(t("error") + "\n" + e);
  } finally {
    $("run").disabled = false; $("run").textContent = t("run");
  }
}

function setPlan(data) {
  Object.assign(state, {
    plan: data, settings: data.settings, tours: data.tours, unplaced: data.unplaced,
    skipped: data.skipped, geocoding: data.geocoding, changes: data.changes, csv: data.csv,
    deltas: Object.fromEntries(data.inserted.map(o => [o.order_id, o.delta])),
    manual: new Set(), hidden: new Set(), history: []
  });
  $("results").hidden = false;
  $("metrics").hidden = false;
  fitMap();
  render();
}

// --------------------------------------------------------------------------
// Map
// --------------------------------------------------------------------------

function initMap() {
  // a view must be set before anything is drawn on the map
  map = L.map("map", { zoomControl: true }).setView([47.0, 5.0], 6);
  L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19, attribution: "&copy; OpenStreetMap contributors"
  }).addTo(map);
  layer = L.layerGroup().addTo(map);
  highlight = L.polyline([], { weight: 12, opacity: 0.45, dashArray: "8 8", interactive: false }).addTo(map);
}

function fitMap() {
  const points = [];
  state.tours.forEach(tr => tr.stops.forEach(s => points.push([s.lat, s.lon])));
  state.unplaced.forEach(u => { if (u.lat !== null) points.push([u.lat, u.lon]); });
  if (points.length) map.fitBounds(points, { padding: [30, 30] });
}

function stopIcon(text, fill, isNew, isDepot, late) {
  const size = isNew ? 26 : 20;
  const style = `width:${size}px;height:${size}px;border-radius:${isDepot ? "3px" : "50%"};` +
    `background:${fill};border:${isNew ? "3px solid #000" : "1px solid #fff"}`;
  return L.divIcon({
    className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    html: `<div class="stop-icon${late ? " late" : ""}" style="${style}">${esc(text)}</div>`
  });
}

function popupHtml(tour, number, s) {
  const lines = [`<b>${esc(s.stop_id)}</b>${s.is_new ? " (" + esc(t("new")) + ")" : ""}`,
    esc(s.address),
    `${esc(t("col_tour"))} ${esc(tour.tour_id)}, ${esc(t("stop_word"))} ${number}`,
    `${esc(t("col_arrival"))} ${hhmm(s.arrival)}`];
  if (s.tw_start !== null || s.tw_end !== null)
    lines.push(`${esc(t("col_window"))} ${hhmm(s.tw_start) || "…"}–${hhmm(s.tw_end) || "…"}`);
  if (s.slack !== null && s.slack !== undefined) lines.push(`${esc(t("col_slack"))}: ${s.slack}`);
  if (s.late) lines.push(`<b style="color:#d7263d">${esc(t("late"))}</b>`);
  if (state.manual.has(s.stop_id)) lines.push(`<i>${esc(t("moved_by_hand"))}</i>`);
  else if (s.moved && !s.is_new) lines.push(`<i>${esc(t("moved"))}</i>`);
  return lines.join("<br>");
}

function renderMap() {
  layer.clearLayers();
  state.tours.forEach((tour, ti) => {
    if (state.hidden.has(tour.tour_id)) return;
    const fill = colour(ti);
    L.polyline(tour.stops.map(s => [s.lat, s.lon]), { color: fill, weight: 4, opacity: 0.8 })
      .bindTooltip(tour.tour_id).addTo(layer);
    let number = 0;
    tour.stops.forEach((s, k) => {
      const depot = (k === 0 || k === tour.stops.length - 1) && !s.is_new;
      if (!depot) number += 1;
      const marker = L.marker([s.lat, s.lon], {
        icon: stopIcon(depot ? "D" : String(number), fill, s.is_new, depot, s.late),
        draggable: !depot, title: `${tour.tour_id} #${depot ? "D" : number}: ${s.stop_id}`,
        zIndexOffset: s.is_new ? 500 : 0
      }).bindPopup(popupHtml(tour, depot ? "D" : number, s));
      if (!depot) attachDrag(marker, { kind: "tour", tourIndex: ti, stopIndex: k });
      marker.addTo(layer);
    });
  });
  state.unplaced.forEach((u, j) => {
    if (u.lat === null || u.lat === undefined) return;
    const marker = L.marker([u.lat, u.lon], {
      draggable: true, zIndexOffset: 1000, title: u.order_id,
      icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
                        html: '<div class="unplaced-icon">✕</div>' })
    }).bindPopup(`<b>${esc(u.order_id)}</b> – ${esc(t("m_unplaced"))}<br>${esc(u.address)}<br>` +
                 `${esc(t(u.reason))}<br><i>${esc(t("unplaced_hint"))}</i>`);
    attachDrag(marker, { kind: "unplaced", index: j });
    marker.addTo(layer);
  });
  renderLegend();
}

function renderLegend() {
  if (legend) legend.remove();
  legend = L.control({ position: "topright" });
  legend.onAdd = () => {
    const box = h("div", { class: "legend" }, h("b", {}, t("legend_title")));
    state.tours.forEach((tour, ti) => {
      const box_ = h("input", { type: "checkbox" });
      box_.checked = !state.hidden.has(tour.tour_id);
      box_.addEventListener("change", () => {
        if (box_.checked) state.hidden.delete(tour.tour_id); else state.hidden.add(tour.tour_id);
        renderMap();
      });
      box.append(h("label", {}, box_, h("span", { class: "dot", style: `background:${colour(ti)}` }),
        `${tour.tour_id} · ${tour.stops.length} · ${hhmm(tour.end)}`));
    });
    L.DomEvent.disableClickPropagation(box);
    L.DomEvent.disableScrollPropagation(box);
    return box;
  };
  legend.addTo(map);
}

// --------------------------------------------------------------------------
// Drag & drop
// --------------------------------------------------------------------------

function distanceToSegment(p, a, b) {
  const dx = b.x - a.x, dy = b.y - a.y;
  const len2 = dx * dx + dy * dy;
  let u = len2 ? ((p.x - a.x) * dx + (p.y - a.y) * dy) / len2 : 0;
  u = Math.max(0, Math.min(1, u));
  return Math.hypot(p.x - (a.x + u * dx), p.y - (a.y + u * dy));
}

// Nearest tour segment (between stop j and j+1) in screen pixels, or null.
function nearestSegment(latlng, source) {
  const p = map.latLngToLayerPoint(latlng);
  let best = null;
  state.tours.forEach((tour, ti) => {
    if (state.hidden.has(tour.tour_id)) return;
    for (let j = 0; j < tour.stops.length - 1; j++) {
      if (source.kind === "tour" && source.tourIndex === ti &&
          (j === source.stopIndex || j + 1 === source.stopIndex)) continue;   // its own place
      const a = tour.stops[j], b = tour.stops[j + 1];
      const d = distanceToSegment(p, map.latLngToLayerPoint([a.lat, a.lon]),
                                  map.latLngToLayerPoint([b.lat, b.lon]));
      if (!best || d < best.d) best = { tourIndex: ti, after: j, d };
    }
  });
  return best && best.d <= SNAP_PX ? best : null;
}

function showHighlight(target) {
  if (!target) { highlight.setLatLngs([]); return; }
  const stops = state.tours[target.tourIndex].stops;
  const a = stops[target.after], b = stops[target.after + 1];
  highlight.setStyle({ color: colour(target.tourIndex) });
  highlight.setLatLngs([[a.lat, a.lon], [b.lat, b.lon]]);
  highlight.bringToFront();
}

function attachDrag(marker, source) {
  marker.on("dragstart", () => map.closePopup());
  marker.on("drag", e => showHighlight(nearestSegment(e.latlng, source)));
  marker.on("dragend", () => {
    const target = nearestSegment(marker.getLatLng(), source);
    showHighlight(null);
    if (target) moveStop(source, target); else renderMap();     // snap back
  });
}

function snapshot() {
  return JSON.stringify({ tours: state.tours, unplaced: state.unplaced, csv: state.csv,
                          manual: [...state.manual] });
}

async function moveStop(source, target) {
  const before = snapshot();
  const tours = JSON.parse(JSON.stringify(state.tours));
  const unplaced = JSON.parse(JSON.stringify(state.unplaced));
  let stop, from;
  let at = target.after + 1;
  if (source.kind === "tour") {
    from = tours[source.tourIndex].tour_id;
    stop = tours[source.tourIndex].stops.splice(source.stopIndex, 1)[0];
    if (source.tourIndex === target.tourIndex && source.stopIndex <= target.after) at -= 1;
  } else {
    from = t("m_unplaced");
    const u = unplaced.splice(source.index, 1)[0];
    stop = { stop_id: u.order_id, address: u.address, country: u.country, lat: u.lat, lon: u.lon,
             tw_start: u.tw_start, tw_end: u.tw_end,
             service_min: u.service_min ?? state.settings.service_min, is_new: true };
  }
  stop.moved = true;
  tours[target.tourIndex].stops.splice(at, 0, stop);
  const to = tours[target.tourIndex].tour_id;
  const oldDuration = Object.fromEntries(state.tours.map(tr => [tr.tour_id, tr.duration]));

  try {
    const response = await fetch("api/schedule", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: state.settings, tours, unplaced })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    state.history.push(before);
    state.tours = data.tours;
    state.unplaced = unplaced;
    state.csv = Object.assign({}, state.csv, data.csv);
    state.manual.add(stop.stop_id);
    const deltas = data.tours
      .filter(tr => tr.tour_id === from || tr.tour_id === to)
      .map(tr => { const d = tr.duration - (oldDuration[tr.tour_id] ?? tr.duration);
                   return `${tr.tour_id} ${d >= 0 ? "+" : ""}${d} min`; }).join(", ");
    toast(t("moved_toast", { stop: stop.stop_id, from, to, deltas }));
  } catch (e) {
    showError(t("error") + "\n" + e);
  }
  render();
}

function undo() {
  const last = state.history.pop();
  if (!last) return;
  const s = JSON.parse(last);
  Object.assign(state, { tours: s.tours, unplaced: s.unplaced, csv: s.csv, manual: new Set(s.manual) });
  render();
}

let toastTimer = null;
function toast(text) {
  $("toast").textContent = text; $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { $("toast").hidden = true; }, 5000);
}

// --------------------------------------------------------------------------
// Metrics, warnings, tables, downloads
// --------------------------------------------------------------------------

function render() {
  renderMetrics();
  renderWarnings();
  renderMap();
  renderTables();
  renderDownloads();
  $("undo").disabled = state.history.length === 0;
}

function lateCount() {
  return state.tours.reduce((n, tr) => n + tr.stops.filter(s => s.late).length, 0);
}

function renderMetrics() {
  const placed = state.tours.reduce((n, tr) => n + tr.stops.filter(s => s.is_new).length, 0);
  const late = lateCount();
  const cards = [["m_tours", state.tours.length], ["m_orders", state.plan.n_orders],
                 ["m_placed", placed], ["m_unplaced", state.unplaced.length], ["m_late", late]];
  $("metrics").replaceChildren(...cards.map(([key, value]) =>
    h("div", { class: "metric" + (key === "m_late" && late ? " bad" : "") },
      h("div", { class: "label" }, t(key)), h("div", { class: "value" }, value))));
}

function renderWarnings() {
  const items = [];
  if (state.skipped.length) items.push(h("div", { class: "warning" }, t("skipped_warn", { n: state.skipped.length })));
  const late = lateCount();
  if (late) items.push(h("div", { class: "warning" }, t("late_warn", { n: late })));
  $("warnings").replaceChildren(...items);
}

function renderTables() {
  const parts = [];

  if (state.changes.length) {
    const before = state.changes.reduce((n, c) => n + c.before, 0);
    const after = state.changes.reduce((n, c) => n + c.after, 0);
    const moved = state.changes.reduce((n, c) => n + c.moved, 0);
    parts.push(h("h2", {}, t("optimization")),
      h("p", {}, t("opt_summary", { before, after, saved: before - after, moved })),
      table([t("col_tour"), t("col_before"), t("col_after"), t("col_saved"), t("col_moved")],
        state.changes.map(c => h("tr", {}, h("td", {}, c.tour_id), h("td", {}, c.before),
          h("td", {}, c.after), h("td", {}, c.saved), h("td", {}, c.moved)))),
      h("p", { class: "small muted" }, t("opt_note")));
  }

  const newRows = [];
  state.tours.forEach(tr => tr.stops.forEach((s, k) => {
    if (!s.is_new) return;
    newRows.push(h("tr", {}, h("td", {}, s.stop_id), h("td", {}, tr.tour_id), h("td", {}, k),
      h("td", {}, state.manual.has(s.stop_id) ? t("manual") : (state.deltas[s.stop_id] ?? "")),
      h("td", { class: s.late ? "late" : null }, hhmm(s.arrival))));
  }));
  const left = h("div", {}, h("h2", {}, t("inserted")),
    table([t("col_order"), t("col_tour"), t("col_pos"), t("col_extra"), t("col_arrival")], newRows));
  const right = h("div", {}, h("h2", {}, t("unplaced")),
    table([t("col_order"), t("col_address"), t("col_reason")],
      state.unplaced.map(u => h("tr", {}, h("td", {}, u.order_id), h("td", {}, u.address),
        h("td", {}, t(u.reason))))),
    state.unplaced.some(u => u.lat !== null) ? h("p", { class: "small muted" }, t("unplaced_hint")) : null);
  parts.push(h("div", { class: "cols" }, left, right));

  if (state.skipped.length) {
    parts.push(h("h2", {}, t("skipped")),
      table([t("col_tour"), t("col_stop"), t("col_address"), t("col_reason")],
        state.skipped.map(u => h("tr", {}, h("td", {}, u.tour_id), h("td", {}, u.order_id),
          h("td", {}, u.address), h("td", {}, t(u.reason))))));
  }

  const all = [];
  state.tours.forEach(tr => tr.stops.forEach((s, k) => {
    const marks = [s.is_new ? t("new") : "", state.manual.has(s.stop_id) ? t("manual")
                   : (s.moved && !s.is_new ? t("moved") : "")].filter(Boolean).join(", ");
    const window_ = (s.tw_start !== null || s.tw_end !== null)
      ? `${hhmm(s.tw_start) || "…"}–${hhmm(s.tw_end) || "…"}` : "";
    all.push(h("tr", {}, h("td", {}, tr.tour_id), h("td", {}, k), h("td", {}, s.stop_id),
      h("td", {}, marks), h("td", {}, s.address), h("td", { class: s.late ? "late" : null }, hhmm(s.arrival)),
      h("td", {}, window_), h("td", {}, s.slack ?? "")));
  }));
  parts.push(h("details", {}, h("summary", {}, t("all_tours")),
    table([t("col_tour"), "#", t("col_stop"), "", t("col_address"), t("col_arrival"),
           t("col_window"), t("col_slack")], all)));

  if (state.geocoding.length) {
    parts.push(h("details", {}, h("summary", {}, t("geocoding")),
      table([t("col_address"), "", t("col_status"), "score", t("col_found")],
        state.geocoding.map(g => h("tr", {}, h("td", {}, g.query), h("td", {}, g.country),
          h("td", { class: g.status === "ok" ? null : "late" }, g.status),
          h("td", {}, g.score === null ? "" : g.score.toFixed(2)), h("td", {}, g.label))))));
  }
  $("tables").replaceChildren(...parts);
}

function download(name, text) {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
  const a = h("a", { href: url, download: name });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

function renderDownloads() {
  const files = [["tours_new.csv", state.csv.tours], ["unplaced.csv", state.csv.unplaced]];
  if (state.skipped.length) files.push(["skipped_stops.csv", state.csv.skipped]);
  if (state.geocoding.length) files.push(["geocoding.csv", state.csv.geocoding]);
  $("downloads").replaceChildren(...files.map(([name, text]) => {
    const b = h("button", { type: "button" }, "⬇ " + name);
    b.addEventListener("click", () => download(name, text || ""));
    return b;
  }));
}

init();
