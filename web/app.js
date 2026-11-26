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
  deltas: {}, manual: new Set(), placed: new Set(), hidden: new Set(), history: [],
  placing: null,              // {kind: "unplaced"|"skipped", index} while waiting for a map click
  selected: null              // tour_id shown below the map (null = overview)
};
let map = null, layer = null, highlight = null, legend = null;
const markers = new Map();    // stop_id -> marker, to open a popup from the tables

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
  $("add-vehicle").addEventListener("click", () => $("vehicles").append(vehicleRow("", {})));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && state.placing) stopPlacing(); });

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
  renderVehicles(p.vehicles || {});
}

// Vehicle types: name, capacity (items), speed (km/h). A tour uses the
// type named in the 'vehicle' column of the tours file.
function vehicleRow(name, spec) {
  const cell = (cls, value, type) => h("td", {}, h("input", {
    class: cls, type, value: value ?? "", min: type === "number" ? "0" : null,
    step: cls === "v-speed" ? "0.5" : (type === "number" ? "1" : null) }));
  const remove = h("button", { type: "button", class: "small-button", title: t("v_remove") }, "✕");
  const row = h("tr", {}, cell("v-name", name, "text"), cell("v-capacity", spec.capacity, "number"),
                cell("v-speed", spec.speed_kmh, "number"), h("td", {}, remove));
  remove.addEventListener("click", () => row.remove());
  return row;
}

function renderVehicles(vehicles) {
  $("vehicles").replaceChildren(...Object.entries(vehicles).map(([n, v]) => vehicleRow(n, v)));
}

function readVehicles() {
  const out = {};
  for (const row of $("vehicles").querySelectorAll("tr")) {
    const name = row.querySelector(".v-name").value.trim();
    if (!name) continue;
    const capacity = row.querySelector(".v-capacity").value.trim();
    const speed = row.querySelector(".v-speed").value.trim();
    out[name] = { capacity: capacity === "" ? null : Math.round(Number(capacity)),
                  speed_kmh: speed === "" ? null : Number(speed) };
  }
  return out;
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
    min_score: Number($("min-score").value),
    vehicles: readVehicles()
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
    manual: new Set(), placed: new Set(), hidden: new Set(), history: [], placing: null,
    selected: null
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
  map.on("zoomend", () => { if (state.plan) renderMap(); });   // spread depends on zoom
  map.on("click", e => placeAt(e.latlng));
}

function fitMap() {
  const points = [];
  state.tours.forEach(tr => tr.stops.forEach(s => points.push([s.lat, s.lon])));
  state.unplaced.forEach(u => { if (u.lat !== null) points.push([u.lat, u.lon]); });
  if (points.length) map.fitBounds(points, { padding: [30, 30] });
}

// Pickups are drawn inverted: white with a ring and number in the tour
// colour. Drops (the normal case) are filled. See the key in the legend.
function stopIcon(text, fill, isNew, isDepot, late, kind) {
  const size = isNew ? 26 : 20;
  const pickup = kind === "pickup" && !isDepot;
  const ring = isNew ? "3px solid #000" : (pickup ? `3px solid ${fill}` : "1px solid #fff");
  const style = `width:${size}px;height:${size}px;border-radius:${isDepot ? "3px" : "50%"};` +
    `background:${pickup ? "#fff" : fill};border:${ring};` + (pickup ? `color:${fill};` : "") +
    (pickup && isNew ? `box-shadow:inset 0 0 0 2px ${fill};` : "");
  return L.divIcon({
    className: "", iconSize: [size, size], iconAnchor: [size / 2, size / 2],
    html: `<div class="stop-icon${late ? " late" : ""}${pickup ? " pickup" : ""}" style="${style}">${esc(text)}</div>`
  });
}

function hasPickups() {
  return state.tours.some(tr => tr.stops.some(s => s.kind === "pickup")) ||
         state.unplaced.some(u => u.kind === "pickup");
}

function kindText(kind) { return kind === "pickup" ? t("pickup") : t("drop"); }

// "Lastenvelo 8/10" or "15 Items" when the tour has no capacity
function loadText(tour) {
  if (tour.capacity === null || tour.capacity === undefined)
    return `${tour.vehicle ? tour.vehicle + " " : ""}${tour.items || 0} ${t("items_short")}`;
  return `${tour.vehicle} ${tour.max_load}/${tour.capacity}`;
}

