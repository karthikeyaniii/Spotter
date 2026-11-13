"""Concurrency behaviour: single-flight routing and the cross-instance rate limiter."""
import threading
import time
from unittest.mock import patch

import numpy as np
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from fuel.services import http, routing
from fuel.services.routing import NoRouteError, Route


def _route():
    coords = np.array([[-97.0, 32.0], [-104.0, 39.0]])
    return Route(coords, np.array([0.0, 800.0]), 800.0, 12.0)


class SingleFlightTests(SimpleTestCase):
    def setUp(self):
        cache.clear()

    def _hammer(self, n, fetch):
        results, errors = [], []
        barrier = threading.Barrier(n)

        def worker():
            barrier.wait()  # all threads start at the same moment
            try:
                results.append(routing.get_route(32.0, -97.0, 39.0, -104.0))
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with patch.object(routing, "_fetch_route", side_effect=fetch) as fetch_mock:
            threads = [threading.Thread(target=worker) for _ in range(n)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
        return results, errors, fetch_mock

    def test_concurrent_identical_requests_make_one_upstream_call(self):
        def slow_fetch(*args):
            time.sleep(0.3)
            return _route()

        results, errors, fetch = self._hammer(20, slow_fetch)
        self.assertEqual(errors, [])
        self.assertEqual(len(results), 20)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(sum(1 for r in results if not r.cached), 1)  # one leader, 19 served from cache

    def test_followers_recover_when_the_leader_fails(self):
        calls = {"n": 0}

        def flaky(*args):
            calls["n"] += 1
            time.sleep(0.1)
            if calls["n"] == 1:
                raise routing.RoutingError("upstream blip")
            return _route()

        results, errors, _ = self._hammer(5, flaky)
        self.assertEqual(len(errors), 1)  # only the leader saw the failure
        self.assertEqual(len(results), 4)

    def test_no_route_answer_is_cached(self):
        with patch.object(routing, "_fetch_route", side_effect=NoRouteError("no road")) as fetch:
            for _ in range(3):
                with self.assertRaises(NoRouteError):
                    routing.get_route(21.3, -157.8, 41.9, -87.6)
        self.assertEqual(fetch.call_count, 1)


class FakeRedis:
    """Just enough of redis.Redis for the slot limiter, driven by a fake clock."""

    def __init__(self, clock):
        self.clock = clock
        self.expiry = {}

    def set(self, key, value, nx=False, px=None):
        now = self.clock["now"]
        if nx and self.expiry.get(key, 0) > now:
            return None
        self.expiry[key] = now + px / 1000.0
        return True

    def pttl(self, key):
        return max(0, int((self.expiry.get(key, 0) - self.clock["now"]) * 1000))


class GlobalRateLimiterTests(SimpleTestCase):
    def test_slots_are_spaced_across_processes(self):
        clock = {"now": 0.0}
        redis = FakeRedis(clock)  # shared by "processes" A and B
        starts = []

        def fake_sleep(s):
            clock["now"] += s

        with patch.object(http.time, "monotonic", side_effect=lambda: clock["now"]), \
                patch.object(http.time, "sleep", side_effect=fake_sleep):
            for _process in ("A", "B", "A", "B"):
                http.acquire_global_slot(redis, "router.project-osrm.org", 1.0)
                starts.append(round(clock["now"], 3))
        self.assertEqual(starts, [0.0, 1.0, 2.0, 3.0])

    def test_gives_up_after_max_wait(self):
        clock = {"now": 0.0}
        redis = FakeRedis(clock)
        with patch.object(http.time, "monotonic", side_effect=lambda: clock["now"]), \
                patch.object(http.time, "sleep", side_effect=lambda s: clock.__setitem__("now", clock["now"] + s)):
            http.acquire_global_slot(redis, "h", 100.0)
            with self.assertRaises(http.SlotTimeout):
                http.acquire_global_slot(redis, "h", 100.0, max_wait=5)


class RedisDownFallbackTests(TestCase):
    @override_settings(REDIS_URL="redis://127.0.0.1:1/0")
    def test_unreachable_redis_degrades_to_local_limiter(self):
        from fuel.tests import ORIGINAL_POLITE_GET

        class BrokenRedis:
            def set(self, *a, **k):
                raise ConnectionError("redis down")

        class FakeSession:
            def get(self, url, params=None, timeout=None):
                return "ok"

        with patch.object(http, "_redis", return_value=BrokenRedis()),                 patch.object(http, "session", return_value=FakeSession()),                 self.assertLogs("fuel.services.http", level="WARNING"):
            self.assertEqual(ORIGINAL_POLITE_GET("https://router.project-osrm.org/x", timeout=1), "ok")
