"""The whole run in one function: rows in, results out.

Used by the command line (cli.py) and the web app (app.py), so both always
compute exactly the same thing.

    rows -> geocode missing coordinates -> tours + orders
         -> insert orders -> schedules
"""

from __future__ import annotations

from dataclasses import dataclass, field

from tour_insertion.csv_io import orders_from_rows, tours_from_rows
from tour_insertion.geocoding import (NOT_FOUND, OK, FranceGeocoder,
                                      GeocodeResult, Geocoder, SwissGeocoder,
                                      fill_coordinates, needs_geocoding)
from tour_insertion.model import InsertionOption, Schedule, Stop, Tour
from tour_insertion.planner import Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel


class InputError(ValueError):
    """The input cannot be planned (bad file, tour address not found, ...)."""


@dataclass(frozen=True)
class Settings:
    detour_factor: float = 1.35
    speed_m_per_min: float = 250.0
    service_min: int = 3
    tolerance_min: int = 0
    open_start: bool = False
    open_end: bool = False
    min_score: float = 0.5


@dataclass
class RunResult:
    tours: dict[str, Tour]
    orders: list[Stop]
    inserted: list[InsertionOption]
    unplaced: list[Unplaced]
    schedules: list[Schedule]
    geocoding: list[GeocodeResult] = field(default_factory=list)

    @property
    def n_orders(self) -> int:
        """All new orders, including those that could not be geocoded."""
        return len(self.inserted) + len(self.unplaced)


def make_geocoder(settings: Settings, cache_path=None) -> Geocoder:
    return Geocoder(cache_path, {
        "FR": FranceGeocoder(min_score=settings.min_score),
        "CH": SwissGeocoder()})


def geocode_rows(tour_rows: list[dict], order_rows: list[dict],
                 geocoder: Geocoder) -> tuple[list[GeocodeResult], list[Unplaced]]:
    """Fill missing coordinates. Returns (all results, unplaced orders).

    A tour stop that cannot be geocoded raises InputError: the tour would be
    wrong. An order that cannot be geocoded is reported, not planned.
    """
    if not any(needs_geocoding(r) for r in tour_rows + order_rows):
        return [], []
    try:
        tour_results = fill_coordinates(tour_rows, geocoder)
        order_results = fill_coordinates(order_rows, geocoder)
    finally:
        geocoder.save()

    bad = [r for r in tour_results if r.status != OK]
    if bad:
        lines = "\n".join(f"  {r.status:<10} {r.query}  ->  {r.label or '-'}"
                          for r in bad)
        raise InputError("Some TOUR stops could not be geocoded reliably. "
                         "Fix the address or add lat/lon:\n" + lines)

    by_query = {r.query: r for r in order_results}
    unplaced = []
    for row in order_rows:
        if needs_geocoding(row):                  # still no coordinates
            address = row["address"].strip()
            reason = ("geocode_not_found" if by_query[address].status == NOT_FOUND
                      else "geocode_uncertain")
            unplaced.append(Unplaced((row.get("order_id") or "").strip(),
                                     address, reason))
    return tour_results + order_results, unplaced


def run(tour_rows: list[dict], order_rows: list[dict], settings: Settings,
        geocoder: Geocoder | None = None) -> RunResult:
    """Plan everything. `rows` are dicts as read from the CSV files;
    rows without lat/lon are geocoded (and changed in place)."""
    if not tour_rows:
        raise InputError("the tours file is empty")
    try:
        geo_results, geo_unplaced = geocode_rows(
            tour_rows, order_rows, geocoder or make_geocoder(settings))
        planned_rows = [r for r in order_rows if not needs_geocoding(r)]
        tours = tours_from_rows(tour_rows, settings.service_min)
        orders = orders_from_rows(planned_rows, settings.service_min)
    except InputError:
        raise
    except OSError as error:                       # network / service down
        raise InputError(f"geocoding service not reachable: {error}") from error
    except (KeyError, ValueError) as error:
        raise InputError(str(error)) from error

    model = CrowFlyModel(settings.detour_factor, settings.speed_m_per_min)
    try:
        result = insert_orders(tours, orders, model,
                               fixed_first=not settings.open_start,
                               fixed_last=not settings.open_end,
                               tolerance_min=settings.tolerance_min)
    except ValueError as error:                       # duplicate ids
        raise InputError(str(error)) from error
    schedules = [forward_pass(result.tours[t], model) for t in sorted(result.tours)]
    return RunResult(tours, orders, result.inserted,
                     geo_unplaced + result.unplaced, schedules, geo_results)
