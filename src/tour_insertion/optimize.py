"""Improve the stop order WITHIN one tour (local search).

Same stops, same tour, only a better order. Time windows stay respected.

Moves, tried over and over until none helps any more:

  relocate   take a run of 1-3 consecutive stops out and put it somewhere
             else in the same tour (also "Or-opt")
  2-opt      reverse a part of the tour: ... a [b ... c] d ...  ->
             ... a [c ... b] d ...  (removes crossing lines)

Objective: tour DURATION, from the start at the depot to the return,
waiting for time windows included. Pure travel time is not enough: with a
window "from 09:00" a shorter route can arrive too early and wait, and the
tour gets LONGER (found 21.11.2026 with the Winterthur example). Among
orders of equal duration the shorter travel time wins.

A move is only accepted if the new tour is feasible: no stop may become
late that was not already late in the tour we started from (same rule as
for insertion, see feasibility.py), and the load must stay within the
capacity (or, if the tour is already over it, not get higher; see load.py).

This is a heuristic: it finds a GOOD order, not provably the best one. For
15-30 stops it is close to optimal in practice and takes milliseconds.
"""

from __future__ import annotations

from dataclasses import replace

from tour_insertion.feasibility import late_stops
from tour_insertion.load import PICKUP, max_load
from tour_insertion.model import Stop, Tour
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import TravelModel

EPS = 1e-6
MAX_SEGMENT = 3


def travel_cost(stops: tuple[Stop, ...], model: TravelModel) -> float:
    """Sum of exact travel minutes along the stops."""
    return sum(model.travel_exact(a.point, b.point)
               for a, b in zip(stops, stops[1:]))


def improve_tour(tour: Tour, model: TravelModel, *, fixed_first: bool = True,
                 fixed_last: bool = True, tolerance_min: int = 0,
                 start_time: int | None = None,
                 max_rounds: int = 200) -> Tour:
    """Return a tour with a shorter (or equal) feasible stop order."""
    stops = tour.stops
    n = len(stops)
    lo = 1 if fixed_first else 0                 # first movable index
    hi = n - 2 if fixed_last else n - 1          # last movable index
    if hi - lo < 1:
        return tour

    # Travel times between all stops, computed once. The search works on
    # tuples of indices into `stops`; evaluate() is forward_pass() +
    # check() on these tables (same rules, much faster).
    exempt = late_stops(forward_pass(tour, model, start_time), tolerance_min)
    start = start_time if start_time is not None else tour.start
    minutes = [[model.travel_min(a.point, b.point) for b in stops] for a in stops]
    exact = [[model.travel_exact(a.point, b.point) for b in stops] for a in stops]
    limit = [None if s.tw_end is None or s.stop_id in exempt
             else s.tw_end + tolerance_min for s in stops]
    # capacity: only pickups make the order matter (see load.py)
    room = None
    if tour.capacity is not None and any(s.kind == PICKUP for s in stops):
        room = max(tour.capacity, max_load(stops))
    change = [(s.items or 0) if s.kind == PICKUP else -(s.items or 0) for s in stops]
    first_load = sum(s.items or 0 for s in stops if s.kind != PICKUP)

    def evaluate(order: tuple[int, ...]) -> float | None:
        """Duration (+ tiny travel tie-break), or None if infeasible."""
        first = stops[order[0]]
        t = start if start is not None else (first.tw_start or 0)
        begin, prev, travel, load = None, None, 0.0, first_load
        for i in order:
            if room is not None:
                load += change[i]
                if load > room:
                    return None
            s = stops[i]
            if prev is not None:
                t += minutes[prev][i]
                travel += exact[prev][i]
            if s.tw_start is not None and t < s.tw_start:
                t = s.tw_start
            if begin is None:
                begin = t
            if limit[i] is not None and t > limit[i]:
                return None
            t += s.service_min
            prev = i
        return (t - begin) + 1e-3 * travel

    order = tuple(range(n))
    best = evaluate(order)
    if best is None:            # cannot happen: own lateness is exempt
        return tour
    for _ in range(max_rounds):
        improved = False
        for candidate in _neighbours(order, lo, hi):
            value = evaluate(candidate)
            if value is not None and value < best - EPS:
                order, best, improved = candidate, value, True
                break                             # first improvement
        if not improved:
            break
    return replace(tour, stops=tuple(stops[i] for i in order))


def _neighbours(stops: tuple, lo: int, hi: int):
    """All relocate and 2-opt moves within the movable range [lo, hi]."""
    # relocate a segment stops[i:i+k] to another place
    for k in range(1, MAX_SEGMENT + 1):
        for i in range(lo, hi - k + 2):
            segment = stops[i:i + k]
            rest = stops[:i] + stops[i + k:]
            # positions in `rest` where the segment may go (keep fixed ends)
            for j in range(lo, hi - k + 2):
                if j == i:
                    continue
                yield rest[:j] + segment + rest[j:]
    # 2-opt: reverse stops[i..j]
    for i in range(lo, hi):
        for j in range(i + 1, hi + 1):
            yield stops[:i] + stops[i:j + 1][::-1] + stops[j + 1:]


def moved_stops(before: Tour, after: Tour) -> frozenset[str]:
    """stop_ids whose predecessor changed: what a driver would notice."""
    def predecessors(tour: Tour) -> dict[str, str | None]:
        ids = tour.stop_ids
        return {s: (ids[i - 1] if i else None) for i, s in enumerate(ids)}
    a, b = predecessors(before), predecessors(after)
    return frozenset(s for s in b if a.get(s) != b[s])
