# tour-insertion-tool

Plan delivery tours from a list of stops, or insert new orders into tours
that were planned the evening before.

Two ways to work:

- **Plan tours** from a list of stops (e.g. an Excel file in the customer's
  own layout): the stops are spread over the available vehicles, keeping
  capacity and the latest return time. See *Planning from a stop list*.
- **Insert orders**: tours are planned in the evening for the next day,
  orders that arrive overnight are added in the morning. Each new stop goes
  to the position in an existing tour where it costs the least extra time,
  keeping all time windows. Existing tours are **not** rearranged, only
  extended.

## Quick start

```bash
uv sync                     # or: pip install -e .
uv run pytest

# offline: coordinates are in the CSV
uv run tour-insert examples/winterthur/tours.csv examples/winterthur/orders.csv \
    --depot "47.5000, 8.7236" -o out --map

# online: only addresses, geocoded on the fly
uv run tour-insert examples/lyon/tours.csv examples/lyon/orders.csv \
    --depot "Place Charles Béraudier, 69003 Lyon" -o out-lyon --map

# plan tours from an Excel stop list (online, geocodes 141 addresses once)
uv run tour-insert --stops examples/praxen/stops.xlsx --template examples/praxen/template.json \
    --depot "Bahnhofplatz 1, 8001 Zürich" --fleet Lastenvelo=10 Auto=2 --end 12:00 -o out-plan --map
```

Output folder:

- `tours_new.csv`: every tour with the new stops (`is_new = 1`), planned arrival
  and departure, and slack to the time window
- `unplaced.csv`: orders that fit into no tour or could not be geocoded, with
  the reason
- `tours.xlsx`: one sheet per tour (order, times, name, address, quantity
  per product, load, time window, info columns, a "done" column), plus a
  sheet with what could not be planned
- `tours.pdf`: one driver sheet per tour (no map): vehicle, start, return,
  items per product, then every stop with arrival, name, address, floor,
  items, time window and notes (e.g. opening hours), and a tick box
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
- Below the map: orders not placed and skipped stops first (so none is
  forgotten), then the tours: an overview with one row per tour and one
  tab per tour with all its stops, times, load and notes. The selected
  tour is highlighted on the map; clicking a stop opens it on the map.
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

## Input

`tours.csv`: the customer stops of each tour, in driving order. No depot
rows: where and when tours start and end is set in the tool (see *Start and
end of the tours*).

| column | meaning |
|---|---|
| tour_id, seq | tour and position in the tour (sorted by seq) |
| stop_id | unique id |
| address | full address with postal code, e.g. `1 Place de la Comédie, 69001 Lyon` |
| country | `FR` or `CH`; if empty, guessed from the postal code (5 digits FR, 4 digits CH) |
| lat, lon | WGS84 coordinates; if empty, the address is geocoded |
| tw_start, tw_end | time window `HH:MM`, optional |
| service_min | minutes at the stop, optional (default `--service-min`) |
| items | number of parcels/items at the stop, optional (empty = 0 for the capacity check) |
| type | `drop` (items are delivered, the default) or `pickup` (items are collected) |
| vehicle | vehicle type of the tour, on at least one row of the tour (e.g. the first); optional |

`orders.csv`: same columns, with `order_id` instead of `tour_id, seq, stop_id`
(and without `vehicle`).

## Start and end of the tours

Set once for all tours (web page: sidebar *Depot and times*; command line
in brackets), for both ways to work:

| setting | choices |
|---|---|
| depot | address or `lat, lon` (`--depot`) |
| start | at the depot, or at the first stop (`--start-at depot\|first`) |
| end | back to the depot, at another place such as a connection point (hand-over, train, second depot), or open at the last stop (`--end-at depot\|open\|ADDRESS`) |
| start time | e.g. 08:00 (`--start`) |
| latest end | optional, e.g. 12:00 (`--end`); with an open end every stop must be done by then |

New stops are never inserted before a start at the depot or after an end
at the depot or a connection point. On the map a round trip's depot is
**D**; otherwise **S** (start) and **E** (end); E turns red when the tour
arrives too late. In `tours_new.csv` start and end are rows with type
`start` / `end`.

## Planning from a stop list

Input: one file with the stops, CSV or Excel (`.xlsx`) in any layout. A
**template** says what each column means (`stop_import.py`):

| role | meaning |
|---|---|
| address | street and number, or the full address; several columns are joined |
| postcode, city | optional, added to the address |
| id | unique id (optional; else 001, 002, ...) |
| name | shown as the stop's name (e.g. practice, location) |
| quantity | number per product; summed to the stop's items, kept per product for the summary and export |
| info | passed on unchanged to the export (contact, opening hours, ...) |

Plus for the whole file: drop or pickup, country (or from the postcode),
items per stop when there is no quantity column. Rows that say "Total" or
are empty are skipped; floors like "1. Stock" are kept for the driver but
not sent to the geocoder. In the web page the columns are guessed and shown
with example values; the mapping can be saved as a template (in the
browser). Examples with templates: `examples/praxen/`, `examples/standorte/`.

