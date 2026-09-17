"""Two defects found by measuring, not by reading: the twin let a destination
swallow more than its rated capacity, and the assignment tracked the plan as a
stock with no integral term, so a shortfall drifted without bound."""

from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath
from mine_sim.events import EventKind
from mine_sim.planning import solve_scenario_plan
from mine_sim.scenario import Scenario, toy_mine, toy_mine_with_failure
from mine_sim.simulation import Simulation


def _run(scenario: Scenario, hours: float) -> tuple[Simulation, object]:
    best_path = BestPath(scenario.mine.network)
    simulation = Simulation(
        scenario,
        plan_provider=lambda conditions: solve_scenario_plan(scenario, best_path, conditions),
        best_path=best_path,
    )
    return simulation, simulation.run(until_s=hours * 3600.0)


def _adherence(simulation: Simulation, kpis, hours: float) -> dict[str, float]:
    return {
        shovel.shovel_id: shovel.tonnes
        / hours
        / simulation.plan.required_rate_tph(shovel.shovel_id)
        for shovel in kpis.shovels
        if simulation.plan.required_rate_tph(shovel.shovel_id) > 0
    }


# --------------------------------------------------------------- intake limit


def test_a_destination_never_takes_more_than_its_rated_capacity() -> None:
    """The crusher is rated at 2,200 t/h; two bays at a minute a tip is 26,400."""
    scenario = toy_mine().build()
    hours = 8.0
    _, kpis = _run(scenario, hours)

    crusher = scenario.mine.dump_zones["crusher"]
    assert crusher.capacity_tph is not None
    delivered_tph = kpis.tonnes_by_dump.get("crusher", 0.0) / hours
    assert delivered_tph <= crusher.capacity_tph + 1e-6


def test_an_uncapped_destination_is_not_throttled() -> None:
    """The waste dump declares no capacity, so nothing should gate it."""
    scenario = toy_mine().build()
    assert scenario.mine.dump_zones["waste_dump"].capacity_tph is None
    _, kpis = _run(scenario, 4.0)
    assert kpis.tonnes_by_dump.get("waste_dump", 0.0) > 0


def test_a_capped_destination_makes_trucks_queue_to_tip() -> None:
    """Queueing at the destination is the observable the cap exists to produce."""
    _, kpis = _run(toy_mine().build(), 4.0)
    assert kpis.dump_queue_time_s > 0.0


# ------------------------------------------------------------ integral action


@pytest.mark.parametrize("hours", [4.0, 8.0, 24.0])
def test_no_shovel_is_starved_however_long_the_shift_runs(hours: float) -> None:
    """The drift used to compound: one shovel fell to 42% of plan over 24 h.

    Comparing only trucks-in-flight against trucks-required is proportional
    control on a stock. A chronic shortfall never accumulated pressure, so it
    grew without bound. This is the regression test for the integral term.
    """
    simulation, kpis = _run(toy_mine().build(), hours)
    worst = min(_adherence(simulation, kpis, hours).values())
    assert worst > 0.80, f"a shovel fell to {worst:.0%} of plan over {hours:g} h"


def test_adherence_does_not_decay_as_the_shift_lengthens() -> None:
    """The signature of the bug was monotonic decay, not a constant offset."""
    short_sim, short_kpis = _run(toy_mine().build(), 4.0)
    long_sim, long_kpis = _run(toy_mine().build(), 24.0)

    short_worst = min(_adherence(short_sim, short_kpis, 4.0).values())
    long_worst = min(_adherence(long_sim, long_kpis, 24.0).values())
    assert long_worst > short_worst - 0.10


def test_an_outage_does_not_build_a_debt_the_shovel_must_repay() -> None:
    """A shovel out of service has a required rate of zero, so it accrues nothing.

    Without that, the ledger would bill it for the whole outage and the fleet
    would pile onto it the moment it came back.
    """
    scenario = toy_mine_with_failure().build()
    simulation, _ = _run(scenario, 4.0)

    down_at = next(
        event.time_s for event in simulation.log.events if event.kind is EventKind.SHOVEL_DOWN
    )
    up_at = next(
        event.time_s for event in simulation.log.events if event.kind is EventKind.SHOVEL_UP
    )
    outage_h = (up_at - down_at) / 3600.0
    assert outage_h > 0.5, "the toy failure scenario stops SH01 for 40 minutes"

    # Its plan debt must be far below what a full-rate accrual over the outage
    # would have produced, or the ledger kept charging a shovel that was down.
    shortfall = simulation._snapshot().plan_shortfall_t["SH01"]
    assert shortfall < simulation.plan.required_rate_tph("SH01") * outage_h
