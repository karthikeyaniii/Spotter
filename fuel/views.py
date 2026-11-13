import logging
import time
import uuid
from urllib.parse import urlencode

from django.conf import settings
from django.core.cache import cache, caches
from django.db import connection
from django.shortcuts import render
from django.urls import reverse
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import FuelStation
from .services.geocoding import GeocodingError
from .services.places import get_place_index
from .services.planner import TripError, plan_trip, to_geojson
from .services.routing import NoRouteError, RoutingError
from .services.stations import get_station_index

log = logging.getLogger(__name__)


class RouteRequestSerializer(serializers.Serializer):
    """Accepted as query params (GET) or JSON body (POST)."""

    start = serializers.CharField(max_length=255, help_text="Start in the USA: 'City, ST', a street address or 'lat,lon'.")
    finish = serializers.CharField(max_length=255, help_text="Finish in the USA, same formats as start.")
    vehicle_range_miles = serializers.FloatField(required=False, min_value=50, max_value=2000)
    mpg = serializers.FloatField(required=False, min_value=1, max_value=100)
    initial_range_miles = serializers.FloatField(required=False, min_value=0, max_value=2000)
    max_detour_miles = serializers.FloatField(required=False, min_value=1, max_value=50)
    stop_penalty_usd = serializers.FloatField(required=False, min_value=0, max_value=1000)
    include_geometry = serializers.BooleanField(required=False, default=True)
    # Not called "format": Django REST Framework reserves that query parameter for renderers.
    output = serializers.ChoiceField(choices=["json", "geojson"], required=False, default="json",
                                     help_text="'geojson' returns a FeatureCollection you can paste into geojson.io.")

    def validate(self, attrs):
        rng = attrs.get("vehicle_range_miles", settings.FUEL_ROUTE["VEHICLE_RANGE_MILES"])
        init = attrs.get("initial_range_miles")
        if init is not None and init > rng:
            raise serializers.ValidationError(
                f"initial_range_miles ({init:g}) cannot exceed the vehicle's range ({rng:g} miles)."
            )
        if attrs["start"].strip().lower() == attrs["finish"].strip().lower():
            raise serializers.ValidationError("start and finish must be different locations.")
        return attrs


class RoutePlanView(APIView):
    """
    Plan a trip: driving route, cheapest fuel stops (500 mi range, 10 MPG) and total fuel cost.

    GET  /api/route/?start=Chicago, IL&finish=Los Angeles, CA
    POST /api/route/  {"start": "Chicago, IL", "finish": "Los Angeles, CA"}
    Add output=geojson for a map-ready FeatureCollection.
    """

    throttle_scope = "route"

    def get(self, request):
        return self._plan(request, request.query_params)

    def post(self, request):
        return self._plan(request, request.data)

    def _plan(self, request, raw):
        ser = RouteRequestSerializer(data=raw)
        if not ser.is_valid():
            return Response({"error": "Invalid request", "details": ser.errors}, status=status.HTTP_400_BAD_REQUEST)
        if len(get_station_index()) == 0:
            return Response(
                {"error": "Fuel price data not loaded",
                 "details": "Run `python manage.py load_fuel_prices` once, then retry."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        params = ser.validated_data
        base_url = request.build_absolute_uri("/").rstrip("/")
        try:
            result = plan_trip(
                params["start"],
                params["finish"],
                vehicle_range_miles=params.get("vehicle_range_miles"),
                mpg=params.get("mpg"),
                initial_range_miles=params.get("initial_range_miles"),
                max_detour_miles=params.get("max_detour_miles"),
                stop_penalty_usd=params.get("stop_penalty_usd"),
                include_geometry=params.get("include_geometry", True) or params.get("output") == "geojson",
                base_url=base_url,
            )
        except GeocodingError as exc:
            return Response({"error": "Location not found", "details": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except TripError as exc:
            return Response({"error": "Invalid trip", "details": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except NoRouteError as exc:
            return Response({"error": "No drivable route", "details": str(exc)}, status=status.HTTP_422_UNPROCESSABLE_ENTITY)
        except RoutingError as exc:
            return Response({"error": "Routing service unavailable", "details": str(exc)}, status=status.HTTP_502_BAD_GATEWAY)
        if params.get("output") == "geojson":
            return Response(to_geojson(result), content_type="application/geo+json")
        return Response(result)


class PlaceSuggestView(APIView):
    """GET /api/places/?q=chi -> US city suggestions (offline, no external calls)."""

    throttle_scope = "places"

    def get(self, request):
        q = request.query_params.get("q", "").strip()[:100]
        try:
            limit = max(1, min(int(request.query_params.get("limit", 8)), 20))
        except ValueError:
            limit = 8
        return Response({"query": q, "results": [p.as_dict() for p in get_place_index().suggest(q, limit)]})


class LivenessView(APIView):
    """GET /api/health/live/ -> 200 while the process can serve requests (no dependencies)."""

    throttle_classes = []

    def get(self, request):
        return Response({"status": "alive"})


class HealthView(APIView):
    """
    GET /api/health/ -> readiness: database, cache and station data.
    503 if any check fails, so a load balancer only routes to working replicas.
    """

    throttle_classes = []

    def get(self, request):
        checks, ok = {}, True
        t = time.perf_counter()
        try:
            with connection.cursor() as cur:
                cur.execute("SELECT 1")
            located = FuelStation.objects.filter(latitude__isnull=False).count()
            checks["database"] = {"ok": True, "ms": round((time.perf_counter() - t) * 1000, 1)}
            checks["stations"] = {"ok": located > 0, "with_coordinates": located}
            ok &= located > 0
        except Exception as exc:  # noqa: BLE001 - report, don't crash the probe
            checks["database"] = {"ok": False, "error": exc.__class__.__name__}
            ok = False
        t = time.perf_counter()
        try:
            probe = f"health:{uuid.uuid4().hex}"
            cache.set(probe, "1", 10)
            cache_ok = cache.get(probe) == "1"
            cache.delete(probe)
            checks["cache"] = {"ok": cache_ok, "backend": caches["default"].__class__.__name__,
                               "ms": round((time.perf_counter() - t) * 1000, 1)}
            ok &= cache_ok
        except Exception as exc:  # noqa: BLE001
            checks["cache"] = {"ok": False, "error": exc.__class__.__name__}
            ok = False
        body = {"status": "ok" if ok else "degraded", "checks": checks}
        return Response(body, status=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE)


class ApiRootView(APIView):
    def get(self, request):
        u = request.build_absolute_uri
        trip = {"start": "Chicago, IL", "finish": "Los Angeles, CA"}
        return Response(
            {
                "name": "Fuel Route Planner API",
                "endpoints": {
                    "plan_route": u(reverse("fuel:route")) + "?" + urlencode(trip),
                    "plan_route_geojson": u(reverse("fuel:route")) + "?" + urlencode({**trip, "output": "geojson"}),
                    "place_suggestions": u(reverse("fuel:places")) + "?q=chi",
                    "health": u(reverse("fuel:health")),
                    "map_ui": u("/"),
                },
                "defaults": {"vehicle_range_miles": settings.FUEL_ROUTE["VEHICLE_RANGE_MILES"],
                             "mpg": settings.FUEL_ROUTE["MPG"]},
            }
        )


def map_page(request):
    """Interactive map that calls the API and draws the route + fuel stops."""
    return render(
        request,
        "fuel/map.html",
        {
            "start": request.GET.get("start", ""),
            "finish": request.GET.get("finish", ""),
            "station_count": f"{len(get_station_index()):,}",
        },
    )
