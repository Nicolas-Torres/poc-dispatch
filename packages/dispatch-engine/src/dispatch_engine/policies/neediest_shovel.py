from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.assignment import Assignment, Destination
from dispatch_engine.domain.equipment import Shovel, ShovelId, TruckId
from dispatch_engine.domain.mine import ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot, TruckStatus
from dispatch_engine.policies.common import (
    arrival_at_shovel_s,
    plan_tracking_destination,
    shovel_free_at_s,
)
from dispatch_engine.production_plan import ProductionPlan


@dataclass(frozen=True, slots=True)
class NeediestShovelPolicy:
    """Reduced form of the empty-truck assignment procedure of US 11,187,547.

    Keeps the shape of the patented procedure — rank shovels by how far behind
    plan they are, pick the lowest-penalty truck for the neediest one, update a
    virtual ledger and repeat until the requesting truck has a destination — but
    leaves out the operational restrictions (short hauls, speed and load
    restrictions).
    """

    best_path: BestPath
    plan: ProductionPlan
    # Buys shovel idle time with truck queueing. Only worth raising when the
    # shovels are the binding constraint: where the fleet is the one short, their
    # idle time is not caused by dispatching and raising this just piles trucks
    # into queues at the busiest shovel for no gain.
    shovel_idle_weight: float = 1.0

    def assign(self, snapshot: MineSnapshot, truck_id: TruckId) -> Assignment | None:
        locked_to = snapshot.overrides.locked.get(truck_id)
        if locked_to is not None:
            shovel = snapshot.shovels[locked_to].shovel
            return self._build(
                snapshot, snapshot.trucks[truck_id], shovel, "locked by dispatcher", 0.0
            )

        pending = {status.truck.id for status in snapshot.trucks_needing_assignment()}
        if truck_id not in pending:
            return None

        committed_t = {sid: snapshot.assigned_haulage_t(sid) for sid in snapshot.shovels}
        free_at_s = {sid: shovel_free_at_s(snapshot, sid) for sid in snapshot.shovels}
        skipped: set[ShovelId] = set()

        while True:
            ranked = self._rank_by_need(snapshot, committed_t, skipped)
            if not ranked:
                return None
            shovel, need_t = ranked[0]

            candidates = [
                status
                for status in snapshot.candidates_for(shovel.id)
                if status.truck.id in pending
            ]
            if not candidates:
                skipped.add(shovel.id)
                continue

            chosen, penalty_s, arrival_s = min(
                (
                    (status, *self._penalty(snapshot, status, shovel, free_at_s[shovel.id]))
                    for status in candidates
                ),
                key=lambda option: option[1],
            )
            if chosen.truck.id == truck_id:
                reason = f"neediest shovel, {need_t:.0f} t behind plan"
                return self._build(snapshot, chosen, shovel, reason, penalty_s)

            # Tentatively assign the other truck and carry on, as the procedure
            # does: only the requesting truck's assignment is ever confirmed.
            committed_t[shovel.id] += chosen.truck.payload_t
            free_at_s[shovel.id] = max(free_at_s[shovel.id], arrival_s) + shovel.load_time_s(
                chosen.truck
            )
            pending.discard(chosen.truck.id)

    def choose_destination(
        self, snapshot: MineSnapshot, truck_id: TruckId, load_zone_id: ZoneId
    ) -> Destination:
        return plan_tracking_destination(
            self.best_path, self.plan, snapshot, truck_id, load_zone_id
        )

    def _rank_by_need(
        self,
        snapshot: MineSnapshot,
        committed_t: dict[ShovelId, float],
        skipped: set[ShovelId],
    ) -> list[tuple[Shovel, float]]:
        needs = [
            (status.shovel, self.plan.required_haulage_t(sid) - committed_t[sid])
            for sid, status in snapshot.available_shovels().items()
            if sid not in skipped
        ]
        # Shovels above plan stay in the ranking with a negative need: a truck
        # asking for work still has to go somewhere.
        needs.sort(key=lambda entry: (-entry[1], -entry[0].priority, entry[0].id))
        return needs

    def _penalty(
        self,
        snapshot: MineSnapshot,
        status: TruckStatus,
        shovel: Shovel,
        free_at_s: float,
    ) -> tuple[float, float]:
        """Total idle time this pairing would cause, and the truck's arrival time."""
        arrival_s = arrival_at_shovel_s(self.best_path, snapshot, status, shovel)
        shovel_idle_s = max(0.0, arrival_s - free_at_s)
        truck_queue_s = max(0.0, free_at_s - arrival_s)
        return self.shovel_idle_weight * shovel_idle_s + truck_queue_s, arrival_s

    def _build(
        self,
        snapshot: MineSnapshot,
        status: TruckStatus,
        shovel: Shovel,
        reason: str,
        penalty_s: float,
    ) -> Assignment:
        zone = snapshot.mine.load_zones[shovel.zone]
        route = self.best_path.route(status.free_at_node, zone.node, loaded=False)
        return Assignment(
            truck_id=status.truck.id,
            shovel_id=shovel.id,
            zone_id=zone.id,
            route=route,
            reason=reason,
            penalty_s=penalty_s,
        )
