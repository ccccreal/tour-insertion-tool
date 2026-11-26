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
  profiles: {}, examples: [], planExamples: [], plan: null, settings: null,
  mode: "new",                // "new" = plan from a list of stops, "insert" = add to tours
  file: null,                 // stops/orders file as chosen (File), for the column mapping
  inspected: null,            // /api/inspect answer for that file
  sheet: 0, roles: {},        // column -> role ("address", "postcode", ...)
  mapOptions: { kind: "drop", country: "", default_items: 1 },
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
    applyTexts(); modeTexts(); fillSources(); renderMapping(); $("optimize-help").title = t("optimize_help");
    if (state.plan) render();
  });
  $("optimize-help").title = t("optimize_help");
  initMap();                        // the map is there from the start
  $("profile").addEventListener("change", () => applyProfile($("profile").value));
  $("run").addEventListener("click", runPlan);
  $("undo").addEventListener("click", undo);
  $("add-vehicle").addEventListener("click", () => $("vehicles").append(vehicleRow("", {})));
  document.querySelectorAll("input[name=mode]").forEach(r =>
    r.addEventListener("change", () => setMode(r.value)));
  $("orders-file").addEventListener("change", () => chooseFile($("orders-file").files[0] || null));
  document.addEventListener("keydown", e => { if (e.key === "Escape" && state.placing) stopPlacing(); });

  try {
    state.profiles = await (await fetch("api/profiles")).json();
    state.examples = await (await fetch("api/examples")).json();
    state.planExamples = await (await fetch("api/plan-examples")).json();
  } catch (e) {
    showError("Server not reachable: " + e);
  }
  $("profile").replaceChildren(...Object.keys(state.profiles).map(n => h("option", { value: n }, n)));
  applyProfile(Object.keys(state.profiles)[0]);
  setMode(load("mode") === "insert" ? "insert" : "new");
}

// Two ways to work: plan tours from a list of stops, or add new orders to
// tours planned the evening before. The page shows only what the mode needs
// (elements with class only-new / only-insert).
function modeTexts() {
  $("intro").textContent = t(state.mode === "new" ? "intro_new" : "intro");
  $("run").textContent = t(state.mode === "new" ? "run_new" : "run");
}

function setMode(mode) {
  state.mode = mode;
  store("mode", mode);
  document.body.classList.toggle("mode-new", mode === "new");
  document.body.classList.toggle("mode-insert", mode === "insert");
  document.querySelector(`input[name=mode][value=${mode}]`).checked = true;
  modeTexts();
  fillSources();
  chooseFile($("orders-file").files[0] || null);
}

function selectedSource() {
  const checked = document.querySelector("input[name=source]:checked");
  return checked ? checked.value : "upload";
}

// Radio buttons: own files, then one per example data set
function fillSources() {
  const current = selectedSource();
  const examples = state.mode === "new"
    ? state.planExamples.map(e => ["plan:" + e.name, t("ex_" + e.name)])
    : state.examples.map(n => [n, t("ex_" + n)]);
  const options = [["upload", t("source_upload")], ...examples];
  $("sources").replaceChildren(...options.map(([value, text]) => {
    const radio = h("input", { type: "radio", name: "source", value });
    radio.checked = value === current;
    radio.addEventListener("change", sourceChanged);
    return h("label", {}, radio, text);
  }));
  if (!document.querySelector("input[name=source]:checked"))
    document.querySelector("input[name=source]").checked = true;
  $("upload-box").hidden = selectedSource() !== "upload";
}

async function sourceChanged() {
  const source = selectedSource();
  $("upload-box").hidden = source !== "upload";
  if (source.startsWith("plan:")) {                    // example stop list + its template
    const name = source.slice(5);
    const blob = await (await fetch(`api/plan-examples/${name}/stops`)).blob();
    const example = state.planExamples.find(e => e.name === name);
    await chooseFile(new File([blob], `${name}.xlsx`), example && example.template);
    if (!$("depot").value.trim()) $("depot").value = "Bahnhofplatz 1, 8001 Zürich";
  } else {
    chooseFile(source === "upload" ? ($("orders-file").files[0] || null) : null);
  }
}