function popupHtml(tour, number, s) {
  const lines = [`<b>${esc(s.stop_id)}</b>${s.is_new ? " (" + esc(t("new")) + ")" : ""}`,
    esc(s.address),
    `${esc(t("col_tour"))} ${esc(tour.tour_id)}, ${esc(t("stop_word"))} ${number}`,
    `${esc(t("col_arrival"))} ${hhmm(s.arrival)}`];
  if (hasPickups()) lines.push(esc(kindText(s.kind)));
  if (s.items !== null && s.items !== undefined) lines.push(`${esc(t("col_items"))}: ${s.items}`);
  if (tour.capacity !== null && tour.capacity !== undefined)
    lines.push(`${esc(t("on_board"))}: ${s.load}/${tour.capacity}`);
  if (s.tw_start !== null || s.tw_end !== null)
    lines.push(`${esc(t("col_window"))} ${hhmm(s.tw_start) || "…"}–${hhmm(s.tw_end) || "…"}`);
  if (s.slack !== null && s.slack !== undefined) lines.push(`${esc(t("col_slack"))}: ${s.slack}`);
  if (s.late) lines.push(`<b style="color:#d7263d">${esc(t("late"))}</b>`);
  if (state.placed.has(s.stop_id)) lines.push(`<i>${esc(t("placed_by_hand"))}</i>`);
  if (state.manual.has(s.stop_id)) lines.push(`<i>${esc(t("moved_by_hand"))}</i>`);
  else if (s.moved && !s.is_new) lines.push(`<i>${esc(t("moved"))}</i>`);
  return lines.join("<br>");
}

// Markers that would sit on the same spot are spread on a small circle
// around it (with a thin line to the real position), so that each one can
// be seen and dragged. Depots are drawn once per location and not spread.
const SAME_SPOT_DIGITS = 5;            // ~1 m: same rounded position = same spot

const SAME_LOCATION_M = 50;           // as in traveltime.py: closer = same place

function metres(a, b) {                // haversine distance between two stops
  const r = Math.PI / 180, dlat = (b.lat - a.lat) * r, dlon = (b.lon - a.lon) * r;
  const h = Math.sin(dlat / 2) ** 2 + Math.cos(a.lat * r) * Math.cos(b.lat * r) * Math.sin(dlon / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.sqrt(h));
}

function spotKey(lat, lon) { return lat.toFixed(SAME_SPOT_DIGITS) + "," + lon.toFixed(SAME_SPOT_DIGITS); }

// Radius of the circle in pixels. Zoomed out: just wide enough that the
// markers do not cover each other (neighbours ~24 px apart). Zoomed in:
// the wider circle as before, so the lines to the real position are easy
// to see. In between, linear.
function spreadRadius(n) {
  const tight = 12 / Math.sin(Math.PI / n);
  const wide = Math.max(tight, 18 + 4 * n);
  const f = Math.min(1, Math.max(0, (map.getZoom() - 13) / 3));   // 0 at zoom <= 13, 1 at zoom >= 16
  return tight + f * (wide - tight);
}

function spreadPositions(entries) {
  const groups = new Map();
  entries.forEach(e => {
    const key = spotKey(e.lat, e.lon);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  });
  for (const group of groups.values()) {
    if (group.length === 1) { group[0].pos = [group[0].lat, group[0].lon]; continue; }
    const centre = map.latLngToLayerPoint([group[0].lat, group[0].lon]);
    const radius = spreadRadius(group.length);
    group.forEach((e, i) => {
      const angle = -Math.PI / 2 + 2 * Math.PI * i / group.length;
      const p = L.point(centre.x + radius * Math.cos(angle), centre.y + radius * Math.sin(angle));
      const ll = map.layerPointToLatLng(p);
      e.pos = [ll.lat, ll.lng];
      e.spread = true;
    });
  }
}

