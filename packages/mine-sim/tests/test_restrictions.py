"""Operational restrictions from US 11,187,547.

A truck with a failing engine, a cracked tray or a bad transmission is not out
of service: it is usable on worse terms. Before this the engine could only
exclude it, which is the hammer.
"""

from __future__ import annotations

from itertools import pairwise

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.snapshot import Overrides, TruckRestriction
from mine_sim.events import EventKind
from mine_sim.planning import solve_scenario_plan
from mine_sim.scenario import (
    ScenarioSpec,
    dump_scenario_spec,
    load_scenario_spec,
    toy_mine,
    toy_mine_restricted,
)
from mine_sim.simulation import Simulation

TRUCK = "CAT01"
HOURS = 8.0


def _run(overrides: Overrides | None = None, hours: float = HOURS):
    scenario = toy_mine().build()
    best_path = BestPath(scenario.mine.network)
    simulation = Simulation(
        scenario,
        plan_provider=lambda conditions: solve_scenario_plan(scenario, best_path, conditions),
        best_path=best_path,
        overrides=overrides,
    )
    return simulation, simulation.run(until_s=hours * 3600.0)


def _cycle_minutes(simulation: Simulation, truck_id: str) -> float:
    """Mean time between this truck's successive loads."""
    times = [
        event.time_s
        for event in simulation.log.events
        if event.kind is EventKind.LOAD_END and event.truck_id == truck_id
    ]
    gaps = [b - a for a, b in pairwise(times)]
    return sum(gaps) / len(gaps) / 60.0 if gaps else 0.0


# ------------------------------------------------------- the justification


def test_a_restricted_truck_earns_more_than_a_parked_one() -> None:
    """The whole reason the feature exists: excluding it is the hammer."""
    _, healthy = _run()
    _, excluded = _run(Overrides(excluded_trucks=frozenset({TRUCK})))
    _, restricted = _run(Overrides(restrictions={TRUCK: TruckRestriction(load_factor=0.6)}))

    assert excluded.tonnes_total < restricted.tonnes_total < healthy.tonnes_total
    # Keeping it on worse terms has to be worth a real margin, not a rounding.
    assert restricted.tonnes_total > excluded.tonnes_total * 1.05


# ------------------------------------------------------------- no regression


def test_a_neutral_restriction_changes_nothing() -> None:
    """Every default is the unrestricted value, so declaring one is a no-op."""
    _, plain = _run()
    _, neutral = _run(Overrides(restrictions={TRUCK: TruckRestriction()}))
    assert neutral.tonnes_total == plain.tonnes_total
    assert neutral.cycles == plain.cycles


@pytest.mark.parametrize("field,value", [("speed_factor", 0.0), ("load_factor", 1.5)])
def test_an_impossible_restriction_is_refused(field: str, value: float) -> None:
    with pytest.raises(ValueError):
        TruckRestriction(**{field: value})


# ---------------------------------------------------------- load reduction


def test_load_reduction_moves_less_rock_on_that_truck_only() -> None:
    simulation, _ = _run(Overrides(restrictions={TRUCK: TruckRestriction(load_factor=0.6)}))
    loads = {
        truck: [
            event.payload_t
            for event in simulation.log.events
            if event.kind is EventKind.LOAD_END and event.truck_id == truck
        ]
        for truck in (TRUCK, "CAT02")
    }
    assert loads[TRUCK] and loads["CAT02"]
    assert all(payload == pytest.approx(220 * 0.6) for payload in loads[TRUCK])
    assert all(payload == pytest.approx(220) for payload in loads["CAT02"])


def test_the_engine_counts_the_reduced_tray_not_the_rated_one() -> None:
    """Assigned haulage is what the patent says the restriction alters."""
    simulation, _ = _run(Overrides(restrictions={TRUCK: TruckRestriction(load_factor=0.5)}))
    snapshot = simulation._snapshot()
    assert snapshot.effective_payload_t(TRUCK) == pytest.approx(110.0)
    assert snapshot.effective_payload_t("CAT02") == pytest.approx(220.0)


# --------------------------------------------------------- speed reduction


