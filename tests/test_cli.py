"""CSV reading/writing and an end-to-end run on the example data."""

from __future__ import annotations

import csv
from pathlib import Path

from tour_insertion.cli import main
from tour_insertion.csv_io import format_time, parse_time, read_orders, read_tours

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def test_time_roundtrip():
    assert parse_time("08:30") == 510
    assert parse_time("") is None
    assert format_time(510) == "08:30"
    assert format_time(None) == ""


def test_read_examples():
    tours = read_tours(EXAMPLES / "tours.csv")
    orders = read_orders(EXAMPLES / "orders.csv")
    assert set(tours) == {"T1", "T2"}
    assert tours["T1"].stops[0].stop_id == "T1-00"      # sorted by seq
    assert all(o.is_new for o in orders)


def test_end_to_end(tmp_path):
    assert main([str(EXAMPLES / "tours.csv"), str(EXAMPLES / "orders.csv"),
                 "-o", str(tmp_path)]) == 0
    with open(tmp_path / "tours_new.csv", newline="") as f:
        rows = list(csv.DictReader(f))
    with open(tmp_path / "unplaced.csv", newline="") as f:
        unplaced = list(csv.DictReader(f))
    inserted = {r["stop_id"] for r in rows if r["is_new"] == "1"}
    n_orders = len(read_orders(EXAMPLES / "orders.csv"))
    assert len(inserted) + len(unplaced) == n_orders
    # depots stay first and last in every tour
    for tour_id in ("T1", "T2"):
        ids = [r["stop_id"] for r in rows if r["tour_id"] == tour_id]
        assert ids[0].endswith("-00") and ids[-1].endswith("-99")