function renderMap() {
  layer.clearLayers();
  markers.clear();
  const dim = id => state.selected !== null && id !== state.selected;   // not the selected tour
  const entries = [];                     // draggable markers, positioned below
  const depots = new Map();               // spot -> {lat, lon, tours: [...], colours}
  state.tours.forEach((tour, ti) => {
    if (state.hidden.has(tour.tour_id)) return;
    const fill = colour(ti);
    L.polyline(tour.stops.map(s => [s.lat, s.lon]),
               { color: fill, weight: dim(tour.tour_id) ? 3 : (state.selected ? 6 : 4),
                 opacity: dim(tour.tour_id) ? 0.2 : 0.85 })
      .bindTooltip(tour.tour_id).addTo(layer);
    let number = 0;
    const last = tour.stops.length - 1;
    const roundTrip = metres(tour.stops[0], tour.stops[last]) < SAME_LOCATION_M;
    tour.stops.forEach((s, k) => {
      const depot = (k === 0 || k === last) && !s.is_new;
      if (depot) {
        const role = roundTrip ? "D" : (k === 0 ? "S" : "E");
        const key = spotKey(s.lat, s.lon);
        if (!depots.has(key)) depots.set(key, { lat: s.lat, lon: s.lon, address: s.address,
                                                roles: {}, fill });
        const d = depots.get(key);
        (d.roles[role] = d.roles[role] || []).includes(tour.tour_id) || d.roles[role].push(tour.tour_id);
        if (role === "E" && s.late) d.late = true;    // connection missed (e.g. after a manual move)
        if (role === "E") (d.ends = d.ends || []).push(
          `${esc(tour.tour_id)}: ${esc(t("col_arrival"))} <span class="${s.late ? "late" : ""}">${hhmm(s.arrival)}</span>` +
          (s.tw_end !== null && s.tw_end !== undefined ? ` (${esc(t("until"))} ${hhmm(s.tw_end)})` : ""));
        return;
      }
      number += 1;
      entries.push({ lat: s.lat, lon: s.lon, kind: "tour", tour, ti, k, s, number, fill });
    });
  });
  state.unplaced.forEach((u, j) => {
    if (u.lat === null || u.lat === undefined) return;
    entries.push({ lat: u.lat, lon: u.lon, kind: "unplaced", u, j });
  });
  spreadPositions(entries);

  for (const d of depots.values()) {
    // D = depot (start and end), S = start, E = end at another place.
    // A place that is start of one tour and end of another is shown as D.
    const roles = Object.keys(d.roles);
    const label = roles.length === 1 ? roles[0] : "D";
    const tours = [...new Set(Object.values(d.roles).flat())];
    const fill = tours.length > 1 ? "#333333" : d.fill;
    const lines = ["D", "S", "E"].filter(r => d.roles[r])
      .map(r => `${esc(t("role_" + r))}: ${esc(d.roles[r].join(", "))}`);
    L.marker([d.lat, d.lon], { icon: stopIcon(label, fill, false, true, !!d.late), zIndexOffset: 2000,
                               title: tours.join(", ") })
      .bindPopup(`<b>${esc(t("role_" + label))}</b><br>${esc(d.address)}<br>${lines.join("<br>")}` +
                 (d.ends ? "<br>" + d.ends.join("<br>") : ""))
      .addTo(layer);
  }
  for (const e of entries) {
    if (e.spread) L.polyline([[e.lat, e.lon], e.pos], { color: "#444", weight: 1, opacity: 0.7,
                                                         interactive: false }).addTo(layer);
    let marker;
    if (e.kind === "tour") {
      marker = L.marker(e.pos, {
        icon: stopIcon(String(e.number), e.fill, e.s.is_new, false, e.s.late, e.s.kind),
        draggable: true, title: `${e.tour.tour_id} #${e.number}: ${e.s.stop_id}`,
        opacity: dim(e.tour.tour_id) ? 0.3 : 1,
        zIndexOffset: (e.s.is_new ? 500 : 0) + (state.selected === e.tour.tour_id ? 1500 : 0)
      }).bindPopup(popupHtml(e.tour, e.number, e.s));
      markers.set(e.s.stop_id, marker);
      attachDrag(marker, { kind: "tour", tourIndex: e.ti, stopIndex: e.k });
    } else {
      const u = e.u;
      marker = L.marker(e.pos, {
        draggable: true, zIndexOffset: 1000, title: u.order_id,
        icon: L.divIcon({ className: "", iconSize: [26, 26], iconAnchor: [13, 13],
                          html: '<div class="unplaced-icon">✕</div>' })
      }).bindPopup(`<b>${esc(u.order_id)}</b> – ${esc(t("m_unplaced"))}<br>${esc(u.address)}<br>` +
                   (hasPickups() ? `${esc(kindText(u.kind))}<br>` : "") +
                   (u.items !== null && u.items !== undefined ? `${esc(t("col_items"))}: ${u.items}<br>` : "") +
                   `${esc(t(u.reason))}<br><i>${esc(t("unplaced_hint"))}</i>`);
      attachDrag(marker, { kind: "unplaced", index: e.j });
    }
    marker.addTo(layer);
  }
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
        h("span", {}, `${tour.tour_id} · ${tour.stops.length} · `,
          h("span", { class: tour.over_capacity ? "over" : null }, loadText(tour)),
          ` · ${hhmm(tour.end)}`)));
    });
    if (hasPickups()) box.append(h("div", { class: "legend-key" },
      h("span", { class: "key drop" }), " " + t("drop"), "   ",
      h("span", { class: "key pickup" }), " " + t("pickup")));
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
  return JSON.stringify({ tours: state.tours, unplaced: state.unplaced, skipped: state.skipped,
                          csv: state.csv, manual: [...state.manual], placed: [...state.placed] });
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
             service_min: u.service_min ?? state.settings.service_min, items: u.items ?? null,
             kind: u.kind || "drop", is_new: true };
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
    const full = data.tours.find(tr => tr.tour_id === to && tr.over_capacity);
    toast(t("moved_toast", { stop: stop.stop_id, from, to, deltas }) +
          (full ? " " + t("over_toast", { tour: to, load: `${full.max_load}/${full.capacity}` }) : ""));
  } catch (e) {
    showError(t("error") + "\n" + e);
  }
  render();
}

