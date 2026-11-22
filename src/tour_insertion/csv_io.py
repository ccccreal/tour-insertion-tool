"""Read tours and orders from CSV, write results to CSV.

tours.csv   tour_id, seq, stop_id, address, country, lat, lon,
            tw_start, tw_end, service_min
orders.csv  order_id, address, country, lat, lon, tw_start, tw_end,
            service_min

Times are "HH:MM". Empty cells are allowed for address, country, tw_start,
tw_end and service_min (then the default service time is used).
lat/lon may be empty: then the address is geocoded (see geocoding.py).
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import IO

from tour_insertion.geocoding import GeocodeResult
from tour_insertion.model import Schedule, Stop, Tour
from tour_insertion.planner import Unplaced


def parse_time(text: str | None) -> int | None:
    """'08:30' -> 510, '' -> None."""
    if text is None or not text.strip():
        return None
    hours, minutes = text.strip().split(":")
    return int(hours) * 60 + int(minutes)


def format_time(minutes: int | None) -> str:
    """510 -> '08:30', None -> ''. Past midnight: '24:10' etc."""
    if minutes is None:
        return ""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _stop_from_row(row: dict[str, str], id_column: str, default_service: int,
                   is_new: bool, where: str) -> Stop:
    stop_id = (row.get(id_column) or "").strip()
    if not stop_id:
        raise ValueError(f"{where}: missing {id_column}")
    lat, lon = (row.get("lat") or "").strip(), (row.get("lon") or "").strip()
    if not lat or not lon:
        raise ValueError(f"{where}: stop {stop_id!r} has no lat/lon "
                         "(geocode first)")
    service = (row.get("service_min") or "").strip()
    return Stop(
        stop_id=stop_id,
        lat=float(lat),
        lon=float(lon),
        address=(row.get("address") or "").strip(),
        country=(row.get("country") or "").strip().upper(),
        tw_start=parse_time(row.get("tw_start")),
        tw_end=parse_time(row.get("tw_end")),
        service_min=int(service) if service else default_service,
        is_new=is_new,
    )


def read_rows(source: str | Path | IO[str], name: str | None = None
              ) -> list[dict[str, str]]:
    """All rows of a CSV as dicts. `source` is a path or an open text file.

    Adds '_where' (file:line) to every row for error messages.
    """
    if hasattr(source, "read"):
        rows = list(csv.DictReader(source))
        name = name or "upload"
    else:
        with open(source, newline="", encoding="utf-8-sig") as f:
            rows = list(csv.DictReader(f))
        name = name or str(source)
    for i, row in enumerate(rows, start=2):
        row["_where"] = f"{name}:{i}"
    return rows


def read_rows_text(text: str, name: str = "upload") -> list[dict[str, str]]:
    """Rows from CSV text (e.g. an uploaded file). Strips a UTF-8 BOM."""
    return read_rows(io.StringIO(text.lstrip("\ufeff")), name)


def tours_from_rows(rows: list[dict[str, str]],
                    default_service: int = 3) -> dict[str, Tour]:
    """tour_id -> Tour, stops sorted by seq."""
    grouped: dict[str, list[tuple[int, Stop]]] = defaultdict(list)
    for row in rows:
        where = row.get("_where", "tours")
        tour_id = (row.get("tour_id") or "").strip()
        if not tour_id:
            raise ValueError(f"{where}: missing tour_id")
        try:
            seq = int(row.get("seq") or "")
        except ValueError:
            raise ValueError(f"{where}: seq must be a whole number, "
                             f"got {row.get('seq')!r}") from None
        stop = _stop_from_row(row, "stop_id", default_service, False, where)
        grouped[tour_id].append((seq, stop))
    tours = {}
    for tour_id, items in grouped.items():
        items.sort(key=lambda item: item[0])
        tours[tour_id] = Tour(tour_id, tuple(stop for _, stop in items))
    return tours


def orders_from_rows(rows: list[dict[str, str]],
                     default_service: int = 3) -> list[Stop]:
    return [_stop_from_row(row, "order_id", default_service, True,
                           row.get("_where", "orders"))
            for row in rows]


def read_tours(path: str | Path, default_service: int = 3) -> dict[str, Tour]:
    """Tours from a CSV that already has lat/lon for every stop."""
    return tours_from_rows(read_rows(path), default_service)


def read_orders(path: str | Path, default_service: int = 3) -> list[Stop]:
    """Orders from a CSV that already has lat/lon for every order."""
    return orders_from_rows(read_rows(path), default_service)


TOUR_COLUMNS = ["tour_id", "seq", "stop_id", "is_new", "moved", "address",
                "country", "lat", "lon", "arrival", "departure", "tw_start",
                "tw_end", "slack_min"]


def schedules_to_csv(schedules: list[Schedule],
                     moved: frozenset[str] = frozenset()) -> str:
    """moved: stop_ids whose order was changed by optimizing (column 'moved')."""
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TOUR_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for schedule in schedules:
        for seq, t in enumerate(schedule.times, start=1):
            s = t.stop
            writer.writerow({
                "tour_id": schedule.tour.tour_id, "seq": seq,
                "stop_id": s.stop_id, "is_new": int(s.is_new),
                "moved": int(s.stop_id in moved),
                "address": s.address, "country": s.country,
                "lat": s.lat, "lon": s.lon,
                "arrival": format_time(t.arrival),
                "departure": format_time(t.departure),
                "tw_start": format_time(s.tw_start),
                "tw_end": format_time(s.tw_end),
                "slack_min": "" if t.slack is None else t.slack,
            })
    return out.getvalue()


def unplaced_to_csv(unplaced: list[Unplaced]) -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["order_id", "address", "tw_start", "tw_end", "reason"])
    for u in unplaced:
        writer.writerow([u.order_id, u.address, format_time(u.tw_start),
                         format_time(u.tw_end), u.reason])
    return out.getvalue()


def skipped_to_csv(skipped: list[Unplaced]) -> str:
    """Stops of the given tours that were left out (address not found)."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["tour_id", "stop_id", "address", "reason"])
    for u in skipped:
        writer.writerow([u.tour_id, u.order_id, u.address, u.reason])
    return out.getvalue()


def geocoding_to_csv(results: list[GeocodeResult]) -> str:
    """One row per looked-up address, to check the geocoding by eye."""
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(["address", "country", "status", "score", "found",
                     "lat", "lon"])
    for r in results:
        writer.writerow([r.query, r.country, r.status,
                         "" if r.score is None else round(r.score, 3),
                         r.label, "" if r.lat is None else r.lat,
                         "" if r.lon is None else r.lon])
    return out.getvalue()


def write_schedules(path: str | Path, schedules: list[Schedule],
                    moved: frozenset[str] = frozenset()) -> None:
    Path(path).write_text(schedules_to_csv(schedules, moved), encoding="utf-8")


def write_skipped(path: str | Path, skipped: list[Unplaced]) -> None:
    Path(path).write_text(skipped_to_csv(skipped), encoding="utf-8")


def write_unplaced(path: str | Path, unplaced: list[Unplaced]) -> None:
    Path(path).write_text(unplaced_to_csv(unplaced), encoding="utf-8")


def write_geocoding(path: str | Path, results: list[GeocodeResult]) -> None:
    Path(path).write_text(geocoding_to_csv(results), encoding="utf-8")
