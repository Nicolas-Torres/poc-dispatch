from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import CycleState, Shovel, StatusCode, Truck
from dispatch_engine.domain.mine import DumpZone, Edge, LoadZone, Material, Mine, RoadNetwork
from dispatch_engine.domain.snapshot import MineSnapshot, ShovelStatus, TruckStatus
from dispatch_engine.lp import LpProductionPlan, RouteFlow
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.production_plan import StaticProductionPlan

ORE = Material(name="ore", is_ore=True, grades={"cu": 0.8})
WASTE = Material(name="waste", is_ore=False)
PAYLOAD_T = 200.0


def _mine() -> Mine:
    # From the bench: the tip is 500 m away, the plant 2 km, at 36 kph = 10 m/s.
    edges = tuple(
        Edge(source=source, target=target, length_m=length_m, speed_limit_kph=36)
        for source, target, length_m in (
            ("bench", "tip", 500),
            ("tip", "bench", 500),
            ("bench", "plant", 2000),
            ("plant", "bench", 2000),
            ("bench", "waste_tip", 300),
            ("waste_tip", "bench", 300),
        )
    )
    return Mine(
        network=RoadNetwork(edges=edges),
        load_zones={"zone_a": LoadZone(id="zone_a", node="bench", material=ORE)},
        dump_zones={
            "plant": DumpZone(id="plant", node="plant", accepts_ore=True),
            "tip": DumpZone(id="tip", node="tip", accepts_ore=True),
            "waste_tip": DumpZone(id="waste_tip", node="waste_tip", accepts_ore=False),
        },
    )


def _snapshot(delivered_t: dict[tuple[str, str], float]) -> MineSnapshot:
    return MineSnapshot(
        now_s=0.0,
        mine=_mine(),
        shovels={
            "SH": ShovelStatus(
                shovel=Shovel(id="SH", zone="zone_a", load_rate_tph=3000),
                status=StatusCode.OPERATING,
            )
        },
        trucks={
            "T1": TruckStatus(
                truck=Truck(id="T1", payload_t=PAYLOAD_T),
                state=CycleState.LOADING,
                status=StatusCode.OPERATING,
                free_at_node="bench",
                free_in_s=0.0,
            )
        },
        delivered_t=delivered_t,
    )


def _policy(plan: LpProductionPlan | StaticProductionPlan) -> NeediestShovelPolicy:
    return NeediestShovelPolicy(best_path=BestPath(_mine().network), plan=plan)


def test_destinations_track_the_planned_split() -> None:
    # Three quarters of the zone's output is planned for the plant.
    policy = _policy(
        LpProductionPlan(
            flows=(
                RouteFlow("SH", "zone_a", "plant", "default", cycle_time_s=600, rate_tph=750),
                RouteFlow("SH", "zone_a", "tip", "default", cycle_time_s=300, rate_tph=250),
            )
        )
    )

    delivered_t: dict[tuple[str, str], float] = {}
    for _ in range(8):
        destination = policy.choose_destination(_snapshot(delivered_t), "T1", "zone_a")
        route = ("zone_a", destination.dump_zone_id)
        delivered_t[route] = delivered_t.get(route, 0.0) + PAYLOAD_T

    assert delivered_t[("zone_a", "plant")] == pytest.approx(6 * PAYLOAD_T)
    assert delivered_t[("zone_a", "tip")] == pytest.approx(2 * PAYLOAD_T)


def test_loads_in_flight_count_towards_the_split() -> None:
    policy = _policy(
        LpProductionPlan(
            flows=(
                RouteFlow("SH", "zone_a", "plant", "default", cycle_time_s=600, rate_tph=500),
                RouteFlow("SH", "zone_a", "tip", "default", cycle_time_s=300, rate_tph=500),
            )
        )
    )
    snapshot = _snapshot({})
    hauling_to_plant = TruckStatus(
        truck=Truck(id="T2", payload_t=PAYLOAD_T),
        state=CycleState.TRAVEL_LOADED,
        status=StatusCode.OPERATING,
        free_at_node="plant",
        free_in_s=300.0,
        origin_zone="zone_a",
        assigned_dump="plant",
    )
    snapshot.trucks["T2"] = hauling_to_plant

    # An even split with one load already on its way to the plant sends this one
    # to the tip, even though nothing has been tipped yet.
    assert policy.choose_destination(snapshot, "T1", "zone_a").dump_zone_id == "tip"


def test_falls_back_to_the_nearest_destination_without_a_route_plan() -> None:
    policy = _policy(StaticProductionPlan(targets_tph={"SH": 1000.0}))

    destination = policy.choose_destination(_snapshot({}), "T1", "zone_a")

    assert destination.dump_zone_id == "tip"
    assert destination.reason == "nearest destination"


def test_material_never_goes_to_a_destination_that_rejects_it() -> None:
    # waste_tip is the closest of all, and still out of the question for ore.
    policy = _policy(StaticProductionPlan(targets_tph={}))

    destination = policy.choose_destination(_snapshot({}), "T1", "zone_a")

    assert destination.dump_zone_id != "waste_tip"
