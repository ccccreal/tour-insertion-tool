"""CSV reading/writing, map and end-to-end runs."""

from __future__ import annotations

import csv
from pathlib import Path

from tour_insertion.cli import main
from tour_insertion.csv_io import format_time, parse_time, read_orders, read_tours
from tour_insertion.geocoding import NOT_FOUND, OK, UNCERTAIN, GeocodeResult, Geocoder

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
WINTERTHUR = EXAMPLES / "winterthur"
DEPOT = ["--depot", "47.5000, 8.7236"]          # Winterthur HB


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_time_roundtrip():
    assert parse_time("08:30") == 510
    assert parse_time("") is None
    assert format_time(510) == "08:30"
    assert format_time(None) == ""


def test_read_examples():
    tours = read_tours(WINTERTHUR / "tours.csv")
    orders = read_orders(WINTERTHUR / "orders.csv")
    assert set(tours) == {"T1", "T2"}
    assert tours["T1"].stops[0].stop_id == "T1-01"      # sorted by seq, no depot rows
    assert tours["T1"].vehicle == "Lastenvelo"
    assert all(o.is_new for o in orders)


def test_end_to_end_with_map(tmp_path):
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path), "--map", *DEPOT]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    unplaced = read_csv(tmp_path / "unplaced.csv")
    inserted = {r["stop_id"] for r in rows if r["is_new"] == "1"}
    assert len(inserted) + len(unplaced) == len(read_orders(WINTERTHUR / "orders.csv"))
    for tour_id in ("T1", "T2"):            # start and end from the settings
        tour = [r for r in rows if r["tour_id"] == tour_id]
        assert tour[0]["stop_id"] == f"{tour_id} start" and tour[0]["type"] == "start"
        assert tour[-1]["stop_id"] == f"{tour_id} end" and tour[-1]["type"] == "end"
        assert tour[0]["arrival"] == "08:00"
    page = (tmp_path / "map.html").read_text(encoding="utf-8")
    assert "leaflet" in page.lower() and "T1 start" in page and "N7" in page
    assert (tmp_path / "tours.xlsx").exists() and (tmp_path / "tours.pdf").exists()
    assert not (tmp_path / "geocoding.csv").exists()     # nothing geocoded


class FakeProvider:
    """Answers from a dict address -> (lat, lon, status)."""

    def __init__(self, country, table):
        self.country, self.table = country, table

    def geocode(self, address):
        if address not in self.table:
            return GeocodeResult(address, self.country, NOT_FOUND)
        lat, lon, status = self.table[address]
        return GeocodeResult(address, self.country, status, lat, lon, address, 0.9)


def test_end_to_end_with_geocoding(tmp_path):
    tours = tmp_path / "tours.csv"
    tours.write_text(
        "tour_id,seq,stop_id,address,country,lat,lon,tw_start,tw_end,service_min\n"
        "A,1,A1,\"Stop 1, 69001 Lyon\",FR,,,,,\n", encoding="utf-8")
    orders = tmp_path / "orders.csv"
    orders.write_text(
        "order_id,address,country,lat,lon,tw_start,tw_end,service_min\n"
        "N1,\"New 1, 69002 Lyon\",,,,,,\n"
        "N2,\"Typo, 69002 Lyon\",FR,,,,,\n"
        "N3,\"Unknown, 69002 Lyon\",FR,,,,,\n", encoding="utf-8")
    table = {"Depot, 69003 Lyon": (45.760, 4.860, OK),
             "Stop 1, 69001 Lyon": (45.767, 4.834, OK),
             "New 1, 69002 Lyon": (45.750, 4.830, OK),
             "Typo, 69002 Lyon": (45.700, 4.800, UNCERTAIN)}
    geocoder = Geocoder(None, {"FR": FakeProvider("FR", table)})

    out = tmp_path / "out"
    assert main([str(tours), str(orders), "-o", str(out), "--map",
                 "--depot", "Depot, 69003 Lyon"], geocoder=geocoder) == 0
    placed = {r["stop_id"] for r in read_csv(out / "tours_new.csv") if r["is_new"] == "1"}
    reasons = {r["order_id"]: r["reason"] for r in read_csv(out / "unplaced.csv")}
    assert placed == {"N1"}
    assert reasons == {"N2": "geocode_uncertain", "N3": "geocode_not_found"}
    assert len(read_csv(out / "geocoding.csv")) == 5     # depot + one per geocoded row


def test_depot_that_cannot_be_geocoded_stops_the_run(tmp_path):
    import pytest
    tours = tmp_path / "tours.csv"
    tours.write_text(
        "tour_id,seq,stop_id,address,country,lat,lon\n"
        "A,1,A1,Stop,FR,45.76,4.86\n", encoding="utf-8")
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,address,country,lat,lon\n", encoding="utf-8")
    geocoder = Geocoder(None, {"FR": FakeProvider("FR", {})})
    with pytest.raises(SystemExit, match="depot"):
        main([str(tours), str(orders), "-o", str(tmp_path / "o"),
              "--depot", "Nowhere, 69003 Lyon"], geocoder=geocoder)
    with pytest.raises(SystemExit, match="depot"):           # no depot given
        main([str(tours), str(orders), "-o", str(tmp_path / "o")], geocoder=geocoder)


