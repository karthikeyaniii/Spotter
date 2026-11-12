import threading

from django.apps import AppConfig


class FuelConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "fuel"
    verbose_name = "Fuel route planner"

    def ready(self):
        # Warm the offline place list (file only, no DB) in the background so the
        # first type-ahead request doesn't pay the ~1 s CSV load.
        from .services.places import get_place_index

        threading.Thread(target=get_place_index, name="warm-place-index", daemon=True).start()
