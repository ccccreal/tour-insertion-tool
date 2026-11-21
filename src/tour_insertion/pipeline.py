"""The whole run in one function: rows in, results out.

Used by the command line (cli.py) and the web app (app.py), so both always
compute exactly the same thing.

    rows -> geocode missing coordinates
         -> tours (stops without coordinates skipped) + orders
         -> [optimize existing tours] -> insert orders -> [optimize again]
         -> schedules

Addresses that cannot be geocoded
---------------------------------
depot (first and last stop of a tour)  -> error, nothing is computed:
                                          every time of the tour depends on it
other stop of a tour                   -> skipped: the tour is computed
                                          without it, reported in `skipped`
new order                              -> not planned, reported in `unplaced`
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from tour_insertion.csv_io import orders_from_rows, tours_from_rows
from tour_insertion.geocoding import (NOT_FOUND, FranceGeocoder,
                                      GeocodeResult, Geocoder, SwissGeocoder,
                                      fill_coordinates, needs_geocoding)
from tour_insertion.model import InsertionOption, Schedule, Stop, Tour
from tour_insertion.optimize import improve_tour, moved_stops
from tour_insertion.planner import Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel


class InputError(ValueError):
    """The input cannot be planned (bad file, depot not found, ...)."""


@dataclass(frozen=True)
class Settings:
    detour_factor: float = 1.35
    speed_m_per_min: float = 250.0
    service_min: int = 3
    tolerance_min: int = 0
    open_start: bool = False
    open_end: bool = False
    min_score: float = 0.5
    optimize: bool = False


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


def _depot_rows(tour_rows: list[dict]) -> set[int]:
    """Indices (in tour_rows) of the first and last stop of every tour."""
    by_tour: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for i, row in enumerate(tour_rows):
        tour_id = (row.get("tour_id") or "").strip()
        try:
            seq = int(row.get("seq") or "")
        except ValueError as error:
            raise InputError(f"{row.get('_where', 'tours')}: seq must be a number") from error
        by_tour[tour_id].append((seq, i))
    ends = set()
    for items in by_tour.values():
        items.sort()
        ends.update({items[0][1], items[-1][1]})
    return ends


def geocode_rows(tour_rows: list[dict], order_rows: list[dict],
                 geocoder: Geocoder
                 ) -> tuple[list[GeocodeResult], list[Unplaced], list[Unplaced]]:
    """Fill missing coordinates (rows are changed in place).

    Returns (all results, unplaced orders, skipped tour stops). Rows that
    still have no coordinates afterwards are skipped / unplaced; a depot
    without coordinates raises InputError.
    """
    if not any(needs_geocoding(r) for r in tour_rows + order_rows):
        return [], [], []
    try:
        tour_results = fill_coordinates(tour_rows, geocoder)
        order_results = fill_coordinates(order_rows, geocoder)
    finally:
        geocoder.save()
    by_query = {r.query: r for r in tour_results + order_results}

    depots = _depot_rows(tour_rows)
    bad_depots = [r for i, r in enumerate(tour_rows)
                  if i in depots and needs_geocoding(r)]
    if bad_depots:
        lines = "\n".join(
            f"  {r.get('tour_id', '')}: {r['address'].strip()}  ->  "
            f"{(by_query.get(r['address'].strip()) or GeocodeResult('', '', NOT_FOUND)).label or 'not found'}"
            for r in bad_depots)
        raise InputError("The depot (first/last stop of a tour) could not be "
                         "geocoded reliably. Fix the address or add lat/lon:\n"
                         + lines)

    skipped = [Unplaced((r.get("stop_id") or "").strip(), r["address"].strip(),
                        _reason(by_query.get(r["address"].strip())),
                        tour_id=(r.get("tour_id") or "").strip())
               for r in tour_rows if needs_geocoding(r)]
    unplaced = [Unplaced((r.get("order_id") or "").strip(), r["address"].strip(),
                         _reason(by_query.get(r["address"].strip())))
                for r in order_rows if needs_geocoding(r)]
    return tour_results + order_results, unplaced, skipped


def run(tour_rows: list[dict], order_rows: list[dict], settings: Settings,
        geocoder: Geocoder | None = None) -> RunResult:
    """Plan everything. `rows` are dicts as read from the CSV files;
    rows without lat/lon are geocoded (and changed in place)."""
    if not tour_rows:
        raise InputError("the tours file is empty")
    try:
        geo_results, geo_unplaced, skipped = geocode_rows(
            tour_rows, order_rows, geocoder or make_geocoder(settings))
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

    model = CrowFlyModel(settings.detour_factor, settings.speed_m_per_min)
    options = dict(fixed_first=not settings.open_start,
                   fixed_last=not settings.open_end,
                   tolerance_min=settings.tolerance_min)
    try:
        plain = insert_orders(tours, orders, model, **options)
    except ValueError as error:                       # duplicate ids
        raise InputError(str(error)) from error

    if not settings.optimize:
        schedules = [forward_pass(plain.tours[t], model) for t in sorted(plain.tours)]
        return RunResult(tours, orders, plain.inserted,
                         geo_unplaced + plain.unplaced, schedules, geo_results,
                         skipped)

    # Optimize the given tours first (a poor plan also misleads WHERE new
    # orders go), insert, then optimize once more with the new stops.
    better = {t: improve_tour(tour, model, **options) for t, tour in tours.items()}
    result = insert_orders(better, orders, model, **options)
    final = {t: improve_tour(tour, model, **options)
             for t, tour in result.tours.items()}

    schedules, changes = [], []
    for t in sorted(final):
        before = forward_pass(plain.tours[t], model)
        after = forward_pass(final[t], model)
        schedules.append(after)
        changes.append(TourChange(t, before.duration, after.duration,
                                  moved_stops(plain.tours[t], final[t])))
    return RunResult(tours, orders, result.inserted,
                     geo_unplaced + result.unplaced, schedules, geo_results,
                     skipped, changes)
