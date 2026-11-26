"""How many items a vehicle carries along its tour (capacity check).

Assumption (stated in the README): all items to be DROPPED on a tour are
loaded at its start; items PICKED UP stay on board until the end of the
tour (depot or connection point).

    load at the start      = sum of items of all drop stops
    after a drop stop      : load - items
    after a pickup stop    : load + items

The vehicle is over capacity if the load is ever larger than its capacity.
With drops only, the highest load is the start load, so the order of the
stops does not matter. With pickups it does: a pickup early in the tour,
before many drops are done, needs more room than the same pickup late.
"""

from __future__ import annotations

from collections.abc import Sequence

from tour_insertion.model import Stop, Tour

DROP = "drop"
PICKUP = "pickup"
KINDS = (DROP, PICKUP)


def start_load(stops: Sequence[Stop]) -> int:
    return sum(s.items or 0 for s in stops if s.kind != PICKUP)


def load_after(stops: Sequence[Stop]) -> list[int]:
    """Load when leaving each stop."""
    load, out = start_load(stops), []
    for s in stops:
        load += (s.items or 0) if s.kind == PICKUP else -(s.items or 0)
        out.append(load)
    return out


def max_load(stops: Sequence[Stop]) -> int:
    return max([start_load(stops), *load_after(stops)])


def over_capacity(tour: Tour) -> bool:
    return tour.capacity is not None and max_load(tour.stops) > tour.capacity
