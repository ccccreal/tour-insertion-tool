"""Distance and travel time.

Adapted from pa-decision-support (src/env/traveltime.py, as of 2026-11-20).

**This is the only place with geometry.** Everything else asks a travel model
for minutes. Switching to real street-network times (OSRM, GraphHopper, ...)
later means writing a second class with the same `travel_min` method, not
rewriting the planner.

Default model: great-circle distance (haversine) x detour factor, divided by
a constant speed. Both numbers are ASSUMPTIONS and must be stated as such in
any result shown to a customer. The PA calibration (Zurich) does not carry
over to other cities.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Protocol

from tour_insertion.model import Point

EARTH_RADIUS_M = 6_371_000.0

#: Two stops closer than this (crow-fly) count as the SAME location:
#: no travel time (walking inside a building). Same threshold as in the PA.
SAME_LOCATION_M = 50.0


class TravelModel(Protocol):
    """Anything that can say how long it takes from a to b.

    travel_exact  minutes as a float, NOT rounded. Used to COMPARE insertion
                  positions: rounding every leg up to whole minutes creates
                  false ties (two gaps "both 9 min" that differ by metres).
    travel_min    whole minutes, rounded up. Used for the SCHEDULE (times
                  shown to people).
    """

    def travel_exact(self, a: Point, b: Point) -> float: ...

    def travel_min(self, a: Point, b: Point) -> int: ...


def haversine_m(a: Point, b: Point) -> float:
    """Great-circle distance in metres between two (lat, lon) points."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = (math.sin(dlat / 2) ** 2
         + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


@dataclass(frozen=True, slots=True)
class CrowFlyModel:
    """Crow-fly distance x detour factor at constant speed.

    detour_factor     street distance / crow-fly distance (assumption)
    speed_m_per_min   average speed incl. stops at lights (assumption);
                      250 m/min = 15 km/h, a cautious cargo-bike value
    """

    detour_factor: float = 1.35
    speed_m_per_min: float = 250.0

    def distance_m(self, a: Point, b: Point) -> float:
        """Driven distance in metres (not rounded)."""
        return haversine_m(a, b) * self.detour_factor

    def travel_exact(self, a: Point, b: Point) -> float:
        """Travel time in minutes, not rounded (0 at the same location)."""
        if haversine_m(a, b) < SAME_LOCATION_M:
            return 0.0
        return self.distance_m(a, b) / self.speed_m_per_min

    def travel_min(self, a: Point, b: Point) -> int:
        """Travel time in whole minutes, rounded UP.

        The tiny epsilon keeps float noise (18.000000000004) from becoming
        an extra minute; real values like 18.01 still round up to 19.
        """
        return math.ceil(self.travel_exact(a, b) - 1e-9)
