from django.db import models


class FuelStation(models.Model):
    """One truck stop from the OPIS price list (duplicates in the CSV are merged)."""

    opis_id = models.IntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=200, blank=True)
    city = models.CharField(max_length=100)
    state = models.CharField(max_length=2, db_index=True)
    rack_id = models.IntegerField(null=True, blank=True)
    # Average retail price across the rows for this station in the source file.
    retail_price = models.DecimalField(max_digits=8, decimal_places=4)
    min_price = models.DecimalField(max_digits=8, decimal_places=4)
    max_price = models.DecimalField(max_digits=8, decimal_places=4)
    price_samples = models.PositiveIntegerField(default=1)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    geocode_source = models.CharField(max_length=40, blank=True)

    class Meta:
        ordering = ["state", "city", "name"]
        indexes = [models.Index(fields=["latitude", "longitude"])]

    def __str__(self):
        return f"{self.name} ({self.city}, {self.state}) ${self.retail_price}"

    @property
    def has_coordinates(self):
        return self.latitude is not None and self.longitude is not None


class GeocodeCache(models.Model):
    """Persistent cache of user-supplied place names -> coordinates."""

    query = models.CharField(max_length=255, unique=True)
    display_name = models.CharField(max_length=400)
    latitude = models.FloatField()
    longitude = models.FloatField()
    provider = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.query} -> ({self.latitude:.4f}, {self.longitude:.4f})"
