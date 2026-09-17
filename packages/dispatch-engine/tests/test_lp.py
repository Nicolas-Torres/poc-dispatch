from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import Shovel
from dispatch_engine.domain.mine import DumpZone, Edge, LoadZone, Material, Mine, RoadNetwork
from dispatch_engine.lp import (
    BlendTarget,
    FleetType,
    InfeasiblePlanError,
    LpProductionPlan,
    PlanInputs,
    RouteFlow,
    solve_production_plan,
)

HIGH = Material(name="ore_high", is_ore=True, grades={"cu": 0.9})
LOW = Material(name="ore_low", is_ore=True, grades={"cu": 0.5})
FLEET = (FleetType(name="default", trucks=4, payload_t=200.0),)


def _mine(*, capacity_tph: float | None = None) -> Mine:
    # "near" is 1 km from the crusher, "far" is 3 km, at 36 kph = 10 m/s.
    edges = tuple(
        Edge(source=source, target=target, length_m=length_m, speed_limit_kph=36)
        for source, target, length_m in (
            ("crusher", "near", 1000),
            ("near", "crusher", 1000),
            ("crusher", "far", 3000),
            ("far", "crusher", 3000),
        )
    )
    return Mine(
        network=RoadNetwork(edges=edges),
        load_zones={
            "zone_near": LoadZone(id="zone_near", node="near", material=HIGH),
            "zone_far": LoadZone(id="zone_far", node="far", material=LOW),
        },
        dump_zones={
            "crusher": DumpZone(
                id="crusher",
                node="crusher",
                accepts_ore=True,
                tipping_bays=2,
                capacity_tph=capacity_tph,
            )
        },
    )


SHOVELS = {
    "SH_NEAR": Shovel(id="SH_NEAR", zone="zone_near", load_rate_tph=3000),
    "SH_FAR": Shovel(id="SH_FAR", zone="zone_far", load_rate_tph=3000),
}


def _solve(mine: Mine | None = None, inputs: PlanInputs | None = None) -> LpProductionPlan:
    mine = mine if mine is not None else _mine()
    return solve_production_plan(
        mine=mine,
        shovels=SHOVELS,
        fleets=FLEET,
        best_path=BestPath(mine.network),
        inputs=inputs,
    )


def test_required_haulage_follows_cycle_time() -> None:
    # The whole point of the LP stage: at the same rate, the shovel with the
    # longer cycle needs more tonnes committed to it.
    plan = LpProductionPlan(
        flows=(
            RouteFlow(
                "SH_NEAR", "zone_near", "crusher", "default", cycle_time_s=1800, rate_tph=600
            ),
            RouteFlow("SH_FAR", "zone_far", "crusher", "default", cycle_time_s=3600, rate_tph=600),
        )
    )

    assert plan.required_rate_tph("SH_NEAR") == pytest.approx(600)
    assert plan.required_haulage_t("SH_NEAR") == pytest.approx(300)
    assert plan.required_haulage_t("SH_FAR") == pytest.approx(600)


def test_fleet_size_caps_the_plan() -> None:
    plan = _solve()

    in_flight_t = sum(plan.required_haulage_t(shovel_id) for shovel_id in SHOVELS)

    assert in_flight_t == pytest.approx(FLEET[0].trucks * FLEET[0].payload_t)


def test_value_decides_where_the_fleet_goes() -> None:
    plan = _solve(inputs=PlanInputs(values_per_tonne={"SH_NEAR": 1.0, "SH_FAR": 10.0}))

    assert plan.required_rate_tph("SH_FAR") > 0
    assert plan.required_rate_tph("SH_NEAR") == pytest.approx(0.0)


def test_blend_window_forces_both_benches() -> None:
    plan = _solve(
        inputs=PlanInputs(
            blend_targets=(
                BlendTarget(dump_zone_id="crusher", element="cu", min_grade=0.6, max_grade=0.8),
            )
        )
    )

    high_tph = plan.required_rate_tph("SH_NEAR")
    low_tph = plan.required_rate_tph("SH_FAR")
    blended_grade = (high_tph * 0.9 + low_tph * 0.5) / (high_tph + low_tph)

    assert high_tph > 0
    assert low_tph > 0
    assert 0.6 - 1e-9 <= blended_grade <= 0.8 + 1e-9


def test_production_floor_is_respected() -> None:
    plan = _solve(inputs=PlanInputs(min_rates_tph={"SH_FAR": 400.0}))

    assert plan.required_rate_tph("SH_FAR") >= 400.0 - 1e-6


def test_destination_capacity_caps_the_flow() -> None:
    plan = _solve(mine=_mine(capacity_tph=500.0))

    assert plan.rate_by_dump_tph("crusher") == pytest.approx(500.0)


def test_floor_above_digging_capacity_is_infeasible() -> None:
    with pytest.raises(InfeasiblePlanError):
        _solve(inputs=PlanInputs(min_rates_tph={"SH_NEAR": 5000.0}))
