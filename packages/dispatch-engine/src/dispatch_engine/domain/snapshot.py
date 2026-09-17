from __future__ import annotations

from dataclasses import dataclass, field

from dispatch_engine.domain.equipment import (
    CycleState,
    Shovel,
    ShovelId,
    StatusCode,
    Truck,
    TruckId,
)
from dispatch_engine.domain.mine import Mine, NodeId


@dataclass(frozen=True, slots=True)
class TruckStatus:
    """A truck as the engine sees it at decision time.

    `free_at_node`/`free_in_s` describe where and when the truck next becomes
    available, which is what the assignment penalty needs — a truck hauling
    loaded is not at its current position for dispatch purposes, it is at the
    dump it is heading to.
    """

    truck: Truck
    state: CycleState
    status: StatusCode
    free_at_node: NodeId
    free_in_s: float
    assigned_shovel: ShovelId | None = None
    eta_to_shovel_s: float | None = None


@dataclass(frozen=True, slots=True)
class Overrides:
    """Manual dispatcher intervention layered on top of the algorithm."""

    locked: dict[TruckId, ShovelId] = field(default_factory=dict)
    excluded_trucks: frozenset[TruckId] = frozenset()
    excluded_shovels: frozenset[ShovelId] = frozenset()


@dataclass(frozen=True, slots=True)
class MineSnapshot:
    """Immutable view of the mine at the instant a truck asks for a destination.

    Mirrors the snapshot step of the assignment procedure in US 11,187,547:
    `arrivals` is Ta(s), `trucks_needing_assignment` is T' and `candidates_for`
    is Tc(s).
    """

    now_s: float
    mine: Mine
    shovels: dict[ShovelId, Shovel]
    trucks: dict[TruckId, TruckStatus]
    overrides: Overrides = field(default_factory=Overrides)

    def arrivals(self, shovel_id: ShovelId) -> list[TruckStatus]:
        """Ta(s): trucks at, heading to, or projected to be dispatched to the shovel."""
        return [t for t in self.trucks.values() if t.assigned_shovel == shovel_id]

    def assigned_haulage_t(self, shovel_id: ShovelId) -> float:
        return sum(t.truck.payload_t for t in self.arrivals(shovel_id))

    def trucks_needing_assignment(self) -> list[TruckStatus]:
        """T': trucks that need, or will soon need, a shovel assignment."""
        return [
            t
            for t in self.trucks.values()
            if t.assigned_shovel is None
            and t.status is StatusCode.OPERATING
            and t.truck.id not in self.overrides.excluded_trucks
        ]

    def candidates_for(self, shovel_id: ShovelId) -> list[TruckStatus]:
        """Tc(s): the subset of T' that may be dispatched to this shovel."""
        if shovel_id in self.overrides.excluded_shovels:
            return []
        return [
            t
            for t in self.trucks_needing_assignment()
            if self.overrides.locked.get(t.truck.id, shovel_id) == shovel_id
        ]