// Placing by hand: for an address the geocoder did not find, the dispatcher
// clicks the right spot on the map. The stop is then inserted at its
// cheapest position (a skipped tour stop only into its own tour).
function startPlacing(kind, index) {
  state.placing = { kind, index };
  const entry = state[kind][index];
  $("placing").replaceChildren(t("placing_hint", { id: entry.order_id, address: entry.address }), " ",
    Object.assign(h("button", { type: "button" }, t("cancel")), { onclick: stopPlacing }));
  $("placing").hidden = false;
  $("map").classList.add("placing");
  $("map").scrollIntoView({ behavior: "smooth", block: "center" });
}

function stopPlacing() {
  state.placing = null;
  $("placing").hidden = true;
  $("map").classList.remove("placing");
}

async function placeAt(latlng) {
  if (!state.placing) return;
  const { kind, index } = state.placing;
  stopPlacing();
  const before = snapshot();
  const entry = state[kind][index];
  const lat = Math.round(latlng.lat * 1e6) / 1e6, lon = Math.round(latlng.lng * 1e6) / 1e6;
  const unplaced = state.unplaced.filter((_, j) => !(kind === "unplaced" && j === index));
  const skipped = state.skipped.filter((_, j) => !(kind === "skipped" && j === index));
  const stop = { stop_id: entry.order_id, address: entry.address, country: entry.country, lat, lon,
                 tw_start: entry.tw_start, tw_end: entry.tw_end,
                 service_min: entry.service_min ?? state.settings.service_min,
                 items: entry.items ?? null, kind: entry.kind || "drop", is_new: kind === "unplaced" };
  try {
    const response = await fetch("api/insert", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: state.settings, tours: state.tours, stop, unplaced, skipped,
                             tour_id: kind === "skipped" ? entry.tour_id : null })
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    state.history.push(before);
    if (data.inserted) {
      state.tours = data.tours;
      state.unplaced = unplaced;
      state.skipped = skipped;
      state.csv = Object.assign({}, state.csv, data.csv);
      state.placed.add(stop.stop_id);
      toast(t("placed_toast", { stop: stop.stop_id, tour: data.tour_id, delta: Math.round(data.delta) }));
    } else {
      // No feasible position: keep it as a not-placed order, now with a position,
      // so that it can be dragged onto a tour by hand.
      const u = Object.assign({}, entry, { lat, lon, reason: data.reason || "no_feasible_position", tour_id: "" });
      if (kind === "unplaced") state.unplaced = state.unplaced.map((x, j) => j === index ? u : x);
      else { state.skipped = skipped; state.unplaced = [...state.unplaced, u]; }
      state.placed.add(stop.stop_id);
      toast(t(data.reason === "capacity" ? "not_inserted_capacity" : "not_inserted", { stop: stop.stop_id }));
    }
  } catch (e) {
    showError(t("error") + "\n" + e);
  }
  render();
}

