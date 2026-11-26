"""CSV reading/writing, map and end-to-end runs."""

from __future__ import annotations

import csv
from pathlib import Path

from tour_insertion.cli import main
from tour_insertion.csv_io import format_time, parse_time, read_orders, read_tours
from tour_insertion.geocoding import NOT_FOUND, OK, UNCERTAIN, GeocodeResult, Geocoder

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"
WINTERTHUR = EXAMPLES / "winterthur"


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
    assert tours["T1"].stops[0].stop_id == "T1-00"      # sorted by seq
    assert all(o.is_new for o in orders)


def test_end_to_end_with_map(tmp_path):
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path), "--map"]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    unplaced = read_csv(tmp_path / "unplaced.csv")
    inserted = {r["stop_id"] for r in rows if r["is_new"] == "1"}
    assert len(inserted) + len(unplaced) == len(read_orders(WINTERTHUR / "orders.csv"))
    for tour_id in ("T1", "T2"):            # depots stay first and last
        ids = [r["stop_id"] for r in rows if r["tour_id"] == tour_id]
        assert ids[0].endswith("-00") and ids[-1].endswith("-99")
    page = (tmp_path / "map.html").read_text(encoding="utf-8")
    assert "leaflet" in page.lower() and "T1-00" in page and "N5" in page
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
        "A,0,A0,\"Depot, 69003 Lyon\",FR,,,08:00,,0\n"
        "A,1,A1,\"Stop 1, 69001 Lyon\",FR,,,,,\n"
        "A,2,A2,\"Depot, 69003 Lyon\",FR,,,,,0\n", encoding="utf-8")
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
    assert main([str(tours), str(orders), "-o", str(out), "--map"],
                geocoder=geocoder) == 0
    placed = {r["stop_id"] for r in read_csv(out / "tours_new.csv") if r["is_new"] == "1"}
    reasons = {r["order_id"]: r["reason"] for r in read_csv(out / "unplaced.csv")}
    assert placed == {"N1"}
    assert reasons == {"N2": "geocode_uncertain", "N3": "geocode_not_found"}
    assert len(read_csv(out / "geocoding.csv")) == 6     # one row per geocoded row


def test_depot_that_cannot_be_geocoded_stops_the_run(tmp_path):
    import pytest
    tours = tmp_path / "tours.csv"
    tours.write_text(
        "tour_id,seq,stop_id,address,country,lat,lon\n"
        "A,0,A0,\"Nowhere, 69003 Lyon\",FR,,\n", encoding="utf-8")
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,address,country,lat,lon\n", encoding="utf-8")
    geocoder = Geocoder(None, {"FR": FakeProvider("FR", {})})
    with pytest.raises(SystemExit, match="depot"):
        main([str(tours), str(orders), "-o", str(tmp_path / "o")], geocoder=geocoder)


def test_tour_stop_not_found_is_skipped_and_reported(tmp_path):
    tours = tmp_path / "tours.csv"
    tours.write_text(
        "tour_id,seq,stop_id,address,country,lat,lon,tw_start,tw_end,service_min\n"
        "A,0,A0,\"Depot, 69003 Lyon\",FR,,,08:00,,0\n"
        "A,1,A1,\"Stop 1, 69001 Lyon\",FR,,,,,\n"
        "A,2,A2,\"Missing, 69001 Lyon\",FR,,,,,\n"
        "A,3,A3,\"Depot, 69003 Lyon\",FR,,,,,0\n", encoding="utf-8")
    orders = tmp_path / "orders.csv"
    orders.write_text("order_id,address,country,lat,lon\n"
                      "N1,\"New 1, 69002 Lyon\",FR,,\n", encoding="utf-8")
    table = {"Depot, 69003 Lyon": (45.760, 4.860, OK),
             "Stop 1, 69001 Lyon": (45.767, 4.834, OK),
             "New 1, 69002 Lyon": (45.750, 4.830, OK)}
    out = tmp_path / "out"
    assert main([str(tours), str(orders), "-o", str(out)],
                geocoder=Geocoder(None, {"FR": FakeProvider("FR", table)})) == 0
    ids = [r["stop_id"] for r in read_csv(out / "tours_new.csv")]
    assert "A2" not in ids and "N1" in ids
    skipped = read_csv(out / "skipped_stops.csv")
    assert [(r["tour_id"], r["stop_id"], r["reason"]) for r in skipped] == [
        ("A", "A2", "geocode_not_found")]


def test_optimize_flag_writes_moved_column(tmp_path):
    assert main([str(WINTERTHUR / "tours.csv"), str(WINTERTHUR / "orders.csv"),
                 "-o", str(tmp_path), "--optimize"]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    assert "moved" in rows[0]
    for tour_id in ("T1", "T2"):            # depots still first and last
        ids = [r["stop_id"] for r in rows if r["tour_id"] == tour_id]
        assert ids[0].endswith("-00") and ids[-1].endswith("-99")


def test_tours_ending_at_a_connection_point(tmp_path):
    folder = EXAMPLES / "anschluss"
    assert main([str(folder / "tours.csv"), str(folder / "orders.csv"),
                 "-o", str(tmp_path), "--map"]) == 0
    rows = read_csv(tmp_path / "tours_new.csv")
    for tour_id, end in (("T1", "T1-99"), ("T2", "T2-99")):
        ids = [r["stop_id"] for r in rows if r["tour_id"] == tour_id]
        assert ids[0] == f"{tour_id}-00" and ids[-1] == end
    # T2 must be at its connection point by 09:30: N2 (+21 min) does not fit
    t2_end = [r for r in rows if r["stop_id"] == "T2-99"][0]
    assert parse_time(t2_end["arrival"]) <= parse_time("09:30")
    assert "N2" in {r["order_id"] for r in read_csv(tmp_path / "unplaced.csv")}
    page = (tmp_path / "map.html").read_text(encoding="utf-8")
    page = page.replace("\\u003e", ">").replace("\\u003c", "<")   # folium escapes < >
    assert ">S</div>" in page and ">E</div>" in page and ">D</div>" not in page
