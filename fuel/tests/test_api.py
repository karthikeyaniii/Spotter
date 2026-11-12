from unittest.mock import patch

import numpy as np
from django.test import TestCase
from django.urls import reverse

from fuel.models import FuelStation, GeocodeCache
from fuel.services.geo import cumulative_distances
from fuel.services.geocoding import GeocodingError, Place, geocode
from fuel.services.routing import Route
from fuel.services.stations import invalidate_station_index


def fake_route(*args, counter=None, **kwargs):
    # Straight line along latitude 35 from lon -100 to -90, scaled to exactly 700 miles.
    lons = np.linspace(-100, -90, 300)
    coords = np.column_stack([lons, np.full_like(lons, 35.0)])
    cum = cumulative_distances(coords)
    cum = cum * (700 / cum[-1])
    if counter is not None:
        counter["routing"] = counter.get("routing", 0) + 1
    return Route(coords, cum, 700.0, 11.0)


def fake_geocode(query, *, counter=None):
    table = {
        "west": Place(query, "West Town, USA", 35.0, -100.0, "test"),
        "east": Place(query, "East Town, USA", 35.0, -90.0, "test"),
    }
    if query not in table:
        raise GeocodingError(f"Could not find {query}")
    if counter is not None:
        counter["geocoding"] = counter.get("geocoding", 0) + 1
    return table[query]


def lon_at_mile(mile):
    return -100.0 + 10.0 * (mile / 700.0)


