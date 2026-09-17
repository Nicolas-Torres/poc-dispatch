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
from dispatch_engine.domain.mine import Mine, NodeId, ZoneId


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
    # Set while the truck is hauling loaded: where the material came from and
    # where it is going, which is what a route's committed haulage is made of.
    origin_zone: ZoneId | None = None
    assigned_dump: ZoneId | None = None


@dataclass(frozen=True, slots=True)
class Overrides:
    """Manual dispatcher intervention layered on top of the algorithm."""

    locked: dict[TruckId, ShovelId] = field(default_factory=dict)
    excluded_trucks: frozenset[TruckId] = frozenset()
    excluded_shovels: frozenset[ShovelId] = frozenset()


@dataclass(frozen=True, slots=True)
class ShovelStatus:
    """A shovel and the status code it is reporting."""

    shovel: Shovel
    status: StatusCode = StatusCode.OPERATING

    @property
    def is_available(self) -> bool:
        return self.status is StatusCode.OPERATING


@dataclass(frozen=True, slots=True)
class MineSnapshot:
    """Immutable view of the mine at the instant a truck asks for a destination.

    Mirrors the snapshot step of the assignment procedure in US 11,187,547:
    `arrivals` is Ta(s), `trucks_needing_assignment` is T' and `candidates_for`
    is Tc(s).
    """

    now_s: float
    mine: Mine
    shovels: dict[ShovelId, ShovelStatus]
    trucks: dict[TruckId, TruckStatus]
    overrides: Overrides = field(default_factory=Overrides)
    # Tonnes already tipped on each (load zone, dump zone) route this shift.
    delivered_t: dict[tuple[ZoneId, ZoneId], float] = field(default_factory=dict)

    def committed_to_route(self, load_zone_id: ZoneId, dump_zone_id: ZoneId) -> float:
        """Tonnes tipped on this route so far plus those in flight towards it.

        Unlike a shovel's committed haulage, which only looks at what is coming
        right now, a destination split is judged over the whole shift: the blend
        a plant receives is a running average, not an instantaneous one.
        """
        in_flight_t = sum(
            status.truck.payload_t
            for status in self.trucks.values()
            if status.origin_zone == load_zone_id and status.assigned_dump == dump_zone_id
        )
        return self.delivered_t.get((load_zone_id, dump_zone_id), 0.0) + in_flight_t

    def available_shovels(self) -> dict[ShovelId, ShovelStatus]:
        """Shovels that can take a truck: operating and not excluded by hand."""
        return {
            shovel_id: status
            for shovel_id, status in self.shovels.items()
            if status.is_available and shovel_id not in self.overrides.excluded_shovels
        }

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
        if shovel_id not in self.available_shovels():
            return []
        return [
            t
            for t in self.trucks_needing_assignment()
            if self.overrides.locked.get(t.truck.id, shovel_id) == shovel_id
        ]
