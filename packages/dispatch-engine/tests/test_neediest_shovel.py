from __future__ import annotations

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import CycleState, Shovel, ShovelId, StatusCode, Truck
from dispatch_engine.domain.mine import DumpZone, Edge, LoadZone, Material, Mine, RoadNetwork
from dispatch_engine.domain.snapshot import MineSnapshot, Overrides, ShovelStatus, TruckStatus
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.production_plan import StaticProductionPlan

ORE = Material(name="ore", is_ore=True)


def _mine() -> Mine:
    # zone_a is 100 s from the dump, zone_b is 200 s away, at 36 kph = 10 m/s.
    edges = tuple(
        Edge(source=source, target=target, length_m=length_m, speed_limit_kph=36)
        for source, target, length_m in (
            ("dump", "zone_a", 1000),
            ("zone_a", "dump", 1000),
            ("dump", "zone_b", 2000),
            ("zone_b", "dump", 2000),
        )
    )
    return Mine(
        network=RoadNetwork(edges=edges),
        load_zones={
            "zone_a": LoadZone(id="zone_a", node="zone_a", material=ORE),
            "zone_b": LoadZone(id="zone_b", node="zone_b", material=ORE),
        },
        dump_zones={"dump": DumpZone(id="dump", node="dump", accepts_ore=True)},
    )


def _policy(targets_tph: dict[ShovelId, float], **kwargs: float) -> NeediestShovelPolicy:
    return NeediestShovelPolicy(
        best_path=BestPath(_mine().network),
        plan=StaticProductionPlan(targets_tph=targets_tph),
        **kwargs,
    )


def _idle_truck(truck_id: str, *, free_in_s: float = 0.0) -> TruckStatus:
    return TruckStatus(
        truck=Truck(id=truck_id, payload_t=220),
        state=CycleState.DUMPING,
        status=StatusCode.OPERATING,
        free_at_node="dump",
        free_in_s=free_in_s,
    )


def _snapshot(
    trucks: list[TruckStatus],
    *,
    priorities: dict[ShovelId, int] | None = None,
    statuses: dict[ShovelId, StatusCode] | None = None,
    overrides: Overrides | None = None,
) -> MineSnapshot:
    priorities = priorities or {}
    statuses = statuses or {}
    return MineSnapshot(
        now_s=0.0,
        mine=_mine(),
        shovels={
            shovel_id: ShovelStatus(
                shovel=Shovel(
                    id=shovel_id,
                    zone=zone_id,
                    load_rate_tph=3000,
                    priority=priorities.get(shovel_id, 0),
                ),
                status=statuses.get(shovel_id, StatusCode.OPERATING),
            )
            for shovel_id, zone_id in (("SH_A", "zone_a"), ("SH_B", "zone_b"))
        },
        trucks={status.truck.id: status for status in trucks},
        overrides=overrides if overrides is not None else Overrides(),
    )


def test_need_beats_proximity() -> None:
    policy = _policy({"SH_A": 0.0, "SH_B": 2000.0})

    assignment = policy.assign(_snapshot([_idle_truck("T1")]), "T1")

    assert assignment is not None
    assert assignment.shovel_id == "SH_B"
    assert assignment.route.nodes == ("dump", "zone_b")


def test_equal_need_breaks_on_shovel_priority() -> None:
    policy = _policy({"SH_A": 1000.0, "SH_B": 1000.0})

    assignment = policy.assign(
        _snapshot([_idle_truck("T1")], priorities={"SH_B": 5}),
        "T1",
    )

    assert assignment is not None
    assert assignment.shovel_id == "SH_B"


def test_committed_haulage_reduces_need() -> None:
    policy = _policy({"SH_A": 1000.0, "SH_B": 1000.0})
    heading_to_b = TruckStatus(
        truck=Truck(id="T2", payload_t=220),
        state=CycleState.TRAVEL_EMPTY,
        status=StatusCode.OPERATING,
        free_at_node="zone_b",
        free_in_s=0.0,
        assigned_shovel="SH_B",
        eta_to_shovel_s=120.0,
    )

    assignment = policy.assign(_snapshot([_idle_truck("T1"), heading_to_b]), "T1")

    assert assignment is not None
    assert assignment.shovel_id == "SH_A"


def test_lookahead_leaves_the_neediest_shovel_to_the_better_truck() -> None:
    # SH_B is neediest, but T1 gets there sooner, so T2 is pushed to SH_A.
    policy = _policy({"SH_A": 1000.0, "SH_B": 1200.0})
    snapshot = _snapshot([_idle_truck("T1"), _idle_truck("T2", free_in_s=600.0)])

    assert policy.assign(snapshot, "T1").shovel_id == "SH_B"
    assert policy.assign(snapshot, "T2").shovel_id == "SH_A"


def test_dispatcher_lock_overrides_the_algorithm() -> None:
    policy = _policy({"SH_A": 0.0, "SH_B": 2000.0})
    snapshot = _snapshot([_idle_truck("T1")], overrides=Overrides(locked={"T1": "SH_A"}))

    assignment = policy.assign(snapshot, "T1")

    assert assignment is not None
    assert assignment.shovel_id == "SH_A"
    assert assignment.reason == "locked by dispatcher"


def test_excluded_shovel_is_never_assigned() -> None:
    policy = _policy({"SH_A": 0.0, "SH_B": 2000.0})
    snapshot = _snapshot(
        [_idle_truck("T1")], overrides=Overrides(excluded_shovels=frozenset({"SH_B"}))
    )

    assignment = policy.assign(snapshot, "T1")

    assert assignment is not None
    assert assignment.shovel_id == "SH_A"


def test_excluded_truck_gets_no_assignment() -> None:
    policy = _policy({"SH_A": 1000.0, "SH_B": 1000.0})
    snapshot = _snapshot(
        [_idle_truck("T1")], overrides=Overrides(excluded_trucks=frozenset({"T1"}))
    )

    assert policy.assign(snapshot, "T1") is None


def test_shovel_out_of_service_gets_no_assignment() -> None:
    policy = _policy({"SH_A": 0.0, "SH_B": 2000.0})
    snapshot = _snapshot([_idle_truck("T1")], statuses={"SH_B": StatusCode.DOWN})

    assignment = policy.assign(snapshot, "T1")

    assert assignment is not None
    assert assignment.shovel_id == "SH_A"


def test_no_assignment_when_every_shovel_is_out_of_service() -> None:
    policy = _policy({"SH_A": 1000.0, "SH_B": 1000.0})
    snapshot = _snapshot(
        [_idle_truck("T1")],
        statuses={"SH_A": StatusCode.DOWN, "SH_B": StatusCode.DELAY},
    )

    assert policy.assign(snapshot, "T1") is None


def test_shovels_above_plan_still_receive_a_truck_that_needs_work() -> None:
    policy = _policy({"SH_A": 0.0, "SH_B": 0.0})

    assignment = policy.assign(_snapshot([_idle_truck("T1")]), "T1")

    assert assignment is not None
    assert assignment.shovel_id in {"SH_A", "SH_B"}
