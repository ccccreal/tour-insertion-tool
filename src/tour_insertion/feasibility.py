"""Feasibility check: does a schedule respect all time windows?

Adapted from pa-decision-support (src/env/zulaessig.py, as of 2026-11-20).
Only one condition is left from the PA's five: the time window. There are no
pickup/delivery pairs, capacities, shifts or vehicle types here.

Existing violations
-------------------
If a stop of the ORIGINAL tour is already late (the customer's plan does not
fit our travel-time assumption), every insertion into that tour would be
rejected. Same rule as in the PA: stops that are already late are exempt; an
insertion must not push any OTHER stop - nor the new one - past its window.
"""

from __future__ import annotations

from typing import NamedTuple

from tour_insertion.model import Schedule


class Verdict(NamedTuple):
    feasible: bool
    min_slack: int | None
    late: tuple[str, ...]  # stop_ids that violate their window (not exempt)


def late_stops(schedule: Schedule, tolerance_min: int = 0) -> frozenset[str]:
    """stop_ids whose service starts after tw_end + tolerance_min."""
    return frozenset(
        t.stop.stop_id for t in schedule.times
        if t.stop.tw_end is not None
        and t.arrival > t.stop.tw_end + tolerance_min
    )


def check(schedule: Schedule, tolerance_min: int = 0,
          exempt: frozenset[str] = frozenset()) -> Verdict:
    """Verdict for a schedule. `exempt`: stops already late before."""
    late = tuple(sorted(late_stops(schedule, tolerance_min) - exempt))
    return Verdict(not late, schedule.min_slack, late)
