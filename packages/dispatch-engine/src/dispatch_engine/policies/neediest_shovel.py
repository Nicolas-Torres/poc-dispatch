from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.assignment import Assignment, Destination
from dispatch_engine.domain.equipment import Shovel, ShovelId, TruckId
from dispatch_engine.domain.mine import DumpZone, LoadZone, ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot, TruckStatus
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
    # Shovel idle time is the scarcer resource in most operations; raise this to
    # bias the engine towards keeping shovels busy at the cost of truck queueing.
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
        free_at_s = {sid: self._shovel_free_at_s(snapshot, sid) for sid in snapshot.shovels}
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
        zone = snapshot.mine.load_zones[load_zone_id]
        compatible = [
            dump for dump in snapshot.mine.dump_zones.values() if dump.accepts(zone.material)
        ]
        planned_tph = self.plan.destination_rates_tph(load_zone_id)
        candidates = [dump for dump in compatible if planned_tph.get(dump.id, 0.0) > 0.0]

        if not candidates:
            nearest = min(
                compatible,
                key=lambda dump: self.best_path.travel_time_s(zone.node, dump.node, loaded=True),
            )
            return self._destination(snapshot, truck_id, zone, nearest, "nearest destination")

        # Send this load wherever the shift is furthest behind the split the plan
        # asked for. Chasing the running total, rather than the instantaneous
        # one, is what keeps a blend on target over a shift.
        payload_t = snapshot.trucks[truck_id].truck.payload_t
        total_planned_tph = sum(planned_tph[dump.id] for dump in candidates)
        delivered_t = {
            dump.id: snapshot.committed_to_route(load_zone_id, dump.id) for dump in candidates
        }
        total_delivered_t = sum(delivered_t.values()) + payload_t

        def shortfall_t(dump: DumpZone) -> tuple[float, float]:
            share = planned_tph[dump.id] / total_planned_tph
            return (share * total_delivered_t - delivered_t[dump.id], planned_tph[dump.id])

        chosen = max(candidates, key=shortfall_t)
        share = planned_tph[chosen.id] / total_planned_tph
        return self._destination(snapshot, truck_id, zone, chosen, f"{share:.0%} of plan share")

    def _destination(
        self,
        snapshot: MineSnapshot,
        truck_id: TruckId,
        zone: LoadZone,
        dump: DumpZone,
        reason: str,
    ) -> Destination:
        return Destination(
            truck_id=truck_id,
            dump_zone_id=dump.id,
            route=self.best_path.route(zone.node, dump.node, loaded=True),
            reason=reason,
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

    def _shovel_free_at_s(self, snapshot: MineSnapshot, shovel_id: ShovelId) -> float:
        """When the shovel finishes the trucks already committed to it.

        A truck being loaded right now is charged its full load time, since the
        snapshot does not carry loading progress — a small pessimism that biases
        the engine away from shovels that just started a load.
        """
        shovel = snapshot.shovels[shovel_id].shovel
        arrivals = sorted(
            (snapshot.now_s + (status.eta_to_shovel_s or 0.0), shovel.load_time_s(status.truck))
            for status in snapshot.arrivals(shovel_id)
        )
        free_s = snapshot.now_s
        for arrival_s, load_time_s in arrivals:
            free_s = max(free_s, arrival_s) + load_time_s
        return free_s

    def _penalty(
        self,
        snapshot: MineSnapshot,
        status: TruckStatus,
        shovel: Shovel,
        shovel_free_s: float,
    ) -> tuple[float, float]:
        """Total idle time this pairing would cause, and the truck's arrival time."""
        zone_node = snapshot.mine.load_zones[shovel.zone].node
        travel_s = self.best_path.travel_time_s(status.free_at_node, zone_node, loaded=False)
        arrival_s = snapshot.now_s + status.free_in_s + travel_s
        shovel_idle_s = max(0.0, arrival_s - shovel_free_s)
        truck_queue_s = max(0.0, shovel_free_s - arrival_s)
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
