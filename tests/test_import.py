"""Stop lists in other layouts (Excel) and planning from scratch."""

from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

import pytest

from tour_insertion.cli import main
from tour_insertion.geocoding import OK, GeocodeResult
from tour_insertion.load import max_load
from tour_insertion.model import Tour
from tour_insertion.pipeline import InputError, Settings, customers, plan_new, prepare_stops
from tour_insertion.stop_import import Template, inspect, order_rows

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


def template(name):
    return Template.from_json(json.loads((EXAMPLES / name / "template.json").read_text()))


def stops(name):
    return (EXAMPLES / name / "stops.xlsx").read_bytes()


class FakeGeocoder:
    """Every address gets a fixed point in Zurich (hash of the text)."""

    def geocode(self, address, country=""):
        h = hashlib.sha256(address.encode()).digest()
        lat = 47.33 + h[0] / 255 * 0.10
        lon = 8.46 + h[1] / 255 * 0.16
        return GeocodeResult(address, "CH", OK, lat, lon, address, None)

    def save(self):
        pass


def test_two_products_are_summed_and_kept():
    rows, missing = order_rows(stops("standorte"), "stops.xlsx", template("standorte"))
    assert len(rows) == 20 and not missing                  # "Total" row skipped
    b = next(r for r in rows if r["order_id"] == "B")
    assert b["_quantities"] == {"Box 50er": 2, "Box 25er": 0} and b["items"] == "2"
    assert b["address"] == "Rautistrasse 303, 8047 Zürich"  # floor not geocoded
    assert b["_info"]["floor"] == "1. Stock"


def test_address_from_three_columns_and_info():
    rows, missing = order_rows(stops("praxen"), "stops.xlsx", template("praxen"))
    assert len(rows) == 141 and not missing
    first = rows[0]
    assert first["order_id"] == "001" and first["items"] == "1"    # "Nr." is always 1
    assert first["address"] == "Rämistrasse 23, 8001 Zürich"
    assert "Öffnungszeiten" in first["_info"]
    assert first["_name"].endswith("Muster")


def test_guess_finds_the_columns():
    g = inspect(stops("standorte"), "stops.xlsx")["guess"]
    assert g["address"] == ["Adresse"] and g["id"] == "Standort"
    assert g["quantities"] == ["Box 50er", "Box 25er"]
    g = inspect(stops("praxen"), "stops.xlsx")["guess"]
    assert (g["address"], g["postcode"], g["city"]) == (["zuweiserstrasse"], "zuweiserplz", "zuweiserort")
    assert g["quantities"] == [] and "Öffnungszeiten" in g["info"]


def test_csv_with_semicolons_and_missing_address():
    data = "Kunde;Strasse;Ort;Pakete\nA;Bahnhofstrasse 1;8001 Zürich;3\nB;;;2\nSumme;;;5\n".encode()
    t = Template.from_json({"address": ["Strasse"], "city": "Ort", "id": "Kunde",
                            "quantities": ["Pakete"]})
    rows, missing = order_rows(data, "kunden.csv", t)
    assert [r["order_id"] for r in rows] == ["A"] and rows[0]["address"] == "Bahnhofstrasse 1, 8001 Zürich"
    assert [r["order_id"] for r in missing] == ["B"]


def test_bad_template_and_columns():
    with pytest.raises(ValueError):
        Template.from_json({"address": []})
    with pytest.raises(ValueError, match="not found"):
        order_rows(stops("standorte"), "stops.xlsx", Template.from_json({"address": ["Nope"]}))


def plan_settings(**kw):
    vehicles = {"Lastenvelo": {"capacity": 10, "speed_kmh": 15, "count": 3},
                "Auto": {"capacity": 40, "speed_kmh": 20, "count": 2}}
    return Settings(depot="47.3779, 8.5403", day_start=480, day_end=660,
                    vehicles=vehicles, **kw)


