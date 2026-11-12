import numpy as np
from django.test import SimpleTestCase

from fuel.services.geo import cumulative_distances, haversine_miles, thin_polyline
from fuel.services.stations import StationIndex


class GeoTests(SimpleTestCase):
    def test_haversine_known_distance(self):
        # Chicago -> Atlanta great-circle distance is ~587 miles
        d = haversine_miles(41.8781, -87.6298, 33.7490, -84.3880)
        self.assertAlmostEqual(d, 587, delta=5)

    def test_cumulative_distances_monotonic(self):
        coords = [[-87.6, 41.9], [-86.0, 41.0], [-84.4, 33.7]]
        cum = cumulative_distances(coords)
        self.assertEqual(cum[0], 0)
        self.assertTrue(np.all(np.diff(cum) > 0))

    def test_thin_polyline_keeps_endpoints(self):
        coords = np.array([[i / 10.0, i / 10.0] for i in range(1000)])
        thinned = thin_polyline(coords, 50)
        self.assertLessEqual(len(thinned), 50)
        self.assertEqual(thinned[0], [0.0, 0.0])
        self.assertEqual(thinned[-1], [99.9, 99.9])


class StationIndexTests(SimpleTestCase):
    def setUp(self):
        # Straight west->east route along latitude 35 from lon -100 to lon -95 (~ 283 miles)
        lons = np.linspace(-100, -95, 200)
        self.coords = np.column_stack([lons, np.full_like(lons, 35.0)])
        self.cum = cumulative_distances(self.coords)
        rows = [
            (1, "On route", "", "A", "TX", 35.0, -98.0, 3.10),  # right on the line
            (2, "Near route", "", "B", "TX", 35.08, -97.0, 3.20),  # ~5.5 mi north
            (3, "Far away", "", "C", "TX", 36.0, -97.0, 2.50),  # ~69 mi north
            (4, "Off the end", "", "D", "TX", 35.0, -94.0, 2.00),  # 56 mi past the finish
        ]
        self.index = StationIndex(rows)

    def test_finds_only_stations_in_corridor(self):
        found = self.index.along_route(self.coords, self.cum, max_detour_miles=10)
        ids = [s.opis_id for s in found]
        self.assertEqual(ids, [1, 2])

    def test_mile_marker_and_detour(self):
        found = {s.opis_id: s for s in self.index.along_route(self.coords, self.cum, max_detour_miles=10)}
        self.assertAlmostEqual(found[1].detour_miles, 0, delta=0.6)
        self.assertAlmostEqual(found[1].mile_marker, self.cum[-1] * (2 / 5), delta=1.5)
        self.assertAlmostEqual(found[2].detour_miles, 5.5, delta=0.6)

    def test_empty_index(self):
        self.assertEqual(StationIndex([]).along_route(self.coords, self.cum, 10), [])
