"""Travel time, forward pass, feasibility, insertion and planner."""

from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import stop
from tour_insertion.feasibility import check
from tour_insertion.insertion import (allowed_positions, best_insertion,
                                      insertion_cost)
from tour_insertion.model import Stop, Tour
from tour_insertion.planner import REASON_NO_POSITION, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel, haversine_m


# --- travel time -----------------------------------------------------------

class TestTravelTime:
    def test_one_degree_latitude_is_about_111_km(self):
        assert haversine_m((47.0, 8.0), (48.0, 8.0)) == pytest.approx(111_195, rel=1e-3)

    def test_symmetric_and_zero(self):
        m = CrowFlyModel()
        a, b = (47.4995, 8.7245), (47.5130, 8.7590)
        assert m.travel_min(a, b) == m.travel_min(b, a)
        assert m.travel_min(a, a) == 0

    def test_same_location_is_free(self):
        # about 30 m apart
        assert CrowFlyModel().travel_min((47.5, 8.7), (47.50027, 8.7)) == 0

    def test_rounds_up(self):
        # 1000 m crow-fly, factor 1.0, 300 m/min -> 3.33 -> 4
        m = CrowFlyModel(detour_factor=1.0, speed_m_per_min=300)
        b = (47.0 + 1000 / 111_195, 8.0)
        assert m.travel_min((47.0, 8.0), b) == 4


# --- forward pass ----------------------------------------------------------

class TestForwardPass:
    def test_square_tour_by_hand(self, grid, square_tour):
        s = forward_pass(square_tour, grid)
        assert [(t.arrival, t.departure) for t in s.times] == [
            (480, 480), (490, 492), (502, 504), (519, 519)]
        assert s.end == 519 and s.duration == 39

    def test_waits_for_tw_start(self, grid, square_tour):
        tour = Tour("t", tuple(replace(x, tw_start=500) if x.stop_id == "A"
                               else x for x in square_tour.stops))
        s = forward_pass(tour, grid)
        assert s.times[1].arrival == 500          # waited 10 minutes
        assert s.times[2].arrival == 512

    def test_start_time_overrides(self, grid, square_tour):
        assert forward_pass(square_tour, grid, start_time=600).times[0].arrival == 600

    def test_slack(self, grid, square_tour):
        tour = Tour("t", tuple(replace(x, tw_end=503) if x.stop_id == "B"
                               else x for x in square_tour.stops))
        s = forward_pass(tour, grid)
        assert s.times[2].slack == 1 and s.min_slack == 1


# --- feasibility -----------------------------------------------------------

class TestFeasibility:
    def test_late_stop_is_reported(self, grid, square_tour):
        tour = Tour("t", tuple(replace(x, tw_end=500) if x.stop_id == "B"
                               else x for x in square_tour.stops))
        v = check(forward_pass(tour, grid))
        assert not v.feasible and v.late == ("B",)

    def test_tolerance_and_exempt(self, grid, square_tour):
        tour = Tour("t", tuple(replace(x, tw_end=500) if x.stop_id == "B"
                               else x for x in square_tour.stops))
        s = forward_pass(tour, grid)
        assert check(s, tolerance_min=2).feasible       # 502 <= 500 + 2
        assert check(s, exempt=frozenset({"B"})).feasible


# --- insertion -------------------------------------------------------------

