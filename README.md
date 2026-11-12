# Fuel Route Planner (Django)

Give it a **start** and a **finish** anywhere in the USA. It returns:

* the driving route as a map (interactive page, GeoJSON, and route geometry in the JSON),
* the **cheapest places to fuel up** along the way for a vehicle with a **500‑mile range**,
* the **total money spent on fuel** at **10 miles per gallon**,

using the supplied truck‑stop price list (`data/fuel-prices-for-be-assessment.csv`) and free,
key‑less map services.

![Stack](https://img.shields.io/badge/Django-6.1-0C4B33) ![Tests](https://img.shields.io/badge/tests-33%20passing-brightgreen)

---

## Requirements checklist

| Requirement (from the assignment) | How it is met |
|---|---|
| API takes a start and finish inside the USA | `GET` or `POST /api/route/` accepts "City, ST", street addresses or `lat,lon`. Places outside the USA are rejected with a clear 400. |
| Return a map of the route | Route geometry (GeoJSON LineString) in every response, `output=geojson` for a ready‑to‑draw FeatureCollection, and an interactive map page at `/` (link in `map_url`). |
| Optimal, cost‑effective fuel stops | A dynamic programme that minimises fuel cost over every station within 10 mi of the route; with `stop_penalty_usd=0` it is the exact cost optimum (tested against the provably optimal greedy). |
| 500‑mile range, multiple stops | Tank is tracked mile by mile; as many stops as needed, never more than 500 miles between fills. |
| Total money spent at 10 MPG | `summary.total_fuel_cost_usd` plus gallons, average price and a full‑trip estimate. |
| Use the attached price file | Loaded automatically by `python manage.py migrate` (6,626 US stations; duplicate rows averaged, Canadian rows dropped). |
| Free map / routing API | OSRM for routing; offline US Census place list, Nominatim and Photon for locations; Esri/OSM map tiles. No keys. |
| Latest stable Django | Django 6.1.3 (+ Django REST Framework). |
| Fast responses | ~20–70 ms of our own work for a coast‑to‑coast route; a repeated trip returns in ~90 ms with zero external calls. |
| Few calls to the map/routing API (1 ideal) | **1 call** for "City, ST" inputs: cities resolve from a bundled Census list, so only the route is fetched. At most 3 for street addresses; repeats are cached (0 calls). Every response reports `external_api_calls`. |

---

## Quick start

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate        # creates the database and loads the fuel price list
python manage.py runserver
```

Open **<http://127.0.0.1:8000>** for the map, or call the API:

```
http://127.0.0.1:8000/api/route/?start=Chicago, IL&finish=Los Angeles, CA
```

Run the tests with `python manage.py test`. A Postman collection is in
`postman/FuelRoutePlanner.postman_collection.json`.

---

## Using the map page

1. Type where you start and finish. Suggestions appear as you type (or pick an example trip).
2. Optionally open **Vehicle settings**: tank range, MPG, how full the tank is when you leave,
   and the plan style ("Fewer stops" or "Cheapest possible").
3. Press **Find cheapest fuel stops**. You get the total fuel cost, distance, drive time and a
   step‑by‑step itinerary. Click a stop to zoom to it on the map.
4. **Copy link** shares the trip; **Copy API URL**, **GeoJSON** and **View JSON** give you the
   raw data.

It works on phones too (map on top, itinerary below).

---

## API

### `GET|POST /api/route/`

| Parameter | Default | Notes |
|---|---|---|
| `start`, `finish` | required | "Chicago, IL", "Austin TX", a street address, or `"lat,lon"` |
| `vehicle_range_miles` | 500 | Range on a full tank |
| `mpg` | 10 | Fuel economy |
| `initial_range_miles` | full tank | Fuel in the tank at the start (`0` = buy every gallon on the way) |
| `stop_penalty_usd` | 10 | A stop is kept only if it saves more than this; `0` = pure cost optimum |
| `max_detour_miles` | 10 | How far from the route a station may be |
| `include_geometry` | true | `false` for a small payload |
| `output` | `json` | `geojson` returns a FeatureCollection (route, start, finish, numbered stops) you can paste into geojson.io |

POST takes the same fields as JSON. Example response (New York → Los Angeles, trimmed):

```json
{
  "start":  {"query": "New York, NY", "display_name": "New York, NY, United States", "provider": "us-places", "lat": 40.66271, "lon": -73.93868},
  "finish": {"query": "Los Angeles, CA", "display_name": "Los Angeles, CA, United States", "provider": "us-places", "lat": 34.01939, "lon": -118.41083},
  "route": {"distance_miles": 2810.4, "duration_hours": 50.3, "provider": "osrm",
            "geometry": {"type": "LineString", "coordinates": [[-73.938719, 40.663098], "... 2,500 points ..."]}},
  "fuel_stops": [
    {"station": {"opis_id": 72445, "name": "SHEETZ #639", "address": "I-80 Exit 223", "city": "Youngstown", "state": "OH",
                 "lat": 41.099095, "lon": -80.645902, "price_per_gallon": 3.059, "mile_marker": 396.0, "detour_miles": 3.8},
     "mile_marker": 396.0, "detour_miles": 3.8, "price_per_gallon": 3.059,
     "range_on_arrival_miles": 104.0, "gallons_purchased": 35.8, "cost_usd": 109.51, "range_on_departure_miles": 462.0},
    "... 5 more stops ..."
  ],
  "summary": {
    "feasible": true, "message": "6 fuel stop(s) needed.", "number_of_stops": 6,
    "total_fuel_cost_usd": 709.05, "total_gallons_purchased": 231.0, "average_price_paid_per_gallon": 3.0695,
    "fuel_consumed_gallons": 281.04, "starting_fuel_used_gallons": 50.0, "estimated_total_trip_fuel_cost_usd": 862.52,
    "unconstrained_optimum": {"total_fuel_cost_usd": 699.11, "number_of_stops": 16},
    "stations_considered": 460
  },
  "map_url": "http://127.0.0.1:8000/?start=New+York%2C+NY&finish=Los+Angeles%2C+CA",
  "external_api_calls": {"geocoding": 0, "routing": 1},
  "timings": {"geocoding_ms": 0.7, "routing_ms": 2147.8, "fuel_optimisation_ms": 30.1},
  "processing_time_ms": 2180.4
}
```

**Errors** use plain‑language messages: `400` invalid input or place not found in the USA,
`422` no drivable road route (e.g. Hawaii → mainland), `502` routing service down, `503` price
data not loaded. If no chain of stations can cover a gap longer than the tank, the response is
`200` with `summary.feasible = false` and the mile where the vehicle would run dry.

### Other endpoints

* `GET /api/places/?q=chi` – city suggestions (offline, ~0.1 ms).
* `GET /api/health/` – station counts.
* `GET /api/` – index of endpoints.
* `GET /` – the map page.

---

## How it works

```
request ─► resolve start/finish ─► OSRM route (1 call) ─► stations near route ─► optimal stops ─► JSON / GeoJSON / map
           offline Census list,     cached in process      KD-tree, ~13 ms        dynamic programme, ~25 ms
           then DB cache, Nominatim,
           Photon
```

1. **Locations** (`fuel/services/places.py`, `geocoding.py`). "City, ST", "City State",
   "City ST 12345" and unambiguous bare city names ("Nashville") resolve from a bundled list of
   31,828 US places (Census Gazetteer + population estimates), with no network call. Street
   addresses and ambiguous names ("Springfield") fall back to Nominatim, then Photon. Results
   are cached in the database and must be inside the USA.
2. **Route** (`routing.py`). One request to the public OSRM server, cached in process.
3. **Stations near the route** (`stations.py`). The route is sampled about every mile into a
   SciPy KD‑tree of 3D unit vectors; one query gives each station's nearest route point within
   `max_detour_miles`, which yields its mile marker and detour. Exact great‑circle distances.
4. **Fuel stops** (`optimizer.py`). A dynamic programme minimises
   **fuel cost + `stop_penalty_usd` × number of stops**. Stations are processed in route order
   and the tank is tracked in 1‑mile units (501 states for a 500‑mile tank). Buying from `f` to
   `g` miles of range costs `penalty + (price / mpg) × (g − f)`; the best `f` is a prefix
   minimum, so each station costs O(tank) with NumPy. With no penalty this equals the
   provably optimal greedy for the *gas‑station problem* (Khuller, Malekian & Mestre), which a
   randomised test checks on 300 instances.
5. **Cost**. `gallons = miles / mpg` priced at each stop. `total_fuel_cost_usd` is what you pay
   at the stops (the tank starts full by default); `estimated_total_trip_fuel_cost_usd` also
   counts the starting fuel, valued at the average price paid.

**Why a stop penalty?** The pure optimum stops to save cents: 16 stops New York → Los Angeles.
The default $10 penalty gives 6 stops for about 1.4 % more. The response always shows both,
and `stop_penalty_usd=0` (or "Cheapest possible" in the UI) returns the pure optimum.

### Station coordinates

The price file has city and state but no coordinates. The 3,813 unique US city/state pairs
were geocoded once and committed in `data/city_coords.json`: first from the US Census
Gazetteer (offline, unambiguous names only), then Nominatim for the rest. Where the two
disagreed by more than 10 miles the Census point was used, after spot checks showed Nominatim
had picked a same‑named town elsewhere. Positions are city‑level, so the planner uses a
10‑mile corridor. Rebuild with `python manage.py geocode_station_cities` if the price list
changes, then `python manage.py load_fuel_prices`.

---

## Configuration

Everything works with no configuration. Optional environment variables (see `.env.example`):
`VEHICLE_RANGE_MILES`, `VEHICLE_MPG`, `MAX_DETOUR_MILES`, `STOP_PENALTY_USD`,
`OSRM_BASE_URL` (e.g. a self‑hosted OSRM), `NOMINATIM_BASE_URL`, `PHOTON_BASE_URL`,
`GEOCODER_TIMEOUT_SECONDS`, `DJANGO_SECRET_KEY`, `DJANGO_DEBUG`, `DJANGO_ALLOWED_HOSTS`.

## Project layout

```
config/                     Django project (settings, urls)
fuel/
  models.py                 FuelStation, GeocodeCache
  views.py, urls.py         /api/route/, /api/places/, /api/health/, /api/ and the map page
  services/
    places.py               offline US place lookup + suggestions
    geocoding.py            location resolution with cache and fallbacks
    routing.py              single OSRM call + cache
    stations.py             KD-tree search for stations along a route
    optimizer.py            dynamic programme (+ reference greedy)
    planner.py              orchestration, response and GeoJSON assembly
    importer.py             CSV -> database (used by migrate and load_fuel_prices)
  migrations/0002_...       loads the price list on migrate
  management/commands/      load_fuel_prices, geocode_station_cities
  templates/fuel/map.html   the map page (Leaflet)
  tests/                    33 tests; external services are mocked
data/                       price CSV, station coordinates, US place list
postman/                    Postman collection
scripts/                    one-off data builders (Census + Nominatim)
```

Data sources: fuel prices from the supplied OPIS list; US places and populations from the
US Census Bureau (public domain); routes from OSRM; map data © OpenStreetMap contributors.