function placeButton(kind, index) {
  const b = h("button", { type: "button", class: "small-button" }, "📍 " + t("place_on_map"));
  b.addEventListener("click", () => startPlacing(kind, index));
  return b;
}

function undo() {
  const last = state.history.pop();
  if (!last) return;
  const s = JSON.parse(last);
  Object.assign(state, { tours: s.tours, unplaced: s.unplaced, skipped: s.skipped, csv: s.csv,
                         manual: new Set(s.manual), placed: new Set(s.placed) });
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
  renderOpen();
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
  const over = state.tours.filter(tr => tr.over_capacity);
  if (over.length) items.push(h("div", { class: "warning" }, t("over_warn", {
    tours: over.map(tr => `${tr.tour_id} (${tr.max_load}/${tr.capacity})`).join(", ") })));
  $("warnings").replaceChildren(...items);
}

// Orders not placed and skipped tour stops sit right under the map, so that
// none of them is forgotten while planning.
function renderOpen() {
  const parts = [];
  if (!state.plan) { $("open").replaceChildren(); return; }
  if (!state.unplaced.length && !state.skipped.length) {
    $("open").replaceChildren(h("div", { class: "open-done" }, "✓ " + t("all_placed")));
    return;
  }
  if (state.unplaced.length) {
    parts.push(h("h3", {}, `${t("unplaced")} (${state.unplaced.length})`),
      table([t("col_order"), t("col_address"), t("col_type"), t("col_items"), t("col_reason"), ""],
        state.unplaced.map((u, j) => h("tr", {}, h("td", {}, u.order_id), h("td", {}, u.address),
          h("td", {}, kindText(u.kind)), h("td", {}, u.items ?? ""), h("td", {}, t(u.reason)),
          h("td", {}, u.lat === null || u.lat === undefined ? placeButton("unplaced", j) : null)))),
      state.unplaced.some(u => u.lat !== null && u.lat !== undefined)
        ? h("p", { class: "small muted" }, t("unplaced_hint")) : null);
  }
  if (state.skipped.length) {
    parts.push(h("h3", {}, `${t("skipped")} (${state.skipped.length})`),
      table([t("col_tour"), t("col_stop"), t("col_address"), t("col_items"), t("col_reason"), ""],
        state.skipped.map((u, j) => h("tr", {}, h("td", {}, u.tour_id), h("td", {}, u.order_id),
          h("td", {}, u.address), h("td", {}, u.items ?? ""), h("td", {}, t(u.reason)),
          h("td", {}, placeButton("skipped", j))))));
  }
  if (state.unplaced.some(u => u.lat === null || u.lat === undefined) || state.skipped.length)
    parts.push(h("p", { class: "small muted" }, t("place_hint")));
  $("open").replaceChildren(h("div", { class: "open-box" }, parts));
}

// Below the map: one tab per tour (plus an overview), instead of one long
// list of all stops. The selected tour is highlighted on the map.
function renderTables() {
  const parts = [];
  if (state.selected && !state.tours.some(tr => tr.tour_id === state.selected)) state.selected = null;

  const tabs = h("div", { class: "tabs", role: "tablist" });
  const tab = (id, label, dot) => {
    const b = h("button", { type: "button", role: "tab", class: "tab" + (state.selected === id ? " active" : ""),
                            "aria-selected": String(state.selected === id) },
      dot ? h("span", { class: "dot", style: `background:${dot}` }) : null, label);
    b.addEventListener("click", () => selectTour(id));
    return b;
  };
  tabs.append(tab(null, t("overview")));
  state.tours.forEach((tr, ti) => tabs.append(
    tab(tr.tour_id, tr.tour_id, colour(ti))));
  parts.push(h("h2", {}, t("tours_title")), tabs);

  const ti = state.tours.findIndex(tr => tr.tour_id === state.selected);
  parts.push(ti < 0 ? overviewPanel() : tourPanel(state.tours[ti], ti));

  if (state.geocoding.length) {
    parts.push(h("details", {}, h("summary", {}, t("geocoding")),
      table([t("col_address"), "", t("col_status"), "score", t("col_found")],
        state.geocoding.map(g => h("tr", {}, h("td", {}, g.query), h("td", {}, g.country),
          h("td", { class: g.status === "ok" ? null : "late" }, g.status),
          h("td", {}, g.score === null ? "" : g.score.toFixed(2)), h("td", {}, g.label))))));
  }
  $("tables").replaceChildren(...parts);
}

