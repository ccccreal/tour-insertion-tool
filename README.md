# tour-insertion-tool

Insert new orders into delivery tours that were planned the evening before.

Tours are planned in the evening for the next day. Orders that arrive overnight
must be added in the morning. This tool puts each new stop at the position in
an existing tour where it costs the least extra time, while keeping all time
windows. Existing tours are **not** rearranged, only extended.

## Quick start

```bash
uv sync                     # or: pip install -e .
uv run tour-insert examples/tours.csv examples/orders.csv -o out
uv run pytest
```

Output in `out/`:

- `tours_new.csv`: every tour with the new stops (`is_new = 1`), planned arrival
  and departure, and slack to the time window
- `unplaced.csv`: orders that fit into no tour, with the reason

## Input

`tours.csv`

| column | meaning |
|---|---|
| tour_id, seq | tour and position in the tour (sorted by seq) |
| stop_id | unique id |
| address, country | text; country `FR` or `CH` (for geocoding later) |
| lat, lon | WGS84 coordinates (required until geocoding is added) |
| tw_start, tw_end | time window `HH:MM`, optional |
| service_min | minutes at the stop, optional (default `--service-min`) |

`orders.csv`: same columns, with `order_id` instead of `tour_id, seq, stop_id`.

The first stop's `tw_start` is the start time of the tour.

## Assumptions

- New orders are single stops (no pickup/delivery pairs), no capacities.
- First and last stop of each tour stay fixed (depot). Use `--open-start` /
  `--open-end` to allow inserting before/after them.
- Cost of an insertion = extra travel time + service time of the new stop.
  Waiting time does not count.
- Several new orders: greedy cheapest insertion. The order with the smallest
  extra time over all tours goes first, then everything is recomputed. The
  result does not depend on the row order in the CSV.
- Stops that are already late in the original plan do not block insertions,
  but an insertion may not make any other stop late.
- **Travel times are estimates:** crow-fly distance x detour factor (default
  1.35) at constant speed (default 250 m/min = 15 km/h). Both are assumptions,
  not measured for any city. Set them with `--detour-factor` and `--speed`.

## Roadmap

1. ~~Core: insertion, schedule, feasibility, CLI~~
2. Geocoding: France (IGN Geoplateforme, BAN), Switzerland (swisstopo)
3. Map of the new tours
4. Optional: street-network travel times (OSRM), OR-Tools, simple UI

## Example data

`examples/` contains **invented** tours in Winterthur with approximate
coordinates. No real customer data is used or stored in this repository.

## Origin

The insertion core (immutable tour model, forward pass, feasibility check,
cheapest insertion) is adapted from the author's ZHAW project work
`pa-decision-support`, as of 2026-11-20. It was rewritten for single stops,
WGS84 coordinates and time windows. This project is not part of that work.

## License

MIT, see [LICENSE](LICENSE).
