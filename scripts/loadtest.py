"""
Concurrent load test for a running deployment (stdlib + requests only).

    python scripts/loadtest.py --base http://localhost:8080 --clients 64 --seconds 30

Traffic mix per client loop: 70% route plans over a fixed set of US city pairs
(the realistic hot path: cached routes, full fuel optimisation every time),
20% city suggestions, 10% readiness checks. Prints throughput, latency
percentiles and every non-2xx status.

Note: the API is rate limited per client IP (nginx + Django). A load test from
one machine needs those limits raised for the run, e.g.
    THROTTLE_ROUTE=1000000/minute THROTTLE_PLACES=1000000/minute NGINX_API_RATE=100000r/s docker compose up -d
"""
import argparse
import random
import statistics
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import requests

PAIRS = [
    ("Chicago, IL", "Los Angeles, CA"), ("New York, NY", "Miami, FL"), ("Seattle, WA", "Dallas, TX"),
    ("Denver, CO", "Atlanta, GA"), ("Boston, MA", "Houston, TX"), ("Phoenix, AZ", "Orlando, FL"),
    ("San Francisco, CA", "Chicago, IL"), ("Dallas, TX", "Denver, CO"),
]
PREFIXES = ["chi", "los", "new y", "sea", "dal", "den", "atl", "bos", "hou", "pho", "san", "mia"]


def one_request(session, base):
    r = random.random()
    if r < 0.7:
        start, finish = random.choice(PAIRS)
        return "route", session.get(f"{base}/api/route/", params={"start": start, "finish": finish}, timeout=90)
    if r < 0.9:
        return "places", session.get(f"{base}/api/places/", params={"q": random.choice(PREFIXES)}, timeout=30)
    return "health", session.get(f"{base}/api/health/", timeout=30)


def client_loop(base, deadline, results, lock):
    s = requests.Session()
    while time.monotonic() < deadline:
        t0 = time.perf_counter()
        try:
            kind, resp = one_request(s, base)
            code = resp.status_code
        except requests.RequestException as exc:
            kind, code = "error", exc.__class__.__name__
        ms = (time.perf_counter() - t0) * 1000
        with lock:
            results.append((kind, code, ms))


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))] if values else 0.0


def warm_up(base):
    """Plan each pair once so the test measures the steady state (cold routes call OSRM)."""
    for start, finish in PAIRS:
        r = requests.get(f"{base}/api/route/", params={"start": start, "finish": finish, "include_geometry": "false"},
                         timeout=120)
        print(f"  warm {start} -> {finish}: {r.status_code}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8080")
    ap.add_argument("--clients", type=int, default=64)
    ap.add_argument("--seconds", type=int, default=30)
    ap.add_argument("--no-warmup", action="store_true")
    args = ap.parse_args()

    if not args.no_warmup:
        print("Warming up route cache ...")
        warm_up(args.base)

    print(f"Load: {args.clients} concurrent clients for {args.seconds}s against {args.base}")
    results, lock = [], threading.Lock()
    deadline = time.monotonic() + args.seconds
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=args.clients) as pool:
        for _ in range(args.clients):
            pool.submit(client_loop, args.base, deadline, results, lock)
    elapsed = time.monotonic() - t0

    codes = Counter(str(code) for _, code, _ in results)
    ok = [ms for _, code, ms in results if isinstance(code, int) and code < 300]
    print(f"\nRequests: {len(results)} in {elapsed:.1f}s  ->  {len(results) / elapsed:.0f} req/s")
    print(f"Status codes: {dict(codes)}")
    print(f"Success rate: {100 * len(ok) / max(len(results), 1):.2f}%")
    for kind in ("route", "places", "health"):
        lat = [ms for k, code, ms in results if k == kind and isinstance(code, int) and code < 300]
        if lat:
            print(f"  {kind:7} n={len(lat):6}  p50={pct(lat, 50):7.1f} ms  p95={pct(lat, 95):7.1f} ms  "
                  f"p99={pct(lat, 99):7.1f} ms  mean={statistics.mean(lat):7.1f} ms")


if __name__ == "__main__":
    main()
