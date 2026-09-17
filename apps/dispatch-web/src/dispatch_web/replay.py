from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.mine import NodeId
from mine_sim.events import EventKind, EventLog
from mine_sim.scenario import Scenario

from dispatch_web.geometry import Point, layout


@dataclass(slots=True)
class _TruckTrace:
    at_node: NodeId
    departed_s: float = 0.0
    heading_to: NodeId | None = None
    movements: list[dict[str, Any]] = field(default_factory=list)


def build(scenario: Scenario, log: EventLog, best_path: BestPath, horizon_s: float) -> dict:
    """Turn a finished run into something a browser can animate.

    The event log says when things happened but not where, so the haul paths are
    recomputed from Best Path — the same routes the engine used to decide — and
    resolved to coordinates here. The frontend then needs no knowledge of the
    domain at all: it interpolates along polylines.
    """
    points = layout(scenario.mine)
    zone_node = {zone_id: zone.node for zone_id, zone in scenario.mine.load_zones.items()}
    shovel_zone = {shovel_id: shovel.zone for shovel_id, shovel in scenario.shovels.items()}
    dump_node = {dump_id: dump.node for dump_id, dump in scenario.mine.dump_zones.items()}

    traces = {
        truck.id: _TruckTrace(at_node=scenario.start_nodes[truck.id]) for truck in scenario.trucks
    }
    states: list[dict[str, Any]] = []
    shovel_states: list[dict[str, Any]] = []

    def path_of(origin: NodeId, target: NodeId, *, loaded: bool) -> list[Point]:
        return [points[node] for node in best_path.route(origin, target, loaded=loaded).nodes]

    def travel(trace: _TruckTrace, truck_id: str, target: NodeId, end_s: float, loaded: bool):
        trace.movements.append(
            {
                "truck": truck_id,
                "t0": trace.departed_s,
                "t1": end_s,
                "loaded": loaded,
                "path": path_of(trace.at_node, target, loaded=loaded),
            }
        )
        trace.at_node = target

    for event in log.events:
        trace = traces.get(event.truck_id)
        if event.kind is EventKind.ASSIGNED and trace is not None:
            trace.departed_s = event.time_s
            trace.heading_to = zone_node[shovel_zone[event.shovel_id]]
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "hauling_empty"})
        elif event.kind in (EventKind.ARRIVE_SHOVEL, EventKind.REASSIGNED) and trace is not None:
            if trace.heading_to is not None:
                travel(trace, event.truck_id, trace.heading_to, event.time_s, loaded=False)
                trace.heading_to = None
            label = "queueing" if event.kind is EventKind.ARRIVE_SHOVEL else "hauling_empty"
            states.append({"truck": event.truck_id, "t": event.time_s, "state": label})
        elif event.kind is EventKind.SPOT_START and trace is not None:
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "loading"})
        elif event.kind is EventKind.LOAD_END and trace is not None:
            trace.departed_s = event.time_s
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "hauling_loaded"})
        elif event.kind is EventKind.ARRIVE_DUMP and trace is not None:
            travel(trace, event.truck_id, dump_node[event.dump_id], event.time_s, loaded=True)
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "dumping"})
        elif event.kind is EventKind.TRUCK_DOWN and trace is not None:
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "down"})
        elif event.kind is EventKind.TRUCK_UP and trace is not None:
            states.append({"truck": event.truck_id, "t": event.time_s, "state": "idle"})
        elif event.kind in (EventKind.SHOVEL_DOWN, EventKind.SHOVEL_UP):
            # A shovel outage is the whole point of one of the demos, so the
            # viewer needs it as a state to paint, not just a row in the log.
            shovel_states.append(
                {
                    "shovel": event.shovel_id,
                    "t": event.time_s,
                    "state": "down" if event.kind is EventKind.SHOVEL_DOWN else "operating",
                    "detail": event.detail,
                }
            )

    movements = [m for trace in traces.values() for m in trace.movements]
    movements.sort(key=lambda m: m["t0"])

    return {
        "duration_s": horizon_s,
        "nodes": {node: list(point) for node, point in points.items()},
        "edges": [
            {"from": edge.source, "to": edge.target, "grade_pct": edge.grade_pct}
            for edge in scenario.mine.network.edges
        ],
        "zones": {
            zone_id: {
                "node": zone.node,
                "material": zone.material.name,
                "is_ore": zone.material.is_ore,
                "grades": dict(zone.material.grades),
            }
            for zone_id, zone in scenario.mine.load_zones.items()
        },
        "shovels": [
            {
                "id": shovel_id,
                "zone": shovel.zone,
                "node": zone_node[shovel.zone],
                "material": scenario.mine.load_zones[shovel.zone].material.name,
                "is_ore": scenario.mine.load_zones[shovel.zone].material.is_ore,
            }
            for shovel_id, shovel in scenario.shovels.items()
        ],
        "dumps": [
            {"id": dump.id, "node": dump.node, "accepts_ore": dump.accepts_ore}
            for dump in scenario.mine.dump_zones.values()
        ],
        "trucks": [{"id": truck.id, "payload_t": truck.payload_t} for truck in scenario.trucks],
        "movements": movements,
        "states": sorted(states, key=lambda s: s["t"]),
        "shovel_states": sorted(shovel_states, key=lambda s: s["t"]),
        "events": [
            {
                "t": event.time_s,
                "kind": event.kind.value,
                "truck": event.truck_id,
                "shovel": event.shovel_id,
                "dump": event.dump_id,
                "zone": event.zone_id,
                "payload_t": event.payload_t,
                "detail": event.detail,
            }
            for event in log.events
        ],
    }
