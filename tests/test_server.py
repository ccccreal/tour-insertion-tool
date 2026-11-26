"""Web API (FastAPI) with the test client, no network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from test_cli import FakeProvider  # noqa: E402
from tour_insertion.geocoding import OK, Geocoder  # noqa: E402
from tour_insertion.server import create_app  # noqa: E402

EXAMPLES = Path(__file__).resolve().parents[1] / "examples"


@pytest.fixture
def client():
    table = {"Depot, 69003 Lyon": (45.760, 4.860, OK),
             "Stop 1, 69001 Lyon": (45.767, 4.834, OK),
             "New 1, 69002 Lyon": (45.750, 4.830, OK)}
    fake = Geocoder(None, {"FR": FakeProvider("FR", table)})
    return TestClient(create_app(lambda settings: fake))


def plan(client, tours: bytes, orders: bytes, settings: dict | None = None):
    return client.post("/api/plan", files={"tours": ("tours.csv", tours),
                                           "orders": ("orders.csv", orders)},
                       data={"settings": json.dumps(settings or {})})


def test_health_profiles_examples(client):
    assert client.get("/api/health").json() == "ok"
    assert "Standard" in client.get("/api/profiles").json()
    names = client.get("/api/examples").json()
    assert "winterthur" in names
    csv_text = client.get("/api/examples/winterthur/tours").text
    assert csv_text.startswith("tour_id,seq")
    assert client.get("/api/examples/nope/tours").status_code == 404
    assert client.get("/api/templates/orders").text.startswith("order_id")


def test_plan_winterthur(client):
    r = plan(client, (EXAMPLES / "winterthur/tours.csv").read_bytes(),
             (EXAMPLES / "winterthur/orders.csv").read_bytes(), {"optimize": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["tours"]) == 2 and body["n_orders"] == 7
    assert len(body["inserted"]) == 6 and body["unplaced"][0]["order_id"] == "N7"
    assert body["unplaced"][0]["lat"] is not None          # can be dragged in
    assert body["changes"] and body["csv"]["tours"].startswith("tour_id")
    first = body["tours"][0]["stops"][0]
    assert first["arrival_hhmm"] == "08:00" and first["stop_id"].endswith("-00")


def test_plan_bad_input_is_400(client):
    r = plan(client, b"tour_id,seq,stop_id,address,lat,lon\nA,x,A0,a,1,2\n", b"order_id\n")
    assert r.status_code == 400 and "seq" in r.json()["detail"]
    r = plan(client, b"\xff\xfe\x00bad", b"x")
    assert r.status_code == 400


def test_plan_with_geocoding_and_skipped_stop(client):
    tours = ("tour_id,seq,stop_id,address,country,lat,lon,tw_start,tw_end,service_min\n"
             "A,0,A0,\"Depot, 69003 Lyon\",FR,,,08:00,,0\n"
             "A,1,A1,\"Stop 1, 69001 Lyon\",FR,,,,,\n"
             "A,2,A2,\"Missing, 69001 Lyon\",FR,,,,,\n"
             "A,3,A3,\"Depot, 69003 Lyon\",FR,,,,,0\n").encode()
    orders = "order_id,address,country\nN1,\"New 1, 69002 Lyon\",FR\n".encode()
    body = plan(client, tours, orders).json()
    assert [(s["tour_id"], s["order_id"]) for s in body["skipped"]] == [("A", "A2")]
    assert "N1" in [s["stop_id"] for s in body["tours"][0]["stops"]]


def test_schedule_after_manual_move(client):
    body = plan(client, (EXAMPLES / "winterthur/tours.csv").read_bytes(),
                (EXAMPLES / "winterthur/orders.csv").read_bytes()).json()
    tours = body["tours"]
    # move the second stop of T1 to the end of T2 (before its depot)
    stop = tours[0]["stops"].pop(1)
    stop["moved"] = True
    tours[1]["stops"].insert(len(tours[1]["stops"]) - 1, stop)
    r = client.post("/api/schedule", json={"settings": body["settings"], "tours": tours,
                                          "unplaced": body["unplaced"]})
    assert r.status_code == 200, r.text
    out = r.json()
    ids_t2 = [s["stop_id"] for s in out["tours"][1]["stops"]]
    assert ids_t2[-2] == stop["stop_id"] and ids_t2[-1].endswith("-99")
    assert out["tours"][1]["duration"] > tours[1]["duration"]       # longer now
    assert "N7" in out["csv"]["unplaced"]


def test_schedule_marks_late_stops(client):
    stops = [{"stop_id": "D", "lat": 47.5, "lon": 8.72, "tw_start": 480},
             {"stop_id": "X", "lat": 47.6, "lon": 8.72, "tw_end": 485, "service_min": 3},
             {"stop_id": "E", "lat": 47.5, "lon": 8.72}]
    out = client.post("/api/schedule", json={"tours": [{"tour_id": "T", "stops": stops}]}).json()
    assert [s["late"] for s in out["tours"][0]["stops"]] == [False, True, False]


def test_password(monkeypatch):
    monkeypatch.setenv("TOOL_PASSWORD", "secret")
    c = TestClient(create_app())
    assert c.get("/api/health").status_code == 401
    assert c.get("/api/health", auth=("x", "wrong")).status_code == 401
    assert c.get("/api/health", auth=("anyone", "secret")).status_code == 200


def test_items_are_read_and_returned(client):
    tours = (b"tour_id,seq,stop_id,address,lat,lon,tw_start,service_min,items\n"
             b"A,0,A0,Depot,47.50,8.72,08:00,0,\n"
             b"A,1,A1,One,47.51,8.73,,,3\n"
             b"A,2,A2,Depot,47.50,8.72,,0,\n")
    orders = b"order_id,address,lat,lon,items\nN1,New,47.505,8.725,2\n"
    body = plan(client, tours, orders).json()
    stops = {s["stop_id"]: s for s in body["tours"][0]["stops"]}
    assert stops["A1"]["items"] == 3 and stops["N1"]["items"] == 2
    assert body["tours"][0]["items"] == 5
    assert "items" in body["csv"]["tours"].splitlines()[0]


def test_insert_endpoint_places_a_stop(client):
    body = plan(client, (EXAMPLES / "winterthur/tours.csv").read_bytes(),
                (EXAMPLES / "winterthur/orders.csv").read_bytes()).json()
    stop = {"stop_id": "X1", "lat": 47.5050, "lon": 8.7400, "service_min": 3,
            "is_new": True, "items": 1}
    r = client.post("/api/insert", json={"settings": body["settings"], "tours": body["tours"],
                                         "stop": stop, "unplaced": []})
    out = r.json()
    assert r.status_code == 200 and out["inserted"]
    all_ids = [s["stop_id"] for t in out["tours"] for s in t["stops"]]
    assert all_ids.count("X1") == 1
    # restricted to one tour
    r = client.post("/api/insert", json={"settings": body["settings"], "tours": body["tours"],
                                         "stop": stop, "tour_id": "T1",
                                         "skipped": [{"order_id": "S1", "tour_id": "T2",
                                                      "address": "Nowhere", "reason": "geocode_not_found"}]})
    assert r.json()["tour_id"] == "T1"
    assert "S1" in r.json()["csv"]["skipped"]
    # impossible time window -> not inserted
    late = dict(stop, tw_end=481)
    r = client.post("/api/insert", json={"settings": body["settings"], "tours": body["tours"],
                                         "stop": late})
    assert r.json() == {"inserted": False, "reason": "no_feasible_position"}
    # same id twice -> 400
    dup = dict(stop, stop_id="T1-01")
    assert client.post("/api/insert", json={"tours": body["tours"], "stop": dup}).status_code == 400


def test_geocode_failure_keeps_details_for_manual_placing(client):
    orders = ("order_id,address,country,tw_end,service_min,items\n"
              "N9,\"Unknown, 69002 Lyon\",FR,10:00,5,7\n").encode()
    body = plan(client, (EXAMPLES / "winterthur/tours.csv").read_bytes(), orders).json()
    u = body["unplaced"][0]
    assert (u["order_id"], u["lat"], u["tw_end"], u["service_min"], u["items"]) == ("N9", None, 600, 5, 7)


def test_vehicles_capacity_and_kind(client):
    tours = ("tour_id,seq,stop_id,address,country,lat,lon,tw_start,tw_end,service_min,items,type,vehicle\n"
             "T1,0,D,Depot,CH,47.5000,8.7236,08:00,,0,,,Velo\n"
             "T1,1,A,A,CH,47.4930,8.7180,,,3,6,,\n"
             "T1,2,E,Depot,CH,47.5000,8.7236,,,0,,,\n").encode()
    orders = ("order_id,address,country,lat,lon,items,type\n"
              "N1,P,CH,47.5030,8.7170,3,pickup\n"
              "N2,Q,CH,47.4990,8.7520,5,drop\n").encode()
    settings = {"vehicles": {"Velo": {"capacity": 10, "speed_kmh": 15}}}
    body = plan(client, tours, orders, settings).json()
    tour = body["tours"][0]
    assert (tour["vehicle"], tour["capacity"]) == ("Velo", 10)
    # N1 is a pickup after A (6 dropped first): fits. N2 would make 11 > 10.
    assert [u["order_id"] for u in body["unplaced"]] == ["N2"]
    assert body["unplaced"][0]["reason"] == "capacity"
    kinds = {s["stop_id"]: (s["kind"], s["load"]) for s in tour["stops"]}
    assert kinds["N1"][0] == "pickup" and tour["max_load"] <= 10
    assert "load" in body["csv"]["tours"].splitlines()[0]
    # a manual move that overloads the tour is allowed but flagged
    n2 = {"stop_id": "N2", "lat": 47.499, "lon": 8.752, "items": 5, "is_new": True}
    tour["stops"].insert(1, n2)
    r = client.post("/api/schedule", json={"settings": body["settings"], "tours": [tour]})
    assert r.json()["tours"][0]["over_capacity"] is True
    # unknown vehicle -> 400
    r = plan(client, tours, orders, {"vehicles": {}})
    assert r.status_code == 400 and "Velo" in r.json()["detail"]


def test_page_files_are_revalidated(client):
    r = client.get("/app.js")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"


@pytest.fixture
def zurich_client():
    from test_import import FakeGeocoder
    return TestClient(create_app(lambda settings: FakeGeocoder()))


def test_inspect_and_plan_examples(zurich_client):
    examples = zurich_client.get("/api/plan-examples").json()
    assert {e["name"] for e in examples} == {"praxen", "standorte"}
    data = zurich_client.get("/api/plan-examples/standorte/stops").content
    r = zurich_client.post("/api/inspect", files={"file": ("standorte.xlsx", data)})
    assert r.status_code == 200
    sheet = r.json()["sheets"][0]
    assert sheet["headers"][:2] == ["Standort", "Adresse"] and sheet["rows"] == 21
    bad = zurich_client.post("/api/inspect", files={"file": ("x.xlsx", b"not excel")})
    assert bad.status_code == 400


def test_plan_from_scratch_with_template(zurich_client):
    data = zurich_client.get("/api/plan-examples/standorte/stops").content
    template = zurich_client.get("/api/plan-examples/standorte/template").json()
    settings = {"depot": "47.3779, 8.5403", "day_start": 480, "day_end": 660,
                "vehicles": {"Lastenvelo": {"capacity": 10, "speed_kmh": 15, "count": 2},
                             "Auto": {"capacity": 40, "speed_kmh": 20, "count": 1}}}
    r = zurich_client.post("/api/plan", files={"orders": ("standorte.xlsx", data)},
                           data={"settings": json.dumps(settings), "template": json.dumps(template)})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "new"
    placed = sum(len(t["stops"]) - 2 for t in body["tours"])
    assert placed + len(body["unplaced"]) == 20
    tour = body["tours"][0]
    assert set(tour["quantities"]) == {"Box 50er", "Box 25er"}
    stop = tour["stops"][1]
    assert stop["name"] and set(stop["quantities"]) == {"Box 50er", "Box 25er"}
    assert "Box 50er" in body["csv"]["tours"].splitlines()[0]
    # a manual move keeps names and quantities
    r = zurich_client.post("/api/schedule", json={"settings": body["settings"],
                                                  "tours": body["tours"]})
    assert r.json()["tours"][0]["stops"][1]["quantities"] == stop["quantities"]
    # Excel without template -> clear message
    r = zurich_client.post("/api/plan", files={"orders": ("standorte.xlsx", data)},
                           data={"settings": json.dumps(settings)})
    assert r.status_code == 400 and "columns" in r.json()["detail"]
