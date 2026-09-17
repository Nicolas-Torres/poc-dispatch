from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath
from mine_sim.events import EventKind
from mine_sim.planning import PlanConditions, solve_scenario_plan
from mine_sim.replicas import Spread, seeds, spread
from mine_sim.scenario import ReliabilitySpec, ScenarioSpec, toy_mine, toy_mine_variable
from mine_sim.simulation import Simulation

HOURS_S = 3600.0


def _unreliable(mtbf_h: float, mttr_h: float) -> ScenarioSpec:
    """The toy mine with equipment that fails often enough to observe."""
    spec = toy_mine().model_dump()
    spec["truck_reliability"] = ReliabilitySpec(mtbf_h=mtbf_h, mttr_h=mttr_h).model_dump()
    for shovel in spec["shovels"]:
        shovel["reliability"] = ReliabilitySpec(mtbf_h=mtbf_h, mttr_h=mttr_h).model_dump()
    return ScenarioSpec.model_validate(spec)


def test_variability_off_leaves_the_twin_exactly_as_it_was() -> None:
    # The whole point of defaulting it off: every number already published stays
    # valid, and any difference later is attributable to the noise alone.
    without_seed = Simulation(toy_mine().build()).run(until_s=4 * HOURS_S)
    other_seed = Simulation(toy_mine().build(), seed=99).run(until_s=4 * HOURS_S)

    assert without_seed.tonnes_by_route == other_seed.tonnes_by_route
    assert without_seed.cycles == other_seed.cycles


def test_a_seed_makes_a_noisy_run_reproducible() -> None:
    scenario = toy_mine_variable().build()

    first = Simulation(scenario, seed=7).run(until_s=4 * HOURS_S)
    again = Simulation(scenario, seed=7).run(until_s=4 * HOURS_S)
    elsewhere = Simulation(scenario, seed=8).run(until_s=4 * HOURS_S)

    assert first.tonnes_by_route == again.tonnes_by_route
    assert first.tonnes_by_route != elsewhere.tonnes_by_route


def test_the_draw_keeps_the_mean_it_was_given() -> None:
    # Lognormal is parameterised so switching variability on changes the spread
    # and not the average cycle: otherwise any result would just be a slower mine.
    simulation = Simulation(toy_mine_variable().build(), seed=3)
    draws = [simulation._sample_s(600.0, 0.25) for _ in range(20_000)]

    assert sum(draws) / len(draws) == pytest.approx(600.0, rel=0.01)
    assert min(draws) > 0.0


def test_a_broken_truck_gets_no_work_until_it_is_repaired() -> None:
    simulation = Simulation(_unreliable(mtbf_h=1, mttr_h=1).build(), seed=5)
    simulation.run(until_s=12 * HOURS_S)

    events = simulation.log.events
    down_at = next(e.time_s for e in events if e.kind is EventKind.TRUCK_DOWN)
    truck_id = next(e.truck_id for e in events if e.kind is EventKind.TRUCK_DOWN)
    up_at = next(
        e.time_s for e in events if e.kind is EventKind.TRUCK_UP and e.truck_id == truck_id
    )

    assert up_at > down_at
    assert not [
        event
        for event in events
        if event.kind is EventKind.ASSIGNED
        and event.truck_id == truck_id
        and down_at < event.time_s < up_at
    ]


def test_a_random_shovel_failure_replans_like_a_scheduled_stop() -> None:
    simulation = Simulation(_unreliable(mtbf_h=1, mttr_h=1).build(), seed=5)
    simulation.run(until_s=12 * HOURS_S)

    breakdowns = [
        event
        for event in simulation.log.events
        if event.kind is EventKind.SHOVEL_DOWN and event.detail == "breakdown"
    ]

    assert breakdowns
    # Same path as a planned outage: every stop is followed by a re-solve.
    assert simulation.log.kpis(
        horizon_s=12 * HOURS_S, shovel_ids=list(simulation.scenario.shovels)
    ).replans >= len(breakdowns)


def test_a_smaller_running_fleet_shrinks_the_plan() -> None:
    scenario = toy_mine().build()
    best_path = BestPath(scenario.mine.network)

    whole = solve_scenario_plan(scenario, best_path)
    halved = solve_scenario_plan(
        scenario, best_path, PlanConditions(available_trucks={"default": 3})
    )

    in_flight = sum(halved.required_haulage_t(shovel_id) for shovel_id in scenario.shovels)
    assert in_flight == pytest.approx(3 * 220.0)
    assert sum(flow.rate_tph for flow in halved.flows) < sum(flow.rate_tph for flow in whole.flows)


def test_a_fleet_too_small_for_the_stripping_floor_still_gets_a_plan() -> None:
    # The waste floor asks for 800 t/h, which a single truck cannot sustain. A
    # commitment it cannot meet must not leave the operation with no plan at all.
    scenario = toy_mine().build()

    plan = solve_scenario_plan(
        scenario,
        BestPath(scenario.mine.network),
        PlanConditions(available_trucks={"default": 1}),
    )

    assert plan.flows
    assert sum(flow.rate_tph for flow in plan.flows) > 0


def test_spread_reports_no_dispersion_for_a_single_replica() -> None:
    scenario = toy_mine_variable().build()
    runs = [Simulation(scenario, seed=s).run(until_s=4 * HOURS_S) for s in seeds(1, 3)]
    # Cycle time rather than tonnage: tipped tonnes come in whole truckloads and
    # two short runs can land on the same total by coincidence.
    single = spread(runs[:1], lambda kpis: kpis.avg_cycle_time_s)
    several = spread(runs, lambda kpis: kpis.avg_cycle_time_s)

    assert single == Spread(mean=runs[0].avg_cycle_time_s, stdev=0.0)
    assert several.stdev > 0.0
    assert "+/-" in several.format()
