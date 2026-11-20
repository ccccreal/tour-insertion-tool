"""Command line: tour-insert tours.csv orders.csv -o out/"""

from __future__ import annotations

import argparse
from pathlib import Path

from tour_insertion.csv_io import (format_time, read_orders, read_tours,
                               write_schedules, write_unplaced)
from tour_insertion.planner import insert_orders
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
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    model = CrowFlyModel(args.detour_factor, args.speed)
    tours = read_tours(args.tours, args.service_min)
    orders = read_orders(args.orders, args.service_min)

    result = insert_orders(tours, orders, model,
                           fixed_first=not args.open_start,
                           fixed_last=not args.open_end,
                           tolerance_min=args.tolerance_min)

    schedules = [forward_pass(result.tours[t], model)
                 for t in sorted(result.tours)]
    args.out.mkdir(parents=True, exist_ok=True)
    write_schedules(args.out / "tours_new.csv", schedules)
    write_unplaced(args.out / "unplaced.csv", result.unplaced)

    print(f"{len(tours)} tours, {len(orders)} new orders "
          f"(crow-fly x {args.detour_factor}, {args.speed:g} m/min)")
    for opt in result.inserted:
        print(f"  {opt.stop_id:<10} -> {opt.tour_id:<8} position {opt.position:>2}"
              f"   +{opt.delta} min")
    for u in result.unplaced:
        print(f"  {u.stop.stop_id:<10} NOT PLACED ({u.reason})")
    for s in schedules:
        print(f"  {s.tour.tour_id:<8} {len(s.tour):>2} stops, "
              f"end {format_time(s.end)}")
    print(f"written: {args.out / 'tours_new.csv'}, {args.out / 'unplaced.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
