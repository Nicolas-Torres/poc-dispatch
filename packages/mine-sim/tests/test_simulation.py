from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.snapshot import Overrides
from mine_sim.events import EventKind
from mine_sim.planning import solve_scenario_plan
from mine_sim.scenario import (
    EdgeSpec,
    LoadZoneSpec,
    MaterialSpec,
    Scenario,
    ScenarioSpec,
    toy_mine,
    toy_mine_with_failure,
)
from mine_sim.simulation import Simulation
from pydantic import ValidationError


def _lp_simulation(scenario: Scenario) -> Simulation:
    best_path = BestPath(scenario.mine.network)
    return Simulation(
        scenario,
        plan_provider=lambda unavailable: solve_scenario_plan(
            scenario, best_path, unavailable=unavailable
        ),
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


def test_ore_and_waste_reach_their_own_destinations() -> None:
    kpis = Simulation(toy_mine().build()).run(until_s=3600.0)

    ore_shovels = {"SH01", "SH02"}
    ore_tonnes = sum(shovel.tonnes for shovel in kpis.shovels if shovel.shovel_id in ore_shovels)

    assert kpis.tonnes_by_dump["crusher"] == pytest.approx(ore_tonnes)


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
    scenario = toy_mine().build()
    lp_run = _lp_simulation(scenario).run(until_s=3600.0)

    static_run = Simulation(toy_mine().build()).run(until_s=3600.0)

    assert lp_run.tonnes_by_dump["crusher"] > static_run.tonnes_by_dump["crusher"]


def test_lp_plan_keeps_the_crusher_blend_inside_its_window() -> None:
    scenario = toy_mine().build()
    best_path = BestPath(scenario.mine.network)
    plan = solve_scenario_plan(scenario, best_path)

    ore = {"SH01": 0.9, "SH02": 0.5}
    rates = {shovel_id: plan.required_rate_tph(shovel_id) for shovel_id in ore}
    blended = sum(rates[s] * grade for s, grade in ore.items()) / sum(rates.values())

    assert 0.6 - 1e-9 <= blended <= 0.8 + 1e-9


def test_plan_stops_feeding_the_crusher_when_the_blend_cannot_be_met() -> None:
    # The window needs both ore benches; with the high grade one out, no mix of
    # what is left lands inside it, so the plan moves waste instead.
    scenario = toy_mine().build()
    plan = solve_scenario_plan(
        scenario, BestPath(scenario.mine.network), unavailable=frozenset({"SH01"})
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
