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
class TruckRestriction:
    """An operational restriction on a truck, as in US 11,187,547.

    A truck with a failing engine, a cracked tray or a bad transmission is not
    out of service: it is usable on worse terms. Excluding it is the hammer, and
    it is the only tool the engine had before this. The patent describes where
    each restriction enters the assignment procedure, and that is where each one
    is applied here: load reduction changes the assigned haulage values, speed
    reduction changes the projected arrival times, and a short-haul restriction
    changes membership of the candidate list Tc(s).
    """

    # Only dispatch to shovels whose haul is short — SH_PARAM in the patent, a
    # fraction of the longest haul on offer.
    short_hauls_only: bool = False
    # Below one, the truck travels slower than the road would allow.
    speed_factor: float = 1.0
    # Below one, the truck is loaded below its rated payload.
    load_factor: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 < self.speed_factor <= 1.0:
            raise ValueError("speed_factor must be in (0, 1]")
        if not 0.0 < self.load_factor <= 1.0:
            raise ValueError("load_factor must be in (0, 1]")


NO_RESTRICTION = TruckRestriction()


@dataclass(frozen=True, slots=True)
class Overrides:
    """Manual dispatcher intervention layered on top of the algorithm."""

    locked: dict[TruckId, ShovelId] = field(default_factory=dict)
    excluded_trucks: frozenset[TruckId] = frozenset()
    excluded_shovels: frozenset[ShovelId] = frozenset()
    # Trucks kept in service on worse terms rather than parked.
    restrictions: dict[TruckId, TruckRestriction] = field(default_factory=dict)

    def restriction(self, truck_id: TruckId) -> TruckRestriction:
        return self.restrictions.get(truck_id, NO_RESTRICTION)


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
    # When each shovel was last sent a truck. Absent means it has not had one.
    last_dispatch_s: dict[ShovelId, float] = field(default_factory=dict)
    # Tonnes the plan expected this shovel to have dug by now, minus what it
    # actually dug. Empty means nobody is keeping the ledger, and a policy that
    # reads it falls back to comparing stocks alone.
    plan_shortfall_t: dict[ShovelId, float] = field(default_factory=dict)

    def waiting_since_s(self, shovel_id: ShovelId) -> float:
        """How long the shovel has gone without being sent a truck."""
        return self.now_s - self.last_dispatch_s.get(shovel_id, 0.0)

    def committed_to_route(self, load_zone_id: ZoneId, dump_zone_id: ZoneId) -> float:
        """Tonnes tipped on this route so far plus those in flight towards it.

        Unlike a shovel's committed haulage, which only looks at what is coming
        right now, a destination split is judged over the whole shift: the blend
        a plant receives is a running average, not an instantaneous one.
        """
        in_flight_t = sum(
            self.effective_payload_t(status.truck.id)
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

    def effective_payload_t(self, truck_id: TruckId) -> float:
        """What the truck will actually deliver, after any load reduction.

        Every haulage value is counted with this rather than the rated payload,
        which is the point of the restriction: a truck coming in at 60 % of its
        tray does not fill 100 % of a shovel's need.
        """
        return (
            self.trucks[truck_id].truck.payload_t * self.overrides.restriction(truck_id).load_factor
        )

    def assigned_haulage_t(self, shovel_id: ShovelId) -> float:
        return sum(self.effective_payload_t(t.truck.id) for t in self.arrivals(shovel_id))

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
