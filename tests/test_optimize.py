"""Local search within one tour."""

from __future__ import annotations

from dataclasses import replace

from conftest import stop
from tour_insertion.feasibility import check
from tour_insertion.model import Tour
from tour_insertion.optimize import improve_tour, moved_stops, travel_cost
from tour_insertion.pipeline import Settings, run
from tour_insertion.schedule import forward_pass


def crossing_tour():
    """D(0,0) -> A(0,1000) -> B(1000,0) -> C(1000,1000) -> E(0,0).

    A -> B and C -> E cross. Best: D A C B E (or the mirror), 4000 m.
    """
    return Tour("t", (stop("D", 0, 0, tw_start=480), stop("A", 0, 1000),
                      stop("B", 1000, 0), stop("C", 1000, 1000), stop("E", 0, 0)))


def test_removes_crossing(grid):
    tour = crossing_tour()
    better = improve_tour(tour, grid)
    assert travel_cost(better.stops, grid) < travel_cost(tour.stops, grid)
    assert better.stop_ids in (("D", "A", "C", "B", "E"), ("D", "B", "C", "A", "E"))
    assert tour.stop_ids == ("D", "A", "B", "C", "E")          # input unchanged


def test_keeps_fixed_ends(grid):
    better = improve_tour(crossing_tour(), grid)
    assert better.stop_ids[0] == "D" and better.stop_ids[-1] == "E"


def test_respects_time_windows(grid):
    # Without windows D A C B E and D B C A E are equally short (4000 m).
    # A must be served by 490 (D->A = 10 min), so only D A C B E is allowed.
    tour = Tour("t", (stop("D", 0, 0, tw_start=480), stop("A", 0, 1000, tw_end=490),
                      stop("B", 1000, 0), stop("C", 1000, 1000), stop("E", 0, 0)))
    better = improve_tour(tour, grid)
    assert check(forward_pass(better, grid)).feasible
    assert better.stop_ids == ("D", "A", "C", "B", "E")


def test_window_can_block_an_improvement(grid):
    # Already-optimal order reversed would be equally short; a window on C
    # that only the given order meets must keep C where it is.
    tour = Tour("t", (stop("D", 0, 0, tw_start=480), stop("B", 1000, 0),
                      stop("A", 0, 1000), stop("C", 1000, 1000, tw_end=515),
                      stop("E", 0, 0)))
    # given: D->B 10 (490), B->A 15 (505), A->C 10 (515): C on time
    better = improve_tour(tour, grid)
    assert check(forward_pass(better, grid)).feasible


def test_already_optimal_tour_is_unchanged(grid):
    tour = Tour("t", (stop("D", 0, 0), stop("A", 0, 1000), stop("C", 1000, 1000),
                      stop("B", 1000, 0), stop("E", 0, 0)))
    assert improve_tour(tour, grid).stop_ids == tour.stop_ids


def test_moved_stops():
    a = Tour("t", (stop("D", 0, 0), stop("A", 0, 0), stop("B", 0, 0), stop("E", 0, 0)))
    b = Tour("t", (stop("D", 0, 0), stop("B", 0, 0), stop("A", 0, 0), stop("E", 0, 0)))
    assert moved_stops(a, b) == {"A", "B", "E"}
    assert moved_stops(a, a) == frozenset()


def test_pipeline_optimize_never_worse_in_total():
    rows = lambda: [
        {"tour_id": "T", "seq": "1", "stop_id": "S1", "lat": "45.7330", "lon": "4.8180"},
        {"tour_id": "T", "seq": "2", "stop_id": "S2", "lat": "45.7670", "lon": "4.8340"},
        {"tour_id": "T", "seq": "3", "stop_id": "S3", "lat": "45.7400", "lon": "4.8150"},
        {"tour_id": "T", "seq": "4", "stop_id": "S4", "lat": "45.7610", "lon": "4.8260"}]
    orders = [{"order_id": "N1", "lat": "45.7450", "lon": "4.8420"}]
    depot = "45.7606, 4.8597"
    plain = run(rows(), [dict(o) for o in orders], Settings(depot=depot))
    opt = run(rows(), [dict(o) for o in orders], Settings(depot=depot, optimize=True))
    assert opt.changes and not plain.changes
    total_before = sum(c.minutes_before for c in opt.changes)
    total_after = sum(c.minutes_after for c in opt.changes)
    assert total_after < total_before
    assert total_before == plain.schedules[0].duration


def test_shorter_route_that_only_waits_is_not_taken(grid):
    """Regression 21.11.2026: less travel but more waiting = longer tour.

    W opens at 500. Given order D W A E: D->W 10 (wait until 500), W->A,
    then home. Visiting A first saves no time because W still opens at 500;
    the optimizer must never return a LONGER tour than it got.
    """
    tour = Tour("t", (stop("D", 0, 0, tw_start=480),
                      stop("W", 1000, 0, tw_start=500),
                      stop("A", 1000, 300), stop("E", 0, 0)))
    before = forward_pass(tour, grid).duration
    after = forward_pass(improve_tour(tour, grid), grid).duration
    assert after <= before


def test_winterthur_optimized_not_longer_in_total():
    from pathlib import Path
    from tour_insertion.csv_io import read_rows
    w = Path(__file__).resolve().parents[1] / "examples" / "winterthur"
    r = run(read_rows(w / "tours.csv"), read_rows(w / "orders.csv"),
            Settings(optimize=True, depot="47.5000, 8.7236"))
    assert sum(c.minutes_after for c in r.changes) <= sum(c.minutes_before for c in r.changes)
