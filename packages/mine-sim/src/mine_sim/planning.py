from __future__ import annotations

from dispatch_engine.best_path import BestPath
from dispatch_engine.lp import LpProductionPlan, solve_production_plan

from mine_sim.scenario import Scenario


def solve_scenario_plan(scenario: Scenario, best_path: BestPath) -> LpProductionPlan:
    """Run stage 2 over a scenario.

    Best Path is an input, not a detail: the fleet constraint is written in
    truck-hours, so the LP cannot size a route without knowing how long its cycle
    takes.
    """
    return solve_production_plan(
        mine=scenario.mine,
        shovels=scenario.shovels,
        fleets=scenario.fleets,
        best_path=best_path,
        spot_time_s=scenario.spot_time_s,
        inputs=scenario.plan_inputs,
    )