def test_plan_from_scratch_respects_capacity_and_return_time():
    rows, missing = order_rows(stops("standorte"), "stops.xlsx", template("standorte"))
    result = plan_new(rows, plan_settings(), FakeGeocoder(), missing)
    placed = sum(len(s.tour) - 2 for s in result.schedules)
    assert placed + len(result.unplaced) == 20
    for s in result.schedules:
        assert s.tour.capacity is None or max_load(s.tour.stops) <= s.tour.capacity
        assert s.times[-1].arrival <= 660
        assert len(s.tour) > 2                                  # unused vehicles left out
        assert s.tour.stops[0].stop_id == f"{s.tour.tour_id} start"
    ids = sorted(s.tour.tour_id for s in result.schedules)
    for vehicle in ("Lastenvelo", "Auto"):           # numbered without gaps
        numbers = [int(i.rsplit(" ", 1)[1]) for i in ids if i.startswith(vehicle)]
        assert numbers == list(range(1, len(numbers) + 1))


def test_plan_needs_depot_and_vehicles():
    with pytest.raises(InputError, match="depot"):
        plan_new([], Settings(), FakeGeocoder())
    with pytest.raises(InputError, match="vehicle"):
        plan_new([], Settings(depot="47.3, 8.5", vehicles={"Velo": {"capacity": 5}}),
                 FakeGeocoder())


def test_cli_plans_from_an_excel_file(tmp_path):
    assert main(["--stops", str(EXAMPLES / "praxen" / "stops.xlsx"),
                 "--template", str(EXAMPLES / "praxen" / "template.json"),
                 "--depot", "47.3779, 8.5403", "--fleet", "Lastenvelo=6", "Auto=2",
                 "--end", "13:00", "-o", str(tmp_path)], geocoder=FakeGeocoder()) == 0
    with open(tmp_path / "tours_new.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    header = list(rows[0])
    assert "Öffnungszeiten" in header and "name" in header
    assert {r["tour_id"].rsplit(" ", 1)[0] for r in rows} <= {"Lastenvelo", "Auto"}
    with open(tmp_path / "unplaced.csv", newline="", encoding="utf-8") as f:
        unplaced = list(csv.DictReader(f))
    new_stops = [r for r in rows if r["is_new"] == "1"]
    assert len(new_stops) + len(unplaced) == 141


def test_vehicle_priority_fills_bikes_first():
    rows, missing = order_rows(stops("standorte"), "stops.xlsx", template("standorte"))
    vehicles = {"Lastenvelo": {"capacity": 10, "speed_kmh": 15, "count": 4},
                "Auto": {"capacity": 40, "speed_kmh": 20, "count": 1}}
    plain = plan_new(rows, Settings(depot="47.3779, 8.5403", vehicles=vehicles), FakeGeocoder())
    bikes = plan_new(rows, Settings(depot="47.3779, 8.5403", vehicles=vehicles,
                                    vehicle_priority=True), FakeGeocoder())
    items = lambda r, v: sum(s.items or 0 for sc in r.schedules if sc.tour.vehicle == v
                             for s in sc.tour.stops)
    assert items(bikes, "Lastenvelo") == 40            # all 4 bikes full first
    assert items(bikes, "Lastenvelo") > items(plain, "Lastenvelo")
    placed = sum(len(customers(sc.tour)) for sc in bikes.schedules)
    assert placed + len(bikes.unplaced) == 20


def test_tours_planned_by_hand_stay_as_they_are():
    rows, missing = order_rows(stops("standorte"), "stops.xlsx", template("standorte"))
    prepared = prepare_stops(rows, plan_settings(), FakeGeocoder())
    by_id = {o.stop_id: o for o in prepared.orders}
    # by hand: an over-full bike tour in a deliberately odd order
    hand = Tour("Lastenvelo 1", tuple(by_id[i] for i in ("T", "A", "K", "B")),
                vehicle="Lastenvelo")
    result = plan_new(rows, plan_settings(), FakeGeocoder(), fixed={"Lastenvelo 1": hand})
    tours = {sc.tour.tour_id: sc for sc in result.schedules}
    kept = tours["Lastenvelo 1"].tour
    assert [s.stop_id for s in customers(kept)] == ["T", "A", "K", "B"]
    assert kept.stops[0].role == "start" and kept.stops[-1].role == "end"
    others = [s.stop_id for t, sc in tours.items() if t != "Lastenvelo 1"
              for s in customers(sc.tour)]
    assert not {"T", "A", "K", "B"} & set(others)           # not planned twice
    assert "Lastenvelo 1" in tours and sum(t.startswith("Lastenvelo") for t in tours) <= 3
    assert len(others) + 4 + len(result.unplaced) == 20
