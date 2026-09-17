from __future__ import annotations

import pytest
from dispatch_engine.domain.snapshot import Overrides
from mine_sim.scenario import EdgeSpec, LoadZoneSpec, MaterialSpec, ScenarioSpec, toy_mine
from mine_sim.simulation import Simulation
from pydantic import ValidationError


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
