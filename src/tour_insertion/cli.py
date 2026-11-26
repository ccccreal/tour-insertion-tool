"""Command line.

    tour-insert tours.csv orders.csv -o out/ [--map]          insert orders
    tour-insert --stops stops.xlsx --template t.json \
                --depot "address" --fleet Lastenvelo=4 Auto=1   plan from scratch
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from tour_insertion.csv_io import (format_time, parse_time, read_rows, write_geocoding,
                                   write_schedules, write_skipped,
                                   write_unplaced)
from tour_insertion.geocoding import Geocoder
from tour_insertion.load import max_load, over_capacity
from tour_insertion.pipeline import InputError, Settings, make_geocoder, plan_new, run
from tour_insertion.stop_import import Template, order_rows


def build_parser() -> argparse.ArgumentParser:
    d = Settings()
    p = argparse.ArgumentParser(
        prog="tour-insert",
        description="Insert new stops into pre-planned tours (cheapest feasible "
                    "insertion), or plan tours from a list of stops (--stops).")
    p.add_argument("tours", type=Path, nargs="?", help="CSV with the planned tours")
    p.add_argument("orders", type=Path, nargs="?",
                   help="new orders: CSV, or Excel/CSV with --template")
    p.add_argument("--stops", type=Path,
                   help="plan from scratch: list of stops (Excel or CSV), no tours")
    p.add_argument("--template", type=Path,
                   help="JSON column mapping for the stops/orders file (see stop_import.py)")
    p.add_argument("--depot", default="",
                   help='with --stops: depot address or "lat, lon"')
    p.add_argument("--fleet", nargs="+", default=[], metavar="TYPE=N",
                   help="with --stops: number of vehicles per type, e.g. Lastenvelo=4 Auto=1")
    p.add_argument("--start", default="08:00", help="with --stops: tour start (default 08:00)")
    p.add_argument("--end", default="", help="with --stops: latest return, e.g. 12:00")
    p.add_argument("-o", "--out", type=Path, default=Path("out"),
                   help="output folder (default: out)")
    p.add_argument("--map", action="store_true",
                   help="also write out/map.html")
    p.add_argument("--lang", choices=["de", "fr", "en"], default="de",
                   help="language of tours.xlsx and tours.pdf (default de)")
    p.add_argument("--detour-factor", type=float, default=d.detour_factor,
                   help="street / crow-fly distance (assumption, default 1.35)")
    p.add_argument("--speed", type=float, default=d.speed_m_per_min,
                   help="average speed in m/min (assumption, default 250)")
    p.add_argument("--service-min", type=int, default=d.service_min,
                   help="service time where the CSV has none (default 3)")
    p.add_argument("--tolerance-min", type=int, default=d.tolerance_min,
                   help="minutes a stop may be later than tw_end (default 0)")
    p.add_argument("--open-start", action="store_true",
                   help="allow inserting before the first stop")
    p.add_argument("--open-end", action="store_true",
                   help="allow inserting after the last stop")
    p.add_argument("--optimize", action="store_true",
                   help="also improve the stop order within every tour")
    p.add_argument("--vehicles", type=Path, default=None,
                   help="JSON file with vehicle types: {name: {capacity, "
                        "speed_kmh}} (default: Lastenvelo 10 items 15 km/h, "
                        "Auto 40 items 20 km/h)")
    p.add_argument("--min-score", type=float, default=d.min_score,
                   help="minimum geocoding score for France (default 0.5)")
    p.add_argument("--geocode-cache", type=Path,
                   default=Path(".geocode_cache.json"),
                   help="cache file for geocoding results")
    return p


def load_orders(path: Path, template: Path | None) -> tuple[list[dict], list[dict]]:
    """Rows of an orders/stops file; with a template any layout (Excel)."""
    if template is None:
        if path.suffix.lower() in (".xlsx", ".xlsm"):
            raise SystemExit(f"{path}: an Excel file needs --template")
        return read_rows(path), []
    try:
        spec = Template.from_json(json.loads(template.read_text(encoding="utf-8")))
        return order_rows(path.read_bytes(), path.name, spec)
    except (OSError, ValueError) as error:
        raise SystemExit(f"{path}: {error}") from error


def main(argv: list[str] | None = None, geocoder: Geocoder | None = None) -> int:
    args = build_parser().parse_args(argv)
    vehicles = Settings().vehicles
    if args.vehicles is not None:
        try:
            vehicles = json.loads(args.vehicles.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise SystemExit(f"cannot read {args.vehicles}: {error}") from error
    vehicles = {k: dict(v) for k, v in vehicles.items()}
    for item in args.fleet:
        name, _, count = item.partition("=")
        if name not in vehicles or not count.isdigit():
            raise SystemExit(f"--fleet {item}: use TYPE=N with a known type "
                             f"({', '.join(vehicles)})")
        vehicles[name]["count"] = int(count)
    try:
        settings = Settings(args.detour_factor, args.speed, args.service_min,
                            args.tolerance_min, args.open_start, args.open_end,
                            args.min_score, args.optimize, vehicles, args.depot,
                            parse_time(args.start), parse_time(args.end))
    except ValueError as error:
        raise SystemExit(f"bad time: {error}") from error
    geocoder = geocoder or make_geocoder(settings, args.geocode_cache)
    try:
        if args.stops:
            if args.tours or args.orders:
                raise SystemExit("use either tours + orders or --stops, not both")
            rows, no_address = load_orders(args.stops, args.template)
            result = plan_new(rows, settings, geocoder, no_address)
        else:
            if not (args.tours and args.orders):
                raise SystemExit("give tours.csv and orders.csv, or --stops")
            rows, no_address = load_orders(args.orders, args.template)
            if no_address:
                raise SystemExit(f"{len(no_address)} order(s) without address, "
                                 f"e.g. {no_address[0]['_where']}")
            result = run(read_rows(args.tours), rows, settings, geocoder)
    except InputError as error:
        raise SystemExit(str(error)) from error

    args.out.mkdir(parents=True, exist_ok=True)
    written = [args.out / "tours_new.csv", args.out / "unplaced.csv"]
    write_schedules(written[0], result.schedules, result.moved)
    write_unplaced(written[1], result.unplaced)
    from tour_insertion.export import tours_pdf, tours_xlsx
    written += [args.out / "tours.xlsx", args.out / "tours.pdf"]
    written[-2].write_bytes(tours_xlsx(result.schedules, result.unplaced, args.lang))
    written[-1].write_bytes(tours_pdf(result.schedules, args.lang))
    if result.skipped:
        written.append(args.out / "skipped_stops.csv")
        write_skipped(written[-1], result.skipped)
    if result.geocoding:
        written.append(args.out / "geocoding.csv")
        write_geocoding(written[-1], result.geocoding)
    if args.map:
        from tour_insertion.mapview import write_map
        written.append(args.out / "map.html")
        write_map(written[-1], result.schedules, result.unplaced,
                  title=f"{len(result.inserted)} of {result.n_orders} "
                        "new orders placed", moved=result.moved)

    if result.geocoding:
        print(f"geocoded {len(result.geocoding)} addresses")
    print(f"{len(result.tours)} tours, {result.n_orders} new orders "
          f"(crow-fly x {args.detour_factor}, {args.speed:g} m/min)")
    position = {t.stop.stop_id: (s.tour.tour_id, i)
                for s in result.schedules for i, t in enumerate(s.times)}
    for opt in result.inserted:
        tour_id, pos = position[opt.stop_id]
        print(f"  {opt.stop_id:<10} -> {tour_id:<8} position {pos:>2}"
              f"   +{opt.delta:.1f} min")
    for u in result.unplaced:
        print(f"  {u.order_id:<10} NOT PLACED ({u.reason})")
    for u in result.skipped:
        print(f"  {u.order_id:<10} SKIPPED in tour {u.tour_id} ({u.reason}): {u.address}")
    for s in result.schedules:
        load = ""
        if s.tour.vehicle or s.tour.capacity is not None:
            used = max_load(s.tour.stops)
            limit = "" if s.tour.capacity is None else f"/{s.tour.capacity}"
            over = "  OVER CAPACITY" if over_capacity(s.tour) else ""
            load = f", {s.tour.vehicle or 'vehicle'} load {used}{limit}{over}"
        print(f"  {s.tour.tour_id:<8} {len(s.tour):>2} stops, "
              f"end {format_time(s.end)}{load}")
    if result.changes:
        before = sum(c.minutes_before for c in result.changes)
        after = sum(c.minutes_after for c in result.changes)
        print(f"optimized: {before} -> {after} min in total "
              f"({before - after} min saved, {len(result.moved)} stops moved)")
    print("written: " + ", ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