class RouteApiTests(TestCase):
    def setUp(self):
        FuelStation.objects.all().delete()  # the data migration loaded the real list; use fixtures
        FuelStation.objects.bulk_create(
            [
                FuelStation(opis_id=1, name="Cheap Stop", city="A", state="TX", retail_price="3.000",
                            min_price="3.0", max_price="3.0", latitude=35.0, longitude=lon_at_mile(250)),
                FuelStation(opis_id=2, name="Pricey Stop", city="B", state="TX", retail_price="3.900",
                            min_price="3.9", max_price="3.9", latitude=35.0, longitude=lon_at_mile(450)),
                FuelStation(opis_id=3, name="Off Route", city="C", state="TX", retail_price="2.000",
                            min_price="2.0", max_price="2.0", latitude=36.5, longitude=lon_at_mile(300)),
                FuelStation(opis_id=4, name="No Coords", city="D", state="TX", retail_price="1.000",
                            min_price="1.0", max_price="1.0"),
            ]
        )
        invalidate_station_index()

    def tearDown(self):
        invalidate_station_index()

    @patch("fuel.services.planner.get_route", side_effect=fake_route)
    @patch("fuel.services.planner.geocode", side_effect=fake_geocode)
    def test_route_plan_success(self, _g, _r):
        resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "east"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["route"]["distance_miles"], 700.0)
        self.assertEqual(data["route"]["geometry"]["type"], "LineString")
        self.assertEqual(data["external_api_calls"], {"geocoding": 2, "routing": 1})
        # 700 miles, 500 mile tank: one stop at the cheap station (mile 250), buy 200 miles = 20 gal
        self.assertTrue(data["summary"]["feasible"])
        self.assertEqual(data["summary"]["number_of_stops"], 1)
        stop = data["fuel_stops"][0]
        self.assertEqual(stop["station"]["opis_id"], 1)
        self.assertAlmostEqual(stop["gallons_purchased"], 20.0, places=1)
        self.assertAlmostEqual(data["summary"]["total_fuel_cost_usd"], 60.0, places=1)
        self.assertEqual(data["summary"]["fuel_consumed_gallons"], 70.0)
        # 50 gal of starting fuel valued at the $3.00 paid + $60 bought
        self.assertAlmostEqual(data["summary"]["estimated_total_trip_fuel_cost_usd"], 210.0, places=1)
        self.assertIn("map_url", data)

    @patch("fuel.services.planner.get_route", side_effect=fake_route)
    @patch("fuel.services.planner.geocode", side_effect=fake_geocode)
    def test_post_json_and_no_geometry(self, _g, _r):
        resp = self.client.post(
            reverse("fuel:route"),
            {"start": "west", "finish": "east", "include_geometry": False, "initial_range_miles": 300},
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIsNone(data["route"]["geometry"])
        # Starting with 300 miles: reach cheap stop (mile 250) with 50 left, buy 400 miles (40 gal)
        # to cover the remaining 450 -> total 40 gal * $3 = $120
        self.assertEqual(data["summary"]["number_of_stops"], 1)
        self.assertAlmostEqual(data["summary"]["total_fuel_cost_usd"], 120.0, places=1)

    def test_validation_errors(self):
        resp = self.client.get(reverse("fuel:route"), {"start": "west"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("finish", resp.json()["details"])
        resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "west"})
        self.assertEqual(resp.status_code, 400)

    @patch("fuel.services.planner.geocode", side_effect=fake_geocode)
    def test_geocode_failure_returns_400(self, _g):
        resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "atlantis"})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Location not found")

    def test_health(self):
        resp = self.client.get(reverse("fuel:health"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["stations_loaded"], 4)
        self.assertEqual(resp.json()["stations_with_coordinates"], 3)

    def test_map_page_renders(self):
        resp = self.client.get("/", {"start": "Chicago, IL", "finish": "Atlanta, GA"})
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Fuel Route Planner")
        self.assertContains(resp, "Chicago, IL")


class GeocodeServiceTests(TestCase):
    def test_lat_lon_literal_needs_no_external_call(self):
        calls = {}
        place = geocode("41.88,-87.63", counter=calls)
        self.assertEqual((place.latitude, place.longitude), (41.88, -87.63))
        self.assertEqual(calls, {})

    def test_lat_lon_outside_usa_rejected(self):
        with self.assertRaises(GeocodingError):
            geocode("48.85,2.35")  # Paris

    def test_db_cache_short_circuits_providers(self):
        # A street address is not in the offline place list, so it goes through the DB cache.
        GeocodeCache.objects.create(query="100 main st, springfield, il", display_name="100 Main St", latitude=39.8,
                                    longitude=-89.6, provider="nominatim")
        calls = {}
        with patch("fuel.services.geocoding._nominatim") as nom:
            place = geocode("  100 Main St,   Springfield, IL ", counter=calls)
        nom.assert_not_called()
        self.assertTrue(place.cached)
        self.assertEqual(calls, {})


class OfflinePlacesTests(TestCase):
    def test_city_state_resolves_without_external_calls(self):
        calls = {}
        with patch("fuel.services.geocoding._nominatim") as nom, patch("fuel.services.geocoding._photon") as pho:
            place = geocode("chicago, il", counter=calls)
        nom.assert_not_called()
        pho.assert_not_called()
        self.assertEqual(calls, {})
        self.assertEqual(place.provider, "us-places")
        self.assertAlmostEqual(place.latitude, 41.84, delta=0.1)

    def test_formats_and_ambiguity(self):
        from fuel.services.places import get_place_index

        ix = get_place_index()
        self.assertEqual(ix.resolve("Austin TX").label, "Austin, TX")
        self.assertEqual(ix.resolve("St. Louis, Missouri 63101").label, "St. Louis, MO")
        self.assertEqual(ix.resolve("Mt Vernon, IL").label, "Mount Vernon, IL")
        self.assertEqual(ix.resolve("Nashville").label, "Nashville, TN")  # consolidated-city alias
        self.assertIsNone(ix.resolve("Springfield"))  # ambiguous -> online geocoder
        self.assertIsNone(ix.resolve("1600 Pennsylvania Ave NW"))  # street address -> online geocoder

    def test_suggestions_endpoint(self):
        resp = self.client.get(reverse("fuel:places"), {"q": "chi", "limit": 3})
        self.assertEqual(resp.status_code, 200)
        labels = [r["label"] for r in resp.json()["results"]]
        self.assertEqual(labels[0], "Chicago, IL")
        self.assertLessEqual(len(labels), 3)
        self.assertEqual(self.client.get(reverse("fuel:places"), {"q": "c"}).json()["results"], [])


class RouteApiExtraTests(TestCase):
    def setUp(self):
        FuelStation.objects.all().delete()
        FuelStation.objects.create(opis_id=1, name="Cheap Stop", city="A", state="TX", retail_price="3.000",
                                   min_price="3.0", max_price="3.0", latitude=35.0, longitude=lon_at_mile(250))
        invalidate_station_index()

    def tearDown(self):
        invalidate_station_index()

    @patch("fuel.services.planner.get_route", side_effect=fake_route)
    @patch("fuel.services.planner.geocode", side_effect=fake_geocode)
    def test_geojson_format(self, _g, _r):
        resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "east", "output": "geojson",
                                                       "include_geometry": "false"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["type"], "FeatureCollection")
        kinds = [f["properties"]["kind"] for f in data["features"]]
        self.assertEqual(kinds, ["route", "start", "finish", "fuel_stop"])  # geometry forced on for geojson

    @patch("fuel.services.planner.geocode", side_effect=fake_geocode)
    def test_no_route_returns_422(self, _g):
        from fuel.services.routing import NoRouteError

        with patch("fuel.services.planner.get_route", side_effect=NoRouteError("no road")):
            resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "east"})
        self.assertEqual(resp.status_code, 422)
        self.assertEqual(resp.json()["error"], "No drivable route")

    def test_missing_station_data_returns_503(self):
        FuelStation.objects.all().delete()
        invalidate_station_index()
        resp = self.client.get(reverse("fuel:route"), {"start": "west", "finish": "east"})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("load_fuel_prices", resp.json()["details"])

    def test_api_root(self):
        resp = self.client.get(reverse("fuel:root"))
        self.assertEqual(resp.status_code, 200)
        self.assertIn("plan_route", resp.json()["endpoints"])


class DataMigrationTests(TestCase):
    def test_migrate_loaded_the_supplied_price_list(self):
        self.assertGreater(FuelStation.objects.count(), 6000)
        self.assertEqual(FuelStation.objects.filter(latitude__isnull=True).count(), 0)
        from fuel.services.geo import US_STATES

        self.assertFalse(FuelStation.objects.exclude(state__in=list(US_STATES)).exists())  # Canada dropped