// --------------------------------------------------------------------------
// Column mapping ("template") for stop lists in any layout, e.g. Excel
// --------------------------------------------------------------------------

const ROLES = ["", "address", "postcode", "city", "id", "name", "quantity", "info"];
const STANDARD = ["order_id", "address"];        // a CSV with these needs no mapping

function savedTemplates() {
  try { return JSON.parse(load("templates") || "{}"); } catch (e) { return {}; }
}

async function chooseFile(file, template) {
  state.file = file; state.inspected = null;
  if (!file) { renderMapping(); return; }
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("api/inspect", { method: "POST", body: form });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || response.status);
    state.inspected = data;
  } catch (e) {
    showError(t("error") + "\n" + e); renderMapping(); return;
  }
  showError("");
  const sheets = state.inspected.sheets;
  const plainCsv = !/\.xls[xm]?$/i.test(file.name) &&
    STANDARD.every(c => sheets[0].headers.includes(c));
  state.needsMapping = state.mode === "new" || !plainCsv;
  state.sheet = 0;
  applyTemplate(template || sheets[0].guess);
  renderMapping();
}

function applyTemplate(tpl) {
  const sheets = state.inspected.sheets;
  const i = sheets.findIndex(sh => sh.name === tpl.sheet);
  if (i >= 0) state.sheet = i;
  const headers = sheets[state.sheet].headers;
  const roles = {};
  const set = (cols, role) => (Array.isArray(cols) ? cols : [cols]).forEach(c => {
    if (c && headers.includes(c) && !roles[c]) roles[c] = role; });
  set(tpl.address, "address"); set(tpl.postcode, "postcode"); set(tpl.city, "city");
  set(tpl.id, "id"); set(tpl.name_columns, "name"); set(tpl.quantities, "quantity");
  set(tpl.info, "info");
  state.roles = roles;
  state.mapOptions = { kind: tpl.kind || "drop", country: tpl.country || "",
                       default_items: tpl.default_items ?? 1 };
  const wanted = [tpl.address, tpl.postcode, tpl.city, tpl.id, tpl.name_columns, tpl.quantities, tpl.info]
    .flat().filter(Boolean);
  state.mappingMissing = wanted.filter(c => !headers.includes(c));
}

function currentTemplate() {
  const headers = state.inspected.sheets[state.sheet].headers;
  const by = role => headers.filter(c => state.roles[c] === role);
  return { sheet: state.inspected.sheets[state.sheet].name, address: by("address"),
           postcode: by("postcode")[0] || "", city: by("city")[0] || "", id: by("id")[0] || "",
           name_columns: by("name"), quantities: by("quantity"), info: by("info"),
           ...state.mapOptions };
}

// What the first data row becomes with the current mapping (like the server)
function sampleStop() {
  const sheet = state.inspected.sheets[state.sheet];
  const tpl = currentTemplate();
  const row = sheet.preview.find(r => r.some(Boolean));
  if (!row) return "";
  const v = c => row[sheet.headers.indexOf(c)] || "";
  const street = tpl.address.map(v).filter(Boolean).join(", ");
  const place = [v(tpl.postcode), v(tpl.city)].filter(Boolean).join(" ");
  const address = [street, place].filter(Boolean).join(", ");
  const items = tpl.quantities.length
    ? tpl.quantities.reduce((n, c) => n + (parseInt(v(c), 10) || 0), 0) : tpl.default_items;
  const name = tpl.name_columns.map(v).filter(Boolean).join(" ");
  return `${name ? name + " – " : ""}${address || "?"} · ${items} ${t("items_short")}`;
}

