"""Shared test helpers.

Most tests use a GridModel instead of real coordinates: lat/lon are read as
plain x/y metres, speed 100 m/min, no detour. Then every number can be
checked by hand on paper, like the tests in the PA.
"""

from __future__ import annotations

import math

import pytest

from tour_insertion.model import Stop, Tour


class GridModel:
    """x/y in metres, 100 m/min, rounded up to whole minutes."""

    def travel_min(self, a, b) -> int:
        d = math.hypot(b[0] - a[0], b[1] - a[1])
        return math.ceil(d / 100 - 1e-9)


def stop(stop_id, x, y, tw_start=None, tw_end=None, service=0, new=False):
    return Stop(stop_id, x, y, tw_start=tw_start, tw_end=tw_end,
                service_min=service, is_new=new)


@pytest.fixture
def grid():
    return GridModel()


@pytest.fixture
def square_tour():
    """Depot D(0,0) at 08:00 -> A(0,1000) -> B(1000,1000) -> back to E(0,0).

    Hand schedule (100 m/min, service 2 at A and B):
        D  arr 480  dep 480
        A  +10 -> 490, dep 492
        B  +10 -> 502, dep 504
        E  +15 (1414 m) -> 519
    """
    return Tour("t1", (
        stop("D", 0, 0, tw_start=480),
        stop("A", 0, 1000, service=2),
        stop("B", 1000, 1000, service=2),
        stop("E", 0, 0),
    ))
