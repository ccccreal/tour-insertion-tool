# tour-insertion-tool

Insert new orders into delivery tours that were planned the evening before.

Tours are planned in the evening for the next day. Orders that arrive overnight
must be added in the morning. This tool puts each new stop at the position in
an existing tour where it costs the least extra time, while keeping all time
windows. Existing tours are **not** rearranged, only extended.

## Quick start

```bash
uv sync                     # or: pip install -e .
uv run pytest

# offline: coordinates are in the CSV
uv run tour-insert examples/winterthur/tours.csv examples/winterthur/orders.csv -o out --map

# online: only addresses, geocoded on the fly
uv run tour-insert examples/lyon/tours.csv examples/lyon/orders.csv -o out-lyon --map
```

Output folder:

- `tours_new.csv`: every tour with the new stops (`is_new = 1`), planned arrival
  and departure, and slack to the time window
- `unplaced.csv`: orders that fit into no tour or could not be geocoded, with
  the reason
- `geocoding.csv`: every looked-up address with what was found (only when
  something was geocoded); check it by eye
- `map.html` (with `--map`): open in a browser. Tours as coloured lines,
  stops numbered in driving order, new stops with a black ring, depot `D`,
  orders that fit nowhere as a red cross. Click a stop for its times.

## Web app

For dispatchers who do not want to use a terminal: upload the two CSV files,
see the map, download the result. Languages: German, French, English.

```bash
uv sync --extra app
uv run streamlit run app.py      # opens http://localhost:8501
```

Default values per customer/city live in `profiles.json`. The same `app.py`
runs on Streamlit Community Cloud (it uses `requirements.txt`).

## Input

`tours.csv`

| column | meaning |
|---|---|
| tour_id, seq | tour and position in the tour (sorted by seq) |
| stop_id | unique id |
| address | full address with postal code, e.g. `1 Place de la Comédie, 69001 Lyon` |
| country | `FR` or `CH`; if empty, guessed from the postal code (5 digits FR, 4 digits CH) |
| lat, lon | WGS84 coordinates; if empty, the address is geocoded |
| tw_start, tw_end | time window `HH:MM`, optional |
| service_min | minutes at the stop, optional (default `--service-min`) |

`orders.csv`: same columns, with `order_id` instead of `tour_id, seq, stop_id`.

The first stop's `tw_start` is the start time of the tour.

## Geocoding

| country | service | quality check |
|---|---|---|
| FR | IGN Géoplateforme (BAN), `data.geopf.fr/geocodage` | score ≥ `--min-score` (default 0.5) |
| CH | swisstopo GeoAdmin API, official building addresses | postal code of the input appears in the result |

Both are free and need no key. Results are cached in `.geocode_cache.json`
(`--geocode-cache`), so each address is looked up once.

- A **tour stop** that cannot be geocoded reliably stops the run with a list
  of the addresses: a wrong depot or customer position would make the whole
  tour wrong.
- A **new order** that cannot be geocoded reliably is not planned and appears
  in `unplaced.csv` (`geocode_not_found` / `geocode_uncertain`).

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
2. ~~Geocoding: France (IGN Géoplateforme, BAN), Switzerland (swisstopo)~~
3. ~~Map of the new tours~~
4. ~~Web app (Streamlit)~~
5. Optional: street-network travel times (OSRM), reordering existing stops

## Example data

All example data is **invented**. No real customer data is used or stored in
this repository.

- `examples/winterthur/`: 2 tours, 5 orders, with approximate coordinates
  (runs offline). Order N5 cannot be reached in time on purpose.
- `examples/lyon/`: 2 tours, 10 orders, addresses only (needs internet).
  Tours start at 08:00. The few time windows are deliberately generous: they
  show the format but do not change the result. These two files are also the
  templates offered for download in the web app.

## Origin

The insertion core (immutable tour model, forward pass, feasibility check,
cheapest insertion) is adapted from the author's ZHAW project work
`pa-decision-support`, as of 2026-11-20. It was rewritten for single stops,
WGS84 coordinates and time windows. This project is not part of that work.

## License

MIT, see [LICENSE](LICENSE).
