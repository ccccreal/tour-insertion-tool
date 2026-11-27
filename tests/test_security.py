"""Headers, body limit, short logs, terms of use (security.py, server.py)."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient

from tour_insertion.security import Legal, short_ip, user_of
from tour_insertion.server import WEB, create_app


def test_security_headers_on_page_and_api():
    c = TestClient(create_app())
    for path in ("/", "/api/health"):
        r = c.get(path)
        csp = r.headers["content-security-policy"]
        assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
        assert r.headers["x-content-type-options"] == "nosniff"
        assert r.headers["x-frame-options"] == "DENY"


def test_page_loads_no_code_from_other_servers():
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "cdn." not in html and "https://" not in html
    assert (WEB / "vendor" / "leaflet" / "leaflet.js").exists()


def test_api_docs_off_by_default(monkeypatch):
    assert TestClient(create_app()).get("/api/docs").status_code != 200
    monkeypatch.setenv("TOOL_API_DOCS", "1")
    assert TestClient(create_app()).get("/api/docs").status_code == 200


def test_body_limit(monkeypatch):
    monkeypatch.setenv("TOOL_MAX_BODY_MB", "0.01")          # about 10 kB
    c = TestClient(create_app())
    r = c.post("/api/inspect", files={"file": ("a.csv", b"x" * 50_000)})
    assert r.status_code == 413


def test_short_ip():
    assert short_ip("192.168.1.23") == "192.168.1.0"
    assert short_ip("2001:db8:85a3:8d3:1319:8a2e:370:7348") == "2001:db8:85a3::"
    assert short_ip(None) == "-"


def test_access_log_has_no_query_and_short_ip(caplog):
    c = TestClient(create_app())
    with caplog.at_level(logging.INFO, logger="tour_insertion.access"):
        c.get("/api/health?secret=Bahnhofstrasse")
    line = caplog.records[-1].getMessage()
    assert "/api/health" in line and "Bahnhofstrasse" not in line


def test_access_log_off(monkeypatch, caplog):
    monkeypatch.setenv("TOOL_ACCESS_LOG", "off")
    c = TestClient(create_app())
    with caplog.at_level(logging.INFO, logger="tour_insertion.access"):
        c.get("/api/health")
    assert not caplog.records


def test_cache_mode_is_checked(monkeypatch):
    monkeypatch.setenv("TOOL_GEOCODE_CACHE_MODE", "sometimes")
    with pytest.raises(ValueError):
        create_app()


def test_user_of():
    basic = {"authorization": "Basic YW5uYTpwdw=="}            # anna:pw
    assert user_of(basic, False) == "anna"
    assert user_of({"x-remote-user": "ben", **basic}, True) == "ben"
    assert user_of({"x-remote-user": "ben"}, False) is None


# --- terms of use ------------------------------------------------------------

@pytest.fixture
def legal_dir(tmp_path):
    folder = tmp_path / "legal"
    folder.mkdir()
    (folder / "terms.html").write_text("<h1>Nutzungsbedingungen</h1>")
    (folder / "imprint.html").write_text("<h1>Impressum</h1>")
    return folder


def test_without_legal_dir_nothing_is_asked():
    c = TestClient(create_app())
    assert c.get("/api/legal").json()["enabled"] is False
    assert c.get("/legal/terms").status_code == 404


def test_terms_must_be_accepted_by_each_user(monkeypatch, tmp_path, legal_dir):
    log = tmp_path / "accepted.jsonl"
    monkeypatch.setenv("TOOL_LEGAL_DIR", str(legal_dir))
    monkeypatch.setenv("TOOL_CONSENT_LOG", str(log))
    c = TestClient(create_app())
    anna = ("anna", "pw")
    state = c.get("/api/legal", auth=anna).json()
    assert state["enabled"] and state["accepted"] is False
    assert state["pages"] == ["terms", "imprint"]
    assert c.get("/api/profiles", auth=anna).status_code == 403
    assert c.get("/legal/imprint").status_code == 200             # always open
    assert c.post("/api/legal/accept", json={"version": "old"}, auth=anna).status_code == 409
    assert c.post("/api/legal/accept", json={"version": state["version"]}, auth=anna).status_code == 200
    assert c.get("/api/profiles", auth=anna).status_code == 200
    assert c.get("/api/profiles", auth=("ben", "pw")).status_code == 403
    entry = json.loads(log.read_text().splitlines()[0])
    assert entry["user"] == "anna" and entry["version"] == state["version"]


def test_changed_terms_ask_again(tmp_path, legal_dir):
    legal = Legal(legal_dir, tmp_path / "log.jsonl")
    first = legal.version()
    assert legal.accept("anna", first) and legal.accepted("anna")
    assert Legal(legal_dir, tmp_path / "log.jsonl").accepted("anna")   # read from file
    (legal_dir / "terms.html").write_text("<h1>Neue Bedingungen</h1>")
    assert legal.version() != first and not legal.accepted("anna")
