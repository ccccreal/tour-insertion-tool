"""Read tours and orders from CSV, write results to CSV.

tours.csv   tour_id, seq, stop_id, address, country, lat, lon,
            tw_start, tw_end, service_min, items, type, vehicle
orders.csv  order_id, address, country, lat, lon, tw_start, tw_end,
            service_min, items, type

Times are "HH:MM". Empty cells are allowed for address, country, tw_start,
tw_end, service_min (then the default service time is used), items
(number of items/parcels at the stop), type ("drop" or "pickup", empty =
drop) and vehicle (vehicle type of the tour; given on at least one row of
the tour, e.g. the first; see vehicles in pipeline.Settings).
lat/lon may be empty: then the address is geocoded (see geocoding.py).
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from pathlib import Path
from typing import IO

from tour_insertion.geocoding import GeocodeResult
from tour_insertion.load import DROP, KINDS, load_after
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
    try:
        service_min = int(service) if service else default_service
        items = parse_items(row.get("items"))
        kind = parse_kind(row.get("type"))
    except ValueError as error:
        raise ValueError(f"{where}: {error}") from None
    return Stop(
        stop_id=stop_id,
        lat=float(lat),
        lon=float(lon),
        address=(row.get("address") or "").strip(),
        country=(row.get("country") or "").strip().upper(),
        tw_start=parse_time(row.get("tw_start")),
        tw_end=parse_time(row.get("tw_end")),
        service_min=service_min,
        is_new=is_new,
        items=items,
        kind=kind,
        **extras_from_row(row),
    )


def extras_from_row(row: dict) -> dict:
    """name, quantities and info of a row (filled by stop_import.py for
    Excel files; a plain CSV may have a 'name' column)."""
    return {
        "name": str(row.get("_name") or row.get("name") or "").strip(),
        "quantities": tuple((row.get("_quantities") or {}).items()),
        "info": tuple((row.get("_info") or {}).items()),
    }


def parse_kind(text: str | None) -> str:
    """'pickup' / 'drop' (any case), '' -> 'drop'."""
    value = (text or "").strip().lower()
    if not value:
        return DROP
    if value not in KINDS:
        raise ValueError(f"type must be 'drop' or 'pickup', got {text!r}")
    return value


def parse_items(text: str | None) -> int | None:
    """'3' -> 3, '' -> None. Number of items (parcels) at a stop."""
    if text is None or not str(text).strip():
        return None
    value = int(str(text).strip())
    if value < 0:
        raise ValueError(f"items must not be negative, got {value}")
    return value


def unplaced_from_row(row: dict[str, str], id_column: str, reason: str,
                      default_service: int, tour_id: str = "") -> Unplaced:
    """An Unplaced for a row that never became a Stop (no coordinates),
    keeping everything needed to place it by hand later."""
    service = (row.get("service_min") or "").strip()
    try:
        items = parse_items(row.get("items"))
        service_min = int(service) if service else default_service
    except ValueError:
        items, service_min = None, default_service
    try:
        kind = parse_kind(row.get("type"))
    except ValueError:
        kind = DROP
    extras = extras_from_row(row)
    return Unplaced((row.get(id_column) or "").strip(),
                    (row.get("address") or "").strip(), reason, None,
                    parse_time(row.get("tw_start")), parse_time(row.get("tw_end")),
                    tour_id, service_min, items,
                    (row.get("country") or "").strip().upper(), kind,
                    extras["name"], extras["quantities"], extras["info"])


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
    """tour_id -> Tour, stops sorted by seq. Vehicle from the 'vehicle'
    column (capacity is set later, from the vehicle types)."""
    grouped: dict[str, list[tuple[int, Stop]]] = defaultdict(list)
    vehicles: dict[str, str] = {}
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
        vehicle = (row.get("vehicle") or "").strip()
        if vehicle:
            if vehicles.setdefault(tour_id, vehicle) != vehicle:
                raise ValueError(f"{where}: tour {tour_id} has two vehicles: "
                                 f"{vehicles[tour_id]!r} and {vehicle!r}")
    tours = {}
    for tour_id, items in grouped.items():
        items.sort(key=lambda item: item[0])
        tours[tour_id] = Tour(tour_id, tuple(stop for _, stop in items),
                              vehicle=vehicles.get(tour_id, ""))
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


TOUR_COLUMNS = ["tour_id", "vehicle", "seq", "stop_id", "name", "is_new", "moved",
                "type", "address", "country", "lat", "lon", "items", "load",
                "arrival", "departure", "tw_start", "tw_end", "slack_min"]


def extra_columns(stops) -> tuple[list[str], list[str]]:
    """Product and info column names, in order of first appearance.
    A name that clashes with a standard column gets a prefix."""
    products, info = [], []
    for s in stops:
        for name, _ in s.quantities:
            if name not in products:
                products.append(name)
        for name, _ in s.info:
            if name not in info:
                info.append(name)
    return products, info


def _column(name: str, prefix: str, taken: set[str]) -> str:
    return f"{prefix}{name}" if name in taken else name


def schedules_to_csv(schedules: list[Schedule],
                     moved: frozenset[str] = frozenset()) -> str:
    """moved: stop_ids whose order was changed by optimizing (column 'moved').
    Quantities per product and info columns of the input come after the
    standard columns."""
    products, info = extra_columns(t.stop for s in schedules for t in s.times)
    taken = set(TOUR_COLUMNS)
    product_cols = [_column(p, "qty_", taken) for p in products]
    info_cols = [_column(i, "info_", taken | set(product_cols)) for i in info]
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=TOUR_COLUMNS + product_cols + info_cols,
                            lineterminator="\n")
    writer.writeheader()
    for schedule in schedules:
        loads = load_after([t.stop for t in schedule.times])
        for seq, (t, load) in enumerate(zip(schedule.times, loads), start=1):
            s = t.stop
            q, i = dict(s.quantities), dict(s.info)
            extra = {c: q.get(p, "") for c, p in zip(product_cols, products)}
            extra.update({c: i.get(n, "") for c, n in zip(info_cols, info)})
            writer.writerow({**extra, "name": s.name,
                "tour_id": schedule.tour.tour_id, "vehicle": schedule.tour.vehicle,
                "seq": seq, "stop_id": s.stop_id, "is_new": int(s.is_new),
                "moved": int(s.stop_id in moved), "type": s.kind,
                "address": s.address, "country": s.country,
                "lat": s.lat, "lon": s.lon,
                "items": "" if s.items is None else s.items, "load": load,
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
    products, info = extra_columns(unplaced)
    writer.writerow(["order_id", "name", "type", "address", "items", "tw_start", "tw_end",
                     "reason", *products, *info])
    for u in unplaced:
        q, i = dict(u.quantities), dict(u.info)
        writer.writerow([u.order_id, u.name, u.kind, u.address,
                         "" if u.items is None else u.items,
                         format_time(u.tw_start), format_time(u.tw_end), u.reason,
                         *(q.get(p, "") for p in products), *(i.get(n, "") for n in info)])
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
