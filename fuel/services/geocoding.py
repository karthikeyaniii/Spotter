"""
Geocode the user's start / finish inputs.

Order of resolution:
  1. "lat,lon" literal: a quick bounding-box test, then one reverse lookup
     (cached) to confirm the point is in the USA and not e.g. Toronto
  2. bundled US place list, e.g. "Chicago, IL" (no external call)
  3. persistent DB cache (GeocodeCache) -> no external call on repeat queries
  4. Nominatim (OpenStreetMap), restricted to the USA
  5. Photon (komoot) fallback if Nominatim fails or rate-limits

So a typical "City, ST" -> "City, ST" request needs only the single routing call.

Every result is validated to be inside the United States.
"""
import logging
import re
from dataclasses import asdict, dataclass

import requests
from django.conf import settings

from fuel.models import GeocodeCache

from .http import polite_get
from .places import get_place_index

log = logging.getLogger(__name__)

_LATLON_RE = re.compile(r"^\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*$")
# Generous bounding box covering CONUS, Alaska and Hawaii.
_US_BOUNDS = ((18.0, 72.0), (-180.0, -64.0))


class GeocodingError(Exception):
    """Raised when a location can't be resolved to a point inside the USA."""


@dataclass
class Place:
    query: str
    display_name: str
    latitude: float
    longitude: float
    provider: str
    cached: bool = False

    def as_dict(self):
        d = asdict(self)
        d["lat"] = d.pop("latitude")
        d["lon"] = d.pop("longitude")
        return d


def _cfg(key):
    return settings.FUEL_ROUTE[key]


def _in_us_bounds(lat, lon):
    (lat0, lat1), (lon0, lon1) = _US_BOUNDS
    return lat0 <= lat <= lat1 and lon0 <= lon <= lon1


def _nominatim(query):
    url = _cfg("NOMINATIM_BASE_URL").rstrip("/") + "/search"
    params = {
        "q": query,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "us",
        "addressdetails": 1,
    }
    r = polite_get(url, params=params, timeout=_cfg("GEOCODER_TIMEOUT_SECONDS"))
    r.raise_for_status()
    data = r.json()
    if not data:
        return None
    hit = data[0]
    if hit.get("address", {}).get("country_code", "us") != "us":
        return None
    return Place(query, hit.get("display_name", query), float(hit["lat"]), float(hit["lon"]), "nominatim")


def _photon(query):
    url = _cfg("PHOTON_BASE_URL").rstrip("/") + "/api/"
    r = polite_get(url, params={"q": query, "limit": 5}, timeout=_cfg("GEOCODER_TIMEOUT_SECONDS"))
    r.raise_for_status()
    for feat in r.json().get("features", []):
        props = feat.get("properties", {})
        if props.get("countrycode", "").upper() != "US":
            continue
        lon, lat = feat["geometry"]["coordinates"]
        parts = [props.get(k) for k in ("name", "city", "state", "country") if props.get(k)]
        return Place(query, ", ".join(dict.fromkeys(parts)), float(lat), float(lon), "photon")
    return None


def _reverse_country(lat, lon):
    """Country code of a point via Nominatim reverse geocoding (country-level zoom)."""
    url = _cfg("NOMINATIM_BASE_URL").rstrip("/") + "/reverse"
    params = {"lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "format": "jsonv2", "zoom": 3, "addressdetails": 1}
    r = polite_get(url, params=params, timeout=_cfg("GEOCODER_TIMEOUT_SECONDS"))
    r.raise_for_status()
    return (r.json().get("address") or {}).get("country_code", "")


def _check_coordinates(query, lat, lon, counter):
    """The bounding box also covers parts of Canada and Mexico, so confirm the country once."""
    if not _in_us_bounds(lat, lon):
        raise GeocodingError(f"Coordinates {lat},{lon} are outside the United States.")
    key = f"rev:{lat:.4f},{lon:.4f}"
    cached = GeocodeCache.objects.filter(query=key).first()
    if cached:
        country = cached.provider
    else:
        try:
            if counter is not None:
                counter["geocoding"] = counter.get("geocoding", 0) + 1
            country = _reverse_country(lat, lon)
        except (requests.RequestException, ValueError) as exc:
            # Service down: fall back to the bounding box rather than fail the request.
            log.warning("reverse geocode failed for %s: %s", query, exc)
            return
        GeocodeCache.objects.get_or_create(
            query=key, defaults={"display_name": country, "latitude": lat, "longitude": lon, "provider": country}
        )
    if country and country != "us":
        raise GeocodingError(f"Coordinates {lat},{lon} are outside the United States.")


def geocode(query, *, counter=None):
    """
    Resolve `query` to a Place inside the USA.
    `counter` (optional dict) is incremented under "geocoding" for every external call made.
    """
    query = (query or "").strip()
    if not query:
        raise GeocodingError("Location is required.")

    m = _LATLON_RE.match(query)
    if m:
        lat, lon = float(m.group(1)), float(m.group(2))
        _check_coordinates(query, lat, lon, counter)
        return Place(query, f"{lat:.5f}, {lon:.5f}", lat, lon, "coordinates")

    local = get_place_index().resolve(query)
    if local is not None:
        return Place(query, f"{local.label}, United States", local.lat, local.lon, "us-places")

    key = re.sub(r"\s+", " ", query).lower()
    cached = GeocodeCache.objects.filter(query=key).first()
    if cached:
        return Place(query, cached.display_name, cached.latitude, cached.longitude, cached.provider, cached=True)

    place = None
    errors = []
    for provider in (_nominatim, _photon):
        try:
            if counter is not None:
                counter["geocoding"] = counter.get("geocoding", 0) + 1
            place = provider(query)
            # A definite answer (found, or "nothing in the USA") ends the search; the
            # fallback is only for when a service fails or rate-limits.
            break
        except (requests.RequestException, ValueError, KeyError) as exc:  # network / parse errors
            errors.append(f"{provider.__name__.strip('_')}: {exc}")
            log.warning("geocoder %s failed for %r: %s", provider.__name__, query, exc)

    if not place:
        # Technical details go to the log; the user gets a plain message.
        if errors and len(errors) == 2:
            raise GeocodingError(
                f"We couldn't look up '{query}' right now (the address search service did not respond)."
            )
        raise GeocodingError(f"We couldn't find '{query}' in the USA.")
    if not _in_us_bounds(place.latitude, place.longitude):
        raise GeocodingError(f"'{query}' resolved to a point outside the United States.")

    GeocodeCache.objects.get_or_create(
        query=key,
        defaults={
            "display_name": place.display_name[:400],
            "latitude": place.latitude,
            "longitude": place.longitude,
            "provider": place.provider,
        },
    )
    return place
