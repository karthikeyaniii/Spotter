"""
(Re)load the OPIS fuel price CSV into the database.

`python manage.py migrate` already does this once, so you only need this
command after the price file changes:

    python manage.py load_fuel_prices
    python manage.py load_fuel_prices --csv other.csv --coords other.json
"""
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from fuel.models import FuelStation
from fuel.services.importer import import_fuel_prices
from fuel.services.stations import invalidate_station_index


class Command(BaseCommand):
    help = "Import fuel prices CSV (+ cached city coordinates) into the database."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(settings.FUEL_ROUTE["PRICES_CSV"]))
        parser.add_argument("--coords", default=str(settings.FUEL_ROUTE["CITY_COORDS_JSON"]))

    def handle(self, *args, **opts):
        try:
            with transaction.atomic():
                result = import_fuel_prices(FuelStation, opts["csv"], opts["coords"])
        except FileNotFoundError as exc:
            raise CommandError(str(exc)) from exc
        invalidate_station_index()
        self.stdout.write(self.style.SUCCESS(str(result)))
