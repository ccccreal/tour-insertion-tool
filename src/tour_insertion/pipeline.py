"""The whole run in one function: rows in, results out.

Used by the command line (cli.py) and the web app (app.py), so both always
compute exactly the same thing.

    rows -> geocode missing coordinates
         -> tours (stops without coordinates skipped) + orders
         -> start and end of every tour from the settings
         -> [optimize existing tours] -> insert orders -> [optimize again]
         -> schedules

Start and end of a tour
-----------------------
The input files hold customer stops only. Where and when tours start and
end is a setting, the same for all tours:

    start_at_depot  True: start at the depot at day_start
                    False: start at the first customer at day_start
    end_mode        "depot": back to the depot
                    "place": end at end_place (e.g. a connection point)
                    "open": end at the last customer
    day_end         latest arrival at the end; with an open end, the
                    latest time for every stop

Addresses that cannot be geocoded
---------------------------------
depot / end place      -> error, nothing is computed
stop of a tour         -> skipped: the tour is computed without it,
                          reported in `skipped`
new order              -> not planned, reported in `unplaced`
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field, replace

import re

from tour_insertion.csv_io import orders_from_rows, tours_from_rows, unplaced_from_row
from tour_insertion.geocoding import (NOT_FOUND, FranceGeocoder,
                                      GeocodeResult, Geocoder, SwissGeocoder,
                                      fill_coordinates, needs_geocoding)
from tour_insertion.model import InsertionOption, Schedule, Stop, Tour
from tour_insertion.optimize import improve_tour, moved_stops
from tour_insertion.planner import REASON_NO_TOURS, Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CachedModel, CrowFlyModel, TravelModel


class InputError(ValueError):
    """The input cannot be planned (bad file, depot not found, ...)."""


#: Vehicle types used when nothing else is given. Assumptions: a cargo bike
#: with trailer carries 10 items at 15 km/h (crow-fly x detour), a car 40
#: items (the cautious end of "40-50") at 20 km/h in town incl. parking.
#: "count" (vehicles available) is only used when planning from scratch.
DEFAULT_VEHICLES = {
    "Lastenvelo": {"capacity": 10, "speed_kmh": 15, "count": 4},
    "Auto": {"capacity": 40, "speed_kmh": 20, "count": 1},
}


@dataclass(frozen=True)
class Settings:
    detour_factor: float = 1.35
    speed_m_per_min: float = 250.0
    service_min: int = 3
    tolerance_min: int = 0
    min_score: float = 0.5
    optimize: bool = False
    vehicle_priority: bool = False      # plan_new: vehicle types in table order
    # Vehicle types: name -> {"capacity": items, "speed_kmh": km/h}. A tour
    # gets its type from the 'vehicle' column. Missing capacity = no limit,
    # missing speed = speed_m_per_min. Tours without a vehicle: no limit.
    vehicles: Mapping[str, Mapping[str, float | int | None]] = field(
        default_factory=lambda: {k: dict(v) for k, v in DEFAULT_VEHICLES.items()})
    # Start and end of every tour (see the module docstring). depot and
    # end_place: "address" or "lat, lon". Times in minutes since midnight;
    # day_end None = no limit. Planning from scratch also uses "count" in
    # `vehicles` (how many vehicles of each type are available).
    depot: str = ""
    start_at_depot: bool = True
    end_mode: str = "depot"
    end_place: str = ""
    day_start: int = 480
    day_end: int | None = None

    @property
    def insertion_options(self) -> dict:
        """fixed_first / fixed_last for insertion and optimizing."""
        return {"fixed_first": self.start_at_depot, "fixed_last": self.end_mode != "open",
                "tolerance_min": self.tolerance_min}


def apply_vehicles(tours: Mapping[str, Tour], settings: Settings
                   ) -> tuple[dict[str, Tour], CrowFlyModel, dict[str, TravelModel]]:
    """Set each tour's capacity from its vehicle type and build the travel
    model per tour (vehicles may have their own speed).

    Returns (tours, default model, model per tour_id).
    """
    model = CrowFlyModel(settings.detour_factor, settings.speed_m_per_min)
    unknown = sorted({t.vehicle for t in tours.values()
                      if t.vehicle and t.vehicle not in settings.vehicles})
    if unknown:
        known = ", ".join(sorted(settings.vehicles)) or "none defined"
        raise InputError(f"unknown vehicle type(s): {', '.join(unknown)} "
                         f"(known: {known})")
    out, models = {}, {}
    for tour_id, tour in tours.items():
        spec = settings.vehicles.get(tour.vehicle) or {}
        capacity = spec.get("capacity")
        out[tour_id] = replace(tour, capacity=None if capacity in (None, "") else int(capacity))
        speed = spec.get("speed_kmh")
        if speed not in (None, ""):
            models[tour_id] = CrowFlyModel(settings.detour_factor, float(speed) * 1000 / 60)
    return out, model, models


@dataclass(frozen=True)
class TourChange:
    """What optimizing changed in one tour (compared with insertion only)."""

    tour_id: str
    minutes_before: int
    minutes_after: int
    moved: frozenset[str]

    @property
    def saved(self) -> int:
        return self.minutes_before - self.minutes_after


@dataclass
class RunResult:
    tours: dict[str, Tour]                 # input tours (skipped stops removed)
    orders: list[Stop]
    inserted: list[InsertionOption]
    unplaced: list[Unplaced]               # new orders not planned
    schedules: list[Schedule]              # the final tours with times
    geocoding: list[GeocodeResult] = field(default_factory=list)
    skipped: list[Unplaced] = field(default_factory=list)   # tour stops left out
    changes: list[TourChange] = field(default_factory=list)  # only with optimize

    @property
    def n_orders(self) -> int:
        """All new orders, including those that could not be geocoded."""
        return len(self.inserted) + len(self.unplaced)

    @property
    def moved(self) -> frozenset[str]:
        return frozenset().union(*(c.moved for c in self.changes))


def make_geocoder(settings: Settings, cache_path=None) -> Geocoder:
    return Geocoder(cache_path, {
        "FR": FranceGeocoder(min_score=settings.min_score),
        "CH": SwissGeocoder()})


def _reason(result: GeocodeResult | None) -> str:
    if result is None or result.status == NOT_FOUND:
        return "geocode_not_found"
    return "geocode_uncertain"


def geocode_rows(tour_rows: list[dict], order_rows: list[dict],
                 geocoder: Geocoder, default_service: int = 3
                 ) -> tuple[list[GeocodeResult], list[Unplaced], list[Unplaced]]:
    """Fill missing coordinates (rows are changed in place).

    Returns (all results, unplaced orders, skipped tour stops). Rows that
    still have no coordinates afterwards are skipped / unplaced.
    """
    if not any(needs_geocoding(r) for r in tour_rows + order_rows):
        return [], [], []
    try:
        tour_results = fill_coordinates(tour_rows, geocoder)
        order_results = fill_coordinates(order_rows, geocoder)
    finally:
        geocoder.save()
    by_query = {r.query: r for r in tour_results + order_results}

    skipped = [unplaced_from_row(r, "stop_id", _reason(by_query.get(r["address"].strip())),
                                 default_service, (r.get("tour_id") or "").strip())
               for r in tour_rows if needs_geocoding(r)]
    unplaced = [unplaced_from_row(r, "order_id", _reason(by_query.get(r["address"].strip())),
                                  default_service)
                for r in order_rows if needs_geocoding(r)]
    return tour_results + order_results, unplaced, skipped


def run(tour_rows: list[dict], order_rows: list[dict], settings: Settings,
        geocoder: Geocoder | None = None) -> RunResult:
    """Insert orders into tours. `rows` are dicts as read from the CSV
    files; rows without lat/lon are geocoded (and changed in place)."""
    if not tour_rows:
        raise InputError("the tours file is empty")
    geocoder = geocoder or make_geocoder(settings)
    start, end, end_results = tour_ends(settings, geocoder)
    try:
        geo_results, geo_unplaced, skipped = geocode_rows(
            tour_rows, order_rows, geocoder, settings.service_min)
        tours = tours_from_rows([r for r in tour_rows if not needs_geocoding(r)],
                                settings.service_min)
        orders = orders_from_rows([r for r in order_rows if not needs_geocoding(r)],
                                  settings.service_min)
    except InputError:
        raise
    except OSError as error:                       # network / service down
        raise InputError(f"geocoding service not reachable: {error}") from error
    except (KeyError, ValueError) as error:
        raise InputError(str(error)) from error

    tours = {t: frame_tour(tour, settings, start, end) for t, tour in tours.items()}
    orders = [cap_window(o, settings) for o in orders]
    geo_results = end_results + geo_results
    tours, model, models = apply_vehicles(tours, settings)

    def model_of(tour_id: str) -> TravelModel:
        return models.get(tour_id, model)

    options = settings.insertion_options
    try:
        plain = insert_orders(tours, orders, model, models=models, **options)
    except ValueError as error:                       # duplicate ids
        raise InputError(str(error)) from error

    if not settings.optimize:
        schedules = [forward_pass(plain.tours[t], model_of(t)) for t in sorted(plain.tours)]
        return RunResult(tours, orders, plain.inserted,
                         geo_unplaced + plain.unplaced, schedules, geo_results,
                         skipped)

    # Optimize the given tours first (a poor plan also misleads WHERE new
    # orders go), insert, then optimize once more with the new stops.
    better = {t: improve_tour(tour, model_of(t), **options) for t, tour in tours.items()}
    result = insert_orders(better, orders, model, models=models, **options)
    final = {t: improve_tour(tour, model_of(t), **options)
             for t, tour in result.tours.items()}

    schedules, changes = [], []
    for t in sorted(final):
        before = forward_pass(plain.tours[t], model_of(t))
        after = forward_pass(final[t], model_of(t))
        schedules.append(after)
        changes.append(TourChange(t, before.duration, after.duration,
                                  moved_stops(plain.tours[t], final[t])))
    return RunResult(tours, orders, result.inserted,
                     geo_unplaced + result.unplaced, schedules, geo_results,
                     skipped, changes)


# --------------------------------------------------------------------------
# Planning from scratch: only a list of stops, no tours yet
# --------------------------------------------------------------------------

COORDINATES = re.compile(r"(-?\d+(?:\.\d+)?)\s*[,; ]\s*(-?\d+(?:\.\d+)?)")


def locate(text: str, what: str, geocoder: Geocoder) -> tuple[Stop, GeocodeResult | None]:
    """A place from a setting: "47.3779, 8.5403" or an address."""
    text = text.strip()
    if not text:
        raise InputError(f"the {what} is missing (address or lat, lon)")
    m = COORDINATES.fullmatch(text)
    if m:
        return Stop(what, float(m.group(1)), float(m.group(2)), address=text), None
    try:
        result = geocoder.geocode(text)
    except OSError as error:
        raise InputError(f"geocoding service not reachable: {error}") from error
    except ValueError as error:
        raise InputError(f"{what}: {error}") from error
    finally:
        geocoder.save()
    if result.status != "ok" or result.lat is None:
        found = f" (found: {result.label})" if result.label else ""
        raise InputError(f"the {what} address could not be found reliably: {text}{found}. "
                         "Check it or give lat, lon.")
    return Stop(what, result.lat, result.lon, address=result.label or text,
                country=result.country), result


def tour_ends(settings: Settings, geocoder: Geocoder
              ) -> tuple[Stop | None, Stop | None, list[GeocodeResult]]:
    """(start place or None, end place or None, geocoding results)."""
    if settings.end_mode not in ("depot", "place", "open"):
        raise InputError(f"end_mode must be depot, place or open, got {settings.end_mode!r}")
    results, depot = [], None
    if settings.start_at_depot or settings.end_mode == "depot":
        depot, r = locate(settings.depot, "depot", geocoder)
        results += [r] if r else []
    end = depot if settings.end_mode == "depot" else None
    if settings.end_mode == "place":
        end, r = locate(settings.end_place, "end place", geocoder)
        results += [r] if r else []
    return (depot if settings.start_at_depot else None), end, results


def frame_tour(tour: Tour, settings: Settings, start: Stop | None, end: Stop | None) -> Tour:
    """The tour with its start and end stops (from the settings) around the
    customer stops. Without a start stop the tour starts at day_start at
    its first customer; with an open end day_end limits every stop."""
    stops = tuple(cap_window(s, settings) for s in tour.stops if not s.role)
    if start is not None:
        stops = (replace(start, stop_id=f"{tour.tour_id} start", role="start",
                         tw_start=settings.day_start, tw_end=None, service_min=0),) + stops
    if end is not None:
        stops += (replace(end, stop_id=f"{tour.tour_id} end", role="end",
                          tw_start=None, tw_end=settings.day_end, service_min=0),)
    return replace(tour, stops=stops,
                   start=None if start is not None else settings.day_start)


def cap_window(stop: Stop, settings: Settings) -> Stop:
    """With an open end there is no end stop to hold day_end: every stop
    must then be served by day_end."""
    if settings.end_mode != "open" or settings.day_end is None:
        return stop
    if stop.tw_end is not None and stop.tw_end <= settings.day_end:
        return stop
    return replace(stop, tw_end=settings.day_end)


def empty_tours(start: Stop | None, end: Stop | None, settings: Settings,
                taken: Mapping[str, int] | None = None) -> dict[str, Tour]:
    """One empty tour per available vehicle. `taken`: vehicles per type
    already used by tours planned by hand. Ids are temporary ("Lastenvelo
    #1"); plan_new gives the used tours their final names."""
    taken = taken or {}
    tours = {}
    for name, spec in settings.vehicles.items():
        try:
            count = int((spec or {}).get("count") or 0)
        except (TypeError, ValueError) as error:
            raise InputError(f"vehicle {name!r}: count must be a whole number") from error
        for i in range(1, max(0, count - taken.get(name, 0)) + 1):
            tour_id = f"{name} #{i}"
            tours[tour_id] = frame_tour(Tour(tour_id, (), vehicle=name), settings, start, end)
    return tours


def customers(tour: Tour) -> list[Stop]:
    return [s for s in tour.stops if not s.role]


@dataclass
class PreparedStops:
    """A stop list made ready for planning (geocoded, checked)."""
    start: Stop | None
    end: Stop | None
    orders: list[Stop]
    unplaced: list[Unplaced]
    geocoding: list[GeocodeResult]


def prepare_stops(order_rows: list[dict], settings: Settings, geocoder: Geocoder | None = None,
                  no_address: list[dict] = ()) -> PreparedStops:
    """Geocode a stop list and the start/end places. Used to show the stops
    on the map before planning, and by plan_new."""
    geocoder = geocoder or make_geocoder(settings)
    start, end, end_results = tour_ends(settings, geocoder)
    try:
        geo_results, geo_unplaced, _ = geocode_rows([], order_rows, geocoder,
                                                    settings.service_min)
        orders = orders_from_rows([r for r in order_rows if not needs_geocoding(r)],
                                  settings.service_min)
    except OSError as error:
        raise InputError(f"geocoding service not reachable: {error}") from error
    except (KeyError, ValueError) as error:
        raise InputError(str(error)) from error
    missing = [unplaced_from_row(r, "order_id", "address_missing", settings.service_min)
               for r in no_address]
    orders = [cap_window(o, settings) for o in orders]
    return PreparedStops(start, end, orders, missing + geo_unplaced, end_results + geo_results)


def plan_new(order_rows: list[dict], settings: Settings, geocoder: Geocoder | None = None,
             no_address: list[dict] = (), fixed: Mapping[str, Tour] | None = None) -> RunResult:
    """Plan tours from scratch: every vehicle starts with an empty tour
    (start -> end), all stops are inserted with the same cheapest insertion
    as for overnight orders, then every tour is improved.

    fixed     tours planned by hand (customer stops in the given order,
              vehicle set). They are kept exactly as they are: nothing is
              added, removed or reordered. Their stops are not planned
              again, and their vehicles are not available any more.
    settings.vehicle_priority
              False: one run over all vehicles (least total time).
              True: vehicle types one after the other in the order of
              settings.vehicles (e.g. cargo bikes first); the next type
              only gets what the previous ones could not take.

    Tours are filled as long as time (day_end) and capacity allow; vehicles
    that get no stop are left out. This is a simple heuristic, not an
    optimal plan (see README).
    """
    prepared = prepare_stops(order_rows, settings, geocoder, no_address)
    start, end = prepared.start, prepared.end
    fixed = {t: frame_tour(tour, settings, start, end) for t, tour in (fixed or {}).items()}
    fixed_ids = {s.stop_id for tour in fixed.values() for s in customers(tour)}
    orders = [o for o in prepared.orders if o.stop_id not in fixed_ids]
    taken = defaultdict(int)
    for tour in fixed.values():
        taken[tour.vehicle] += 1

    empty = empty_tours(start, end, settings, taken)
    if not empty and not fixed:
        raise InputError("planning without tours needs at least one vehicle "
                         "(set how many of each type are available)")
    everything, model, models = apply_vehicles({**fixed, **empty}, settings)
    model = CachedModel(model)
    models = {t: CachedModel(m) for t, m in models.items()}
    options = settings.insertion_options
    auto = {t: everything[t] for t in empty}

    # one run over all vehicles, or one run per vehicle type in order
    if settings.vehicle_priority:
        groups = [[t for t, tour in auto.items() if tour.vehicle == name]
                  for name in settings.vehicles]
        groups = [g for g in groups if g]
    else:
        groups = [list(auto)] if auto else []
    pending, inserted, unplaced = orders, [], []
    filled: dict[str, Tour] = {}
    for group in groups:
        try:
            result = insert_orders({t: auto[t] for t in group}, pending, model,
                                   models=models, **options)
        except ValueError as error:
            raise InputError(str(error)) from error
        filled.update(result.tours)
        inserted += result.inserted
        pending = [u.stop for u in result.unplaced]
        unplaced = result.unplaced
    if not groups:
        unplaced = [Unplaced.from_stop(o, REASON_NO_TOURS) for o in orders]

    used = {t: improve_tour(tour, models.get(t, model), **options)
            for t, tour in filled.items() if customers(tour)}
    # final names: "Lastenvelo 1", "Lastenvelo 2", ... skipping names of
    # the tours planned by hand
    final = {t: everything[t] for t in fixed}
    final_models = {t: models[t] for t in fixed if t in models}
    renamed = {}
    for old_id, tour in used.items():
        n = 1
        while f"{tour.vehicle} {n}" in final:
            n += 1
        new_id = f"{tour.vehicle} {n}"
        stops = tuple(replace(s, stop_id=f"{new_id} {s.role}") if s.role else s
                      for s in tour.stops)
        final[new_id] = replace(tour, tour_id=new_id, stops=stops)
        renamed[old_id] = new_id
        if old_id in models:
            final_models[new_id] = models[old_id]
    schedules = [forward_pass(final[t], final_models.get(t, model)) for t in final]
    inserted = [replace(o, tour_id=renamed.get(o.tour_id, o.tour_id)) for o in inserted
                if o.tour_id in renamed]
    problems = [u for u in prepared.unplaced if u.order_id not in fixed_ids]
    return RunResult(final, orders, inserted, problems + unplaced, schedules,
                     prepared.geocoding)
