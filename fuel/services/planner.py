"""Orchestrates geocoding -> routing -> station lookup -> fuel optimisation."""
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlencode

import numpy as np
from django.conf import settings
from django.db import close_old_connections, connections

from .geo import thin_polyline
from .geocoding import geocode
from .optimizer import plan_fuel_stops
from .routing import get_route
from .stations import get_station_index


def _geocode_in_thread(query, counter):
    """Run geocode() in a worker thread; that thread gets (and must close) its own DB connection."""
    try:
        close_old_connections()
        return geocode(query, counter=counter)
    finally:
        connections.close_all()


def _geocode_pair(start, finish, calls):
    counters = ({}, {})
    with ThreadPoolExecutor(max_workers=2) as pool:
        fa = pool.submit(_geocode_in_thread, start, counters[0])
        fb = pool.submit(_geocode_in_thread, finish, counters[1])
        try:
            origin, destination = fa.result(), fb.result()
        finally:
            calls["geocoding"] += counters[0].get("geocoding", 0) + counters[1].get("geocoding", 0)
    return origin, destination


def plan_trip(start, finish, *, vehicle_range_miles=None, mpg=None, initial_range_miles=None,
              max_detour_miles=None, stop_penalty_usd=None, include_geometry=True, base_url=""):
    cfg = settings.FUEL_ROUTE
    vehicle_range_miles = float(vehicle_range_miles or cfg["VEHICLE_RANGE_MILES"])
    mpg = float(mpg or cfg["MPG"])
    max_detour_miles = float(max_detour_miles or cfg["MAX_DETOUR_MILES"])
    stop_penalty_usd = float(cfg["STOP_PENALTY_USD"] if stop_penalty_usd is None else stop_penalty_usd)
    initial_range_miles = float(vehicle_range_miles if initial_range_miles is None else initial_range_miles)

    t0 = time.perf_counter()
    timings = {}
    calls = {"geocoding": 0, "routing": 0}

    origin, destination = _geocode_pair(start, finish, calls)
    t1 = time.perf_counter()
    timings["geocoding_ms"] = round((t1 - t0) * 1000, 1)

    route = get_route(origin.latitude, origin.longitude, destination.latitude, destination.longitude, counter=calls)
    t2 = time.perf_counter()
    timings["routing_ms"] = round((t2 - t1) * 1000, 1)

    corridor = get_station_index().along_route(route.coordinates, route.cumulative_miles, max_detour_miles)
    plan = plan_fuel_stops(
        corridor,
        route.distance_miles,
        tank_range_miles=vehicle_range_miles,
        mpg=mpg,
        initial_range_miles=initial_range_miles,
        stop_penalty_usd=stop_penalty_usd,
    )
    t3 = time.perf_counter()
    timings["fuel_optimisation_ms"] = round((t3 - t2) * 1000, 1)

    avg_price = (plan.total_cost / plan.total_gallons) if plan.total_gallons else None
    # The headline cost is money spent at the stops. For a full-trip figure, value the
    # starting fuel actually burned at the average price paid (or the corridor median).
    ref_price = avg_price or (float(np.median([s.price for s in corridor])) if corridor else None)
    starting_gallons_used = min(initial_range_miles, route.distance_miles) / mpg
    trip_cost = plan.total_cost + starting_gallons_used * ref_price if ref_price and plan.feasible else None
    geometry = None
    if include_geometry:
        geometry = {"type": "LineString", "coordinates": thin_polyline(route.coordinates, cfg["MAX_GEOMETRY_POINTS"])}

    return {
        "start": origin.as_dict(),
        "finish": destination.as_dict(),
        "route": {
            "distance_miles": round(route.distance_miles, 1),
            "duration_hours": round(route.duration_hours, 2),
            "provider": route.provider,
            "geometry": geometry,
        },
        "assumptions": {
            "vehicle_range_miles": vehicle_range_miles,
            "initial_range_miles": initial_range_miles,
            "mpg": mpg,
            "max_detour_miles": max_detour_miles,
            "stop_penalty_usd": stop_penalty_usd,
            "note": (
                "The vehicle starts with `initial_range_miles` of fuel (default: a full tank). "
                "`total_fuel_cost_usd` is the money spent at the recommended stops; "
                "`estimated_total_trip_fuel_cost_usd` also values the starting fuel burned at the "
                "average price paid (set initial_range_miles=0 to buy every gallon on the way). A stop is only kept if it "
                "saves more than `stop_penalty_usd` (0 = pure cost optimum). Station locations come "
                "from city-level geocoding of the supplied price list."
            ),
        },
        "fuel_stops": [s.as_dict() for s in plan.stops],
        "summary": {
            "feasible": plan.feasible,
            "message": plan.message,
            "stranded_at_mile": plan.stranded_at_mile,
            "number_of_stops": len(plan.stops),
            "total_fuel_cost_usd": round(plan.total_cost, 2),
            "total_gallons_purchased": round(plan.total_gallons, 2),
            "average_price_paid_per_gallon": round(avg_price, 4) if avg_price else None,
            "fuel_consumed_gallons": round(route.distance_miles / mpg, 2),
            "starting_fuel_used_gallons": round(starting_gallons_used, 2),
            "estimated_total_trip_fuel_cost_usd": round(trip_cost, 2) if trip_cost is not None else None,
            "unconstrained_optimum": {
                "total_fuel_cost_usd": round(plan.unconstrained_cost or 0.0, 2),
                "number_of_stops": plan.unconstrained_stops,
            },
            "stations_considered": len(corridor),
        },
        "map_url": f"{base_url}/?{urlencode({'start': start, 'finish': finish})}",
        "external_api_calls": calls,
        "timings": timings,
        "processing_time_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


def to_geojson(result):
    """Turn a plan_trip() result into a GeoJSON FeatureCollection (route + start/finish + stops)."""
    features = []
    geometry = result["route"].get("geometry")
    if geometry:
        features.append({
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "kind": "route",
                "distance_miles": result["route"]["distance_miles"],
                "duration_hours": result["route"]["duration_hours"],
                "stroke": "#2563eb",
                "stroke-width": 4,
            },
        })
    for kind, place, color in (("start", result["start"], "#16a34a"), ("finish", result["finish"], "#dc2626")):
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [place["lon"], place["lat"]]},
            "properties": {"kind": kind, "name": place["display_name"], "marker-color": color},
        })
    for i, stop in enumerate(result["fuel_stops"], 1):
        st = stop["station"]
        features.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [st["lon"], st["lat"]]},
            "properties": {
                "kind": "fuel_stop",
                "stop_number": i,
                "name": st["name"],
                "address": f"{st['address']}, {st['city']}, {st['state']}",
                "price_per_gallon": stop["price_per_gallon"],
                "gallons_purchased": stop["gallons_purchased"],
                "cost_usd": stop["cost_usd"],
                "mile_marker": stop["mile_marker"],
                "marker-color": "#f59e0b",
                "marker-symbol": str(i),
            },
        })
    return {"type": "FeatureCollection", "properties": {"summary": result["summary"]}, "features": features}