function renderMapping() {
  const box = $("mapping");
  if (!state.inspected || !state.needsMapping) { box.hidden = true; box.replaceChildren(); return; }
  box.hidden = false;
  const sheets = state.inspected.sheets, sheet = sheets[state.sheet];

  // templates: guessed, built-in (examples), saved in this browser
  const saved = savedTemplates();
  const choose = h("select", {}, h("option", { value: "" }, t("tpl_choose")),
    h("option", { value: "guess" }, t("tpl_guess")),
    ...state.planExamples.map(e => h("option", { value: "ex:" + e.name }, e.template.title || e.name)),
    ...Object.keys(saved).map(n => h("option", { value: "saved:" + n }, "★ " + n)));
  choose.addEventListener("change", () => {
    const v = choose.value;
    if (v === "guess") applyTemplate(sheet.guess);
    else if (v.startsWith("ex:")) applyTemplate(state.planExamples.find(e => "ex:" + e.name === v).template);
    else if (v.startsWith("saved:")) applyTemplate(saved[v.slice(6)]);
    renderMapping();
  });
  const nameInput = h("input", { type: "text", placeholder: t("tpl_name") });
  const save = h("button", { type: "button", class: "small-button" }, t("tpl_save"));
  save.addEventListener("click", () => {
    const n = nameInput.value.trim();
    if (!n) { nameInput.focus(); return; }
    const all = savedTemplates(); all[n] = { ...currentTemplate(), title: n };
    store("templates", JSON.stringify(all));
    toast(t("tpl_saved", { name: n }));
    renderMapping();
  });

  const sheetSelect = h("select", {}, ...sheets.map((sh, i) =>
    h("option", { value: String(i), selected: i === state.sheet ? "selected" : null }, `${sh.name} (${sh.rows})`)));
  sheetSelect.addEventListener("change", () => {
    state.sheet = Number(sheetSelect.value);
    applyTemplate(state.inspected.sheets[state.sheet].guess); renderMapping();
  });

  const rows = sheet.headers.map((col, ci) => {
    const select = h("select", {}, ...ROLES.map(r =>
      h("option", { value: r, selected: (state.roles[col] || "") === r ? "selected" : null }, t("role_col_" + (r || "none")))));
    select.addEventListener("change", () => {
      if (select.value) state.roles[col] = select.value; else delete state.roles[col];
      if (["postcode", "city", "id"].includes(select.value))       // only one column each
        for (const other of sheet.headers) if (other !== col && state.roles[other] === select.value) delete state.roles[other];
      renderMapping();
    });
    const examples = sheet.preview.map(r => r[ci]).filter(Boolean).slice(0, 3)
      .map(x => x.length > 40 ? x.slice(0, 38) + "…" : x).join(" · ");
    return h("tr", { class: state.roles[col] ? "used" : null },
      h("td", {}, h("b", {}, col)), h("td", { class: "small muted" }, examples), h("td", {}, select));
  });

  const opt = state.mapOptions;
  const kind = h("select", {}, h("option", { value: "drop" }, t("drop")), h("option", { value: "pickup" }, t("pickup")));
  kind.value = opt.kind;
  kind.addEventListener("change", () => { opt.kind = kind.value; });
  const country = h("select", {}, h("option", { value: "" }, t("country_auto")),
    h("option", { value: "CH" }, "CH"), h("option", { value: "FR" }, "FR"));
  country.value = opt.country;
  country.addEventListener("change", () => { opt.country = country.value; });
  const items = h("input", { type: "number", min: "0", step: "1", value: String(opt.default_items) });
  items.addEventListener("change", () => { opt.default_items = Math.max(0, parseInt(items.value, 10) || 0); renderMapping(); });
  const hasQty = Object.values(state.roles).includes("quantity");
  const hasAddress = Object.values(state.roles).includes("address");

  box.replaceChildren(...[
    h("div", { class: "mapping-head" }, h("h3", {}, t("mapping_title")),
      h("span", { class: "small muted" }, state.file ? state.file.name : "")),
    h("p", { class: "small muted" }, t("mapping_help")),
    h("div", { class: "mapping-bar" },
      h("label", {}, h("span", {}, t("tpl_label")), choose),
      sheets.length > 1 ? h("label", {}, h("span", {}, t("sheet")), sheetSelect) : null,
      h("label", {}, h("span", {}, t("col_type")), kind),
      h("label", {}, h("span", {}, t("country")), country),
      hasQty ? null : h("label", {}, h("span", {}, t("items_per_stop")), items)),
    state.mappingMissing && state.mappingMissing.length
      ? h("div", { class: "warning" }, t("tpl_missing", { cols: state.mappingMissing.join(", ") })) : null,
    table([t("map_column"), t("map_examples"), t("map_use")], rows),
    h("p", { class: hasAddress ? "sample" : "sample bad" },
      hasAddress ? t("map_sample", { stop: sampleStop() }) : t("map_need_address")),
    h("div", { class: "mapping-bar" }, nameInput, save)].filter(Boolean));
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
  if (p.depot) $("depot").value = p.depot;
  if (p.day_start !== undefined) $("day-start").value = hhmm(p.day_start);
  if (p.day_end !== undefined && p.day_end !== null) $("day-end").value = hhmm(p.day_end);
}

