"""Stop types (drop / pickup), vehicle capacity and the load along a tour."""

from __future__ import annotations

from dataclasses import replace

from conftest import GridModel, stop
from tour_insertion.load import load_after, max_load, over_capacity, start_load
from tour_insertion.model import Tour
from tour_insertion.optimize import improve_tour
from tour_insertion.planner import REASON_CAPACITY, REASON_NO_POSITION, insert_orders


def drop(stop_id, x, y, items, **kw):
    return replace(stop(stop_id, x, y, **kw), items=items)


def pickup(stop_id, x, y, items, **kw):
    return replace(stop(stop_id, x, y, **kw), items=items, kind="pickup")


def test_load_profile_by_hand():
    # start: drops 5 + 2 = 7 on board; A -5 -> 2; P +4 -> 6; B -2 -> 4
    stops = [stop("D", 0, 0), drop("A", 0, 1, 5), pickup("P", 0, 2, 4),
             drop("B", 0, 3, 2), stop("E", 0, 0)]
    assert start_load(stops) == 7
    assert load_after(stops) == [7, 2, 6, 4, 4]
    assert max_load(stops) == 7
    assert over_capacity(Tour("t", tuple(stops), capacity=6))
    assert not over_capacity(Tour("t", tuple(stops), capacity=7))
    assert not over_capacity(Tour("t", tuple(stops)))          # no capacity


def test_missing_items_count_as_zero():
    assert max_load([stop("D", 0, 0), stop("A", 0, 1)]) == 0


class TestCapacityOnInsertion:
    def tour(self, capacity):
        return Tour("t1", (stop("D", 0, 0, tw_start=480), drop("A", 0, 1000, 5),
                           drop("B", 0, 2000, 5), stop("E", 0, 0)),
                    vehicle="Velo", capacity=capacity)

    def test_drop_that_does_not_fit_is_unplaced_with_reason_capacity(self):
        order = replace(drop("N", 0, 1500, 3), is_new=True)
        full = insert_orders({"t1": self.tour(10)}, [order], GridModel())
        assert [(u.order_id, u.reason) for u in full.unplaced] == [("N", REASON_CAPACITY)]
        room = insert_orders({"t1": self.tour(13)}, [order], GridModel())
        assert room.tours["t1"].stop_ids == ("D", "A", "N", "B", "E")
        assert room.tours["t1"].capacity == 13 and room.tours["t1"].vehicle == "Velo"

    def test_pickup_goes_where_there_is_room(self):
        # P lies right next to the depot: cheapest right after D, but there
        # the van still carries all 10 drop items (10 + 3 > 10). After A
        # (5 dropped) there is room.
        order = replace(pickup("P", 100, 0, 3), is_new=True)
        result = insert_orders({"t1": self.tour(10)}, [order], GridModel())
        ids = result.tours["t1"].stop_ids
        assert ids.index("P") > ids.index("A")
        assert max_load(result.tours["t1"].stops) <= 10

    def test_time_windows_still_give_the_old_reason(self):
        order = replace(drop("N", 0, 1500, 1, tw_end=470), is_new=True)
        result = insert_orders({"t1": self.tour(10)}, [order], GridModel())
        assert result.unplaced[0].reason == REASON_NO_POSITION


def test_optimizer_keeps_the_capacity_with_pickups():
    # The tour ends at E(0,2100), beyond A. D P A E (21 min) is much
    # shorter than D A P E (59 min), but P before A means 5 + 3 > 6 on board.
    tour = Tour("t1", (stop("D", 0, 0, tw_start=480), drop("A", 0, 2000, 5),
                       pickup("P", 0, 100, 3), stop("E", 0, 2100)), capacity=6)
    assert improve_tour(tour, GridModel()).stop_ids == ("D", "A", "P", "E")
    roomy = replace(tour, capacity=8)
    better = improve_tour(roomy, GridModel())
    assert better.stop_ids == ("D", "P", "A", "E") and better.capacity == 8


def test_each_tour_can_have_its_own_speed():
    class Slow(GridModel):
        def travel_exact(self, a, b):
            return 10 * super().travel_exact(a, b)

    tours = {"fast": Tour("fast", (stop("D", 0, 0, tw_start=480), stop("E", 0, 0))),
             "slow": Tour("slow", (stop("D2", 0, 0, tw_start=480), stop("E2", 0, 0)))}
    order = stop("N", 0, 1000, tw_end=480 + 30, new=True)
    # fast: 10 min there -> fits. slow: 100 min -> too late.
    result = insert_orders(tours, [order], GridModel(), models={"slow": Slow()})
    assert result.inserted[0].tour_id == "fast"
    only_slow = insert_orders({"slow": tours["slow"]}, [order], GridModel(),
                              models={"slow": Slow()})
    assert only_slow.unplaced and only_slow.unplaced[0].order_id == "N"
