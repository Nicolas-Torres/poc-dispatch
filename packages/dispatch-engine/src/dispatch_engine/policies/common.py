from __future__ import annotations

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.assignment import Destination
from dispatch_engine.domain.equipment import Shovel, ShovelId, TruckId
from dispatch_engine.domain.mine import DumpZone, ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot, TruckStatus
from dispatch_engine.production_plan import ProductionPlan

"""Building blocks shared by dispatch policies.

Keeping the queueing arithmetic and the destination split here means two
policies can differ in how they pick a shovel while being compared on equal
terms everywhere else.
"""


def shovel_free_at_s(snapshot: MineSnapshot, shovel_id: ShovelId) -> float:
    """When the shovel finishes the trucks already committed to it.

    A truck being loaded right now is charged its full load time, since the
    snapshot does not carry loading progress — a small pessimism that biases the
    engine away from shovels that just started a load.
    """
    shovel = snapshot.shovels[shovel_id].shovel
    arrivals = sorted(
        (
            snapshot.now_s + (status.eta_to_shovel_s or 0.0),
            # A truck coming in light is also quicker to fill.
            shovel.load_time_s(status.truck)
            * snapshot.overrides.restriction(status.truck.id).load_factor,
        )
        for status in snapshot.arrivals(shovel_id)
    )
    free_s = snapshot.now_s
    for arrival_s, load_time_s in arrivals:
        free_s = max(free_s, arrival_s) + load_time_s
    return free_s


def arrival_at_shovel_s(
    best_path: BestPath, snapshot: MineSnapshot, status: TruckStatus, shovel: Shovel
) -> float:
    """When the truck would reach the shovel, counting the work it has left.

    A speed restriction stretches the haul, so the engine projects the arrival it
    will really get rather than the one the road would allow.
    """
    zone_node = snapshot.mine.load_zones[shovel.zone].node
    travel_s = best_path.travel_time_s(status.free_at_node, zone_node, loaded=False)
    speed_factor = snapshot.overrides.restriction(status.truck.id).speed_factor
    return snapshot.now_s + status.free_in_s + travel_s / speed_factor


def dispatchable_candidates(
    best_path: BestPath,
    snapshot: MineSnapshot,
    shovel: Shovel,
    pending: set[TruckId],
    sh_param: float,
) -> list[TruckStatus]:
    """Tc(s), with the short-haul restriction applied to list membership.

    A truck restricted to short hauls is dropped from the candidates of any
    shovel further than `sh_param` of the longest haul available to it. The
    threshold is relative rather than absolute so it means the same thing in a
    small pit and a large one — SH_PARAM in the patent is a percentage.

    If the restriction would leave the truck nowhere to go, it keeps its nearest
    shovel: a restriction is meant to limit a truck, not strand it.
    """
    candidates = [
        status for status in snapshot.candidates_for(shovel.id) if status.truck.id in pending
    ]
    return [
        status
        for status in candidates
        if not snapshot.overrides.restriction(status.truck.id).short_hauls_only
        or _is_short_haul(best_path, snapshot, status, shovel, sh_param)
    ]


def _is_short_haul(
    best_path: BestPath,
    snapshot: MineSnapshot,
    status: TruckStatus,
    shovel: Shovel,
    sh_param: float,
) -> bool:
    reachable = [
        best_path.travel_time_s(
            status.free_at_node, snapshot.mine.load_zones[other.shovel.zone].node, loaded=False
        )
        for other in snapshot.available_shovels().values()
    ]
    if not reachable:
        return True
    this_haul_s = best_path.travel_time_s(
        status.free_at_node, snapshot.mine.load_zones[shovel.zone].node, loaded=False
    )
    # Always allow the nearest shovel, however far it is, so the restriction
    # never leaves a working truck with no legal destination.
    return this_haul_s <= max(sh_param * max(reachable), min(reachable))


def plan_tracking_destination(
    best_path: BestPath,
    plan: ProductionPlan,
    snapshot: MineSnapshot,
    truck_id: TruckId,
    load_zone_id: ZoneId,
) -> Destination:
    """Send the load wherever the shift is furthest behind the plan's split.

    Chasing the running total, rather than the instantaneous one, is what keeps a
    blend on target over a shift. When the plan has no opinion about
    destinations, the nearest compatible one wins.
    """
    zone = snapshot.mine.load_zones[load_zone_id]
    compatible = [dump for dump in snapshot.mine.dump_zones.values() if dump.accepts(zone.material)]
    planned_tph = plan.destination_rates_tph(load_zone_id)
    candidates = [dump for dump in compatible if planned_tph.get(dump.id, 0.0) > 0.0]

    if not candidates:
        nearest = min(
            compatible,
            key=lambda dump: best_path.travel_time_s(zone.node, dump.node, loaded=True),
        )
        return _destination(
            best_path, snapshot, truck_id, load_zone_id, nearest, "nearest destination"
        )

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
    return _destination(
        best_path, snapshot, truck_id, load_zone_id, chosen, f"{share:.0%} of plan share"
    )


def _destination(
    best_path: BestPath,
    snapshot: MineSnapshot,
    truck_id: TruckId,
    load_zone_id: ZoneId,
    dump: DumpZone,
    reason: str,
) -> Destination:
    zone = snapshot.mine.load_zones[load_zone_id]
    return Destination(
        truck_id=truck_id,
        dump_zone_id=dump.id,
        route=best_path.route(zone.node, dump.node, loaded=True),
        reason=reason,
    )
