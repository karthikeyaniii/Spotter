"""Small geometry helpers (all distances in miles)."""
import math

import numpy as np

EARTH_RADIUS_MILES = 3958.7613
US_STATES = frozenset(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)


def haversine_miles(lat1, lon1, lat2, lon2):
    """Great-circle distance between two points (scalar)."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * EARTH_RADIUS_MILES * math.asin(math.sqrt(a))


def cumulative_distances(coords):
    """
    coords: (N, 2) array of [lon, lat]. Returns (N,) array with the distance
    travelled along the polyline at each vertex, in miles.
    """
    coords = np.asarray(coords, dtype=float)
    if len(coords) < 2:
        return np.zeros(len(coords))
    lon = np.radians(coords[:, 0])
    lat = np.radians(coords[:, 1])
    dlat = np.diff(lat)
    dlon = np.diff(lon)
    a = np.sin(dlat / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(dlon / 2) ** 2
    seg = 2 * EARTH_RADIUS_MILES * np.arcsin(np.sqrt(a))
    return np.concatenate([[0.0], np.cumsum(seg)])


def thin_polyline(coords, max_points):
    """Uniformly thin a polyline to at most max_points vertices (keeps both ends)."""
    n = len(coords)
    if n <= max_points:
        return [[float(c[0]), float(c[1])] for c in coords]
    idx = np.unique(np.linspace(0, n - 1, max_points).round().astype(int))
    return [[float(coords[i][0]), float(coords[i][1])] for i in idx]
