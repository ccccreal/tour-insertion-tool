"""Geocoding without network: the HTTP call is replaced by canned answers
in the format the two services return."""

from __future__ import annotations

import pytest

from tour_insertion.geocoding import (NOT_FOUND, OK, UNCERTAIN, FranceGeocoder,
                                      Geocoder, SwissGeocoder, detect_country,
                                      fill_coordinates, postcode_of)


def fr_answer(lon, lat, score, label="label"):
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {"label": label, "score": score}}]}


def ch_answer(lat, lon, label):
    return {"results": [{"attrs": {"label": label, "lat": lat, "lon": lon,
                                   "origin": "address"}}]}


class Recorder:
    """Fake fetch: returns `answer`, remembers the calls."""

    def __init__(self, answer):
        self.answer, self.calls = answer, []

    def __call__(self, url, params):
        self.calls.append((url, params))
        return self.answer


# --- country ---------------------------------------------------------------

def test_postcode_and_country():
    assert postcode_of("1 Place de la Comédie, 69001 Lyon") == "69001"
    assert postcode_of("Technikumstrasse 9, 8400 Winterthur") == "8400"
    assert detect_country("x, 69001 Lyon") == "FR"
    assert detect_country("x, 8400 Winterthur") == "CH"
    assert detect_country("anything", "fr") == "FR"
    with pytest.raises(ValueError):
        detect_country("no postcode here")
    with pytest.raises(ValueError):
        detect_country("x", "DE")


# --- France ----------------------------------------------------------------

def test_france_swaps_lon_lat():
    fetch = Recorder(fr_answer(4.8357, 45.7640, 0.97, "1 Place de la Comédie 69001 Lyon"))
    r = FranceGeocoder(fetch, pause_s=0).geocode("1 place de la comedie lyon")
    assert (r.lat, r.lon) == (45.7640, 4.8357)        # GeoJSON is [lon, lat]
    assert r.status == OK and r.score == 0.97
    assert fetch.calls[0][1]["q"] == "1 place de la comedie lyon"


def test_france_low_score_is_uncertain():
    r = FranceGeocoder(Recorder(fr_answer(4.8, 45.7, 0.31)), pause_s=0).geocode("x")
    assert r.status == UNCERTAIN


def test_france_not_found():
    r = FranceGeocoder(Recorder({"features": []}), pause_s=0).geocode("x")
    assert r.status == NOT_FOUND and r.lat is None


# --- Switzerland -----------------------------------------------------------

def test_swiss_strips_html_and_checks_postcode():
    fetch = Recorder(ch_answer(47.4997, 8.7290,
                               "Technikumstrasse 9 <b>8400 Winterthur</b>"))
    r = SwissGeocoder(fetch).geocode("Technikumstrasse 9, 8400 Winterthur")
    assert r.status == OK and r.label == "Technikumstrasse 9 8400 Winterthur"
    assert (r.lat, r.lon) == (47.4997, 8.7290)
    assert fetch.calls[0][1]["origins"] == "address"


def test_swiss_other_postcode_is_uncertain():
    fetch = Recorder(ch_answer(46.9, 7.4, "Technikumstrasse 9 <b>3000 Bern</b>"))
    assert SwissGeocoder(fetch).geocode("Technikumstrasse 9, 8400 Winterthur").status == UNCERTAIN


# --- cache and filling rows ------------------------------------------------

def test_cache_avoids_second_lookup(tmp_path):
    fetch = Recorder(fr_answer(4.8, 45.7, 0.9))
    cache = tmp_path / "cache.json"
    g = Geocoder(cache, {"FR": FranceGeocoder(fetch, pause_s=0)})
    g.geocode("1 Rue A, 69001 Lyon")
    g.geocode("1  rue a, 69001 LYON")            # same after normalising
    g.save()
    assert len(fetch.calls) == 1
    g2 = Geocoder(cache, {"FR": FranceGeocoder(fetch, pause_s=0)})
    assert g2.geocode("1 Rue A, 69001 Lyon").status == OK
    assert len(fetch.calls) == 1 and g2.lookups == 0


def test_fill_coordinates_only_where_missing():
    fetch = Recorder(fr_answer(4.8, 45.7, 0.9))
    g = Geocoder(None, {"FR": FranceGeocoder(fetch, pause_s=0)})
    rows = [{"address": "1 Rue A, 69001 Lyon", "lat": "", "lon": ""},
            {"address": "2 Rue B, 69001 Lyon", "lat": "45.0", "lon": "4.0"}]
    results = fill_coordinates(rows, g)
    assert len(results) == 1 and len(fetch.calls) == 1
    assert rows[0]["lat"] == "45.7" and rows[0]["country"] == "FR"
    assert rows[1]["lat"] == "45.0"


def test_uncertain_rows_stay_without_coordinates():
    g = Geocoder(None, {"FR": FranceGeocoder(Recorder(fr_answer(4.8, 45.7, 0.2)),
                                             pause_s=0)})
    rows = [{"address": "1 Rue A, 69001 Lyon", "lat": "", "lon": ""}]
    fill_coordinates(rows, g)
    assert rows[0]["lat"] == ""
