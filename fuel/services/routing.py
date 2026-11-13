"""
Driving route between two points using the public OSRM demo server
(free, no API key). Exactly one HTTP call per route; results go to the
Django cache (file-based by default, so they survive restarts and are shared
by worker processes), so a repeated request makes zero external calls.
"""
import hashlib
import logging
import time
from dataclasses import dataclass

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from .geo import compact_polyline, cumulative_distances
from .http import polite_get

log = logging.getLogger(__name__)
METERS_PER_MILE = 1609.344


class RoutingError(Exception):
    """The routing service could not be reached or failed."""


class NoRouteError(RoutingError):
    """The service answered, but there is no drivable route between the points."""


@dataclass
class Route:
    coordinates: np.ndarray  # (N, 2) [lon, lat]
    cumulative_miles: np.ndarray  # (N,)
    distance_miles: float
    duration_hours: float
    provider: str = "osrm"
    cached: bool = False


def _cache_key(a, b):
    raw = f"{a[0]:.5f},{a[1]:.5f}|{b[0]:.5f},{b[1]:.5f}"
    return "route:" + hashlib.sha1(raw.encode()).hexdigest()


# Single-flight: while one request fetches a route, concurrent requests for the
# same route wait for it instead of calling OSRM too.
LOCK_TTL = 45  # seconds; longer than the HTTP timeout + rate-limit wait
FOLLOWER_WAIT = 40.0
NO_ROUTE_TTL = 60 * 60  # "no road route" answers are cached too
ROUTE_SPACING_MILES = 0.5  # vertex spacing kept in the cached route geometry


def get_route(start_lat, start_lon, end_lat, end_lon, *, counter=None):
    key = _cache_key((start_lat, start_lon), (end_lat, end_lon))
    hit = _from_cache(key)
    if hit is not None:
        return hit

    lock_key = key + ":lock"
    if not cache.add(lock_key, "1", timeout=LOCK_TTL):
        # Another request (possibly on another server) is fetching this route: wait for it.
        deadline = time.monotonic() + FOLLOWER_WAIT
        delay = 0.05
        while time.monotonic() < deadline:
            time.sleep(delay)
            delay = min(delay * 1.5, 0.5)
            hit = _from_cache(key)
            if hit is not None:
                return hit
            if cache.get(lock_key) is None:  # leader finished without a result: try ourselves
                break
        cache.add(lock_key, "1", timeout=LOCK_TTL)
    try:
        return _fetch_and_cache(key, start_lat, start_lon, end_lat, end_lon, counter)
    finally:
        cache.delete(lock_key)


def _from_cache(key):
    hit = cache.get(key)
    if hit is None:
        return None
    if "no_route" in hit:
        raise NoRouteError(hit["no_route"])
    return Route(**hit, cached=True)


def _fetch_and_cache(key, start_lat, start_lon, end_lat, end_lon, counter):
    try:
        route = _fetch_route(start_lat, start_lon, end_lat, end_lon, counter)
    except NoRouteError as exc:
        cache.set(key, {"no_route": str(exc)}, NO_ROUTE_TTL)
        raise
    cache.set(
        key,
        {
            "coordinates": route.coordinates,
            "cumulative_miles": route.cumulative_miles,
            "distance_miles": route.distance_miles,
            "duration_hours": route.duration_hours,
            "provider": route.provider,
        },
    )
    return route


def _fetch_route(start_lat, start_lon, end_lat, end_lon, counter):
    base = settings.FUEL_ROUTE["OSRM_BASE_URL"].rstrip("/")
    url = f"{base}/route/v1/driving/{start_lon:.6f},{start_lat:.6f};{end_lon:.6f},{end_lat:.6f}"
    params = {"overview": "full", "geometries": "geojson", "steps": "false", "alternatives": "false"}
    if counter is not None:
        counter["routing"] = counter.get("routing", 0) + 1
    log.info("OSRM request %s,%s -> %s,%s", round(start_lat, 4), round(start_lon, 4), round(end_lat, 4), round(end_lon, 4))
    try:
        r = polite_get(url, params=params, timeout=settings.FUEL_ROUTE["HTTP_TIMEOUT_SECONDS"])
    except requests.RequestException as exc:
        raise RoutingError(f"Routing service unavailable: {exc}") from exc
    try:
        body = r.json()
    except ValueError:
        body = {}
    if body.get("code") in ("NoRoute", "NoSegment", "InvalidValue"):
        raise NoRouteError(
            "There is no drivable road route between these places "
            "(for example Hawaii or Alaska to the mainland)."
        )
    if r.status_code != 200:
        raise RoutingError(f"Routing service returned HTTP {r.status_code}: {r.text[:200]}")
    if body.get("code") != "Ok" or not body.get("routes"):
        raise NoRouteError(f"No drivable route found ({body.get('code')}: {body.get('message', '')}).")

    best = body["routes"][0]
    coords = np.asarray(best["geometry"]["coordinates"], dtype=float)
    cum = cumulative_distances(coords)
    distance_miles = best["distance"] / METERS_PER_MILE
    # Scale polyline mileage so positions line up with the router's reported distance.
    if cum[-1] > 0:
        cum = cum * (distance_miles / cum[-1])
    coords, cum = compact_polyline(coords, cum, ROUTE_SPACING_MILES)
    return Route(coords, cum, distance_miles, best["duration"] / 3600.0)
