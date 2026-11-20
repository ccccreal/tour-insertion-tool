"""Cheapest feasible insertion of ONE stop into ONE tour.

Adapted from pa-decision-support (src/env/insertion.py, as of 2026-11-20).
The PA inserts pickup/delivery pairs (four cases). With single stops there is
only one case: put the new stop x into a gap between two existing stops.

    tour:   s_0  s_1 ... s_{p-1} | s_p ... s_{n-1}
                                 ^ position p (x goes before s_p)

    delta = c(s_{p-1}, x) + c(x, s_p) - c(s_{p-1}, s_p) + service(x)

At the open ends one term drops out (no predecessor or no successor).
`c` is travel time in whole minutes. Waiting time does not count.

Which gaps are allowed
----------------------
fixed_first  True: nothing goes before the first stop (depot / start)
fixed_last   True: nothing goes after the last stop (depot / end)

Choice among feasible positions
-------------------------------
1. smallest delta
2. larger min_slack (more reserve) on equal delta
3. earlier position on a full tie, so results are reproducible
"""

from __future__ import annotations

from tour_insertion.feasibility import check, late_stops
from tour_insertion.model import InsertionOption, Stop, Tour
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import TravelModel


def allowed_positions(n: int, fixed_first: bool = True,
                      fixed_last: bool = True) -> range:
    """Positions 0..n where a new stop may go in a tour with n stops."""
    low = 1 if fixed_first and n >= 1 else 0
    high = n - 1 if fixed_last and n >= 2 else n
    return range(low, high + 1)


def insertion_cost(tour: Tour, stop: Stop, position: int,
                   model: TravelModel) -> int:
    """delta for putting `stop` before tour.stops[position]."""
    n = len(tour)
    if not 0 <= position <= n:
        raise IndexError(f"position {position} outside 0..{n}")
    prev = tour.stops[position - 1] if position > 0 else None
    nxt = tour.stops[position] if position < n else None
    cost = stop.service_min
    if prev is not None:
        cost += model.travel_min(prev.point, stop.point)
    if nxt is not None:
        cost += model.travel_min(stop.point, nxt.point)
    if prev is not None and nxt is not None:
        cost -= model.travel_min(prev.point, nxt.point)
    return cost


def best_insertion(tour: Tour, stop: Stop, model: TravelModel, *,
                   fixed_first: bool = True, fixed_last: bool = True,
                   tolerance_min: int = 0,
                   start_time: int | None = None) -> InsertionOption | None:
    """Best feasible insertion of `stop` into `tour`, or None if none fits."""
    exempt = late_stops(forward_pass(tour, model, start_time), tolerance_min)
    best: InsertionOption | None = None
    best_key: tuple | None = None
    for pos in allowed_positions(len(tour), fixed_first, fixed_last):
        new_tour = tour.with_inserted(stop, pos)
        verdict = check(forward_pass(new_tour, model, start_time),
                        tolerance_min, exempt)
        if not verdict.feasible:
            continue
        delta = insertion_cost(tour, stop, pos, model)
        key = (delta, -_reserve(verdict.min_slack), pos)
        if best_key is None or key < best_key:
            best_key = key
            best = InsertionOption(tour.tour_id, stop.stop_id, pos, delta,
                                   verdict.min_slack, new_tour)
    return best


def _reserve(min_slack: int | None) -> float:
    """No time windows at all = unlimited reserve."""
    return float("inf") if min_slack is None else float(min_slack)


def option_key(option: InsertionOption) -> tuple:
    """Sort key to compare options across tours and stops (smaller wins)."""
    return (option.delta, -_reserve(option.min_slack), option.tour_id,
            option.stop_id, option.position)
