"""Load the bundled fuel price list on `migrate`, so a fresh checkout works immediately."""
from django.conf import settings
from django.db import migrations

from fuel.services.importer import import_fuel_prices


def load(apps, schema_editor):
    FuelStation = apps.get_model("fuel", "FuelStation")
    cfg = settings.FUEL_ROUTE
    if not cfg["PRICES_CSV"].exists():  # nothing to load (e.g. a slimmed-down deployment)
        return
    import_fuel_prices(FuelStation, cfg["PRICES_CSV"], cfg["CITY_COORDS_JSON"])


def unload(apps, schema_editor):
    apps.get_model("fuel", "FuelStation").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [("fuel", "0001_initial")]
    operations = [migrations.RunPython(load, unload)]