def test_speed_reduction_lengthens_that_truck_s_cycle_only() -> None:
    simulation, _ = _run(Overrides(restrictions={TRUCK: TruckRestriction(speed_factor=0.5)}))
    baseline, _ = _run()

    assert _cycle_minutes(simulation, TRUCK) > _cycle_minutes(baseline, TRUCK) * 1.2
    assert _cycle_minutes(simulation, "CAT02") < _cycle_minutes(simulation, TRUCK)


# ------------------------------------------------------------- short hauls


def test_a_short_haul_truck_is_never_sent_on_a_long_one() -> None:
    """A constraint, not a degradation: the engine must enforce it."""
    scenario = toy_mine().build()
    best_path = BestPath(scenario.mine.network)
    simulation, _ = _run(Overrides(restrictions={TRUCK: TruckRestriction(short_hauls_only=True)}))

    hauls_s = {
        shovel_id: best_path.travel_time_s(
            scenario.mine.dump_zones["crusher"].node,
            scenario.mine.load_zones[shovel.zone].node,
            loaded=False,
        )
        for shovel_id, shovel in scenario.shovels.items()
    }
    longest = max(hauls_s.values())
    allowed = {
        shovel_id
        for shovel_id, haul_s in hauls_s.items()
        if haul_s <= max(0.5 * longest, min(hauls_s.values()))
    }
    assert allowed != set(hauls_s), "the toy mine must have a shovel out of reach to test this"

    sent_to = {
        event.shovel_id
        for event in simulation.log.events
        if event.kind is EventKind.ASSIGNED and event.truck_id == TRUCK
    }
    assert sent_to <= allowed, f"{TRUCK} was sent to {sent_to - allowed}"


def test_a_short_haul_truck_still_works() -> None:
    """A restriction limits a truck; it must not strand it."""
    restriction = TruckRestriction(short_hauls_only=True)
    simulation, kpis = _run(Overrides(restrictions={TRUCK: restriction}))
    loads = [
        event
        for event in simulation.log.events
        if event.kind is EventKind.LOAD_END and event.truck_id == TRUCK
    ]
    assert loads, "a short-haul truck must still be dispatched somewhere"
    assert kpis.standby_events == 0


# ------------------------------------------------ declared with the mine


def test_a_scenario_file_can_declare_the_dispatcher_s_intervention(tmp_path) -> None:
    """Before this, Overrides could only be reached by writing Python."""
    spec = toy_mine_restricted()
    built = spec.build()
    assert built.overrides.restriction("CAT01").load_factor == pytest.approx(0.6)
    assert built.overrides.restriction("CAT02").speed_factor == pytest.approx(0.7)
    assert built.overrides.restriction("CAT03").short_hauls_only
    # A truck nobody restricted comes back neutral rather than missing.
    assert built.overrides.restriction("CAT04").load_factor == pytest.approx(1.0)

    path = tmp_path / "restricted.yaml"
    dump_scenario_spec(spec, path)
    assert "short_hauls_only" in path.read_text(encoding="utf-8")
    assert load_scenario_spec(path).dispatcher == spec.dispatcher


def test_the_restricted_scenario_runs_without_stranding_anyone() -> None:
    _, kpis = _run(hours=8.0)  # healthy control
    scenario = toy_mine_restricted().build()
    best_path = BestPath(scenario.mine.network)
    restricted_sim = Simulation(
        scenario,
        plan_provider=lambda conditions: solve_scenario_plan(scenario, best_path, conditions),
        best_path=best_path,
    )
    restricted = restricted_sim.run(until_s=8.0 * 3600.0)

    # Half the fleet damaged costs production, but nobody sits idle and the
    # crusher still gets ore inside its window.
    assert restricted.tonnes_total < kpis.tonnes_total
    assert restricted.standby_events == 0
    hauled = {
        event.truck_id for event in restricted_sim.log.events if event.kind is EventKind.LOAD_END
    }
    assert {"CAT01", "CAT02", "CAT03"} <= hauled, "a restricted truck must still work"


def test_the_dispatcher_section_is_validated_against_the_fleet() -> None:
    spec = toy_mine().model_dump()
    spec["dispatcher"] = {"restrictions": [{"truck": "NOPE", "load_factor": 0.5}]}
    with pytest.raises(ValueError, match="unknown truck"):
        ScenarioSpec.model_validate(spec)

    spec["dispatcher"] = {"locked": {"CAT01": "SH99"}}
    with pytest.raises(ValueError, match="unknown shovel"):
        ScenarioSpec.model_validate(spec)