function selectTour(id) {
  state.selected = id;
  renderMap();
  renderTables();
  if (id) {
    const tour = state.tours.find(tr => tr.tour_id === id);
    if (tour && !state.hidden.has(id))
      map.fitBounds(L.latLngBounds(tour.stops.map(s => [s.lat, s.lon])), { padding: [40, 40], maxZoom: 15 });
  }
}

function changeOf(tourId) { return state.changes.find(c => c.tour_id === tourId); }

function tourFacts(tr) {
  const last = tr.stops[tr.stops.length - 1];
  const roundTrip = metres(tr.stops[0], last) < SAME_LOCATION_M;
  return {
    stops: tr.stops.length - 2,
    added: tr.stops.filter(s => s.is_new).length,
    late: tr.stops.filter(s => s.late).length,
    roundTrip, last
  };
}

function overviewPanel() {
  const rows = state.tours.map((tr, ti) => {
    const f = tourFacts(tr), c = changeOf(tr.tour_id);
    const row = h("tr", { class: "clickable" },
      h("td", {}, h("span", { class: "dot", style: `background:${colour(ti)}` }), " ", tr.tour_id),
      h("td", {}, tr.vehicle || "–"),
      h("td", { class: tr.over_capacity ? "late" : null },
        tr.capacity !== null && tr.capacity !== undefined ? `${tr.max_load}/${tr.capacity}`
                                                         : `${tr.items || 0} ${t("items_short")}`),
      h("td", {}, hhmm(tr.start)), h("td", {}, hhmm(tr.end)), h("td", {}, `${tr.duration} min`),
      h("td", {}, f.stops), h("td", {}, f.added || ""),
      h("td", { class: f.late ? "late" : null }, f.late || ""),
      h("td", {}, f.roundTrip ? t("end_depot") : t("end_connection")),
      h("td", {}, c && c.saved ? `−${c.saved} min` : ""));
    row.addEventListener("click", () => selectTour(tr.tour_id));
    return row;
  });
  return h("div", { class: "panel" },
    table([t("col_tour"), t("v_vehicle"), t("col_load"), t("col_start"), t("col_end"), t("col_duration"),
           t("col_stops"), t("col_new"), t("late"), t("col_tour_end"), t("col_saved")], rows),
    h("p", { class: "small muted" }, t("overview_hint")));
}

