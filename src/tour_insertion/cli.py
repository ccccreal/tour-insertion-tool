"""Command line: tour-insert tours.csv orders.csv -o out/ [--map]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tour_insertion.csv_io import (format_time, read_rows, write_geocoding,
                                   write_schedules, write_skipped,
                                   write_unplaced)
from tour_insertion.geocoding import Geocoder
from tour_insertion.pipeline import InputError, Settings, make_geocoder, run


def build_parser() -> argparse.ArgumentParser:
    d = Settings()
    p = argparse.ArgumentParser(
        prog="tour-insert",
        description="Insert new stops into pre-planned tours "
                    "(cheapest feasible insertion).")
    p.add_argument("tours", type=Path, help="CSV with the planned tours")
    p.add_argument("orders", type=Path, help="CSV with the new orders")
    p.add_argument("-o", "--out", type=Path, default=Path("out"),
                   help="output folder (default: out)")
    p.add_argument("--map", action="store_true",
                   help="also write out/map.html")
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
    p.add_argument("--min-score", type=float, default=d.min_score,
                   help="minimum geocoding score for France (default 0.5)")
    p.add_argument("--geocode-cache", type=Path,
                   default=Path(".geocode_cache.json"),
                   help="cache file for geocoding results")
    return p


def main(argv: list[str] | None = None, geocoder: Geocoder | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = Settings(args.detour_factor, args.speed, args.service_min,
                        args.tolerance_min, args.open_start, args.open_end,
                        args.min_score, args.optimize)
    try:
        result = run(read_rows(args.tours), read_rows(args.orders), settings,
                     geocoder or make_geocoder(settings, args.geocode_cache))
    except InputError as error:
        raise SystemExit(str(error)) from error

    args.out.mkdir(parents=True, exist_ok=True)
    written = [args.out / "tours_new.csv", args.out / "unplaced.csv"]
    write_schedules(written[0], result.schedules, result.moved)
    write_unplaced(written[1], result.unplaced)
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
        print(f"  {s.tour.tour_id:<8} {len(s.tour):>2} stops, "
              f"end {format_time(s.end)}")
    if result.changes:
        before = sum(c.minutes_before for c in result.changes)
        after = sum(c.minutes_after for c in result.changes)
        print(f"optimized: {before} -> {after} min in total "
              f"({before - after} min saved, {len(result.moved)} stops moved)")
    print("written: " + ", ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
