"""
Cost-optimal refuelling plan along a route.

Main planner: dynamic programme (``plan_fuel_stops``)
-----------------------------------------------------
Minimises   total fuel cost  +  stop_penalty_usd * number_of_stops.

* Stations are processed in route order; fuel in the tank is tracked in
  1‑mile units (0 .. tank range), so the state space is tiny
  (#stations x 501 for a 500 mile tank).
* Between consecutive stations the tank simply drains by the distance driven
  (skipping a station == passing it without buying).
* At a station you may buy any amount. With a per-stop penalty this is
      after[g] = min( before[g],                                  # don't stop
                      penalty + c*g + min_{f<g}(before[f] - c*f) ) # stop, fill f -> g
  where c = price / mpg is the cost of one mile of range. The inner min is a
  prefix-minimum, so each station costs O(tank) with NumPy.
* Back-pointers recover where and how much to buy.

With ``stop_penalty_usd = 0`` this is the exact minimum-cost plan (it equals
the provably optimal greedy below, up to 1‑mile rounding of positions).
A small positive penalty removes "stop to save 3 cents" top-ups; the response
reports the unconstrained optimum too so the trade-off is visible.

Reference planner: greedy (``greedy_plan``)
-------------------------------------------
The classic "gas station problem" greedy (Khuller, Malekian & Mestre, *To Fill
or Not to Fill*): go to the nearest cheaper reachable station buying just
enough; if none, fill up and go to the cheapest reachable one. It is used for
tests and to explain infeasible routes (where the vehicle would run dry).
"""
from bisect import bisect_right
from dataclasses import dataclass, field

import numpy as np

from .stations import RouteStation

# Tiny penalty used for the "unconstrained" optimum: breaks ties in favour of
# fewer stops without measurably changing the cost.
_TIE_BREAK_PENALTY = 1e-6


@dataclass
class FuelStop:
    station: RouteStation
    range_on_arrival_miles: float
    gallons: float
    cost: float
    range_on_departure_miles: float

    def as_dict(self):
        return {
            "station": self.station.as_dict(),
            "mile_marker": round(self.station.mile_marker, 1),
            "detour_miles": round(self.station.detour_miles, 1),
            "price_per_gallon": round(self.station.price, 4),
            "range_on_arrival_miles": round(self.range_on_arrival_miles, 1),
            "gallons_purchased": round(self.gallons, 2),
            "cost_usd": round(self.cost, 2),
            "range_on_departure_miles": round(self.range_on_departure_miles, 1),
        }


@dataclass
class FuelPlan:
    stops: list = field(default_factory=list)
    feasible: bool = True
    message: str = ""
    total_cost: float = 0.0
    total_gallons: float = 0.0
    stranded_at_mile: float | None = None
    # Exact optimum without the stop penalty (for transparency).
    unconstrained_cost: float | None = None
    unconstrained_stops: int | None = None

    def finalise_message(self):
        if self.feasible:
            self.message = (
                f"{len(self.stops)} fuel stop(s) needed." if self.stops
                else "No refuelling needed: the route is within the vehicle's starting range."
            )


# --------------------------------------------------------------------------- DP
def plan_fuel_stops(stations, total_miles, *, tank_range_miles, mpg, initial_range_miles=None,
                    stop_penalty_usd=0.0):
    """
    stations: RouteStation list (any order) lying within the route corridor.
    total_miles: route length in miles. Returns a FuelPlan.
    """
    tank = int(round(float(tank_range_miles)))
    initial = tank if initial_range_miles is None else int(np.floor(min(float(initial_range_miles), tank)))
    total = int(round(float(total_miles)))
    # At the same (1-mile) position a pricier station is never better: keep the cheapest.
    cheapest = {}
    for s in stations:
        if 0.0 <= s.mile_marker < total_miles:  # a station at the origin is usable
            key = int(round(s.mile_marker))
            if key not in cheapest or (s.price, s.detour_miles) < (cheapest[key].price, cheapest[key].detour_miles):
                cheapest[key] = s
    cands = sorted(cheapest.values(), key=lambda s: (s.mile_marker, s.price))

    best = _dp(cands, total, tank, initial, float(mpg), max(float(stop_penalty_usd), _TIE_BREAK_PENALTY))
    if best is None:  # no feasible plan: let the greedy explain where the vehicle runs dry
        return greedy_plan(stations, total_miles, tank_range_miles=tank_range_miles, mpg=mpg,
                           initial_range_miles=initial_range_miles)

    if stop_penalty_usd > _TIE_BREAK_PENALTY:
        raw = _dp(cands, total, tank, initial, float(mpg), _TIE_BREAK_PENALTY)
        best.unconstrained_cost, best.unconstrained_stops = raw.total_cost, len(raw.stops)
    else:
        best.unconstrained_cost, best.unconstrained_stops = best.total_cost, len(best.stops)
    return best


