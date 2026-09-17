from __future__ import annotations

from dataclasses import dataclass, field, replace

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import ShovelId
from dispatch_engine.lp import (
    FleetType,
    InfeasiblePlanError,
    LpProductionPlan,
    solve_production_plan,
)

from mine_sim.scenario import Scenario


@dataclass(frozen=True, slots=True)
class PlanConditions:
    """What changed in the mine that makes the plan worth re-solving."""

    unavailable_shovels: frozenset[ShovelId] = frozenset()
    # Trucks still running, per fleet type. None means the whole fleet.
    available_trucks: dict[str, int] | None = field(default=None)


def solve_scenario_plan(
    scenario: Scenario,
    best_path: BestPath,
    conditions: PlanConditions | None = None,
) -> LpProductionPlan:
    """Run stage 2 over a scenario under the conditions the mine is actually in.

    Best Path is an input, not a detail: the fleet constraint is written in
    truck-hours, so the LP cannot size a route without knowing how long its cycle
    takes. Dropping the shovels that are out of service, and shrinking the fleet
    to what is running, is what makes a re-solve mean something.
    """
    conditions = conditions if conditions is not None else PlanConditions()
    shovels = {
        shovel_id: shovel
        for shovel_id, shovel in scenario.shovels.items()
        if shovel_id not in conditions.unavailable_shovels
    }
    fleets = _available_fleets(scenario, conditions.available_trucks)

    try:
        return solve_production_plan(
            mine=scenario.mine,
            shovels=shovels,
            fleets=fleets,
            best_path=best_path,
            spot_time_s=scenario.spot_time_s,
            inputs=scenario.plan_inputs,
        )
    except InfeasiblePlanError:
        # A production floor is a commitment, not a law of physics: a fleet too
        # small to meet it should still get the best plan it can, rather than
        # leaving the operation with no plan at all.
        return solve_production_plan(
            mine=scenario.mine,
            shovels=shovels,
            fleets=fleets,
            best_path=best_path,
            spot_time_s=scenario.spot_time_s,
            inputs=replace(scenario.plan_inputs, min_rates_tph={}),
        )


def _available_fleets(
    scenario: Scenario, available_trucks: dict[str, int] | None
) -> tuple[FleetType, ...]:
    if available_trucks is None:
        return scenario.fleets
    return tuple(
        replace(fleet, trucks=available_trucks.get(fleet.name, 0))
        for fleet in scenario.fleets
        if available_trucks.get(fleet.name, 0) > 0
    )
