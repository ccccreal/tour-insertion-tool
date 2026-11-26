"""Data classes: stops, tours, schedules and insertion options.

Adapted from pa-decision-support (src/env/route.py, as of 2026-11-20).
Differences to the PA:
  - single stops instead of pickup/delivery pairs
  - WGS84 coordinates (lat/lon) instead of LV95 metres
  - time windows per stop instead of order deadlines

All classes are immutable (frozen). Inserting a stop returns a NEW tour, so a
candidate tour can never overwrite the real one by accident.

Times are whole minutes since midnight of the planning day (08:30 -> 510).
"""

from __future__ import annotations

from dataclasses import dataclass, replace

Point = tuple[float, float]  # (lat, lon)


@dataclass(frozen=True, slots=True)
class Stop:
    """One stop: an address with coordinates and an optional time window.

    tw_start   earliest start of service; arriving earlier means waiting
    tw_end     latest start of service; None = no limit
    is_new     True for stops inserted by this tool (overnight orders)
    items      number of items (parcels) at this stop. None = not given
               (counts as 0 for the capacity check, see load.py)
    kind       "drop" (items are delivered here, the default) or "pickup"
               (items are collected here and carried to the end of the tour)
    name       short label for people (e.g. location or practice name)
    quantities items per product, e.g. (("Box 50er", 2), ("Box 25er", 1));
               their sum is `items`. Only for the summary and the export.
    info       other columns of the input (contact, opening hours, ...),
               not used for planning, passed on to the export
    """

    stop_id: str
    lat: float
    lon: float
    address: str = ""
    country: str = ""
    tw_start: int | None = None
    tw_end: int | None = None
    service_min: int = 0
    is_new: bool = False
    items: int | None = None
    kind: str = "drop"
    name: str = ""
    quantities: tuple[tuple[str, int], ...] = ()
    info: tuple[tuple[str, str], ...] = ()

    @property
    def point(self) -> Point:
        return (self.lat, self.lon)

    def __repr__(self) -> str:
        return f"{'+' if self.is_new else ''}{self.stop_id}"


@dataclass(frozen=True, slots=True)
class Tour:
    """A planned stop sequence, without times (times live in Schedule).

    vehicle    name of the vehicle type ("" = not given)
    capacity   most items the vehicle can carry at once (None = no limit)
    """

    tour_id: str
    stops: tuple[Stop, ...] = ()
    vehicle: str = ""
    capacity: int | None = None

    def __len__(self) -> int:
        return len(self.stops)

    def __iter__(self):
        return iter(self.stops)

    @property
    def stop_ids(self) -> tuple[str, ...]:
        return tuple(s.stop_id for s in self.stops)

    def with_inserted(self, stop: Stop, position: int) -> Tour:
        """New tour with `stop` placed BEFORE stops[position].

        position == len(self) appends at the end.
        """
        if not 0 <= position <= len(self.stops):
            raise IndexError(f"position {position} outside 0..{len(self.stops)}")
        stops = self.stops[:position] + (stop,) + self.stops[position:]
        return replace(self, stops=stops)

    def __repr__(self) -> str:
        return f"Tour({self.tour_id}: {' '.join(repr(s) for s in self.stops)})"


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    """A stop with its planned times.

    `arrival` is the START OF SERVICE, waiting for tw_start included.
    `slack` is tw_end - arrival (minutes of reserve), None without tw_end.
    """

    stop: Stop
    arrival: int
    departure: int
    slack: int | None


@dataclass(frozen=True, slots=True)
class Schedule:
    """A tour with planned times: the result of the forward pass."""

    tour: Tour
    times: tuple[ScheduledStop, ...] = ()

    @property
    def end(self) -> int | None:
        return self.times[-1].departure if self.times else None

    @property
    def duration(self) -> int:
        if not self.times:
            return 0
        return self.times[-1].departure - self.times[0].arrival

    @property
    def min_slack(self) -> int | None:
        slacks = [t.slack for t in self.times if t.slack is not None]
        return min(slacks) if slacks else None


@dataclass(frozen=True, slots=True)
class InsertionOption:
    """The best feasible insertion of one stop into one tour.

    position   index in the NEW tour where the stop sits
    delta      extra minutes (float, not rounded): travel detour + service
               of the new stop (waiting time is not counted, as in the PA)
    min_slack  smallest reserve to a time window in the new tour, or None
    """

    tour_id: str
    stop_id: str
    position: int
    delta: float
    min_slack: int | None
    new_tour: Tour
