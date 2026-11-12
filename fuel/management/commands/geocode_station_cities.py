"""
Rebuild data/city_coords.json (station city -> lat/lon). Only needed if the
price list changes; the committed JSON already covers the supplied file.

    python manage.py geocode_station_cities --gazetteer 2024_Gaz_place_national.txt

1. Optional offline pass from the US Census Gazetteer (fast, unambiguous names).
2. Nominatim pass for everything left (1 request/second, resumable).
Then run `python manage.py load_fuel_prices`.
"""
import runpy

from django.conf import settings
from django.core.management.base import BaseCommand

SCRIPTS = settings.BASE_DIR / "scripts"


def _script_main(name):
    # The scripts are standalone files (not a package); load one and return its main().
    return runpy.run_path(str(SCRIPTS / name), run_name="fuel_script")["main"]


class Command(BaseCommand):
    help = "Geocode the unique city/state pairs of the fuel price list into data/city_coords.json."

    def add_arguments(self, parser):
        parser.add_argument("--gazetteer", help="Path to the Census 'Gaz_place_national.txt' file (optional).")

    def handle(self, *args, **opts):
        csv_path = str(settings.FUEL_ROUTE["PRICES_CSV"])
        out_path = str(settings.FUEL_ROUTE["CITY_COORDS_JSON"])
        if opts.get("gazetteer"):
            _script_main("fill_from_gazetteer.py")([opts["gazetteer"], csv_path, out_path])
        _script_main("geocode_cities.py")([csv_path, out_path])
        self.stdout.write(self.style.SUCCESS("Done. Now run: python manage.py load_fuel_prices"))
