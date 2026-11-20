"""Command line: tour-insert tours.csv orders.csv -o out/ [--map]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tour_insertion.csv_io import (format_time, orders_from_rows, read_rows,
                                   tours_from_rows, write_geocoding,
                                   write_schedules, write_unplaced)
from tour_insertion.geocoding import (NOT_FOUND, OK, FranceGeocoder, Geocoder,
                                      SwissGeocoder, fill_coordinates,
                                      needs_geocoding)
from tour_insertion.planner import Unplaced, insert_orders
from tour_insertion.schedule import forward_pass
from tour_insertion.traveltime import CrowFlyModel


def build_parser() -> argparse.ArgumentParser:
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
    p.add_argument("--detour-factor", type=float, default=1.35,
                   help="street / crow-fly distance (assumption, default 1.35)")
    p.add_argument("--speed", type=float, default=250.0,
                   help="average speed in m/min (assumption, default 250)")
    p.add_argument("--service-min", type=int, default=3,
                   help="service time where the CSV has none (default 3)")
    p.add_argument("--tolerance-min", type=int, default=0,
                   help="minutes a stop may be later than tw_end (default 0)")
    p.add_argument("--open-start", action="store_true",
                   help="allow inserting before the first stop")
    p.add_argument("--open-end", action="store_true",
                   help="allow inserting after the last stop")
    p.add_argument("--min-score", type=float, default=0.5,
                   help="minimum geocoding score for France (default 0.5)")
    p.add_argument("--geocode-cache", type=Path,
                   default=Path(".geocode_cache.json"),
                   help="cache file for geocoding results")
    return p


def make_geocoder(args) -> Geocoder:
    return Geocoder(args.geocode_cache, {
        "FR": FranceGeocoder(min_score=args.min_score), "CH": SwissGeocoder()})


def geocode_rows(tour_rows, order_rows, geocoder: Geocoder
                 ) -> tuple[list, list[Unplaced]]:
    """Fill missing coordinates. Returns (all results, unplaced orders).

    A tour stop that cannot be geocoded stops the run: the tour would be
    wrong. An order that cannot be geocoded is reported, not planned.
    """
    if not any(needs_geocoding(r) for r in tour_rows + order_rows):
        return [], []
    try:
        tour_results = fill_coordinates(tour_rows, geocoder)
        order_results = fill_coordinates(order_rows, geocoder)
    finally:
        geocoder.save()
    print(f"geocoded {len(tour_results) + len(order_results)} addresses "
          f"({geocoder.lookups} looked up, rest from cache)")

    bad_tours = [r for r in tour_results if r.status != OK]
    if bad_tours:
        lines = "\n".join(f"  {r.status:<10} {r.query}  ->  {r.label or '-'}"
                          for r in bad_tours)
        raise SystemExit("Some TOUR stops could not be geocoded reliably. "
                         "Fix the address or add lat/lon:\n" + lines)

    by_query = {r.query: r for r in order_results}
    unplaced = []
    for row in order_rows:
        if needs_geocoding(row):                 # still no coordinates
            address = row["address"].strip()
            reason = ("geocode_not_found"
                      if by_query[address].status == NOT_FOUND
                      else "geocode_uncertain")
            unplaced.append(Unplaced((row.get("order_id") or "").strip(),
                                     address, reason))
    return tour_results + order_results, unplaced


def main(argv: list[str] | None = None, geocoder: Geocoder | None = None) -> int:
    args = build_parser().parse_args(argv)
    model = CrowFlyModel(args.detour_factor, args.speed)
    tour_rows, order_rows = read_rows(args.tours), read_rows(args.orders)

    geo_results, geo_unplaced = geocode_rows(
        tour_rows, order_rows, geocoder or make_geocoder(args))
    order_rows = [r for r in order_rows if not needs_geocoding(r)]

    tours = tours_from_rows(tour_rows, args.service_min)
    orders = orders_from_rows(order_rows, args.service_min)
    result = insert_orders(tours, orders, model,
                           fixed_first=not args.open_start,
                           fixed_last=not args.open_end,
                           tolerance_min=args.tolerance_min)
    unplaced = geo_unplaced + result.unplaced
    n_orders = len(orders) + len(geo_unplaced)

    schedules = [forward_pass(result.tours[t], model)
                 for t in sorted(result.tours)]
    args.out.mkdir(parents=True, exist_ok=True)
    written = [args.out / "tours_new.csv", args.out / "unplaced.csv"]
    write_schedules(written[0], schedules)
    write_unplaced(written[1], unplaced)
    if geo_results:
        written.append(args.out / "geocoding.csv")
        write_geocoding(written[-1], geo_results)
    if args.map:
        from tour_insertion.mapview import write_map
        written.append(args.out / "map.html")
        write_map(written[-1], schedules, unplaced,
                  title=f"{len(result.inserted)} of {n_orders} new orders placed")

    print(f"{len(tours)} tours, {n_orders} new orders "
          f"(crow-fly x {args.detour_factor}, {args.speed:g} m/min)")
    for opt in result.inserted:
        print(f"  {opt.stop_id:<10} -> {opt.tour_id:<8} position {opt.position:>2}"
              f"   +{opt.delta} min")
    for u in unplaced:
        print(f"  {u.order_id:<10} NOT PLACED ({u.reason})")
    for s in schedules:
        print(f"  {s.tour.tour_id:<8} {len(s.tour):>2} stops, "
              f"end {format_time(s.end)}")
    print("written: " + ", ".join(str(p) for p in written))
    return 0


if __name__ == "__main__":
    sys.exit(main())
