"""
Fill data/city_coords.json offline from the US Census Gazetteer places file.

Download (free, public domain):
  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip

Usage:
  python scripts/fill_from_gazetteer.py <2024_Gaz_place_national.txt> [csv] [out_json] [--check]

Pairs not yet in out_json are filled. Existing (Nominatim) entries are kept
unless an unambiguous gazetteer match is more than 10 miles away: spot checks
showed Nominatim was the wrong one in those cases (e.g. Bethel PA resolved to
Bethel Park near Pittsburgh instead of the Bethel on I-78). --check compares gazetteer matches against
existing Nominatim entries and prints the distance error instead of writing.
Anything left unmatched can then be geocoded with scripts/geocode_cities.py.
"""
import csv
import json
import math
import re
import sys
from collections import defaultdict

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
SUFFIXES = re.compile(
    r"\s+(city and borough|consolidated government|metropolitan government|unified government|"
    r"urban county|city|town|township|village|borough|cdp|municipality|plantation|corporation|"
    r"comunidad|zona urbana)(\s*\(.*\))?$"
)
WORD_SUBS = [(r"\bsaint\b", "st"), (r"\bsainte\b", "ste"), (r"\bmount\b", "mt"), (r"\bfort\b", "ft"),
             (r"\bmc\s+", "mc"), (r"\bpoint\b", "pt")]


def norm(name, strip_suffix=True):
    s = name.lower().strip()
    s = re.sub(r"\(.*?\)", "", s)
    if strip_suffix:  # Census names carry a type suffix ("Elk City city"); CSV names don't.
        s = SUFFIXES.sub("", s)
    s = s.replace("-", " ").replace("'", "").replace(".", "")
    for pat, rep in WORD_SUBS:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


def haversine(a, b):
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.76 * math.asin(math.sqrt(h))


def load_gazetteer(path):
    index = defaultdict(list)
    with open(path, encoding="latin-1") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        col = {h: i for i, h in enumerate(header)}
        for row in reader:
            row = [c.strip() for c in row]
            if len(row) < len(header):
                continue
            state = row[col["USPS"]]
            # Prefer incorporated places (FUNCSTAT A) and larger land area on ties.
            score = (row[col["FUNCSTAT"]] == "A", float(row[col["ALAND"]] or 0))
            entry = (score, float(row[col["INTPTLAT"]]), float(row[col["INTPTLONG"]]), row[col["NAME"]])
            index[(norm(row[col["NAME"]]), state)].append(entry)
    # Names that occur more than once in a state are ambiguous: leave them to Nominatim.
    return {k: max(v) for k, v in index.items() if len(v) == 1}


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check = "--check" in sys.argv
    gaz_path = args[0]
    csv_path = args[1] if len(args) > 1 else "data/fuel-prices-for-be-assessment.csv"
    out_path = args[2] if len(args) > 2 else "data/city_coords.json"

    gaz = load_gazetteer(gaz_path)
    try:
        with open(out_path, encoding="utf-8") as f:
            cache = json.load(f)
    except FileNotFoundError:
        cache = {}

    pairs = set()
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            st = row["State"].strip()
            if st in US_STATES:
                pairs.add((row["City"].strip(), st))

    if check:
        errs = []
        for city, st in pairs:
            existing = cache.get(f"{city}|{st}")
            hit = gaz.get((norm(city, strip_suffix=False), st))
            if existing and hit:
                errs.append((haversine((existing["lat"], existing["lon"]), (hit[1], hit[2])), city, st))
        errs.sort()
        n = len(errs)
        print(f"compared {n}; median {errs[n // 2][0]:.2f} mi; p95 {errs[int(n * .95)][0]:.2f} mi; "
              f"over 10 mi: {sum(e[0] > 10 for e in errs)}")
        print("worst:", [(round(e[0]), e[1], e[2]) for e in errs[-8:]])
        return

    filled, corrected, unmatched = 0, [], []
    for city, st in sorted(pairs):
        key = f"{city}|{st}"
        hit = gaz.get((norm(city, strip_suffix=False), st))
        existing = cache.get(key)
        if existing:
            if hit and haversine((existing["lat"], existing["lon"]), (hit[1], hit[2])) > 10:
                cache[key] = {"lat": hit[1], "lon": hit[2], "name": f"{hit[3]}, {st} (census gazetteer)"}
                corrected.append(key)
            continue
        if hit:
            cache[key] = {"lat": hit[1], "lon": hit[2], "name": f"{hit[3]}, {st} (census gazetteer)"}
            filled += 1
        else:
            cache.pop(key, None)  # leave for the Nominatim script
            unmatched.append(key)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=0, sort_keys=True)
    print(f"corrected {len(corrected)} Nominatim entries: {corrected}")
    print(f"filled {filled} from gazetteer; {len(unmatched)} unmatched (run scripts/geocode_cities.py next)")
    print("sample unmatched:", unmatched[:25])


if __name__ == "__main__":
    main()
