"""
Import the OPIS fuel price CSV into the FuelStation table.

* Only US stations are kept (the file also has Canadian rows).
* Several rows can share one "OPIS Truckstop ID" (price samples / name
  variants). They are merged into one station using the average price.
* Coordinates come from data/city_coords.json (built once by the scripts in
  scripts/). Stations without coordinates are stored but ignored by the planner.

Used by the `load_fuel_prices` command and by the data migration that runs on
`python manage.py migrate`, so a fresh checkout is ready after migrating.
"""
import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from .geo import US_STATES


@dataclass
class ImportResult:
    stations: int
    with_coordinates: int
    skipped_non_us_rows: int

    def __str__(self):
        return (f"Loaded {self.stations} US stations ({self.with_coordinates} with coordinates, "
                f"{self.stations - self.with_coordinates} without); skipped {self.skipped_non_us_rows} non-US rows.")


def import_fuel_prices(station_model, csv_path, coords_path):
    """Replace all rows of `station_model` with the stations in `csv_path`."""
    csv_path, coords_path = Path(csv_path), Path(coords_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Fuel price CSV not found: {csv_path}")
    coords = {}
    if coords_path.exists():
        with open(coords_path, encoding="utf-8") as f:
            coords = json.load(f)

    grouped = defaultdict(lambda: {"prices": [], "row": None})
    skipped_non_us = 0
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            state = row["State"].strip().upper()
            if state not in US_STATES:
                skipped_non_us += 1
                continue
            try:
                opis_id = int(row["OPIS Truckstop ID"])
                price = Decimal(row["Retail Price"].strip())
            except (ValueError, ArithmeticError):
                continue
            g = grouped[opis_id]
            g["prices"].append(price)
            if g["row"] is None:
                g["row"] = {
                    "name": row["Truckstop Name"].strip()[:200],
                    "address": row["Address"].strip()[:200],
                    "city": row["City"].strip()[:100],
                    "state": state,
                    "rack_id": int(row["Rack ID"]) if row["Rack ID"].strip().isdigit() else None,
                }

    objs, located = [], 0
    for opis_id, g in grouped.items():
        r, prices = g["row"], g["prices"]
        c = coords.get(f"{r['city']}|{r['state']}")
        located += bool(c)
        objs.append(
            station_model(
                opis_id=opis_id,
                name=r["name"],
                address=r["address"],
                city=r["city"],
                state=r["state"],
                rack_id=r["rack_id"],
                retail_price=(sum(prices) / len(prices)).quantize(Decimal("0.0001")),
                min_price=min(prices),
                max_price=max(prices),
                price_samples=len(prices),
                latitude=c["lat"] if c else None,
                longitude=c["lon"] if c else None,
                geocode_source="city-geocode" if c else "",
            )
        )
    station_model.objects.all().delete()
    station_model.objects.bulk_create(objs, batch_size=1000)
    return ImportResult(len(objs), located, skipped_non_us)