// Vehicle types: name, capacity (items), speed (km/h). A tour uses the
// type named in the 'vehicle' column of the tours file.
function vehicleRow(name, spec) {
  const cell = (cls, value, type) => h("td", {}, h("input", {
    class: cls, type, value: value ?? "", min: type === "number" ? "0" : null,
    step: cls === "v-speed" ? "0.5" : (type === "number" ? "1" : null) }));
  const remove = h("button", { type: "button", class: "small-button", title: t("v_remove") }, "✕");
  const count = cell("v-count", spec.count ?? 0, "number");
  count.classList.add("only-new");
  const row = h("tr", {}, cell("v-name", name, "text"), count, cell("v-capacity", spec.capacity, "number"),
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
                  speed_kmh: speed === "" ? null : Number(speed),
                  count: Math.max(0, Math.round(Number(row.querySelector(".v-count").value) || 0)) };
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
    vehicles: readVehicles(),
    depot: $("depot").value.trim(),
    day_start: minutes($("day-start").value) ?? 480,
    day_end: minutes($("day-end").value)
  };
}

function minutes(hhmmText) {           // "08:30" -> 510, "" -> null
  const m = /^(\d{1,2}):(\d{2})$/.exec((hhmmText || "").trim());
  return m ? Number(m[1]) * 60 + Number(m[2]) : null;
}

function showError(text) { $("error").textContent = text; $("error").hidden = !text; }

