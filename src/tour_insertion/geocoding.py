"""Geocoding: address text -> (lat, lon), for France and Switzerland.

France       IGN Geoplateforme, BAN data
             https://data.geopf.fr/geocodage/search
             limit: 50 requests/s per IP; data licence etalab-2.0
Switzerland  swisstopo GeoAdmin API, official building addresses
             https://api3.geo.admin.ch/rest/services/ech/SearchServer

Both services are free and need no API key. Results are cached in a JSON
file, so the same address is only looked up once.

Quality
-------
The French service returns a score from 0 to 1. Below `min_score` the result
counts as uncertain: the order is NOT planned but reported, because a wrong
coordinate would silently put the stop in the wrong place.
The Swiss service has no score. There we check that the postal code of the
input appears in the result; if not, the result counts as uncertain.

The HTTP call is injectable (`fetch`), so the tests run without network.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

FR_URL = "https://data.geopf.fr/geocodage/search"
CH_URL = "https://api3.geo.admin.ch/rest/services/ech/SearchServer"
USER_AGENT = "tour-insertion-tool (+https://github.com/ccccreal/tour-insertion-tool)"

Fetch = Callable[[str, dict], dict]

OK = "ok"
UNCERTAIN = "uncertain"
NOT_FOUND = "not_found"


@dataclass(frozen=True, slots=True)
class GeocodeResult:
    """status: ok | uncertain | not_found. score: 0..1, None for CH."""

    query: str
    country: str
    status: str
    lat: float | None = None
    lon: float | None = None
    label: str = ""
    score: float | None = None


def http_get_json(url: str, params: dict) -> dict:
    """GET url?params and parse JSON. Raises on network/HTTP errors."""
    full = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(full, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)


# --------------------------------------------------------------------------
# Country
# --------------------------------------------------------------------------

_POSTCODE = re.compile(r"\b(\d{4,5})\b")


def postcode_of(address: str) -> str | None:
    """Last 4- or 5-digit number in the address, e.g. '8400' or '69001'."""
    found = _POSTCODE.findall(address)
    return found[-1] if found else None


def detect_country(address: str, country: str = "") -> str:
    """'FR' or 'CH'. Uses the country column; else the postal code length."""
    country = country.strip().upper()
    if country in ("FR", "CH"):
        return country
    if country:
        raise ValueError(f"unsupported country {country!r} (only FR, CH)")
    postcode = postcode_of(address)
    if postcode and len(postcode) == 5:
        return "FR"
    if postcode and len(postcode) == 4:
        return "CH"
    raise ValueError(f"cannot tell the country of {address!r}: "
                     "fill the country column (FR or CH)")


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------

class FranceGeocoder:
    """IGN Geoplateforme (BAN). Response is GeoJSON: coordinates are [lon, lat]!"""

    def __init__(self, fetch: Fetch = http_get_json, min_score: float = 0.5,
                 pause_s: float = 0.03):
        self.fetch, self.min_score, self.pause_s = fetch, min_score, pause_s

    def geocode(self, address: str) -> GeocodeResult:
        data = self.fetch(FR_URL, {"q": address, "limit": 1})
        time.sleep(self.pause_s)          # stay well below 50 requests/s
        features = data.get("features") or []
        if not features:
            return GeocodeResult(address, "FR", NOT_FOUND)
        feature = features[0]
        lon, lat = feature["geometry"]["coordinates"]
        props = feature.get("properties", {})
        score = props.get("score")
        status = OK if score is not None and score >= self.min_score else UNCERTAIN
        return GeocodeResult(address, "FR", status, float(lat), float(lon),
                             props.get("label", ""), score)


class SwissGeocoder:
    """swisstopo SearchServer, origin 'address'. No score in the response."""

    def __init__(self, fetch: Fetch = http_get_json):
        self.fetch = fetch

    def geocode(self, address: str) -> GeocodeResult:
        data = self.fetch(CH_URL, {"searchText": address, "type": "locations",
                                   "origins": "address", "limit": 1})
        results = data.get("results") or []
        if not results:
            return GeocodeResult(address, "CH", NOT_FOUND)
        attrs = results[0]["attrs"]
        label = re.sub(r"<[^>]+>", "", attrs.get("label", "")).strip()
        postcode = postcode_of(address)
        status = OK if postcode is None or postcode in label else UNCERTAIN
        return GeocodeResult(address, "CH", status, float(attrs["lat"]),
                             float(attrs["lon"]), label, None)


# --------------------------------------------------------------------------
# Cache + dispatch by country
# --------------------------------------------------------------------------

class Geocoder:
    """Picks the provider by country and caches results in a JSON file."""

    def __init__(self, cache_path: str | Path | None = None,
                 providers: dict | None = None):
        self.providers = providers or {"FR": FranceGeocoder(),
                                       "CH": SwissGeocoder()}
        self.cache_path = Path(cache_path) if cache_path else None
        self.cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.lookups = 0   # requests actually sent (not from cache)

    @staticmethod
    def key(address: str, country: str) -> str:
        return f"{country}|{' '.join(address.lower().split())}"

    def geocode(self, address: str, country: str = "") -> GeocodeResult:
        country = detect_country(address, country)
        key = self.key(address, country)
        if key in self.cache:
            return GeocodeResult(**self.cache[key])
        result = self.providers[country].geocode(address)
        self.lookups += 1
        if result.status != NOT_FOUND:      # retry not-found next time
            self.cache[key] = asdict(result)
        return result

    def save(self) -> None:
        if self.cache_path:
            self.cache_path.write_text(
                json.dumps(self.cache, ensure_ascii=False, indent=1),
                encoding="utf-8")


def needs_geocoding(row: dict[str, str]) -> bool:
    return not (row.get("lat") or "").strip() or not (row.get("lon") or "").strip()


def fill_coordinates(rows: list[dict[str, str]],
                     geocoder: Geocoder) -> list[GeocodeResult]:
    """Fill lat/lon in rows that have none. Changes `rows` in place.

    Only rows with status ok get coordinates. Returns one result per
    geocoded row (rows that already had coordinates are skipped).
    """
    results = []
    for row in rows:
        if not needs_geocoding(row):
            continue
        address = (row.get("address") or "").strip()
        if not address:
            raise ValueError(f"row without address and without lat/lon: {row}")
        result = geocoder.geocode(address, row.get("country") or "")
        if not (row.get("country") or "").strip():
            row["country"] = result.country
        if result.status == OK:
            row["lat"], row["lon"] = str(result.lat), str(result.lon)
        results.append(result)
    return results
