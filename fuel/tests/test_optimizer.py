from django.test import SimpleTestCase

from fuel.services.optimizer import plan_fuel_stops
from fuel.services.stations import RouteStation


def station(opis_id, mile, price, detour=1.0):
    return RouteStation(
        opis_id=opis_id, name=f"S{opis_id}", address="", city="X", state="TX",
        latitude=0.0, longitude=0.0, price=price, mile_marker=mile, detour_miles=detour,
    )


class OptimizerTests(SimpleTestCase):
    def test_no_stop_needed_when_destination_within_initial_range(self):
        plan = plan_fuel_stops([station(1, 100, 3.0)], 400, tank_range_miles=500, mpg=10)
        self.assertTrue(plan.feasible)
        self.assertEqual(plan.stops, [])
        self.assertEqual(plan.total_cost, 0)

    def test_buys_just_enough_when_cheaper_station_ahead(self):
        # A at 100 ($3.00), B at 400 ($2.90). Trip 850 mi, start full (500).
        # Optimal: buy nothing at A (B is cheaper and reachable), then at B buy
        # just enough for the remaining 450 miles: arrive with 100 -> buy 350 mi = 35 gal.
        stations = [station(1, 100, 3.0), station(2, 400, 2.9)]
        plan = plan_fuel_stops(stations, 850, tank_range_miles=500, mpg=10)
        self.assertTrue(plan.feasible)
        self.assertEqual([s.station.opis_id for s in plan.stops], [2])
        stop = plan.stops[0]
        self.assertAlmostEqual(stop.range_on_arrival_miles, 100)
        self.assertAlmostEqual(stop.gallons, 35)
        self.assertAlmostEqual(plan.total_cost, 35 * 2.9)

    def test_fill_up_rule_when_everything_ahead_is_pricier(self):
        # A at 100 ($2.90) cheapest, B at 450 ($2.95), C at 950 ($3.50); trip 1000, start 500.
        # Expected: fill at A (100 mi -> 10 gal), top up at B to reach C, finish at C.
        stations = [station(1, 100, 2.90), station(2, 450, 2.95), station(3, 950, 3.50)]
        plan = plan_fuel_stops(stations, 1000, tank_range_miles=500, mpg=10)
        self.assertTrue(plan.feasible)
        ids = [s.station.opis_id for s in plan.stops]
        self.assertEqual(ids, [1, 2, 3])
        gallons = [round(s.gallons, 3) for s in plan.stops]
        self.assertEqual(gallons, [10.0, 35.0, 5.0])
        self.assertAlmostEqual(plan.total_cost, 10 * 2.90 + 35 * 2.95 + 5 * 3.50, places=6)

    def test_prefers_cheapest_reachable_not_nearest(self):
        # Expensive station near, cheap one farther but still reachable.
        stations = [station(1, 50, 4.00), station(2, 450, 3.00)]
        plan = plan_fuel_stops(stations, 900, tank_range_miles=500, mpg=10)
        self.assertEqual([s.station.opis_id for s in plan.stops], [2])
        self.assertAlmostEqual(plan.stops[0].gallons, 40)  # arrive with 50 mi, need 450

    def test_infeasible_when_gap_exceeds_range(self):
        stations = [station(1, 100, 3.0), station(2, 800, 3.0)]  # 700 mile gap
        plan = plan_fuel_stops(stations, 1200, tank_range_miles=500, mpg=10)
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.stranded_at_mile, 100)
        self.assertIn("No fuel station", plan.message)

    def test_infeasible_when_first_station_out_of_initial_range(self):
        stations = [station(1, 300, 3.0)]
        plan = plan_fuel_stops(stations, 700, tank_range_miles=500, mpg=10, initial_range_miles=100)
        self.assertFalse(plan.feasible)
        self.assertEqual(plan.stranded_at_mile, 0.0)

    def test_stop_penalty_removes_stops_that_save_little(self):
        # Pure optimum: tiny top-up at mile 7 ($3.00) because mile 300 is pricier ($3.20).
        stations = [station(1, 7, 3.00), station(2, 300, 3.20)]
        raw = plan_fuel_stops(stations, 700, tank_range_miles=500, mpg=10)
        self.assertEqual([s.station.opis_id for s in raw.stops], [1, 2])
        self.assertLess(raw.stops[0].gallons, 1)
        clean = plan_fuel_stops(stations, 700, tank_range_miles=500, mpg=10, stop_penalty_usd=10)
        self.assertEqual([s.station.opis_id for s in clean.stops], [2])
        self.assertTrue(clean.feasible)
        self.assertGreaterEqual(clean.total_cost, raw.total_cost)  # never beats the optimum
        self.assertAlmostEqual(clean.unconstrained_cost, raw.total_cost)
        self.assertEqual(clean.unconstrained_stops, 2)

    def test_stop_penalty_keeps_stop_that_saves_a_lot(self):
        # Filling at a $2.00 station instead of a $4.00 one saves far more than $10.
        stations = [station(1, 100, 2.00), station(2, 450, 4.00)]
        plan = plan_fuel_stops(stations, 900, tank_range_miles=500, mpg=10, stop_penalty_usd=10)
        self.assertEqual([s.station.opis_id for s in plan.stops], [1, 2])

    def test_stop_penalty_never_makes_plan_infeasible(self):
        stations = [station(1, 495, 3.00)]  # the only way to finish
        plan = plan_fuel_stops(stations, 520, tank_range_miles=500, mpg=10, stop_penalty_usd=1000)
        self.assertTrue(plan.feasible)
        self.assertEqual(len(plan.stops), 1)


class DynamicProgrammeMatchesGreedyTests(SimpleTestCase):
    """With no stop penalty the DP must reproduce the provably optimal greedy cost."""

    def test_random_instances(self):
        import random

        from fuel.services.optimizer import greedy_plan

        rng = random.Random(42)
        checked = 0
        for _ in range(300):
            total = rng.randint(300, 3000)
            n = rng.randint(1, 60)
            stations = [station(i, rng.randint(1, total - 1), round(rng.uniform(2.5, 4.5), 3)) for i in range(n)]
            initial = rng.choice([None, rng.randint(50, 500)])
            g = greedy_plan(stations, total, tank_range_miles=500, mpg=10, initial_range_miles=initial)
            d = plan_fuel_stops(stations, total, tank_range_miles=500, mpg=10, initial_range_miles=initial)
            self.assertEqual(g.feasible, d.feasible)
            if g.feasible:
                checked += 1
                self.assertAlmostEqual(d.total_cost, g.total_cost, places=4)
                # every purchase stays within the tank and the fuel never goes negative
                for stop in d.stops:
                    self.assertGreaterEqual(stop.range_on_arrival_miles, 0)
                    self.assertLessEqual(stop.range_on_departure_miles, 500)
        self.assertGreater(checked, 50)
