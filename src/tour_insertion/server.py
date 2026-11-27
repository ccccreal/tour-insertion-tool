"""Web API around the planning core, and the web page (web/).

Run locally:   uv sync --extra server && uv run tour-insert-server
               then open http://localhost:8000

Endpoints (all JSON unless noted):

  GET  /api/health                  "ok"
  GET  /api/profiles                default settings per customer/city
  GET  /api/examples                names of the example data sets
  GET  /api/examples/{name}/{kind}  CSV text, kind = tours | orders
  GET  /api/templates/{kind}        CSV template to download
  POST /api/plan                    form: tours (file, optional), orders
                                    (file), settings (JSON text), template
                                    (JSON text, optional) -> full result.
                                    Without tours: plan from scratch.
  POST /api/stops                   form: orders (stop list), settings, template
                                    -> geocoded stops, start/end, problems
  POST /api/inspect                 form: file -> sheets, columns, preview
                                    and a guessed template (Excel import)
  POST /api/export/{fmt}            {settings, tours, unplaced, lang, tour_id?}
                                    -> tours.xlsx (one sheet per tour) or
                                    tours.pdf (driver sheets)
  GET  /api/plan-examples           stop lists for planning from scratch
  GET  /api/plan-examples/{name}/{part}  part = stops (file) | template
  POST /api/schedule                {settings, tours, unplaced} -> times,
                                    late stops and CSV for tours in a given
                                    order (used after drag & drop)

The server keeps no state between requests: the browser holds the current
plan and sends it back when it changes.

Optional password: set TOOL_PASSWORD; the browser then asks for it (HTTP
Basic auth, any user name). Geocoding cache: TOOL_GEOCODE_CACHE (default
.geocode_cache.json in the working directory).
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
from collections.abc import Callable
from dataclasses import asdict, fields, replace
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from tour_insertion.csv_io import (format_time, geocoding_to_csv, parse_kind, read_rows_text,
                                   unplaced_from_row,
                                   schedules_to_csv, skipped_to_csv,
                                   unplaced_to_csv)
from tour_insertion.export import tours_pdf, tours_xlsx
from tour_insertion.feasibility import late_stops
from tour_insertion.load import load_after, max_load, over_capacity
from tour_insertion.geocoding import Geocoder
from tour_insertion.model import Schedule, Stop, Tour
from tour_insertion.pipeline import (DEFAULT_VEHICLES, InputError, RunResult, Settings,
                                     apply_vehicles, make_geocoder, plan_new, prepare_stops,
                                     run)
from tour_insertion.stop_import import REASON_NO_ADDRESS, Template, inspect
from tour_insertion.stop_import import order_rows as import_rows
from tour_insertion.planner import Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel

ROOT = Path(os.environ.get("TOOL_ROOT", Path(__file__).resolve().parents[2]))
WEB = ROOT / "web"

#: Example data sets offered in the web page: name -> (tours, orders)
EXAMPLES = {
    "winterthur": ("winterthur/tours.csv", "winterthur/orders.csv"),
    "zuerich": ("zuerich/tours_zuerich.csv", "zuerich/orders_zuerich.csv"),
    "lyon": ("lyon/tours.csv", "lyon/orders.csv"),
    "paris": ("paris/tours_paris.csv", "paris/orders_paris.csv"),
}
#: Where the example tours start and end (set in the page with the example)
EXAMPLE_DEPOTS = {
    "winterthur": "47.5000, 8.7236",
    "zuerich": "47.3779, 8.5403",
    "lyon": "Place Charles Béraudier, 69003 Lyon",
    "paris": "1 Bd du Palais, 75004 Paris",
    "praxen": "47.3779, 8.5403",
    "standorte": "47.3779, 8.5403",
}
TEMPLATES = {"tours": "lyon/tours.csv", "orders": "lyon/orders.csv"}
#: Stop lists for planning from scratch: examples/<name>/stops.xlsx + template.json
PLAN_EXAMPLES = ("praxen", "standorte")
MAX_UPLOAD_BYTES = 2_000_000


# --------------------------------------------------------------------------
# JSON <-> objects
# --------------------------------------------------------------------------

def settings_from_json(data: dict | None) -> Settings:
    """Settings from a dict; unknown keys are ignored, missing keep defaults."""
    data = data or {}
    known = {f.name for f in fields(Settings)}
    values = {k: v for k, v in data.items() if k in known}
    if "vehicles" in values:
        values["vehicles"] = vehicles_from_json(values["vehicles"])
    try:
        return Settings(**values)
    except TypeError as error:
        raise InputError(f"bad settings: {error}") from error


def vehicles_from_json(data) -> dict:
    """{name: {capacity, speed_kmh}}; empty values = no limit / default speed."""
    if not isinstance(data, dict):
        raise InputError("bad settings: vehicles must be an object")
    out = {}
    for name, spec in data.items():
        name = str(name).strip()
        if not name:
            continue
        spec = spec if isinstance(spec, dict) else {}
        try:
            capacity = spec.get("capacity")
            speed = spec.get("speed_kmh")
            capacity = None if capacity in (None, "") else int(capacity)
            speed = None if speed in (None, "") else float(speed)
        except (TypeError, ValueError) as error:
            raise InputError(f"bad vehicle {name!r}: {error}") from error
        if (capacity is not None and capacity < 0) or (speed is not None and speed <= 0):
            raise InputError(f"bad vehicle {name!r}: capacity >= 0 and speed > 0 needed")
        try:
            count = int(spec.get("count") or 0)
        except (TypeError, ValueError) as error:
            raise InputError(f"bad vehicle {name!r}: count must be a whole number") from error
        out[name] = {"capacity": capacity, "speed_kmh": speed, "count": max(count, 0)}
    return out


def stop_from_json(d: dict) -> Stop:
    try:
        return Stop(stop_id=str(d["stop_id"]), lat=float(d["lat"]), lon=float(d["lon"]),
                    address=d.get("address") or "", country=d.get("country") or "",
                    tw_start=d.get("tw_start"), tw_end=d.get("tw_end"),
                    service_min=int(d.get("service_min") or 0),
                    is_new=bool(d.get("is_new")),
                    items=None if d.get("items") in (None, "") else int(d["items"]),
                    kind=parse_kind(d.get("kind")), name=str(d.get("name") or ""),
                    quantities=tuple((str(k), int(v)) for k, v in (d.get("quantities") or {}).items()),
                    info=tuple((str(k), str(v)) for k, v in (d.get("info") or {}).items()),
                    role=str(d.get("role") or "") if d.get("role") in ("start", "end") else "")
    except (KeyError, TypeError, ValueError) as error:
        raise InputError(f"bad stop {d!r}: {error}") from error


def tours_from_json(data: list | None, settings: Settings
                    ) -> tuple[dict[str, Tour], set[str], CrowFlyModel, dict]:
    """Tours as sent by the page; capacity and speed come from the vehicle
    types in the settings (same rules as for a planning run)."""
    tours, moved = {}, set()
    for t in data or []:
        stops = tuple(stop_from_json(d) for d in t.get("stops") or [])
        moved |= {d["stop_id"] for d in t.get("stops") or [] if d.get("moved")}
        tour_id = str(t.get("tour_id"))
        tours[tour_id] = Tour(tour_id, stops, vehicle=str(t.get("vehicle") or ""),
                              start=None if settings.start_at_depot else settings.day_start)
    tours, model, models = apply_vehicles(tours, settings)
    return tours, moved, model, models


def stop_json(stop: Stop) -> dict:
    """A stop without times (see schedule_json for a stop in a tour)."""
    return {
        "stop_id": stop.stop_id, "address": stop.address,
        "country": stop.country, "lat": stop.lat, "lon": stop.lon,
        "tw_start": stop.tw_start, "tw_end": stop.tw_end,
        "service_min": stop.service_min, "is_new": stop.is_new,
        "items": stop.items, "kind": stop.kind,
        "name": stop.name, "quantities": dict(stop.quantities), "role": stop.role,
        "info": dict(stop.info),
    }


def fixed_tours_from_json(data: list | None) -> dict[str, Tour]:
    """Tours planned by hand in the page: customer stops in order + vehicle."""
    tours = {}
    for t in data or []:
        tour_id = str(t.get("tour_id") or "").strip()
        if not tour_id or tour_id in tours:
            raise InputError(f"tours planned by hand need unique names, got {tour_id!r}")
        stops = tuple(stop_from_json(d) for d in t.get("stops") or [] if not d.get("role"))
        tours[tour_id] = Tour(tour_id, stops, vehicle=str(t.get("vehicle") or ""))
    return tours


def schedule_json(s: Schedule, tolerance_min: int,
                  moved: frozenset[str] = frozenset()) -> dict:
    late = late_stops(s, tolerance_min)
    stops = [t.stop for t in s.times]
    loads = load_after(stops)
    return {
        "tour_id": s.tour.tour_id,
        "vehicle": s.tour.vehicle,
        "capacity": s.tour.capacity,
        "max_load": max_load(stops),
        "over_capacity": over_capacity(s.tour),
        "items": sum(t.stop.items or 0 for t in s.times),
        "quantities": totals(t.stop for t in s.times),
        "start": s.times[0].arrival if s.times else None,
        "end": s.end,
        "duration": s.duration,
        "stops": [{
            **stop_json(t.stop), "load": load,
            "arrival": t.arrival, "departure": t.departure,
            "arrival_hhmm": format_time(t.arrival), "slack": t.slack,
            "late": t.stop.stop_id in late, "moved": t.stop.stop_id in moved,
        } for t, load in zip(s.times, loads)],
    }


def unplaced_json(u: Unplaced) -> dict:
    d = {"order_id": u.order_id, "address": u.address, "reason": u.reason,
         "tour_id": u.tour_id, "tw_start": u.tw_start, "tw_end": u.tw_end,
         "lat": None, "lon": None, "country": u.country,
         "service_min": u.service_min, "items": u.items, "kind": u.kind,
         "name": u.name, "quantities": dict(u.quantities), "info": dict(u.info)}
    if u.stop is not None:
        d.update(lat=u.stop.lat, lon=u.stop.lon, country=u.stop.country,
                 service_min=u.stop.service_min, items=u.stop.items, kind=u.stop.kind)
    return d


def unplaced_from_json(d: dict) -> Unplaced:
    stop = None
    if d.get("lat") is not None and d.get("lon") is not None:
        stop = stop_from_json({**d, "stop_id": d.get("order_id"), "is_new": True})
    return Unplaced(str(d.get("order_id") or ""), d.get("address") or "",
                    d.get("reason") or "", stop, d.get("tw_start"), d.get("tw_end"),
                    d.get("tour_id") or "", d.get("service_min"), d.get("items"),
                    d.get("country") or "", parse_kind(d.get("kind")),
                    str(d.get("name") or ""),
                    tuple((str(k), int(v)) for k, v in (d.get("quantities") or {}).items()),
                    tuple((str(k), str(v)) for k, v in (d.get("info") or {}).items()))


def totals(stops) -> dict[str, int]:
    """Quantity per product over some stops, e.g. {"Box 50er": 14}."""
    out: dict[str, int] = {}
    for s in stops:
        for name, n in s.quantities:
            out[name] = out.get(name, 0) + n
    return out


def result_json(r: RunResult, settings: Settings, mode: str = "insert") -> dict:
    moved = r.moved
    return {
        "mode": mode,
        "settings": asdict(settings),
        "tours": [schedule_json(s, settings.tolerance_min, moved) for s in r.schedules],
        "inserted": [{"order_id": o.stop_id, "tour_id": o.tour_id,
                      "delta": round(o.delta, 1)} for o in r.inserted],
        "unplaced": [unplaced_json(u) for u in r.unplaced],
        "skipped": [unplaced_json(u) for u in r.skipped],
        "geocoding": [asdict(g) for g in r.geocoding],
        "changes": [{"tour_id": c.tour_id, "before": c.minutes_before,
                     "after": c.minutes_after, "saved": c.saved,
                     "moved": len(c.moved)} for c in r.changes],
        "n_orders": r.n_orders,
        "csv": {"tours": schedules_to_csv(r.schedules, moved),
                "unplaced": unplaced_to_csv(r.unplaced),
                "skipped": skipped_to_csv(r.skipped),
                "geocoding": geocoding_to_csv(r.geocoding)},
    }


# --------------------------------------------------------------------------
# App
# --------------------------------------------------------------------------

def create_app(geocoder_factory: Callable[[Settings], Geocoder] | None = None) -> FastAPI:
    """The FastAPI app. `geocoder_factory` is replaced in tests."""
    cache_path = Path(os.environ.get("TOOL_GEOCODE_CACHE", ".geocode_cache.json"))
    geocoders: dict[float, Geocoder] = {}

    def default_factory(settings: Settings) -> Geocoder:
        # one geocoder (and cache) per score threshold, shared by all requests
        if settings.min_score not in geocoders:
            geocoders[settings.min_score] = make_geocoder(settings, cache_path)
        return geocoders[settings.min_score]

    get_geocoder = geocoder_factory or default_factory
    password = os.environ.get("TOOL_PASSWORD")
    app = FastAPI(title="Tour insertion tool", docs_url="/api/docs", redoc_url=None)

    @app.middleware("http")
    async def always_revalidate(request: Request, call_next):
        """The browser must ask every time whether the page files changed
        (cheap: unchanged files come back as 304 without content). Without
        this, after an update it may mix old and new files."""
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    if password:
        @app.middleware("http")
        async def require_password(request: Request, call_next):
            header = request.headers.get("authorization", "")
            ok = False
            if header.startswith("Basic "):
                try:
                    _, _, given = base64.b64decode(header[6:]).decode().partition(":")
                    ok = secrets.compare_digest(given, password)
                except (ValueError, UnicodeDecodeError):
                    ok = False
            if not ok:
                return Response(status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="tour-insert"'})
            return await call_next(request)

    @app.exception_handler(InputError)
    async def input_error(_: Request, error: InputError):
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.get("/api/health")
    def health():
        return "ok"

    @app.get("/api/profiles")
    def profiles():
        data = json.loads((ROOT / "profiles.json").read_text(encoding="utf-8"))
        data.pop("_comment", None)
        for profile in data.values():          # profiles without own vehicles
            profile.setdefault("vehicles", DEFAULT_VEHICLES)
        return data

    @app.get("/api/examples")
    def examples():
        return [name for name, (t, o) in EXAMPLES.items()
                if (ROOT / "examples" / t).exists() and (ROOT / "examples" / o).exists()]

    @app.get("/api/examples/{name}/{kind}", response_class=PlainTextResponse)
    def example(name: str, kind: str):
        if name not in EXAMPLES or kind not in ("tours", "orders"):
            raise HTTPException(404)
        path = ROOT / "examples" / EXAMPLES[name][0 if kind == "tours" else 1]
        if not path.exists():
            raise HTTPException(404)
        return path.read_text(encoding="utf-8-sig")

    @app.get("/api/templates/{kind}")
    def template(kind: str):
        if kind not in TEMPLATES:
            raise HTTPException(404)
        return FileResponse(ROOT / "examples" / TEMPLATES[kind], media_type="text/csv",
                            filename=f"{kind}_template.csv")

    @app.get("/api/example-depots")
    def example_depots():
        return EXAMPLE_DEPOTS

    @app.get("/api/plan-examples")
    def plan_examples():
        out = []
        for name in PLAN_EXAMPLES:
            folder = ROOT / "examples" / name
            if (folder / "stops.xlsx").exists() and (folder / "template.json").exists():
                t = json.loads((folder / "template.json").read_text(encoding="utf-8"))
                out.append({"name": name, "title": t.get("title", name), "template": t})
        return out

    @app.get("/api/plan-examples/{name}/{part}")
    def plan_example(name: str, part: str):
        if name not in PLAN_EXAMPLES or part not in ("stops", "template"):
            raise HTTPException(404)
        folder = ROOT / "examples" / name
        if part == "template":
            return json.loads((folder / "template.json").read_text(encoding="utf-8"))
        return FileResponse(folder / "stops.xlsx", filename=f"{name}.xlsx",
                            media_type="application/vnd.openxmlformats-officedocument."
                                       "spreadsheetml.sheet")

    @app.post("/api/inspect")
    async def inspect_file(file: UploadFile = File(...)):
        try:
            return await run_in_threadpool(inspect, await read_bytes(file), file.filename or "upload")
        except ValueError as error:
            raise InputError(str(error)) from error

    async def read_bytes(upload: UploadFile) -> bytes:
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise InputError(f"{upload.filename}: file too large (max 2 MB)")
        return raw

    async def read_upload(upload: UploadFile) -> str:
        raw = await read_bytes(upload)
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InputError(f"{upload.filename}: not UTF-8 text. Save the CSV as "
                             "UTF-8 and try again.") from error

    def with_positions(rows, no_address, positions: str):
        """Stops placed by hand on the map: {order_id: [lat, lon]}."""
        try:
            known = json.loads(positions) if positions.strip() else {}
            known = {str(k): (float(v[0]), float(v[1])) for k, v in known.items()}
        except (json.JSONDecodeError, TypeError, ValueError, IndexError) as error:
            raise InputError(f"bad positions: {error}") from error
        for row in rows + list(no_address):
            if row.get("order_id") in known:
                row["lat"], row["lon"] = (str(x) for x in known[row["order_id"]])
                row["address"] = row.get("address") or "–"
        return (rows + [r for r in no_address if r.get("order_id") in known],
                [r for r in no_address if r.get("order_id") not in known])

    async def order_rows_of(orders: UploadFile, spec: Template | None):
        name = orders.filename or "orders"
        if spec is not None:
            try:
                return import_rows(await read_bytes(orders), name, spec)
            except ValueError as error:
                raise InputError(str(error)) from error
        if name.lower().endswith((".xlsx", ".xlsm", ".xls")):
            raise InputError(f"{name}: for an Excel file, set up the columns first")
        return read_rows_text(await read_upload(orders), name), []

    @app.post("/api/stops")
    async def show_stops(orders: UploadFile = File(...), settings: str = Form("{}"),
                         template: str = Form("")):
        """Geocode a stop list, nothing planned yet: to show the stops on the
        map and plan tours by hand."""
        try:
            s = settings_from_json(json.loads(settings))
            spec = Template.from_json(json.loads(template)) if template.strip() else None
        except (json.JSONDecodeError, ValueError) as error:
            raise InputError(f"bad settings or template: {error}") from error
        rows, no_address = await order_rows_of(orders, spec)
        p = await run_in_threadpool(prepare_stops, rows, s, get_geocoder(s), no_address)
        return {"settings": asdict(s), "stops": [stop_json(o) for o in p.orders],
                # start and end as the planning will use them (times included)
                "start": stop_json(replace(p.start, role="start", tw_start=s.day_start,
                                           tw_end=None, service_min=0)) if p.start else None,
                "end": stop_json(replace(p.end, role="end", tw_start=None, tw_end=s.day_end,
                                         service_min=0)) if p.end else None,
                "unplaced": [unplaced_json(u) for u in p.unplaced],
                "geocoding": [asdict(g) for g in p.geocoding],
                "csv": {"geocoding": geocoding_to_csv(p.geocoding)}}

    @app.post("/api/plan")
    async def plan(orders: UploadFile = File(...), tours: UploadFile | None = File(None),
                   settings: str = Form("{}"), template: str = Form(""),
                   fixed_tours: str = Form(""), positions: str = Form("")):
        """With tours: insert the orders. Without: plan tours from scratch.
        With a template the orders file may have any layout (Excel)."""
        try:
            s = settings_from_json(json.loads(settings))
            spec = Template.from_json(json.loads(template)) if template.strip() else None
            fixed = fixed_tours_from_json(json.loads(fixed_tours)) if fixed_tours.strip() else {}
        except json.JSONDecodeError as error:
            raise InputError(f"bad settings or template: {error}") from error
        except ValueError as error:
            raise InputError(f"bad template: {error}") from error
        name = orders.filename or "orders"
        rows, no_address = await order_rows_of(orders, spec)
        rows, no_address = with_positions(rows, no_address, positions)
        # Planning (and geocoding many addresses) can take a while: run it in
        # a worker thread, so the server keeps answering other requests
        # (the page itself, other users) in the meantime.
        if tours is None or not tours.filename:
            result = await run_in_threadpool(plan_new, rows, s, get_geocoder(s), no_address, fixed)
            out = result_json(result, s, "new")
            out["locked"] = list(fixed)
            return out
        tour_rows = read_rows_text(await read_upload(tours), tours.filename or "tours")
        result = await run_in_threadpool(run, tour_rows, rows, s, get_geocoder(s))
        result.unplaced = [unplaced_from_row(r, "order_id", REASON_NO_ADDRESS, s.service_min)
                           for r in no_address] + result.unplaced
        return result_json(result, s)

    @app.post("/api/schedule")
    def schedule(body: dict):
        s = settings_from_json(body.get("settings"))
        tours, moved, model, models = tours_from_json(body.get("tours"), s)
        schedules = [forward_pass(t, models.get(i, model)) for i, t in tours.items()]
        unplaced = [unplaced_from_json(d) for d in body.get("unplaced") or []]
        return {
            "tours": [schedule_json(sc, s.tolerance_min, frozenset(moved))
                      for sc in schedules],
            "csv": {"tours": schedules_to_csv(schedules, frozenset(moved)),
                    "unplaced": unplaced_to_csv(unplaced)},
        }

    @app.post("/api/insert")
    def insert(body: dict):
        """Insert ONE stop at its cheapest feasible position.

        body: {settings, tours, stop: {stop_id, lat, lon, ...},
               tour_id: only this tour (optional), unplaced: [...], skipped: [...]}
        Used when the dispatcher places a stop by clicking on the map.
        Returns {"inserted": false} if no tour has a feasible position.
        """
        s = settings_from_json(body.get("settings"))
        tours, moved, model, models = tours_from_json(body.get("tours"), s)
        new_stop = stop_from_json(body.get("stop") or {})
        only = body.get("tour_id")
        candidates = {only: tours[only]} if only in tours else tours
        try:
            result = insert_orders(candidates, [new_stop], model, models=models,
                                   **s.insertion_options)
        except ValueError as error:                   # e.g. id already in a tour
            raise InputError(str(error)) from error
        if not result.inserted:
            reason = result.unplaced[0].reason if result.unplaced else ""
            return {"inserted": False, "reason": reason}
        tours.update(result.tours)
        moved.add(new_stop.stop_id)
        schedules = [forward_pass(tours[t], models.get(t, model)) for t in tours]
        unplaced = [unplaced_from_json(d) for d in body.get("unplaced") or []]
        skipped = [unplaced_from_json(d) for d in body.get("skipped") or []]
        option = result.inserted[0]
        return {
            "inserted": True, "tour_id": option.tour_id, "delta": round(option.delta, 1),
            "tours": [schedule_json(sc, s.tolerance_min, frozenset(moved)) for sc in schedules],
            "csv": {"tours": schedules_to_csv(schedules, frozenset(moved)),
                    "unplaced": unplaced_to_csv(unplaced),
                    "skipped": skipped_to_csv(skipped)},
        }

    @app.post("/api/export/{fmt}")
    def export(fmt: str, body: dict):
        if fmt not in ("xlsx", "pdf"):
            raise HTTPException(404)
        s = settings_from_json(body.get("settings"))
        tours, _, model, models = tours_from_json(body.get("tours"), s)
        only = body.get("tour_id")
        if only:
            tours = {only: tours[only]} if only in tours else {}
        schedules = [forward_pass(t, models.get(i, model)) for i, t in tours.items()]
        lang = str(body.get("lang") or "de")
        name = f"tour_{only}" if only else "tours"
        name = re.sub(r"[^A-Za-z0-9_.-]+", "_", name)
        if fmt == "xlsx":
            unplaced = [] if only else [unplaced_from_json(d) for d in body.get("unplaced") or []]
            data = tours_xlsx(schedules, unplaced, lang)
            media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else:
            data = tours_pdf(schedules, lang)
            media = "application/pdf"
        return Response(data, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}.{fmt}"'})

    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def main() -> None:
    """Entry point `tour-insert-server`: serve on http://localhost:8000."""
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))


app = create_app()
