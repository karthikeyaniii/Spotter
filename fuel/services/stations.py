"""
In-memory spatial index of fuel stations.

All stations with coordinates are loaded from the DB once per process and
kept as 3D unit vectors. For a route we sample the polyline about every mile,
put the samples in a KD-tree and ask, for every station, for its nearest route
sample within the corridor. Chord distance on the unit sphere maps exactly to
great-circle distance, so the result is exact and takes a few milliseconds even
for coast-to-coast routes.
"""
import threading
import time
from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree

from fuel.models import FuelStation

EARTH_RADIUS_MILES = 3958.7613


def _unit_vectors(lat_deg, lon_deg):
    lat = np.radians(np.asarray(lat_deg, dtype=float))
    lon = np.radians(np.asarray(lon_deg, dtype=float))
    cos_lat = np.cos(lat)
    return np.column_stack([cos_lat * np.cos(lon), cos_lat * np.sin(lon), np.sin(lat)])


def _miles_to_chord(miles):
    return 2.0 * np.sin(np.asarray(miles, dtype=float) / (2.0 * EARTH_RADIUS_MILES))


def _chord_to_miles(chord):
    return 2.0 * EARTH_RADIUS_MILES * np.arcsin(np.clip(np.asarray(chord, dtype=float) / 2.0, 0.0, 1.0))


@dataclass
class RouteStation:
    """A station that lies within the search corridor of the route."""

    opis_id: int
    name: str
    address: str
    city: str
    state: str
    latitude: float
    longitude: float
    price: float
    mile_marker: float  # distance along the route of the closest route point
    detour_miles: float  # straight-line distance from the route

    def as_dict(self):
        return {
            "opis_id": self.opis_id,
            "name": self.name,
            "address": self.address,
            "city": self.city,
            "state": self.state,
            "lat": self.latitude,
            "lon": self.longitude,
            "price_per_gallon": round(self.price, 4),
            "mile_marker": round(self.mile_marker, 1),
            "detour_miles": round(self.detour_miles, 1),
        }


class StationIndex:
    def __init__(self, rows):
        # rows: iterable of (opis_id, name, address, city, state, lat, lon, price)
        self.meta = list(rows)
        self.lat = np.array([r[5] for r in self.meta], dtype=float)
        self.lon = np.array([r[6] for r in self.meta], dtype=float)
        self.price = np.array([r[7] for r in self.meta], dtype=float)
        self.xyz = _unit_vectors(self.lat, self.lon) if self.meta else np.empty((0, 3))

    def __len__(self):
        return len(self.meta)

    @classmethod
    def from_db(cls):
        qs = (
            FuelStation.objects.filter(latitude__isnull=False, longitude__isnull=False)
            .values_list("opis_id", "name", "address", "city", "state", "latitude", "longitude", "retail_price")
            .order_by("opis_id")
        )
        return cls((a, b, c, d, e, f, g, float(h)) for a, b, c, d, e, f, g, h in qs)

    def along_route(self, coords, cumulative_miles, max_detour_miles, sample_spacing_miles=1.0):
        """
        coords: (N,2) [lon,lat] route polyline; cumulative_miles: (N,).
        Returns RouteStation list for stations within max_detour_miles of the
        route, sorted by mile marker.
        """
        if len(self) == 0 or len(coords) == 0:
            return []
        coords = np.asarray(coords, dtype=float)
        cumulative_miles = np.asarray(cumulative_miles, dtype=float)
        total = float(cumulative_miles[-1])
        if total <= 0:
            return []

        # Sample the route about every `sample_spacing_miles` (plus the last point).
        targets = np.arange(0.0, total, sample_spacing_miles)
        sample_idx = np.unique(np.append(np.searchsorted(cumulative_miles, targets), len(coords) - 1))
        sample_idx = sample_idx[sample_idx < len(coords)]
        tree = cKDTree(_unit_vectors(coords[sample_idx, 1], coords[sample_idx, 0]))

        chord, nearest = tree.query(self.xyz, k=1, distance_upper_bound=float(_miles_to_chord(max_detour_miles)))
        hits = np.nonzero(np.isfinite(chord))[0]
        detours = _chord_to_miles(chord[hits])
        miles = cumulative_miles[sample_idx[nearest[hits]]]

        results = []
        for i, mile, detour in zip(hits.tolist(), miles.tolist(), detours.tolist()):
            m = self.meta[i]
            results.append(RouteStation(m[0], m[1], m[2], m[3], m[4], m[5], m[6], float(m[7]), mile, detour))
        results.sort(key=lambda s: (s.mile_marker, s.price))
        return results


# ---------------------------------------------------------------- singleton
# The index is cached per process. A cheap fingerprint of the table is checked at
# most every FINGERPRINT_TTL seconds, so `load_fuel_prices` run from another
# process (or a new deploy's migration) is picked up without restarting the server.
FINGERPRINT_TTL = 30.0
_lock = threading.Lock()
_index = None
_fingerprint = None
_checked_at = 0.0


def _db_fingerprint():
    from django.db.models import Count, Max

    agg = FuelStation.objects.aggregate(n=Count("id"), last=Max("id"))
    return agg["n"], agg["last"]


def get_station_index(force_reload=False):
    global _index, _fingerprint, _checked_at
    # Fast path without the lock: the index is immutable once built, and the fingerprint
    # only needs re-checking every FINGERPRINT_TTL seconds.
    index = _index
    if index is not None and not force_reload and time.monotonic() - _checked_at < FINGERPRINT_TTL:
        return index
    with _lock:
        now = time.monotonic()
        if _index is not None and not force_reload and now - _checked_at < FINGERPRINT_TTL:
            return _index
        fp = _db_fingerprint()
        _checked_at = now
        if _index is None or force_reload or fp != _fingerprint:
            _index = StationIndex.from_db()
            _fingerprint = fp
        return _index


def station_data_version():
    """Fingerprint of the station data the current index was built from (for cache keys)."""
    get_station_index()
    return _fingerprint


def invalidate_station_index():
    global _index, _fingerprint
    with _lock:
        _index = None
        _fingerprint = None
