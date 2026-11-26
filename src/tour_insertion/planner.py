"""Insert many new stops into many tours (greedy cheapest insertion).

Replaces the PA's candidate set (src/env/kandidaten.py) for this use case:
there is no simulator and no decision over time, all orders are known in
the morning.

Algorithm
---------
    while orders are left:
        for every order: best insertion over all tours
        orders that fit nowhere -> unplaced
        insert the ONE order with the smallest delta (global cheapest)
        recompute, because the changed tour affects the next order

Inserting the globally cheapest order first makes the result independent of
the row order in the CSV. An order that fits nowhere will not fit later
either: adding stops only makes tours longer.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from tour_insertion.insertion import best_insertion, option_key
from tour_insertion.model import InsertionOption, Stop, Tour
from tour_insertion.traveltime import TravelModel

REASON_NO_POSITION = "no_feasible_position"
REASON_CAPACITY = "capacity"          # would fit in time, but no tour has room
REASON_NO_TOURS = "no_tours"


@dataclass(frozen=True, slots=True)
class Unplaced:
    """An order that is not in any tour, or a tour stop that was skipped,
    and why. `stop` is None when it never became a Stop (geocoding failed).
    """

    order_id: str
    address: str
    reason: str
    stop: Stop | None = None
    tw_start: int | None = None
    tw_end: int | None = None
    tour_id: str = ""          # set for skipped stops of an existing tour
    # kept so the stop can still be placed by hand (e.g. on the map)
    service_min: int | None = None
    items: int | None = None
    country: str = ""
    kind: str = "drop"

    @classmethod
    def from_stop(cls, stop: Stop, reason: str) -> Unplaced:
        return cls(stop.stop_id, stop.address, reason, stop,
                   stop.tw_start, stop.tw_end, "", stop.service_min,
                   stop.items, stop.country, stop.kind)


@dataclass
class PlanResult:
    tours: dict[str, Tour]
    inserted: list[InsertionOption] = field(default_factory=list)
    unplaced: list[Unplaced] = field(default_factory=list)


def insert_orders(tours: Mapping[str, Tour], orders: Sequence[Stop],
                  model: TravelModel, *, fixed_first: bool = True,
                  fixed_last: bool = True, tolerance_min: int = 0,
                  start_times: Mapping[str, int] | None = None,
                  models: Mapping[str, TravelModel] | None = None) -> PlanResult:
    """Insert all `orders` into `tours`. Input objects are not changed.

    models: travel model per tour_id (vehicles with different speeds);
            tours not in it use `model`.
    """
    _check_ids(tours, orders)
    start_times = start_times or {}
    models = models or {}
    result = PlanResult(tours=dict(tours))
    pending = sorted(orders, key=lambda s: s.stop_id)

    if not result.tours:
        result.unplaced = [Unplaced.from_stop(o, REASON_NO_TOURS) for o in pending]
        return result

    # Best option per (order, tour). After an insertion only the changed
    # tour has to be recomputed: the result is the same as recomputing
    # everything, but about n_tours times faster.
    cache: dict[tuple[str, str], InsertionOption | None] = {}

    def option_for(order: Stop, tour_id: str) -> InsertionOption | None:
        key = (order.stop_id, tour_id)
        if key not in cache:
            cache[key] = best_insertion(
                result.tours[tour_id], order, models.get(tour_id, model),
                fixed_first=fixed_first,
                fixed_last=fixed_last, tolerance_min=tolerance_min,
                start_time=start_times.get(tour_id))
        return cache[key]

    while pending:
        best: InsertionOption | None = None
        nowhere: list[Stop] = []
        for order in pending:
            option = None
            for tour_id in sorted(result.tours):
                candidate = option_for(order, tour_id)
                if candidate is not None and (
                        option is None or option_key(candidate) < option_key(option)):
                    option = candidate
            if option is None:
                nowhere.append(order)
            elif best is None or option_key(option) < option_key(best):
                best = option

        for order in nowhere:
            fits_without_capacity = any(
                best_insertion(result.tours[t], order, models.get(t, model),
                               fixed_first=fixed_first, fixed_last=fixed_last,
                               tolerance_min=tolerance_min,
                               start_time=start_times.get(t), check_capacity=False)
                for t in result.tours if result.tours[t].capacity is not None)
            reason = REASON_CAPACITY if fits_without_capacity else REASON_NO_POSITION
            result.unplaced.append(Unplaced.from_stop(order, reason))
            pending.remove(order)
        if best is None:
            break

        result.tours[best.tour_id] = best.new_tour
        result.inserted.append(best)
        pending = [o for o in pending if o.stop_id != best.stop_id]
        for order in pending:                     # that tour has changed
            cache.pop((order.stop_id, best.tour_id), None)

    return result


def _check_ids(tours: Mapping[str, Tour], orders: Sequence[Stop]) -> None:
    seen: set[str] = set()
    for tour in tours.values():
        for stop in tour.stops:
            if stop.stop_id in seen:
                raise ValueError(f"duplicate stop_id: {stop.stop_id!r}")
            seen.add(stop.stop_id)
    for order in orders:
        if order.stop_id in seen:
            raise ValueError(f"order id already used: {order.stop_id!r}")
        seen.add(order.stop_id)
