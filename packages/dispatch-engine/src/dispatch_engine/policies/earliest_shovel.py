from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.assignment import Assignment, Destination
from dispatch_engine.domain.equipment import Shovel, TruckId
from dispatch_engine.domain.mine import ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot
from dispatch_engine.policies.common import (
    arrival_at_shovel_s,
    dispatchable_candidates,
    plan_tracking_destination,
    shovel_free_at_s,
)
from dispatch_engine.production_plan import ProductionPlan


@dataclass(frozen=True, slots=True)
class EarliestShovelPolicy:
    """Send the truck wherever it can start loading soonest.

    The "one truck for n shovels" heuristic the literature contrasts DISPATCH
    with (Alarie and
    Gamache, 2002). It looks at one truck against every shovel and minimises
    that truck's own waiting time, ignoring the production plan and the other
    trucks that will need a destination next. It exists here as the baseline to
    measure the plan-following engine against, on the same mine and fleet.

    It still uses the plan to choose destinations, so a comparison isolates the
    shovel decision instead of mixing in a different tipping rule.
    """

    best_path: BestPath
    plan: ProductionPlan
    # SH_PARAM: a haul counts as short when it is within this fraction of the
    # longest haul on offer. Only bites on trucks restricted to short hauls.
    sh_param: float = 0.5

    def assign(self, snapshot: MineSnapshot, truck_id: TruckId) -> Assignment | None:
        locked_to = snapshot.overrides.locked.get(truck_id)
        if locked_to is not None:
            return self._build(snapshot, truck_id, snapshot.shovels[locked_to].shovel, 0.0)

        if truck_id not in {status.truck.id for status in snapshot.trucks_needing_assignment()}:
            return None

        status = snapshot.trucks[truck_id]
        starts_at_s = {
            sid: max(
                arrival_at_shovel_s(self.best_path, snapshot, status, shovel_status.shovel),
                shovel_free_at_s(snapshot, sid),
            )
            for sid, shovel_status in snapshot.available_shovels().items()
            if truck_id
            in {
                candidate.truck.id
                for candidate in dispatchable_candidates(
                    self.best_path,
                    snapshot,
                    snapshot.shovels[sid].shovel,
                    {truck_id},
                    self.sh_param,
                )
            }
        }
        if not starts_at_s:
            return None

        chosen_id = min(starts_at_s, key=lambda sid: (starts_at_s[sid], sid))
        return self._build(
            snapshot,
            truck_id,
            snapshot.shovels[chosen_id].shovel,
            starts_at_s[chosen_id] - snapshot.now_s,
        )

    def choose_destination(
        self, snapshot: MineSnapshot, truck_id: TruckId, load_zone_id: ZoneId
    ) -> Destination:
        return plan_tracking_destination(
            self.best_path, self.plan, snapshot, truck_id, load_zone_id
        )

    def _build(
        self, snapshot: MineSnapshot, truck_id: TruckId, shovel: Shovel, wait_s: float
    ) -> Assignment:
        zone = snapshot.mine.load_zones[shovel.zone]
        status = snapshot.trucks[truck_id]
        return Assignment(
            truck_id=truck_id,
            shovel_id=shovel.id,
            zone_id=zone.id,
            route=self.best_path.route(status.free_at_node, zone.node, loaded=False),
            reason=f"earliest loading start, in {wait_s / 60:.1f} min",
            penalty_s=wait_s,
        )
