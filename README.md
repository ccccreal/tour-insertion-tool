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

For dispatchers: upload the two CSV files (or pick an example), see the
tours on a map, **drag stops with the mouse onto another tour's line**, and
download the result. Languages: German, French, English.

```bash
uv sync --extra server
uv run tour-insert-server        # opens on http://localhost:8000
```

- `src/tour_insertion/server.py`: web API (FastAPI) around the planning
  core. It keeps no state; the page holds the current plan.
  `POST /api/plan` plans, `POST /api/schedule` recomputes times after a
  manual move, `POST /api/insert` inserts one stop placed by hand.
  API docs: `/api/docs`.
- `web/`: the page (plain JavaScript, Leaflet map, no build step).
- Drag & drop: drop a stop (or an order marked ✕ that fits nowhere) on a
  tour line; it goes between the two stops of that line, both tours are
  recomputed, stops after their time window turn red, *Undo* goes back.
- Stops at the same spot are spread on a small circle around it (a thin
  line points to the real position), so each one can be seen and dragged.
- Address not found: *Place on map*, then click the right spot. A new
  order goes to its cheapest feasible position in any tour, a skipped tour
  stop to the cheapest position in its own tour (its original position is
  not kept). If nothing fits, it stays as ✕ and can be dragged by hand.
- `TOOL_PASSWORD=...` asks for a password (HTTP Basic auth).
  `TOOL_GEOCODE_CACHE` sets the cache file.

### Docker

```bash
docker compose up -d             # http://localhost:8000
# or: docker build -t tour-insertion . && docker run -p 8000:8000 tour-insertion
```

The same image runs on any server. Put it behind HTTPS (e.g. Caddy or the
provider's proxy) and set `TOOL_PASSWORD` before it is reachable from the
internet.

### Streamlit demo (older)

`streamlit_demo/app.py` is the first web version (Streamlit, no drag &
drop). It still runs the demo on Streamlit Community Cloud (main file path
`streamlit_demo/app.py`; its own `requirements.txt` next to it):
`uv sync --extra app && uv run streamlit run streamlit_demo/app.py`.

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
| items | number of parcels/items unloaded at the stop, optional; shown only, no capacity check |

`orders.csv`: same columns, with `order_id` instead of `tour_id, seq, stop_id`.

The first stop's `tw_start` is the start time of the tour.

The last stop does not have to be the depot. A tour may end at a
connection point (hand-over to another vehicle, a train, a second depot):
give the last stop its own address and, if it must be reached in time, a
`tw_end`. New stops are always inserted before it. On the map the depot of
a round trip is marked **D**; a tour that ends elsewhere has **S** (start)
and **E** (end), and E turns red when the tour arrives too late.
Example: `examples/anschluss/`.

## Geocoding

| country | service | quality check |
|---|---|---|
| FR | IGN Géoplateforme (BAN), `data.geopf.fr/geocodage` | score ≥ `--min-score` (default 0.5) |
| CH | swisstopo GeoAdmin API, official building addresses | postal code of the input appears in the result |

Both are free and need no key. Results are cached in `.geocode_cache.json`
(`--geocode-cache`), so each address is looked up once.

- The **depot** (first and last stop of a tour) must be found: otherwise the
  run stops with a list of the addresses, because every time of the tour
  depends on it. Tip: give the depot as lat/lon, it rarely changes.
- Any **other tour stop** that cannot be geocoded reliably is left out: the
  tour is computed without it and the stop is listed in `skipped_stops.csv`
  (and in the app). Times after that stop are then slightly too early.
- A **new order** that cannot be geocoded reliably is not planned and appears
  in `unplaced.csv` (`geocode_not_found` / `geocode_uncertain`).

## Optimising tours (optional)

`--optimize` (or the checkbox in the app) also improves the order of the
stops **within each tour**: same stops, same tour, time windows kept, depot
stays first and last. Steps: improve the given tours, insert the new orders,
improve once more.

- Moves: move a run of 1-3 stops elsewhere in the tour, or reverse a part of
  the tour (2-opt), repeated until nothing helps (local search, a heuristic).
- Objective: tour duration from start to return, **waiting included** (a
  shorter route that arrives too early and waits is not better). Ties: less
  travel.
- Output: before/after minutes per tour (before = insertion only) and the
  stops whose order changed (column `moved`, marked in the map popup).

Stops never change tour. Moving stops between tours is not implemented.

## Travel time estimate

    distance = great-circle distance (haversine, WGS84) x detour factor (1.35)
    time     = distance / speed (250 m/min = 15 km/h), rounded up to minutes
    < 50 m   = same location, no travel time

Per stop the service time is added (default 3 min, column `service_min`), and
the driver waits if a stop's time window has not opened yet. Comparing
insertion positions uses the unrounded times; schedules use whole minutes.
Detour factor and speed are assumptions, not measured for any city.

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
4. ~~Web app (Streamlit demo, then API + JavaScript page with drag & drop)~~
5. ~~Optional reordering within each tour~~
6. Optional: street-network travel times (OSRM), moving stops between tours

## Example data

All example data is **invented**. No real customer data is used or stored in
this repository.

- `examples/winterthur/`: 2 tours, 5 orders, with approximate coordinates
  (runs offline). Order N5 cannot be reached in time on purpose.
- `examples/anschluss/`: the same tours, but each ends at a railway station
  (connection point). T2 must be there by 09:30, so N2 no longer fits.
- `examples/lyon/`: 2 tours, 7 orders, addresses only (needs internet).
  Public places and squares; C6 has no country (guessed from the postal code),
  C7 is a made-up address that must end up in `unplaced.csv`.

## Origin

The insertion core (immutable tour model, forward pass, feasibility check,
cheapest insertion) is adapted from the author's ZHAW project work
`pa-decision-support`, as of 2026-11-20. It was rewritten for single stops,
WGS84 coordinates and time windows. This project is not part of that work.

## License

MIT, see [LICENSE](LICENSE).
