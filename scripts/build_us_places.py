"""
Build data/us_places.csv from the US Census Gazetteer places file (public domain).

  https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2024_Gazetteer/2024_Gaz_place_national.zip

The app uses this bundled file to resolve "City, ST" inputs and to power the
type-ahead suggestions without calling any external geocoder.

Optional population (used to rank suggestions), also public domain:
  https://www2.census.gov/programs-surveys/popest/datasets/2020-2023/cities/totals/sub-est2023.csv

Usage:  python scripts/build_us_places.py <2024_Gaz_place_national.txt> [out_csv] [--population sub-est2023.csv]
"""
import csv
import re
import sys

US_STATES = set(
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split()
)
SUFFIX = re.compile(
    r"\s+(city and borough|consolidated government( \(balance\))?|metropolitan government( \(balance\))?|"
    r"unified government( \(balance\))?|urban county|city|town|township|village|borough|CDP|municipality|"
    r"plantation|corporation)(\s*\(.*\))?$",
    re.IGNORECASE,
)


def clean(name):
    name = SUFFIX.sub("", name.strip())
    return re.sub(r"\s*\(balance\)$", "", name).strip()


def load_population(path):
    pop = {}
    if not path:
        return pop
    with open(path, encoding="latin-1", newline="") as f:
        for r in csv.DictReader(f):
            if r["PLACE"] != "00000" and r["COUNTY"] == "000":
                pop[r["STATE"] + r["PLACE"]] = int(r["POPESTIMATE2023"] or 0)
    return pop


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    pop_path = None
    if "--population" in args:
        i = args.index("--population")
        pop_path = args[i + 1]
        del args[i:i + 2]
    src = args[0]
    out = args[1] if len(args) > 1 else "data/us_places.csv"
    population = load_population(pop_path)
    rows = {}
    with open(src, encoding="latin-1") as f:
        reader = csv.reader(f, delimiter="\t")
        header = [h.strip() for h in next(reader)]
        col = {h: i for i, h in enumerate(header)}
        for r in reader:
            r = [c.strip() for c in r]
            state = r[col["USPS"]]
            if state not in US_STATES:
                continue
            name = clean(r[col["NAME"]])
            incorporated = 0 if r[col["FUNCSTAT"]] == "S" else 1  # S = statistical area (CDP)
            pop = population.get(r[col["GEOID"]], 0)
            area = float(r[col["ALAND_SQMI"]] or 0)
            key = (name.lower(), state)
            cand = (incorporated, area, name, float(r[col["INTPTLAT"]]), float(r[col["INTPTLONG"]]), pop)
            # Same name twice in a state (e.g. a city and a CDP): keep the incorporated / larger one.
            if key not in rows or (cand[0], cand[5], cand[1]) > (rows[key][0], rows[key][5], rows[key][1]):
                rows[key] = cand
    # Aliases for names people actually type: consolidated cities ("Nashville-Davidson",
    # "Lexington-Fayette", "Louisville/Jefferson County ...") and a few Census quirks.
    manual = {("boise city", "ID"): "Boise", ("urban honolulu", "HI"): "Honolulu"}
    # Honolulu is a Census "CDP", so it has no city population estimate.
    if ("urban honolulu", "HI") in rows:
        u = rows[("urban honolulu", "HI")]
        rows[("urban honolulu", "HI")] = (1, u[1], u[2], u[3], u[4], max(u[5], 345000))
    for (key, state), cand in list(rows.items()):
        alias = manual.get((key, state))
        if alias is None and re.search(r"[-/]", cand[2]) and cand[1] >= 50:
            alias = re.split(r"[-/]", cand[2])[0].strip()
        if not alias:
            continue
        akey = (alias.lower(), state)
        existing = rows.get(akey)
        if existing is None or existing[1] * 4 < cand[1]:
            rows[akey] = (cand[0], cand[1], alias, cand[3], cand[4], cand[5])
            if akey != (key, state):
                del rows[(key, state)]  # keep only the name people use
    with open(out, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["name", "state", "lat", "lon", "incorporated", "land_sqmi", "population"])
        for (_, state), (inc, area, name, lat, lon, pop) in sorted(rows.items(), key=lambda kv: (kv[0][1], kv[0][0])):
            w.writerow([name, state, f"{lat:.5f}", f"{lon:.5f}", inc, f"{area:.2f}", pop])
    print(f"wrote {len(rows)} places to {out}")


if __name__ == "__main__":
    main()
