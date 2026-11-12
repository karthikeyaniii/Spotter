"""
Polite HTTP access to the free public map services.

* One pooled ``requests.Session`` per process (connection reuse, shared User-Agent).
* A per-host minimum interval between requests. The public Nominatim and OSRM
  servers allow at most 1 request per second (their usage policies); calls made
  from several request threads are serialised here instead of breaking that rule.

The limiter is per process. With several worker processes, point the app at a
self-hosted OSRM / Nominatim (``OSRM_BASE_URL`` / ``NOMINATIM_BASE_URL``) or keep
the API throttle low enough; the app already avoids most calls (offline city
lookup, caches).
"""
import threading
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings

_session = None
_session_lock = threading.Lock()


class HostRateLimiter:
    """Thread-safe: allows at most one call per `interval` seconds to each host."""

    def __init__(self):
        self._locks = {}
        self._last = {}
        self._registry = threading.Lock()

    def lock_for(self, host):
        with self._registry:
            return self._locks.setdefault(host, threading.Lock())

    def wait(self, host, interval):
        """Sleep until `host` may be called again. Call while holding lock_for(host)."""
        delay = self._last.get(host, 0.0) + interval - time.monotonic()
        if delay > 0:
            time.sleep(delay)

    def mark(self, host):
        self._last[host] = time.monotonic()


limiter = HostRateLimiter()


def session():
    global _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            s.headers["User-Agent"] = settings.FUEL_ROUTE["USER_AGENT"]
            adapter = requests.adapters.HTTPAdapter(pool_connections=8, pool_maxsize=16)
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            _session = s
        return _session


def polite_get(url, *, params=None, timeout):
    """GET `url`, waiting first if the host's minimum interval has not passed yet."""
    host = urlsplit(url).hostname or ""
    interval = settings.FUEL_ROUTE["MIN_SECONDS_BETWEEN_CALLS"].get(host, 0.0)
    if interval <= 0:
        return session().get(url, params=params, timeout=timeout)
    with limiter.lock_for(host):  # one in-flight request per rate-limited host
        limiter.wait(host, interval)
        try:
            return session().get(url, params=params, timeout=timeout)
        finally:
            limiter.mark(host)