Settings: depot (address or `lat, lon`), start time, latest return
("back by", optional), and how many vehicles of each type are available.

Method (a simple heuristic, not an optimal plan): every vehicle starts with
an empty tour depot → depot; all stops are inserted with the same cheapest
insertion as overnight orders, as long as capacity and return time allow;
then the order within each tour is improved. Vehicles that get no stop are
left out. Without a return time, the cheapest plan is usually ONE long tour
of the fastest vehicle: set "back by" to spread the stops. A real vehicle
routing solver (e.g. OR-Tools) would find better plans for many stops.

### Tours by hand, then the rest automatically

Dispatchers often know at a glance which stops belong together. In the web
page (plan tours):

1. **Show stops on the map**: the list is geocoded and shown as grey dots;
   addresses not found appear right away.
2. **Tours by hand**: choose a vehicle, *New tour*, then click the stops in
   the order you want. Times, load and the latest end are recomputed after
   every click; a tour that is too full or too long turns red but is
   allowed. *Remove last stop*, *Done*, *continue*, *dissolve*.
3. **Plan the remaining stops**: the automatic planning uses the remaining
   vehicles for the remaining stops and does not change the tours by hand
   at all (🔒). Afterwards everything, those tours included, can be
   changed by drag & drop.

**Vehicles in order** (checkbox under the vehicle table): the vehicle types
are used one after the other in the order of the table (▲ moves a type up),
e.g. cargo bikes first; cars only get what the bikes cannot take. Without
it the planning looks for the least total time over all vehicles.

## Vehicles and capacity

A tour's `vehicle` names a vehicle type. Each type has a capacity (most
items on board at once) and its own speed. Defaults (assumptions):

| type | capacity | speed |
|---|---|---|
| Lastenvelo (cargo bike with trailer) | 10 items | 15 km/h |
| Auto (car) | 40 items | 20 km/h |

Change them in the web page (*Vehicles*) or with `--vehicles file.json`,
e.g. `{"Lastenvelo": {"capacity": 10, "speed_kmh": 15}}`. Empty capacity
= no limit, empty speed = the general speed. A tour without vehicle has no
limit. An unknown vehicle name stops the run with an error.

Load along a tour (assumption): all items to be **dropped** are loaded at
the start; items **picked up** stay on board until the end of the tour.
The load must never be above the capacity. With drops only this is just
the sum of items; with pickups the order matters, so a pickup is placed
where enough has already been delivered. An order that would fit in time
but finds no room gets the reason `capacity`. Moving a stop by hand may
overload a tour: the page then shows the tour in red and a warning.

On the map, pickups are drawn inverted (white with a coloured ring),
drops filled. `tours_new.csv` has the columns `vehicle`, `type` and `load`
(items on board after the stop).

## Geocoding

| country | service | quality check |
|---|---|---|
| FR | IGN Géoplateforme (BAN), `data.geopf.fr/geocodage` | score ≥ `--min-score` (default 0.5) |
| CH | swisstopo GeoAdmin API, official building addresses | postal code of the input appears in the result |

Both are free and need no key. Results are cached in `.geocode_cache.json`
(`--geocode-cache`), so each address is looked up once.

- The **depot** (and an end place) must be found: otherwise the run stops,
  because every time of the tour depends on it. Tip: give it as lat/lon.
- Any **other tour stop** that cannot be geocoded reliably is left out: the
  tour is computed without it and the stop is listed in `skipped_stops.csv`
  (and in the app). Times after that stop are then slightly too early.
- A **new order** that cannot be geocoded reliably is not planned and appears
  in `unplaced.csv` (`geocode_not_found` / `geocode_uncertain`).

## Optimising tours (optional)

`--optimize` (or the checkbox in the app) also improves the order of the
stops **within each tour**: same stops, same tour, time windows kept, start
and end stay first and last. Steps: improve the given tours, insert the new orders,
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

- New orders are single stops (no pickup/delivery pairs).
- Start and end are the same for all tours (settings, not in the files).
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
4. ~~Web app (API + JavaScript page with drag & drop)~~
5. ~~Optional reordering within each tour~~
6. ~~End at a connection point; vehicles with capacity; pickups and drops~~
7. Optional: street-network travel times (OSRM)

## Example data

All example data is **invented**. No real customer data is used or stored in
this repository.

- `examples/winterthur/`: 2 tours, 7 orders, with approximate coordinates
  (runs offline). Shows most features at once: T1 is a cargo bike (10
  items), T2 a car (40 items); depot Winterthur HB `47.5000, 8.7236`.
  N3 lies on the bike's route, but the bike is too full, so the car takes
  it; N5 and N6 are pickups; N7 cannot be reached in time on purpose.
- `examples/praxen/stops.xlsx`: 141 invented practices (Zurich region),
  street, postcode and city in separate columns, opening hours as info.
- `examples/standorte/stops.xlsx`: 20 invented locations, address in one
  column, two products (Box 50er, Box 25er), a total row.
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
