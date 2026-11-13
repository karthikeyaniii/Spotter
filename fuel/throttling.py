"""
Race-free API throttling for many processes and hosts.

DRF's default throttles keep a list of request timestamps per client and do a
read-modify-write on it. Concurrent requests (several threads or replicas) can
overwrite each other's updates, so the limit is under-enforced exactly when it
matters, and each check costs O(limit). This fixed-window counter uses one
atomic increment per request (Redis INCR when Redis is the cache): O(1) and
race-free across every replica.

Trade-off: a fixed window can admit up to 2x the rate across a window boundary,
which is acceptable for abuse protection.
"""
import time

from rest_framework.throttling import ScopedRateThrottle


class FixedWindowScopedThrottle(ScopedRateThrottle):
    def allow_request(self, request, view):
        self.scope = getattr(view, self.scope_attr, None)
        if not self.scope:
            return True
        self.rate = self.get_rate()
        if self.rate is None:
            return True
        self.num_requests, self.duration = self.parse_rate(self.rate)

        now = time.time()
        window = int(now // self.duration)
        key = f"throttle:{self.scope}:{self.get_ident(request)}:{window}"
        self.cache.add(key, 0, timeout=self.duration + 1)
        try:
            count = self.cache.incr(key)
        except ValueError:  # key expired between add() and incr()
            self.cache.set(key, 1, timeout=self.duration + 1)
            count = 1
        if count > self.num_requests:
            self._wait = (window + 1) * self.duration - now
            return False
        return True

    def wait(self):
        return getattr(self, "_wait", None)
