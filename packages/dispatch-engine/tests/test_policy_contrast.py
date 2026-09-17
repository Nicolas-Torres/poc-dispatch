from __future__ import annotations

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import CycleState, Shovel, StatusCode, Truck
from dispatch_engine.domain.mine import DumpZone, Edge, LoadZone, Material, Mine, RoadNetwork
from dispatch_engine.domain.snapshot import MineSnapshot, Overrides, ShovelStatus, TruckStatus
from dispatch_engine.policies.earliest_shovel import EarliestShovelPolicy
from dispatch_engine.policies.longest_waiting_shovel import LongestWaitingShovelPolicy
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.production_plan import StaticProductionPlan

ORE = Material(name="ore", is_ore=True)


def _mine() -> Mine:
    # zone_near is 100 s from the dump, zone_far is 300 s, at 36 kph = 10 m/s.
    edges = tuple(
        Edge(source=source, target=target, length_m=length_m, speed_limit_kph=36)
        for source, target, length_m in (
            ("dump", "near", 1000),
            ("near", "dump", 1000),
            ("dump", "far", 3000),
            ("far", "dump", 3000),
        )
    )
    return Mine(
        network=RoadNetwork(edges=edges),
        load_zones={
            "zone_near": LoadZone(id="zone_near", node="near", material=ORE),
            "zone_far": LoadZone(id="zone_far", node="far", material=ORE),
        },
        dump_zones={"dump": DumpZone(id="dump", node="dump", accepts_ore=True)},
    )


def _snapshot(*, overrides: Overrides | None = None) -> MineSnapshot:
    return MineSnapshot(
        now_s=0.0,
        mine=_mine(),
        shovels={
            shovel_id: ShovelStatus(
                shovel=Shovel(id=shovel_id, zone=zone_id, load_rate_tph=3000),
                status=StatusCode.OPERATING,
            )
            for shovel_id, zone_id in (("SH_NEAR", "zone_near"), ("SH_FAR", "zone_far"))
        },
        trucks={
            "T1": TruckStatus(
                truck=Truck(id="T1", payload_t=220),
                state=CycleState.DUMPING,
                status=StatusCode.OPERATING,
                free_at_node="dump",
                free_in_s=0.0,
            )
        },
        overrides=overrides if overrides is not None else Overrides(),
    )


# Only the far shovel is behind plan; both are free and neither has a queue.
PLAN = StaticProductionPlan(targets_tph={"SH_NEAR": 0.0, "SH_FAR": 2000.0})


def test_the_two_policies_disagree_on_purpose() -> None:
    best_path = BestPath(_mine().network)
    snapshot = _snapshot()

    follows_plan = NeediestShovelPolicy(best_path=best_path, plan=PLAN)
    myopic = EarliestShovelPolicy(best_path=best_path, plan=PLAN)

    # The plan-following engine takes the longer trip because that shovel is the
    # one starving; the baseline just goes wherever it can load soonest.
    assert follows_plan.assign(snapshot, "T1").shovel_id == "SH_FAR"
    assert myopic.assign(snapshot, "T1").shovel_id == "SH_NEAR"


def test_the_even_baseline_goes_to_whichever_shovel_waited_longest() -> None:
    best_path = BestPath(_mine().network)
    # SH_NEAR is closer and SH_FAR is the one behind plan, but SH_NEAR is the one
    # that has gone without a truck.
    snapshot = MineSnapshot(
        now_s=3600.0,
        mine=_mine(),
        shovels=_snapshot().shovels,
        trucks=_snapshot().trucks,
        last_dispatch_s={"SH_NEAR": 600.0, "SH_FAR": 3000.0},
    )

    assert (
        LongestWaitingShovelPolicy(best_path=best_path, plan=PLAN).assign(snapshot, "T1").shovel_id
        == "SH_NEAR"
    )


def test_the_even_baseline_honours_dispatcher_overrides() -> None:
    policy = LongestWaitingShovelPolicy(best_path=BestPath(_mine().network), plan=PLAN)

    locked = policy.assign(_snapshot(overrides=Overrides(locked={"T1": "SH_FAR"})), "T1")
    nowhere = policy.assign(
        _snapshot(overrides=Overrides(excluded_shovels=frozenset({"SH_NEAR", "SH_FAR"}))), "T1"
    )

    assert locked.shovel_id == "SH_FAR"
    assert nowhere is None


def test_the_baseline_still_honours_dispatcher_overrides() -> None:
    policy = EarliestShovelPolicy(best_path=BestPath(_mine().network), plan=PLAN)

    locked = policy.assign(_snapshot(overrides=Overrides(locked={"T1": "SH_FAR"})), "T1")
    excluded = policy.assign(
        _snapshot(overrides=Overrides(excluded_shovels=frozenset({"SH_NEAR"}))), "T1"
    )

    assert locked.shovel_id == "SH_FAR"
    assert excluded.shovel_id == "SH_FAR"


def test_the_baseline_gives_up_when_no_shovel_can_take_the_truck() -> None:
    policy = EarliestShovelPolicy(best_path=BestPath(_mine().network), plan=PLAN)
    snapshot = _snapshot(overrides=Overrides(excluded_shovels=frozenset({"SH_NEAR", "SH_FAR"})))

    assert policy.assign(snapshot, "T1") is None