def test_tour_stop_not_found_is_skipped_and_reported(tmp_path):
    tours = tmp_path / "tours.csv"
    tours.write_text(
        "tour_id,seq,stop_id,address,country,lat,lon,tw_start,tw_end,service_min\n"
        "A,1,A1,\"Stop 1, 69001 Lyon\",FR,,,,,\n"
        "A,2,A2,\"Missing, 69001 Lyon\",FR,,,,,\n", encoding="utf-8")
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,address,country,lat,lon\n"
                      "N1,\"New 1, 69002 Lyon\",FR,,\n", encoding="utf-8")
    table = {"Depot, 69003 Lyon": (45.760, 4.860, OK),
             "Stop 1, 69001 Lyon": (45.767, 4.834, OK),
             "New 1, 69002 Lyon": (45.750, 4.830, OK)}
    out = tmp_path / "out"
    assert main([str(tours), str(orders), "-o", str(out), "--depot", "Depot, 69003 Lyon"],
                geocoder=Geocoder(None, {"FR": FakeProvider("FR", table)})) == 0
    ids = [r["stop_id"] for r in read_csv(out / "tours_new.csv")]
    assert "A2" not in ids and "N1" in ids
    skipped = read_csv(out / "skipped_stops.csv")
    assert [(r["tour_id"], r["stop_id"], r["reason"]) for r in skipped] == [
        ("A", "A2", "geocode_not_found")]


def test_optimize_flag_writes_moved_column(tmp_path):
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path), "--optimize", *DEPOT]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    assert "moved" in rows[0]
    for tour_id in ("T1", "T2"):            # start and end stay first and last
        ids = [r["stop_id"] for r in rows if r["tour_id"] == tour_id]
        assert ids[0] == f"{tour_id} start" and ids[-1] == f"{tour_id} end"


def test_start_and_end_settings(tmp_path):
    def tour_rows(*extra):
        out = tmp_path / "_".join(extra or ("default",))
        assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                     "-o", str(out), "--map", *DEPOT, *extra]) == 0
        rows = read_csv(out / "tours_new.csv")
        return {t: [r for r in rows if r["tour_id"] == t] for t in ("T1", "T2")}, out

    # end at a connection point by 10:00
    tours, out = tour_rows("--end-at", "47.5076, 8.7614", "--end", "10:00")
    for t in tours.values():
        assert t[-1]["type"] == "end" and t[-1]["lat"] == "47.5076"
        assert parse_time(t[-1]["arrival"]) <= parse_time("10:00")
    page = (out / "map.html").read_text(encoding="utf-8")
    page = page.replace("\\u003e", ">").replace("\\u003c", "<")   # folium escapes < >
    assert ">S</div>" in page and ">E</div>" in page and ">D</div>" not in page

    # open start and end: the tour starts at its first customer at 08:00
    tours, _ = tour_rows("--start-at", "first", "--end-at", "open")
    for t in tours.values():
        assert t[0]["type"] in ("drop", "pickup") and t[0]["arrival"] == "08:00"
        assert t[-1]["type"] in ("drop", "pickup")


def test_vehicles_capacity_and_pickups(tmp_path):
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path), *DEPOT]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    tour_of = {r["stop_id"]: r["tour_id"] for r in rows}
    # N3 lies on the bike tour T1, but the bike (10 items) is too full
    assert tour_of["N3"] == "T2"
    for tour_id, capacity in (("T1", 10), ("T2", 40)):
        loads = [int(r["load"]) for r in rows if r["tour_id"] == tour_id]
        assert max(loads) <= capacity
    t1 = [r["stop_id"] for r in rows if r["tour_id"] == "T1"]
    # the pickup N6 (6 items) only fits after some drops are done
    assert t1.index("N6") > t1.index("T1-02")
    assert {r["vehicle"] for r in rows if r["tour_id"] == "T1"} == {"Lastenvelo"}
    # N7 cannot be reached by 08:05
    assert [r["order_id"] for r in read_csv(tmp_path / "unplaced.csv")] == ["N7"]


def test_vehicles_file_and_unknown_vehicle(tmp_path):
    small = tmp_path / "vehicles.json"
    small.write_text('{"Lastenvelo": {"capacity": 5}, "Auto": {"capacity": 40}}')
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path / "out"), "--vehicles", str(small), *DEPOT]) == 0
    only_auto = tmp_path / "auto.json"
    only_auto.write_text('{"Auto": {"capacity": 40}}')
    try:
        main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
              "-o", str(tmp_path / "out2"), "--vehicles", str(only_auto), *DEPOT])
    except SystemExit as error:
        assert "Lastenvelo" in str(error)
    else:
        raise AssertionError("unknown vehicle must stop the run")
