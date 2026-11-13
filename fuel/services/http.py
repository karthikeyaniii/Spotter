"""
Polite HTTP access to the free public map services.

* One pooled ``requests.Session`` per process (connection reuse, shared User-Agent).
* A per-host minimum interval between requests. The public Nominatim and OSRM
  servers allow at most 1 request per second (their usage policies); calls made
  from several request threads are serialised here instead of breaking that rule.

With REDIS_URL set the limit is enforced across every process on every host
(an atomic Redis ``SET NX PX`` per call slot). Without Redis, or if Redis is
unreachable, it falls back to a per-process limiter.
"""
import logging
import threading
import time
from urllib.parse import urlsplit

import requests
from django.conf import settings

log = logging.getLogger(__name__)

_session = None
_session_lock = threading.Lock()
_redis_client = None
_redis_lock = threading.Lock()
# How long a caller may wait for a call slot before giving up (seconds).
MAX_SLOT_WAIT = 30.0


class SlotTimeout(requests.RequestException):
    """No call slot for the rate-limited host became free in time (treated like a network error)."""


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


def _redis():
    """Shared Redis client, or None when Redis is not configured."""
    global _redis_client
    url = getattr(settings, "REDIS_URL", "")
    if not url:
        return None
    with _redis_lock:
        if _redis_client is None:
            import redis

            _redis_client = redis.Redis.from_url(url, socket_timeout=2, socket_connect_timeout=2)
        return _redis_client


def acquire_global_slot(client, host, interval, *, max_wait=MAX_SLOT_WAIT):
    """
    Block until this process owns the next call slot for `host` across the whole
    deployment. A slot is a Redis key that lives for `interval`; only one caller can
    create it (SET NX), so call starts are at least `interval` apart everywhere.
    """
    key = f"fuel:ratelimit:{host}"
    ms = max(1, int(interval * 1000))
    deadline = time.monotonic() + max_wait
    while True:
        if client.set(key, "1", nx=True, px=ms):
            return
        if time.monotonic() >= deadline:
            raise SlotTimeout(f"No call slot for {host} within {max_wait:.0f}s (service busy).")
        ttl_ms = client.pttl(key)
        time.sleep(min(max(ttl_ms, 5), 250) / 1000.0)


def polite_get(url, *, params=None, timeout):
    """GET `url`, waiting first if the host's minimum interval has not passed yet."""
    host = urlsplit(url).hostname or ""
    interval = settings.FUEL_ROUTE["MIN_SECONDS_BETWEEN_CALLS"].get(host, 0.0)
    if interval <= 0:
        return session().get(url, params=params, timeout=timeout)
    client = _redis()
    if client is not None:
        try:
            acquire_global_slot(client, host, interval)
        except SlotTimeout:
            raise
        except Exception as exc:  # Redis unreachable: degrade to the per-process limiter
            log.warning("Redis rate limiter unavailable (%s); using the per-process limiter", exc)
        else:
            return session().get(url, params=params, timeout=timeout)
    with limiter.lock_for(host):  # one in-flight request per rate-limited host
        limiter.wait(host, interval)
        try:
            return session().get(url, params=params, timeout=timeout)
        finally:
            limiter.mark(host)
