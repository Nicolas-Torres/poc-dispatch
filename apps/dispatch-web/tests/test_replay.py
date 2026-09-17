"""The replay turns a finished run into something a browser can animate."""

from __future__ import annotations

import math
from itertools import pairwise

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_web import replay
from mine_sim.events import EventKind
from mine_sim.planning import solve_scenario_plan
from mine_sim.scenario import SCENARIOS
from mine_sim.simulation import Simulation

HOURS = 2.0


@pytest.fixture(scope="module")
def toy_run():
    return _run("toy", HOURS)


def _run(name: str, hours: float):
    built = SCENARIOS[name]().build()
    best_path = BestPath(built.mine.network)
    simulation = Simulation(
        built,
        lambda plan: NeediestShovelPolicy(best_path=best_path, plan=plan),
        plan_provider=lambda conditions: solve_scenario_plan(built, best_path, conditions),
        best_path=best_path,
        seed=1,
    )
    kpis = simulation.run(until_s=hours * 3600.0)
    payload = replay.build(built, simulation.log, best_path, hours * 3600.0)
    return built, simulation, payload, kpis


def test_every_movement_is_a_real_route_in_time_order(toy_run) -> None:
    _, _, payload, _ = toy_run
    places = {tuple(p) for p in payload["nodes"].values()}
    assert payload["movements"], "a two hour shift should move trucks"

    for move in payload["movements"]:
        assert move["t1"] > move["t0"], "a haul cannot end before it starts"
        assert len(move["path"]) >= 2, "a haul needs at least an origin and a target"
        # Every vertex must be a node of the network: the frontend interpolates
        # along these polylines, so an invented point puts a truck off-road.
        for point in move["path"]:
            assert tuple(point) in places


def test_trucks_are_never_teleported_between_consecutive_hauls(toy_run) -> None:
    """Each haul has to start where the previous one left the truck."""
    _, _, payload, _ = toy_run
    by_truck: dict[str, list[dict]] = {}
    for move in payload["movements"]:
        by_truck.setdefault(move["truck"], []).append(move)

    for truck, moves in by_truck.items():
        moves.sort(key=lambda m: m["t0"])
        for previous, following in pairwise(moves):
            gap = math.dist(previous["path"][-1], following["path"][0])
            assert gap < 1e-6, f"{truck} jumped {gap:.1f} m between hauls"


def test_the_state_track_covers_the_whole_cycle(toy_run) -> None:
    _, _, payload, _ = toy_run
    seen = {entry["state"] for entry in payload["states"]}
    assert {"hauling_empty", "loading", "hauling_loaded", "dumping"} <= seen


def test_states_are_sorted_so_the_viewer_can_walk_them_once(toy_run) -> None:
    _, _, payload, _ = toy_run
    times = [entry["t"] for entry in payload["states"]]
    assert times == sorted(times)


def test_a_shovel_outage_reaches_the_viewer(toy_run) -> None:
    """The failure demo exists to show SH01 stop, so the payload must say so."""
    built, simulation, _, _ = _run("toy-failure", HOURS)
    best_path = BestPath(built.mine.network)
    payload = replay.build(built, simulation.log, best_path, HOURS * 3600.0)

    outages = payload["shovel_states"]
    assert [o["state"] for o in outages] == ["down", "operating"]
    assert all(o["shovel"] == "SH01" for o in outages)
    # The scenario schedules the stop at minute 30 for 40 minutes.
    assert outages[0]["t"] == pytest.approx(30 * 60)
    assert outages[1]["t"] == pytest.approx(70 * 60)


def test_a_quiet_mine_reports_no_outages(toy_run) -> None:
    _, _, payload, _ = toy_run
    assert payload["shovel_states"] == []


def test_the_event_stream_matches_the_log(toy_run) -> None:
    """The dashboard accumulates KPIs from these events, so none may be lost."""
    _, simulation, payload, kpis = toy_run
    assert len(payload["events"]) == len(simulation.log.events)

    tipped = sum(e["payload_t"] for e in payload["events"] if e["kind"] == EventKind.DUMP_END)
    assert tipped == pytest.approx(kpis.tonnes_total)
