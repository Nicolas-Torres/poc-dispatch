from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.assignment import Assignment, Destination
from dispatch_engine.domain.equipment import Shovel, TruckId
from dispatch_engine.domain.mine import ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot
from dispatch_engine.policies.common import plan_tracking_destination
from dispatch_engine.production_plan import ProductionPlan


@dataclass(frozen=True, slots=True)
class LongestWaitingShovelPolicy:
    """Send the truck to whichever shovel has gone longest without one.

    The second "one truck for n shovels" heuristic the literature contrasts
    DISPATCH with (Alarie and Gamache, 2002). Where the earliest-start rule
    optimises for the truck, this one optimises for the shovels — it spreads the
    fleet evenly — but it is just as blind to the production plan: an even split
    is only the right split when every shovel is worth the same.

    Like the other baseline it takes destinations from the plan, so a comparison
    isolates the shovel decision.
    """

    best_path: BestPath
    plan: ProductionPlan

    def assign(self, snapshot: MineSnapshot, truck_id: TruckId) -> Assignment | None:
        locked_to = snapshot.overrides.locked.get(truck_id)
        if locked_to is not None:
            return self._build(snapshot, truck_id, snapshot.shovels[locked_to].shovel, 0.0)

        if truck_id not in {status.truck.id for status in snapshot.trucks_needing_assignment()}:
            return None

        waiting_s = {
            shovel_id: snapshot.waiting_since_s(shovel_id)
            for shovel_id in snapshot.available_shovels()
            if truck_id in {candidate.truck.id for candidate in snapshot.candidates_for(shovel_id)}
        }
        if not waiting_s:
            return None

        chosen_id = max(waiting_s, key=lambda shovel_id: (waiting_s[shovel_id], shovel_id))
        return self._build(
            snapshot, truck_id, snapshot.shovels[chosen_id].shovel, waiting_s[chosen_id]
        )

    def choose_destination(
        self, snapshot: MineSnapshot, truck_id: TruckId, load_zone_id: ZoneId
    ) -> Destination:
        return plan_tracking_destination(
            self.best_path, self.plan, snapshot, truck_id, load_zone_id
        )

    def _build(
        self, snapshot: MineSnapshot, truck_id: TruckId, shovel: Shovel, waiting_s: float
    ) -> Assignment:
        zone = snapshot.mine.load_zones[shovel.zone]
        status = snapshot.trucks[truck_id]
        return Assignment(
            truck_id=truck_id,
            shovel_id=shovel.id,
            zone_id=zone.id,
            route=self.best_path.route(status.free_at_node, zone.node, loaded=False),
            reason=f"waiting {waiting_s / 60:.0f} min for a truck",
            penalty_s=0.0,
        )
