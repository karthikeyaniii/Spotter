"""
Offline US place lookup (bundled US Census Gazetteer, data/us_places.csv).

* ``resolve(query)`` turns "Chicago, IL", "chicago illinois", "Austin TX",
  "St. Louis, MO 63101" or an unambiguous bare city name ("Los Angeles") into
  coordinates with no network call. Anything it can't resolve with confidence
  (street addresses, ambiguous names like "Springfield") returns None and the
  caller falls back to an online geocoder.
* ``suggest(prefix)`` powers the type-ahead in the UI. It matches the start of
  any word ("louis" finds "St. Louis") via binary search over a sorted word
  index and ranks by population, so a call takes about a millisecond.
"""
import csv
import heapq
import re
import threading
from bisect import bisect_left
from dataclasses import dataclass

from django.conf import settings

STATE_NAMES = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA",
    "colorado": "CO", "connecticut": "CT", "delaware": "DE", "florida": "FL", "georgia": "GA",
    "hawaii": "HI", "idaho": "ID", "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD", "massachusetts": "MA",
    "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
    "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM",
    "new york": "NY", "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI", "south carolina": "SC",
    "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT", "vermont": "VT",
    "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC",
}
STATE_CODES = set(STATE_NAMES.values())
# Longest names first so "west virginia" wins over "virginia".
_STATE_NAME_RE = re.compile(
    r"(?:,|\s)\s*(" + "|".join(sorted(map(re.escape, STATE_NAMES), key=len, reverse=True)) + r")$"
)
_COUNTRY_RE = re.compile(r"[,\s]+(usa|u\.s\.a\.?|us|united states( of america)?)$", re.IGNORECASE)
_ZIP_RE = re.compile(r"\s+\d{5}(-\d{4})?$")
_WORD_SUBS = [(r"\bsaint\b", "st"), (r"\bsainte\b", "ste"), (r"\bmount\b", "mt"), (r"\bfort\b", "ft")]
# A bare city name is accepted if its biggest match is this many times bigger than the next one.
_DOMINANCE = 4.0


def norm(text):
    s = text.lower().replace(".", "").replace("'", "").replace("-", " ")
    for pat, rep in _WORD_SUBS:
        s = re.sub(pat, rep, s)
    return re.sub(r"\s+", " ", s).strip()


@dataclass(frozen=True)
class USPlace:
    name: str
    state: str
    lat: float
    lon: float
    area: float
    population: int
    key: str  # normalised name

    @property
    def size(self):
        return (self.population, self.area)

    @property
    def label(self):
        return f"{self.name}, {self.state}"

    def as_dict(self):
        return {"label": self.label, "name": self.name, "state": self.state, "lat": self.lat, "lon": self.lon}


class PlaceIndex:
    def __init__(self, places):
        self.places = sorted(places, key=lambda p: p.size, reverse=True)  # biggest first
        self.by_key = {}
        words = []
        for rank, p in enumerate(self.places):
            self.by_key.setdefault(p.key, []).append(p)  # each list stays sorted by size
            parts = p.key.split(" ")
            for i in range(len(parts)):  # "st louis park" -> "st louis park", "louis park", "park"
                words.append((" ".join(parts[i:]), rank))
        words.sort()
        self.word_keys = [w for w, _ in words]
        self.word_ranks = [r for _, r in words]

    @classmethod
    def from_csv(cls, path):
        places = []
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                places.append(USPlace(r["name"], r["state"], float(r["lat"]), float(r["lon"]),
                                      float(r["land_sqmi"]), int(r.get("population") or 0), norm(r["name"])))
        return cls(places)

    # ------------------------------------------------------------- parsing
    @staticmethod
    def split_state(query):
        """'Austin, TX' -> ('austin', 'TX'); 'chicago illinois' -> ('chicago', 'IL'); 'Reno' -> ('reno', None)."""
        q = _COUNTRY_RE.sub("", query.strip()).strip(" ,")
        q = _ZIP_RE.sub("", q).strip(" ,")
        low = q.lower()
        m = _STATE_NAME_RE.search(low)
        if m and m.start() > 0:
            return norm(q[: m.start()].strip(" ,")), STATE_NAMES[m.group(1)]
        m = re.search(r"(?:,|\s)\s*([A-Za-z]{2})$", q)
        if m and m.group(1).upper() in STATE_CODES and m.start() > 0:
            return norm(q[: m.start()].strip(" ,")), m.group(1).upper()
        return norm(q.strip(" ,")), None

    def resolve(self, query):
        name, state = self.split_state(query)
        if not name or any(ch.isdigit() for ch in name):  # street addresses go to the online geocoder
            return None
        matches = self.by_key.get(name, [])
        if state:
            matches = [p for p in matches if p.state == state]
            return matches[0] if matches else None
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            first, second = matches[0], matches[1]
            if first.population and first.population >= _DOMINANCE * max(second.population, 1):
                return first
            if not first.population and first.area >= _DOMINANCE * max(second.area, 0.01):
                return first
        return None  # ambiguous, e.g. "Springfield"

    def suggest(self, query, limit=8):
        name, state = self.split_state(query)
        if len(name) < 2:
            return []
        lo = bisect_left(self.word_keys, name)
        ranks = set()
        for i in range(lo, len(self.word_keys)):
            if not self.word_keys[i].startswith(name):
                break
            ranks.add(self.word_ranks[i])
        candidates = (self.places[r] for r in ranks)
        if state:
            candidates = (p for p in candidates if p.state == state)
        # Biggest places first; an exact name match counts as 10x bigger.
        def order(p):
            boost = 10 if p.key == name else 1
            return (-max(p.population, 1) * boost, -p.area)
        return heapq.nsmallest(limit, candidates, key=order)

_lock = threading.Lock()
_index = None


def get_place_index():
    global _index
    with _lock:
        if _index is None:
            _index = PlaceIndex.from_csv(settings.FUEL_ROUTE["US_PLACES_CSV"])
        return _index
