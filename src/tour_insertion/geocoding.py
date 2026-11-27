"""Geocoding: address text -> (lat, lon), for France and Switzerland.

France       IGN Geoplateforme, BAN data
             https://data.geopf.fr/geocodage/search
             limit: 50 requests/s per IP; data licence etalab-2.0
Switzerland  swisstopo GeoAdmin API, official building addresses
             https://api3.geo.admin.ch/rest/services/ech/SearchServer

Both services are free and need no API key. Results can be cached, so the
same address is only looked up once (see "Cache" below).

Quality
-------
The French service returns a score from 0 to 1. Below `min_score` the result
counts as uncertain: the order is NOT planned but reported, because a wrong
coordinate would silently put the stop in the wrong place.
The Swiss service has no score. There we check that the postal code of the
input appears in the result; if not, the result counts as uncertain.

Cache
-----
The cache must not become a readable list of every address ever uploaded.
So it stores no address text at all: the key is an HMAC-SHA256 of the
normalised address with a secret key, the value only country, status,
coordinates, score and an expiry date (default 30 days). Old entries in the
former plain-text format are dropped when the file is read.

Pseudonymous, not anonymous: coordinates can be turned back into an address
(reverse geocoding). Hence the expiry. The secret comes from the environment
variable TOOL_CACHE_SECRET or, if unset, from a key file next to the cache
(created with permissions 0600).

The HTTP call is injectable (`fetch`), so the tests run without network.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
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

CACHE_DAYS = 30.0
_HEX_KEY = re.compile(r"^[0-9a-f]{64}$")


def cache_secret(cache_path: str | Path | None) -> bytes:
    """Secret for the cache keys: TOOL_CACHE_SECRET, else a key file next to
    the cache (created once, readable only by the owner), else random (then
    the cache is only useful while the process runs)."""
    env = os.environ.get("TOOL_CACHE_SECRET", "")
    if env:
        return env.encode("utf-8")
    if not cache_path:
        return secrets.token_bytes(32)
    key_file = Path(f"{cache_path}.key")
    if key_file.exists():
        return bytes.fromhex(key_file.read_text(encoding="ascii").strip())
    secret = secrets.token_bytes(32)
    key_file.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(secret.hex())
    return secret


class Geocoder:
    """Picks the provider by country and caches results (see module doc).

    cache_path  JSON file for the cache; None keeps it in memory only
    keep        False: no cache at all, every address is looked up again
    days        how long a cached result stays valid
    """

    def __init__(self, cache_path: str | Path | None = None,
                 providers: dict | None = None, *, keep: bool = True,
                 days: float = CACHE_DAYS, secret: bytes | None = None,
                 now: Callable[[], float] = time.time):
        self.providers = providers or {"FR": FranceGeocoder(),
                                       "CH": SwissGeocoder()}
        self.keep, self.days, self.now = keep, days, now
        self.cache_path = Path(cache_path) if cache_path and keep else None
        self._secret = secret            # created on first use (key file)
        self.cache: dict[str, dict] = {}
        if self.cache_path and self.cache_path.exists():
            raw = json.loads(self.cache_path.read_text(encoding="utf-8"))
            self.cache = {k: v for k, v in raw.items() if self._valid(k, v)}
        self.lookups = 0   # requests actually sent (not from cache)

    @property
    def secret(self) -> bytes:
        if self._secret is None:
            self._secret = cache_secret(self.cache_path)
        return self._secret

    def _valid(self, key: str, value) -> bool:
        """Hashed key, new format, not expired. Drops old plain-text entries."""
        return (bool(_HEX_KEY.match(key)) and isinstance(value, dict)
                and "query" not in value and "label" not in value
                and float(value.get("until", 0)) > self.now())

    def key(self, address: str, country: str) -> str:
        text = f"{country}|{' '.join(address.lower().split())}"
        return hmac.new(self.secret, text.encode("utf-8"), hashlib.sha256).hexdigest()

    def geocode(self, address: str, country: str = "") -> GeocodeResult:
        country = detect_country(address, country)
        key = self.key(address, country) if self.keep else ""
        hit = self.cache.get(key) if self.keep else None
        if hit and float(hit["until"]) > self.now():
            return GeocodeResult(address, hit["country"], hit["status"],
                                 hit["lat"], hit["lon"], "", hit.get("score"))
        result = self.providers[country].geocode(address)
        self.lookups += 1
        if self.keep and result.status != NOT_FOUND:    # retry not-found next time
            self.cache[key] = {"country": result.country, "status": result.status,
                               "lat": result.lat, "lon": result.lon,
                               "score": result.score,
                               "until": round(self.now() + self.days * 86400)}
        return result

    def save(self) -> None:
        """Write the cache (expired entries left out), atomically, mode 0600."""
        if not self.cache_path:
            return
        now = self.now()
        self.cache = {k: v for k, v in self.cache.items() if float(v["until"]) > now}
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.cache_path.with_name(self.cache_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=1)
        os.replace(tmp, self.cache_path)


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