def _dp(cands, total, tank, initial, mpg, penalty):
    levels = np.arange(tank + 1, dtype=float)
    INF = np.inf
    cost = np.full(tank + 1, INF)
    cost[initial] = 0.0
    prev_pos = 0
    back, drives = [], []

    for st in cands:
        pos = min(max(int(round(st.mile_marker)), prev_pos), total)
        d = pos - prev_pos
        arrive = np.full(tank + 1, INF)
        if d <= tank:
            arrive[: tank + 1 - d] = cost[d:]
        c = st.price / mpg  # dollars per mile of range
        vals = arrive - c * levels
        pmin = np.minimum.accumulate(vals)
        is_new_min = vals <= pmin
        pidx = np.maximum.accumulate(np.where(is_new_min, np.arange(tank + 1), 0))
        # exclusive prefix (strictly less fuel than g)
        pmin_ex = np.concatenate(([INF], pmin[:-1]))
        pidx_ex = np.concatenate(([0], pidx[:-1]))
        buy = penalty + c * levels + pmin_ex
        take = buy < arrive
        cost = np.where(take, buy, arrive)
        back.append(np.where(take, pidx_ex, np.arange(tank + 1)).astype(np.int32))
        drives.append(d)
        prev_pos = pos

    d_last = total - prev_pos
    if d_last > tank or not np.isfinite(cost[d_last:]).any():
        return None
    g = d_last + int(np.argmin(cost[d_last:]))

    plan = FuelPlan()
    stops = []
    for j in range(len(cands) - 1, -1, -1):
        f = int(back[j][g])
        if f != g:
            st = cands[j]
            miles = g - f
            gallons = miles / mpg
            stops.append(FuelStop(st, float(f), gallons, gallons * st.price, float(g)))
        g = f + drives[j]
    stops.reverse()
    plan.stops = stops
    plan.total_cost = sum(s.cost for s in stops)
    plan.total_gallons = sum(s.gallons for s in stops)
    plan.finalise_message()
    return plan


# ----------------------------------------------------------------------- greedy
def greedy_plan(stations, total_miles, *, tank_range_miles, mpg, initial_range_miles=None):
    U = float(tank_range_miles)
    fuel = U if initial_range_miles is None else max(0.0, min(float(initial_range_miles), U))
    total = float(total_miles)
    cands = sorted((s for s in stations if 0.0 < s.mile_marker < total), key=lambda s: (s.mile_marker, s.price))
    miles = [s.mile_marker for s in cands]
    plan, pos, current, eps = FuelPlan(), 0.0, None, 1e-9

    for _ in range(len(cands) + 2):  # each iteration moves strictly forward
        ahead = cands[bisect_right(miles, pos): bisect_right(miles, pos + U)]
        dest_in_range = (total - pos) <= U + eps
        cur_price = current.price if current is not None else float("inf")
        cheaper = next((s for s in ahead if s.price < cur_price), None)

        if cheaper is not None:  # nearest cheaper station: buy only what is needed to get there
            target, target_mile = cheaper, cheaper.mile_marker
            need = target_mile - pos
        elif dest_in_range:  # everything ahead is pricier: buy just enough to finish
            target, target_mile = None, total
            need = total - pos
        elif ahead:  # fill up, then head to the cheapest station we can reach
            target = min(ahead, key=lambda s: (s.price, -s.mile_marker))
            target_mile = target.mile_marker
            need = U if current is not None else target_mile - pos
        else:
            plan.feasible = False
            plan.stranded_at_mile = round(pos, 1)
            plan.message = (
                f"No fuel station found within {U:.0f} miles after mile {pos:.0f}; "
                "the vehicle cannot complete this route with the available price list."
            )
            return plan

        if need > fuel + eps:
            if current is None:
                plan.feasible = False
                plan.stranded_at_mile = 0.0
                plan.message = (
                    f"The first reachable fuel station is {need:.0f} miles away but the vehicle "
                    f"starts with only {fuel:.0f} miles of range."
                )
                return plan
            buy_miles = min(need, U) - fuel
            gallons = buy_miles / mpg
            cost = gallons * current.price
            plan.stops.append(FuelStop(current, fuel, gallons, cost, fuel + buy_miles))
            plan.total_cost += cost
            plan.total_gallons += gallons
            fuel += buy_miles

        fuel -= target_mile - pos  # drive
        pos, current = target_mile, target
        if target is None:
            break
    else:
        plan.feasible = False
        plan.message = "Planner did not converge."
        return plan

    plan.unconstrained_cost, plan.unconstrained_stops = plan.total_cost, len(plan.stops)
    plan.finalise_message()
    return plan
