from __future__ import annotations

from functools import partial

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.snapshot import Overrides
from dispatch_engine.policies.earliest_shovel import EarliestShovelPolicy
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from mine_sim.events import EventKind, Kpis
from mine_sim.planning import PlanConditions, solve_scenario_plan
from mine_sim.scenario import (
    EdgeSpec,
    LoadZoneSpec,
    MaterialSpec,
    Scenario,
    ScenarioSpec,
    toy_mine,
    toy_mine_with_failure,
    toy_mine_with_stockpile,
)
from mine_sim.simulation import Simulation
from pydantic import ValidationError


def _lp_simulation(scenario: Scenario) -> Simulation:
    best_path = BestPath(scenario.mine.network)
    return Simulation(
        scenario,
        plan_provider=lambda conditions: solve_scenario_plan(scenario, best_path, conditions),
        best_path=best_path,
    )


def test_toy_scenario_runs_a_full_shift_without_stalling() -> None:
    kpis = Simulation(toy_mine().build()).run(until_s=3600.0)

    assert kpis.cycles > 0
    assert kpis.tonnes_total > 0
    assert kpis.standby_events == 0
    assert set(kpis.tonnes_by_dump) == {"crusher", "waste_dump"}
    # Every shovel is kept fed: the need ranking must not starve one of them.
    assert all(shovel.loads > 0 for shovel in kpis.shovels)


def test_material_still_riding_at_the_cut_off_is_reported_apart() -> None:
    # Tipped tonnage alone makes every short run look like it missed the plan,
    # when the difference is simply still on a truck.
    short = Simulation(toy_mine().build()).run(until_s=3600.0)
    long_run = Simulation(toy_mine().build()).run(until_s=4 * 3600.0)

    assert short.tonnes_in_transit > 0
    assert short.tonnes_moved == short.tonnes_total + short.tonnes_in_transit
    # The truncation is a fixed amount of material, so it weighs less the longer
    # the run: the shortfall it causes has to shrink.
    assert short.tonnes_in_transit / short.tonnes_moved > (
        long_run.tonnes_in_transit / long_run.tonnes_moved
    )


def test_a_blend_margin_keeps_the_delivered_grade_inside_the_window() -> None:
    spec = toy_mine().model_dump()
    spec["blend_targets"][0]["margin"] = 0.05
    scenario = ScenarioSpec.model_validate(spec).build()

    kpis = _lp_simulation(scenario).run(until_s=4 * 3600.0)

    grades = {"zone_n": 0.9, "zone_s": 0.5}
    crusher_t = kpis.tonnes_by_dump["crusher"]
    delivered = (
        sum(
            tonnes * grades[zone_id]
            for (zone_id, dump_id), tonnes in kpis.tonnes_by_route.items()
            if dump_id == "crusher"
        )
        / crusher_t
    )

    assert delivered <= 0.8


def test_a_margin_wider_than_the_window_is_rejected() -> None:
    spec = toy_mine().model_dump()
    spec["blend_targets"][0]["margin"] = 0.2

    with pytest.raises(ValidationError, match="leaves no room"):
        ScenarioSpec.model_validate(spec)


def test_ore_and_waste_reach_their_own_destinations() -> None:
    kpis = Simulation(toy_mine().build()).run(until_s=3600.0)

    # Asserted over routes rather than by comparing shovel and dump totals: those
    # two differ by whatever is still riding at the cut-off, which makes the
    # comparison depend on where the horizon happens to fall.
    ore_zones = {"zone_n", "zone_s"}
    destinations = {
        zone_id: dump_id for (zone_id, dump_id), tonnes in kpis.tonnes_by_route.items() if tonnes
    }

    assert destinations == {"zone_n": "crusher", "zone_s": "crusher", "zone_w": "waste_dump"}
    assert all(destinations[zone] == "crusher" for zone in ore_zones)


def test_locking_a_truck_pins_it_to_one_shovel() -> None:
    scenario = toy_mine().build()
    simulation = Simulation(scenario, overrides=Overrides(locked={"CAT01": "SH02"}))

    simulation.run(until_s=3600.0)

    assigned = {
        event.shovel_id
        for event in simulation.log.events
        if event.truck_id == "CAT01" and event.shovel_id is not None
    }
    assert assigned == {"SH02"}


def test_lp_plan_delivers_more_ore_than_fixed_targets() -> None:
    # Same fleet, same mine: the LP spends the truck hours on the valuable
    # material instead of on whichever shovel refills its shortfall fastest.
    # Over a full shift: an hour is barely a dozen cycles, too few for the split
    # to settle.
    scenario = toy_mine().build()
    lp_run = _lp_simulation(scenario).run(until_s=4 * 3600.0)

    static_run = Simulation(toy_mine().build()).run(until_s=4 * 3600.0)

    assert lp_run.tonnes_by_dump["crusher"] > static_run.tonnes_by_dump["crusher"]


def test_lp_plan_keeps_the_crusher_blend_inside_its_window() -> None:
    scenario = toy_mine().build()
    best_path = BestPath(scenario.mine.network)
    plan = solve_scenario_plan(scenario, best_path)

    ore = {"SH01": 0.9, "SH02": 0.5}
    rates = {shovel_id: plan.required_rate_tph(shovel_id) for shovel_id in ore}
    blended = sum(rates[s] * grade for s, grade in ore.items()) / sum(rates.values())

    assert 0.6 - 1e-9 <= blended <= 0.8 + 1e-9