class TestInsertion:
    def test_allowed_positions(self):
        assert list(allowed_positions(4)) == [1, 2, 3]
        assert list(allowed_positions(4, False, False)) == [0, 1, 2, 3, 4]
        assert list(allowed_positions(1)) == [1]
        assert list(allowed_positions(0)) == [0]

    def test_costs_by_hand(self, grid, square_tour):
        x = stop("X", 500, 1000, service=2, new=True)
        # exact minutes (not rounded), D-X = sqrt(500^2 + 1000^2) m = 11.18 min
        # between D and A: 11.18 + 5 - 10    + 2 = 8.18
        assert insertion_cost(square_tour, x, 1, grid) == pytest.approx(8.180, abs=1e-3)
        # between A and B:  5    + 5 - 10    + 2 = 2
        assert insertion_cost(square_tour, x, 2, grid) == pytest.approx(2.0)
        # between B and E:  5 + 11.18 - 14.14 + 2 = 4.04
        assert insertion_cost(square_tour, x, 3, grid) == pytest.approx(4.038, abs=1e-3)

    def test_best_is_cheapest(self, grid, square_tour):
        x = stop("X", 500, 1000, service=2, new=True)
        opt = best_insertion(square_tour, x, grid)
        assert opt.position == 2 and opt.delta == pytest.approx(2.0)
        assert opt.new_tour.stop_ids == ("D", "A", "X", "B", "E")
        assert square_tour.stop_ids == ("D", "A", "B", "E")   # unchanged

    def test_time_window_pushes_to_next_best(self, grid, square_tour):
        # B must start by 503. With X before B, B starts at 504 -> too late.
        tour = Tour("t1", tuple(replace(s, tw_end=503) if s.stop_id == "B"
                                else s for s in square_tour.stops))
        x = stop("X", 500, 1000, service=2, new=True)
        opt = best_insertion(tour, x, grid)
        assert opt.position == 3 and opt.delta == pytest.approx(4.038, abs=1e-3)

    def test_new_stop_window_respected(self, grid, square_tour):
        x = stop("X", 500, 1000, tw_end=490, service=2, new=True)
        assert best_insertion(square_tour, x, grid) is None

    def test_fixed_ends(self, grid, square_tour):
        # X right next to the depot: cheapest would be before D or after E
        x = stop("X", 0, -500, new=True)
        assert best_insertion(square_tour, x, grid).position in (1, 3)
        open_opt = best_insertion(square_tour, x, grid,
                                  fixed_first=False, fixed_last=False)
        assert open_opt.position in (0, 4)

    def test_no_false_tie_from_rounding(self):
        """Regression, Lyon example 20.11.2026 (coordinates read off the map).

        Rounded to whole minutes, Rockefeller costs 9 min both before and
        after Ambroise Courtois; the tie-break took the earlier gap and the
        next order (Rue de la Balme) then got a 13-min detour. With exact
        times the later gap is (slightly) cheaper and the tour becomes
        Jean Mace -> Courtois -> Rockefeller -> Balme -> depot.
        """
        def s(i, lat, lon, new=False):
            return Stop(i, lat, lon, is_new=new)
        tour = Tour("SUD", (s("D0", 45.7606, 4.8597), s("JM", 45.7457, 4.8424),
                            s("C", 45.7445, 4.8705), s("D1", 45.7606, 4.8597)))
        orders = [s("R", 45.7405, 4.8790, True), s("B", 45.7505, 4.8915, True)]
        r = insert_orders({"SUD": tour}, orders, CrowFlyModel())
        assert r.tours["SUD"].stop_ids == ("D0", "JM", "C", "R", "B", "D1")

    def test_existing_lateness_does_not_block(self, grid, square_tour):
        # B is already late in the original plan (tw_end 495, arrives 502)
        tour = Tour("t1", tuple(replace(s, tw_end=495) if s.stop_id == "B"
                                else s for s in square_tour.stops))
        x = stop("X", 500, 1000, service=2, new=True)
        assert best_insertion(tour, x, grid) is not None


# --- planner ---------------------------------------------------------------

class TestPlanner:
    def make_tours(self):
        west = Tour("west", (stop("Dw", 0, 0, tw_start=480),
                             stop("W1", -1000, 0, service=2),
                             stop("Ew", 0, 0)))
        east = Tour("east", (stop("De", 0, 0, tw_start=480),
                             stop("E1", 1000, 0, service=2),
                             stop("Ee", 0, 0)))
        return {"west": west, "east": east}

    def test_orders_go_to_nearby_tour(self, grid):
        orders = [stop("n_east", 1200, 0, service=2, new=True),
                  stop("n_west", -1200, 0, service=2, new=True)]
        r = insert_orders(self.make_tours(), orders, grid)
        assert "n_east" in r.tours["east"].stop_ids
        assert "n_west" in r.tours["west"].stop_ids
        assert r.unplaced == []

    def test_impossible_order_is_unplaced(self, grid):
        orders = [stop("late", 5000, 0, tw_end=485, new=True)]
        r = insert_orders(self.make_tours(), orders, grid)
        assert [u.order_id for u in r.unplaced] == ["late"]
        assert r.unplaced[0].reason == REASON_NO_POSITION

    def test_result_independent_of_input_order(self, grid):
        orders = [stop("a", 1200, 0, new=True), stop("b", 1300, 100, new=True),
                  stop("c", -800, 300, new=True)]
        r1 = insert_orders(self.make_tours(), orders, grid)
        r2 = insert_orders(self.make_tours(), orders[::-1], grid)
        assert ({k: t.stop_ids for k, t in r1.tours.items()}
                == {k: t.stop_ids for k, t in r2.tours.items()})

    def test_duplicate_ids_rejected(self, grid):
        with pytest.raises(ValueError):
            insert_orders(self.make_tours(), [stop("W1", 0, 0)], grid)
