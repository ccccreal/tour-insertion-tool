"""Forward pass: planned arrival and departure for every stop.

Adapted from pa-decision-support (src/env/vorwaerts.py, as of 2026-11-20).

    t = start time (first stop: its tw_start, or `start_time`)
    for each stop:
        t = t + travel_min(previous, stop)      (not for the first stop)
        t = max(t, tw_start)                    wait if too early
        arrival = t                             start of service
        t = t + service_min
        departure = t

Waiting is INSIDE the arrival: `max` comes before `arrival = t`.
"""

from __future__ import annotations

from tour_insertion.model import Schedule, ScheduledStop, Tour
from tour_insertion.traveltime import TravelModel


def forward_pass(tour: Tour, model: TravelModel,
                 start_time: int | None = None) -> Schedule:
    """Schedule of `tour`.

    start_time   when the tour starts at its first stop. None: tour.start,
                 else the first stop's tw_start, else 0.
    """
    if start_time is None:
        start_time = tour.start
    times: list[ScheduledStop] = []
    t = 0
    previous = None
    for stop in tour.stops:
        if previous is None:
            if start_time is not None:
                t = start_time
            elif stop.tw_start is not None:
                t = stop.tw_start
        else:
            t += model.travel_min(previous.point, stop.point)
        if stop.tw_start is not None:
            t = max(t, stop.tw_start)
        arrival = t
        t += stop.service_min
        slack = stop.tw_end - arrival if stop.tw_end is not None else None
        times.append(ScheduledStop(stop, arrival, t, slack))
        previous = stop
    return Schedule(tour, tuple(times))
