"""Tests for the architecture-review fixes."""
import time
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from fuel.models import FuelStation
from fuel.services import http, stations
from fuel.services.geocoding import Place

from .test_api import fake_route


def same_point_geocode(query, *, counter=None):
    return Place(query, "Chicago, IL", 41.84, -87.68, "test")


class TripValidationTests(TestCase):
    @patch("fuel.services.planner.get_route")
    @patch("fuel.services.planner.geocode", side_effect=same_point_geocode)
    def test_same_place_spelled_differently_is_rejected_before_routing(self, _g, route):
        resp = self.client.get(reverse("fuel:route"), {"start": "Chicago", "finish": "Chicago, IL"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Invalid trip")
        route.assert_not_called()  # no routing call wasted

    def test_initial_fuel_cannot_exceed_default_tank(self):
        resp = self.client.get(reverse("fuel:route"), {"start": "A", "finish": "B", "initial_range_miles": 800})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("cannot exceed", str(resp.json()["details"]))


class ApiErrorShapeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_unexpected_error_returns_json_500_without_traceback(self):
        with patch("fuel.views.plan_trip", side_effect=ZeroDivisionError("boom")), \
                self.assertLogs("fuel.api", level="ERROR"):
            resp = self.client.get(reverse("fuel:route"), {"start": "Dallas, TX", "finish": "Denver, CO"})
        self.assertEqual(resp.status_code, 500)
        self.assertEqual(resp["Content-Type"], "application/json")
        self.assertNotIn("boom", resp.content.decode())

    def test_rate_limit_returns_json_429(self):
        from rest_framework.settings import api_settings

        rates = {**api_settings.DEFAULT_THROTTLE_RATES, "places": "2/minute"}
        with patch("rest_framework.throttling.ScopedRateThrottle.THROTTLE_RATES", rates):
            codes = [self.client.get(reverse("fuel:places"), {"q": "chi"}).status_code for _ in range(3)]
            resp = self.client.get(reverse("fuel:places"), {"q": "chi"})
        self.assertEqual(codes, [200, 200, 429])
        self.assertEqual(resp.json()["error"], "Too many requests")

    @patch("fuel.services.planner.get_route", side_effect=fake_route)
    def test_responses_are_gzipped(self, _r):
        resp = self.client.get(reverse("fuel:route"), {"start": "Dallas, TX", "finish": "Denver, CO"},
                               HTTP_ACCEPT_ENCODING="gzip")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp["Content-Encoding"], "gzip")


class StationIndexRefreshTests(TestCase):
    def test_index_reloads_when_table_changes(self):
        FuelStation.objects.all().delete()
        stations.invalidate_station_index()
        self.assertEqual(len(stations.get_station_index()), 0)
        FuelStation.objects.create(opis_id=1, name="New", city="X", state="TX", retail_price="3.0",
                                   min_price="3.0", max_price="3.0", latitude=30.0, longitude=-97.0)
        with patch.object(stations, "FINGERPRINT_TTL", 0.0):
            self.assertEqual(len(stations.get_station_index()), 1)
        stations.invalidate_station_index()


class RateLimiterTests(TestCase):
    """Deterministic: a fake clock instead of real sleeping (Windows timers are ~16 ms coarse)."""

    def test_calls_to_one_host_are_spaced_and_other_hosts_are_not_delayed(self):
        clock = {"now": 100.0}
        slept = []

        def fake_sleep(seconds):
            slept.append(round(seconds, 3))
            clock["now"] += seconds

        lim = http.HostRateLimiter()
        with patch.object(http.time, "monotonic", side_effect=lambda: clock["now"]),                 patch.object(http.time, "sleep", side_effect=fake_sleep):
            for host in ("a.example", "b.example", "a.example"):
                with lim.lock_for(host):
                    lim.wait(host, 1.0)
                    lim.mark(host)
                clock["now"] += 0.25  # time spent on the request itself
        # First calls to a and b: no wait. Second call to a: 1.0 - 0.5 elapsed = 0.5 s wait.
        self.assertEqual(slept, [0.5])


class NotFoundTests(TestCase):
    def test_unknown_api_url_returns_json_404(self):
        resp = self.client.get("/api/does-not-exist/")
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"], "Not found")