def test_planned_destinations_keep_the_crusher_fed_and_on_grade() -> None:
    scenario = toy_mine_with_stockpile().build()

    kpis = _lp_simulation(scenario).run(until_s=4 * 3600.0)

    crusher_t = kpis.tonnes_by_dump["crusher"]
    delivered_grade = (
        sum(
            tonnes * scenario.mine.load_zones[zone_id].material.grades["cu"]
            for (zone_id, dump_id), tonnes in kpis.tonnes_by_route.items()
            if dump_id == "crusher"
        )
        / crusher_t
    )

    assert crusher_t > 0
    assert kpis.tonnes_by_dump["stockpile"] > 0
    assert 0.6 <= delivered_grade <= 0.8


def test_without_a_route_plan_every_tonne_takes_the_shortest_haul() -> None:
    # The stockpile is the closer ore destination, so the fallback starves the
    # plant entirely. This is what choosing destinations by proximity costs.
    kpis = Simulation(toy_mine_with_stockpile().build()).run(until_s=4 * 3600.0)

    assert kpis.tonnes_by_dump.get("crusher", 0.0) == 0.0
    assert kpis.tonnes_by_dump["stockpile"] > 0


def test_following_the_plan_beats_the_myopic_baseline_on_value() -> None:
    # Same mine, same fleet, same destination rule: only the shovel decision
    # differs. The baseline queues less and still earns less, which is the whole
    # argument for the two-stage engine.
    scenario = toy_mine_with_stockpile().build()
    best_path = BestPath(scenario.mine.network)
    provider = partial(solve_scenario_plan, scenario, best_path)

    def run(policy_class: type) -> Kpis:
        return Simulation(
            scenario,
            lambda plan: policy_class(best_path=best_path, plan=plan),
            plan_provider=provider,
            best_path=best_path,
        ).run(until_s=4 * 3600.0)

    follows_plan = run(NeediestShovelPolicy)
    myopic = run(EarliestShovelPolicy)

    assert _value(scenario, follows_plan) > _value(scenario, myopic)
    assert myopic.truck_queue_time_s < follows_plan.truck_queue_time_s


def _value(scenario: Scenario, kpis: Kpis) -> float:
    shovel_by_zone = {shovel.zone: shovel_id for shovel_id, shovel in scenario.shovels.items()}
    inputs = scenario.plan_inputs
    return sum(
        tonnes
        * inputs.values_per_tonne.get(shovel_by_zone[zone_id], 1.0)
        * inputs.dump_values_per_tonne.get(dump_id, 1.0)
        for (zone_id, dump_id), tonnes in kpis.tonnes_by_route.items()
    )


def test_plan_stops_feeding_the_crusher_when_the_blend_cannot_be_met() -> None:
    # The window needs both ore benches; with the high grade one out, no mix of
    # what is left lands inside it, so the plan moves waste instead.
    scenario = toy_mine().build()
    plan = solve_scenario_plan(
        scenario,
        BestPath(scenario.mine.network),
        PlanConditions(unavailable_shovels=frozenset({"SH01"})),
    )

    assert plan.rate_by_dump_tph("crusher") == pytest.approx(0.0)
    assert plan.required_rate_tph("SH03") > 0


def test_outage_replans_when_the_shovel_goes_down_and_comes_back() -> None:
    simulation = _lp_simulation(toy_mine_with_failure().build())

    kpis = simulation.run(until_s=7200.0)

    assert kpis.replans == 2
    assert kpis.tonnes_by_dump["waste_dump"] > kpis.tonnes_by_dump["crusher"]


def test_no_truck_is_served_by_a_shovel_while_it_is_out_of_service() -> None:
    simulation = _lp_simulation(toy_mine_with_failure().build())
    simulation.run(until_s=7200.0)

    down_at = _first_time(simulation, EventKind.SHOVEL_DOWN)
    up_at = _first_time(simulation, EventKind.SHOVEL_UP)
    served = [
        event
        for event in simulation.log.events
        if event.kind is EventKind.SPOT_START
        and event.shovel_id == "SH01"
        and down_at <= event.time_s < up_at
    ]

    assert up_at > down_at
    assert served == []


def test_truck_already_on_its_way_is_redispatched() -> None:
    simulation = _lp_simulation(toy_mine_with_failure().build())

    kpis = simulation.run(until_s=7200.0)

    assert kpis.reassignments >= 1
    assert kpis.standby_events == 0


def _first_time(simulation: Simulation, kind: EventKind) -> float:
    return next(event.time_s for event in simulation.log.events if event.kind is kind)


def test_scenario_rejects_a_disruption_on_an_unknown_shovel() -> None:
    spec = toy_mine().model_dump()
    spec["disruptions"] = [{"shovel": "SH99", "start_min": 10, "duration_min": 5}]

    with pytest.raises(ValidationError, match="unknown shovel"):
        ScenarioSpec.model_validate(spec)


def test_scenario_rejects_a_blend_target_without_limits() -> None:
    spec = toy_mine().model_dump()
    spec["blend_targets"] = [{"dump_zone": "crusher", "element": "cu"}]

    with pytest.raises(ValidationError, match="neither a floor nor a ceiling"):
        ScenarioSpec.model_validate(spec)


def test_scenario_rejects_a_zone_on_an_unknown_node() -> None:
    spec = toy_mine().model_dump()
    spec["load_zones"].append(
        LoadZoneSpec(id="zone_x", node="nowhere", material=MaterialSpec(name="ore_x")).model_dump()
    )

    with pytest.raises(ValidationError, match="unknown node"):
        ScenarioSpec.model_validate(spec)


def test_scenario_rejects_material_without_a_destination() -> None:
    spec = toy_mine().model_dump()
    spec["dump_zones"] = [dump for dump in spec["dump_zones"] if dump["accepts_ore"]]

    with pytest.raises(ValidationError, match="no dump zone"):
        ScenarioSpec.model_validate(spec)


def test_edge_spec_rejects_non_positive_length() -> None:
    with pytest.raises(ValidationError):
        EdgeSpec(source="a", target="b", length_m=0)
