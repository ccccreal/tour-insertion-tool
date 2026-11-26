"""Web API around the planning core, and the web page (web/).

Run locally:   uv sync --extra server && uv run tour-insert-server
               then open http://localhost:8000

Endpoints (all JSON unless noted):

  GET  /api/health                  "ok"
  GET  /api/profiles                default settings per customer/city
  GET  /api/examples                names of the example data sets
  GET  /api/examples/{name}/{kind}  CSV text, kind = tours | orders
  GET  /api/templates/{kind}        CSV template to download
  POST /api/plan                    form: tours (file), orders (file),
                                    settings (JSON text) -> full result
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
import secrets
from collections.abc import Callable
from dataclasses import asdict, fields
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from tour_insertion.csv_io import (format_time, geocoding_to_csv, parse_kind, read_rows_text,
                                   schedules_to_csv, skipped_to_csv,
                                   unplaced_to_csv)
from tour_insertion.feasibility import late_stops
from tour_insertion.load import load_after, max_load, over_capacity
from tour_insertion.geocoding import Geocoder
from tour_insertion.model import Schedule, Stop, Tour
from tour_insertion.pipeline import (DEFAULT_VEHICLES, InputError, RunResult, Settings,
                                     apply_vehicles, make_geocoder, run)
from tour_insertion.planner import Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel

ROOT = Path(os.environ.get("TOOL_ROOT", Path(__file__).resolve().parents[2]))
WEB = ROOT / "web"

#: Example data sets offered in the web page: name -> (tours, orders)
EXAMPLES = {
    "winterthur": ("winterthur/tours.csv", "winterthur/orders.csv"),
    "anschluss": ("anschluss/tours.csv", "anschluss/orders.csv"),
    "fahrzeuge": ("fahrzeuge/tours.csv", "fahrzeuge/orders.csv"),
    "zuerich": ("zuerich/tours_zuerich.csv", "zuerich/orders_zuerich.csv"),
    "lyon": ("lyon/tours.csv", "lyon/orders.csv"),
    "paris": ("paris/tours_paris_2.csv", "paris/orders_paris_2.csv"),
}
TEMPLATES = {"tours": "lyon/tours.csv", "orders": "lyon/orders.csv"}
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
        out[name] = {"capacity": capacity, "speed_kmh": speed}
    return out


def stop_from_json(d: dict) -> Stop:
    try:
        return Stop(stop_id=str(d["stop_id"]), lat=float(d["lat"]), lon=float(d["lon"]),
                    address=d.get("address") or "", country=d.get("country") or "",
                    tw_start=d.get("tw_start"), tw_end=d.get("tw_end"),
                    service_min=int(d.get("service_min") or 0),
                    is_new=bool(d.get("is_new")),
                    items=None if d.get("items") in (None, "") else int(d["items"]),
                    kind=parse_kind(d.get("kind")))
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
        tours[tour_id] = Tour(tour_id, stops, vehicle=str(t.get("vehicle") or ""))
    tours, model, models = apply_vehicles(tours, settings)
    return tours, moved, model, models


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
        "start": s.times[0].arrival if s.times else None,
        "end": s.end,
        "duration": s.duration,
        "stops": [{
            "stop_id": t.stop.stop_id, "address": t.stop.address,
            "country": t.stop.country, "lat": t.stop.lat, "lon": t.stop.lon,
            "tw_start": t.stop.tw_start, "tw_end": t.stop.tw_end,
            "service_min": t.stop.service_min, "is_new": t.stop.is_new,
            "items": t.stop.items, "kind": t.stop.kind, "load": load,
            "arrival": t.arrival, "departure": t.departure,
            "arrival_hhmm": format_time(t.arrival), "slack": t.slack,
            "late": t.stop.stop_id in late, "moved": t.stop.stop_id in moved,
        } for t, load in zip(s.times, loads)],
    }


def unplaced_json(u: Unplaced) -> dict:
    d = {"order_id": u.order_id, "address": u.address, "reason": u.reason,
         "tour_id": u.tour_id, "tw_start": u.tw_start, "tw_end": u.tw_end,
         "lat": None, "lon": None, "country": u.country,
         "service_min": u.service_min, "items": u.items, "kind": u.kind}
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
                    d.get("country") or "", parse_kind(d.get("kind")))


def result_json(r: RunResult, settings: Settings) -> dict:
    moved = r.moved
    return {
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

    async def read_upload(upload: UploadFile) -> str:
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise InputError(f"{upload.filename}: file too large (max 2 MB)")
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise InputError(f"{upload.filename}: not UTF-8 text. Save the CSV as "
                             "UTF-8 and try again.") from error

    @app.post("/api/plan")
    async def plan(tours: UploadFile = File(...), orders: UploadFile = File(...),
                   settings: str = Form("{}")):
        try:
            s = settings_from_json(json.loads(settings))
        except json.JSONDecodeError as error:
            raise InputError(f"bad settings: {error}") from error
        tour_rows = read_rows_text(await read_upload(tours), tours.filename or "tours")
        order_rows = read_rows_text(await read_upload(orders), orders.filename or "orders")
        result = run(tour_rows, order_rows, s, get_geocoder(s))
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
                                   fixed_first=not s.open_start, fixed_last=not s.open_end,
                                   tolerance_min=s.tolerance_min)
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

    if WEB.exists():
        app.mount("/", StaticFiles(directory=WEB, html=True), name="web")
    return app


def main() -> None:
    """Entry point `tour-insert-server`: serve on http://localhost:8000."""
    import uvicorn
    uvicorn.run(create_app(), host=os.environ.get("HOST", "127.0.0.1"),
                port=int(os.environ.get("PORT", "8000")))


app = create_app()
