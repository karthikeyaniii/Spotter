"""
One-off helper: geocode every unique (city, state) pair in the fuel price CSV
using Nominatim (OpenStreetMap) and cache the result in data/city_coords.json.

The live API never calls this; it only reads the cached JSON. Respects the
Nominatim usage policy (max 1 request/second, descriptive User-Agent).

Usage:  python scripts/geocode_cities.py [csv_path] [out_json]
Safe to re-run: already-geocoded pairs are skipped.
"""
import csv
import json
import os
import sys
import time

import requests

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
NOMINATIM = "https://nominatim.openstreetmap.org/search"
HEADERS = {"User-Agent": "spotter-fuel-route-assessment/1.0 (one-off station geocoding)"}


def lookup(city, state):
    params = [
        {"city": city, "state": state, "country": "United States", "format": "jsonv2", "limit": 1},
        {"q": f"{city}, {state}, USA", "countrycodes": "us", "format": "jsonv2", "limit": 1},
    ]
    for p in params:
        try:
            r = requests.get(NOMINATIM, params=p, headers=HEADERS, timeout=20)
            if r.status_code == 200 and r.json():
                hit = r.json()[0]
                return {"lat": float(hit["lat"]), "lon": float(hit["lon"]), "name": hit.get("display_name", "")}
        except requests.RequestException:
            pass
        time.sleep(1.1)
    return None


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    csv_path = argv[0] if len(argv) > 0 else "data/fuel-prices-for-be-assessment.csv"
    out_path = argv[1] if len(argv) > 1 else "data/city_coords.json"
    cache = {}
    if os.path.exists(out_path):
        with open(out_path, encoding="utf-8") as f:
            cache = json.load(f)
    pairs = set()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            state = row["State"].strip()
            if state in US_STATES:
                pairs.add((row["City"].strip(), state))
    todo = sorted(p for p in pairs if f"{p[0]}|{p[1]}" not in cache)
    print(f"{len(pairs)} pairs, {len(todo)} to geocode", flush=True)
    for i, (city, state) in enumerate(todo, 1):
        res = lookup(city, state)
        cache[f"{city}|{state}"] = res  # None recorded too, so failures are visible
        if i % 25 == 0 or i == len(todo):
            with open(out_path, "w", encoding="utf-8") as f:
                json.dump(cache, f, indent=0, sort_keys=True)
            ok = sum(1 for v in cache.values() if v)
            print(f"{i}/{len(todo)} done, {ok} resolved", flush=True)
        time.sleep(1.1)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=0, sort_keys=True)
    print("finished", flush=True)


if __name__ == "__main__":
    main()