async function runPlan() {
  showError("");
  const form = new FormData();
  const source = selectedSource();
  try {
    if (state.mode === "new") {
      if (!state.file || !state.inspected) { showError(t("need_stops")); return; }
      if (!currentTemplate().address.length) { showError(t("map_need_address")); return; }
      form.append("orders", state.file);
      form.append("template", JSON.stringify(currentTemplate()));
    } else if (source !== "upload") {
      const name = source;
      for (const kind of ["tours", "orders"]) {
        const text = await (await fetch(`api/examples/${name}/${kind}`)).text();
        form.append(kind, new Blob([text], { type: "text/csv" }), `${name}_${kind}.csv`);
      }
    } else {
      const tours = $("tours-file").files[0], orders = $("orders-file").files[0];
      if (!tours || !orders) { showError(t("need_files")); return; }
      form.append("tours", tours); form.append("orders", orders);
      if (state.needsMapping && state.inspected) form.append("template", JSON.stringify(currentTemplate()));
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
    $("run").disabled = false; modeTexts();
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

// When planning from scratch every stop is "new": no special marking then.
function showNew(s) { return s.is_new && !(state.plan && state.plan.mode === "new"); }

// "14 × Box 50er · 13 × Box 25er"
function quantityText(q) {
  return Object.entries(q || {}).filter(([, n]) => n).map(([k, n]) => `${n} × ${k}`).join(" · ");
}

function kindText(kind) { return kind === "pickup" ? t("pickup") : t("drop"); }

// "Lastenvelo 8/10" or "15 Items" when the tour has no capacity
function loadText(tour) {
  if (tour.capacity === null || tour.capacity === undefined)
    return `${tour.vehicle ? tour.vehicle + " " : ""}${tour.items || 0} ${t("items_short")}`;
  return `${tour.vehicle} ${tour.max_load}/${tour.capacity}`;
}

function popupHtml(tour, number, s) {
  const lines = [`<b>${esc(s.name || s.stop_id)}</b>${s.name ? ` <span style="color:#888">${esc(s.stop_id)}</span>` : ""}` +
                 `${showNew(s) ? " (" + esc(t("new")) + ")" : ""}`,
    esc(s.address),
    `${esc(t("col_tour"))} ${esc(tour.tour_id)}, ${esc(t("stop_word"))} ${number}`,
    `${esc(t("col_arrival"))} ${hhmm(s.arrival)}`];
  if (hasPickups()) lines.push(esc(kindText(s.kind)));
  if (s.items !== null && s.items !== undefined)
    lines.push(`${esc(t("col_items"))}: ${s.items}` + (quantityText(s.quantities) ? ` (${esc(quantityText(s.quantities))})` : ""));
  for (const [k, v] of Object.entries(s.info || {}))
    lines.push(`<span style="color:#666">${esc(k)}:</span> ${esc(v).replace(/\n/g, "<br>")}`);
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
        icon: stopIcon(String(e.number), e.fill, showNew(e.s), false, e.s.late, e.s.kind),
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
  const cards = [["m_tours", state.tours.length],
                 [state.plan.mode === "new" ? "m_stops" : "m_orders", state.plan.n_orders],
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
    const isNew = state.plan.mode === "new";
    parts.push(h("h3", {}, `${t(isNew ? "unplaced_stops" : "unplaced")} (${state.unplaced.length})`),
      table([t(isNew ? "col_stop" : "col_order"), t("col_address"), t("col_type"), t("col_items"), t("col_reason"), ""],
        state.unplaced.map((u, j) => h("tr", {}, h("td", {}, u.name ? h("b", {}, u.name) : null, u.name ? " " : null, u.order_id),
          h("td", {}, u.address || "–"),
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
  const isNew = state.plan.mode === "new";
  const products = [...new Set(state.tours.flatMap(tr => Object.keys(tr.quantities || {})))];
  const saved = state.changes.length > 0;
  const total = { stops: 0, added: 0, late: 0, items: 0, q: {} };
  const rows = state.tours.map((tr, ti) => {
    const f = tourFacts(tr), c = changeOf(tr.tour_id);
    total.stops += f.stops; total.added += f.added; total.late += f.late; total.items += tr.items || 0;
    for (const p of products) total.q[p] = (total.q[p] || 0) + ((tr.quantities || {})[p] || 0);
    const row = h("tr", { class: "clickable" },
      h("td", {}, h("span", { class: "dot", style: `background:${colour(ti)}` }), " ", tr.tour_id),
      h("td", {}, tr.vehicle || "–"),
      h("td", { class: tr.over_capacity ? "late" : null },
        tr.capacity !== null && tr.capacity !== undefined ? `${tr.max_load}/${tr.capacity}`
                                                         : `${tr.items || 0} ${t("items_short")}`),
      ...products.map(p => h("td", {}, (tr.quantities || {})[p] || 0)),
      h("td", {}, hhmm(tr.start)), h("td", {}, hhmm(tr.end)), h("td", {}, `${tr.duration} min`),
      h("td", {}, f.stops), isNew ? null : h("td", {}, f.added || ""),
      h("td", { class: f.late ? "late" : null }, f.late || ""),
      h("td", {}, f.roundTrip ? t("end_depot") : t("end_connection")),
      saved ? h("td", {}, c && c.saved ? `−${c.saved} min` : "") : null);
    row.addEventListener("click", () => selectTour(tr.tour_id));
    return row;
  });
  rows.push(h("tr", { class: "total" }, h("td", {}, t("total")), h("td", {}),
    h("td", {}, `${total.items} ${t("items_short")}`),
    ...products.map(p => h("td", {}, total.q[p])),
    h("td", {}), h("td", {}), h("td", {}), h("td", {}, total.stops),
    isNew ? null : h("td", {}, total.added || ""), h("td", {}, total.late || ""), h("td", {}),
    saved ? h("td", {}) : null));
  const head = [t("col_tour"), t("v_vehicle"), t("col_load"), ...products, t("col_start"), t("col_end"),
                t("col_duration"), t("col_stops"), isNew ? null : t("col_new"), t("late"),
                t("col_tour_end"), saved ? t("col_saved") : null].filter(x => x !== null);
  return h("div", { class: "panel" }, table(head, rows),
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
    card(t("col_stops"), f.stops, f.added && state.plan.mode !== "new" ? t("n_new", { n: f.added }) : ""),
    card(t("col_tour_end"), f.roundTrip ? t("end_depot") : t("end_connection"),
         f.roundTrip ? "" : `${hhmm(f.last.arrival)}` +
           (f.last.tw_end !== null && f.last.tw_end !== undefined ? ` (${t("until")} ${hhmm(f.last.tw_end)})` : ""),
         f.last.late),
  ];
  if (Object.keys(tr.quantities || {}).length)
    cards.push(card(t("products"), quantityText(tr.quantities) || "0", `${tr.items || 0} ${t("items_short")}`));
  if (f.late) cards.push(card(t("late"), f.late, t("stops_late"), true));
  if (c) cards.push(card(t("optimization"), `${c.before} → ${c.after} min`, t("n_moved", { n: c.moved })));

  let number = 0;
  const rows = tr.stops.map((s, k) => {
    const depot = (k === 0 || k === tr.stops.length - 1) && !s.is_new;
    const label = depot ? (f.roundTrip ? "D" : (k === 0 ? "S" : "E")) : String(++number);
    const marks = [];
    if (showNew(s)) marks.push(h("span", { class: "tag new" },
      t("new") + (state.deltas[s.stop_id] !== undefined && !state.manual.has(s.stop_id)
        ? ` +${Math.round(state.deltas[s.stop_id])} min` : "")));
    if (state.placed.has(s.stop_id)) marks.push(h("span", { class: "tag" }, t("placed_short")));
    if (state.manual.has(s.stop_id)) marks.push(h("span", { class: "tag" }, t("manual")));
    else if (s.moved && !s.is_new && !depot) marks.push(h("span", { class: "tag" }, t("moved")));
    if (s.late) marks.push(h("span", { class: "tag bad" }, t("late")));
    const window_ = (s.tw_start !== null || s.tw_end !== null)
      ? `${hhmm(s.tw_start) || "…"}–${hhmm(s.tw_end) || "…"}` : "";
    const over = tr.capacity !== null && tr.capacity !== undefined && s.load > tr.capacity;
    const row = h("tr", { class: "clickable" + (showNew(s) ? " is-new" : "") },
      h("td", {}, h("span", { class: "num" + (depot ? " depot" : "") + (s.kind === "pickup" && !depot ? " pickup" : ""),
                              style: `--c:${colour(ti)}` }, label)),
      h("td", {}, h("div", {}, s.name ? h("b", {}, s.name) : null, s.name ? " " : null,
                     h("span", { class: s.name ? "small muted" : null }, depot ? (s.name || t("depot")) : s.stop_id)),
        h("div", { class: "small muted" }, s.address),
        s.info && s.info.floor ? h("div", { class: "small muted" }, s.info.floor) : null),
      h("td", {}, depot ? "" : kindText(s.kind)),
      h("td", { title: quantityText(s.quantities) || null }, s.items ?? "",
        quantityText(s.quantities) ? h("div", { class: "small muted" }, quantityText(s.quantities)) : null),
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