function tourPanel(tr, ti) {
  const f = tourFacts(tr), c = changeOf(tr.tour_id);
  const card = (label, value, extra, bad) => h("div", { class: "card" + (bad ? " bad" : "") },
    h("div", { class: "label" }, label), h("div", { class: "value" }, value),
    extra ? h("div", { class: "extra" }, extra) : null);
  const cards = [
    card(t("v_vehicle"), tr.vehicle || "–",
         tr.capacity !== null && tr.capacity !== undefined ? `${t("v_capacity")}: ${tr.capacity}` : t("no_limit")),
    card(t("col_load"), tr.capacity !== null && tr.capacity !== undefined ? `${tr.max_load}/${tr.capacity}` : `${tr.items || 0}`,
         tr.capacity ? loadBar(tr.max_load, tr.capacity) : t("items_short"), tr.over_capacity),
    card(t("col_time"), `${hhmm(tr.start)}–${hhmm(tr.end)}`, `${tr.duration} min`),
    card(t("col_stops"), f.stops, f.added ? t("n_new", { n: f.added }) : ""),
    card(t("col_tour_end"), f.roundTrip ? t("end_depot") : t("end_connection"),
         f.roundTrip ? "" : `${hhmm(f.last.arrival)}` +
           (f.last.tw_end !== null && f.last.tw_end !== undefined ? ` (${t("until")} ${hhmm(f.last.tw_end)})` : ""),
         f.last.late),
  ];
  if (f.late) cards.push(card(t("late"), f.late, t("stops_late"), true));
  if (c) cards.push(card(t("optimization"), `${c.before} → ${c.after} min`, t("n_moved", { n: c.moved })));

  let number = 0;
  const rows = tr.stops.map((s, k) => {
    const depot = (k === 0 || k === tr.stops.length - 1) && !s.is_new;
    const label = depot ? (f.roundTrip ? "D" : (k === 0 ? "S" : "E")) : String(++number);
    const marks = [];
    if (s.is_new) marks.push(h("span", { class: "tag new" },
      t("new") + (state.deltas[s.stop_id] !== undefined && !state.manual.has(s.stop_id)
        ? ` +${Math.round(state.deltas[s.stop_id])} min` : "")));
    if (state.placed.has(s.stop_id)) marks.push(h("span", { class: "tag" }, t("placed_short")));
    if (state.manual.has(s.stop_id)) marks.push(h("span", { class: "tag" }, t("manual")));
    else if (s.moved && !s.is_new && !depot) marks.push(h("span", { class: "tag" }, t("moved")));
    if (s.late) marks.push(h("span", { class: "tag bad" }, t("late")));
    const window_ = (s.tw_start !== null || s.tw_end !== null)
      ? `${hhmm(s.tw_start) || "…"}–${hhmm(s.tw_end) || "…"}` : "";
    const over = tr.capacity !== null && tr.capacity !== undefined && s.load > tr.capacity;
    const row = h("tr", { class: "clickable" + (s.is_new ? " is-new" : "") },
      h("td", {}, h("span", { class: "num" + (depot ? " depot" : "") + (s.kind === "pickup" && !depot ? " pickup" : ""),
                              style: `--c:${colour(ti)}` }, label)),
      h("td", {}, h("div", {}, s.stop_id), h("div", { class: "small muted" }, s.address)),
      h("td", {}, depot ? "" : kindText(s.kind)),
      h("td", {}, s.items ?? ""),
      h("td", { class: over ? "late" : null },
        tr.capacity !== null && tr.capacity !== undefined ? h("span", {}, loadBar(s.load, tr.capacity), ` ${s.load}`) : s.load),
      h("td", { class: s.late ? "late" : null }, hhmm(s.arrival)),
      h("td", {}, hhmm(s.departure)),
      h("td", {}, window_), h("td", {}, s.slack ?? ""),
      h("td", {}, marks));
    row.addEventListener("click", () => focusStop(s));
    return row;
  });
  const csvButton = h("button", { type: "button", class: "small-button" }, `⬇ ${tr.tour_id}.csv`);
  csvButton.addEventListener("click", () => download(`tour_${tr.tour_id}.csv`, tourCsv(tr.tour_id)));
  return h("div", { class: "panel" },
    h("div", { class: "cards" }, cards),
    table(["#", t("col_stop"), t("col_type"), t("col_items"), t("on_board"), t("col_arrival"),
           t("col_departure"), t("col_window"), t("col_slack"), ""], rows),
    csvButton);
}

function loadBar(load, capacity) {
  const share = capacity > 0 ? Math.min(1, load / capacity) : 1;
  return h("span", { class: "bar" + (load > capacity ? " over" : "") },
    h("span", { style: `width:${Math.round(share * 100)}%` }));
}

function focusStop(s) {
  map.setView([s.lat, s.lon], Math.max(map.getZoom(), 15));
  const marker = markers.get(s.stop_id);
  if (marker) setTimeout(() => (markers.get(s.stop_id) || marker).openPopup(), 300);
  $("map").scrollIntoView({ behavior: "smooth", block: "center" });
}

// Lines of tours_new.csv that belong to one tour (first column = tour_id).
function tourCsv(tourId) {
  const lines = (state.csv.tours || "").split("\n");
  const firstField = line => line.startsWith('"') ? line.slice(1, line.indexOf('"', 1)) : line.split(",")[0];
  return [lines[0], ...lines.slice(1).filter(l => l && firstField(l) === tourId)].join("\n") + "\n";
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
