from __future__ import annotations

from itertools import pairwise

import networkx as nx

from dispatch_engine.domain.mine import Mine, NodeId, RoadNetwork
from dispatch_engine.domain.routing import Route


class NoRouteError(LookupError):
    """No drivable path exists between two points of the road network."""


def _build_graph(network: RoadNetwork, *, loaded: bool) -> nx.DiGraph:
    graph = nx.DiGraph()
    for edge in network.edges:
        time_s = edge.travel_time_s(loaded=loaded)
        existing = graph.get_edge_data(edge.source, edge.target)
        # Parallel segments collapse to the fastest one for this haul condition.
        if existing is not None and existing["time_s"] <= time_s:
            continue
        graph.add_edge(edge.source, edge.target, time_s=time_s, length_m=edge.length_m)
    return graph


class BestPath:
    """Stage 1: minimum-travel-time routes over the mine road graph.

    Empty and loaded hauls run on separate weightings because a loaded truck is
    slower, especially uphill, so the fastest way out of the pit is not
    necessarily the reverse of the fastest way in. Routes are cached and only
    dropped when the network topology changes, mirroring the real stage, which
    re-runs on road closures or new ramps rather than per assignment.
    """

    def __init__(self, network: RoadNetwork) -> None:
        self._network = network
        self._graphs: dict[bool, nx.DiGraph] = {}
        self._cache: dict[tuple[NodeId, NodeId, bool], Route] = {}
        self._rebuild()

    def _rebuild(self) -> None:
        self._graphs = {
            loaded: _build_graph(self._network, loaded=loaded) for loaded in (False, True)
        }
        self._cache.clear()

    @property
    def network(self) -> RoadNetwork:
        return self._network

    @property
    def cached_route_count(self) -> int:
        return len(self._cache)

    def update_network(self, network: RoadNetwork) -> None:
        self._network = network
        self._rebuild()

    def route(self, origin: NodeId, destination: NodeId, *, loaded: bool) -> Route:
        key = (origin, destination, loaded)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        graph = self._graphs[loaded]
        try:
            travel_time_s, nodes = nx.single_source_dijkstra(
                graph, origin, destination, weight="time_s"
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound) as exc:
            raise NoRouteError(f"no route from {origin!r} to {destination!r}") from exc

        route = Route(
            origin=origin,
            destination=destination,
            nodes=tuple(nodes),
            travel_time_s=travel_time_s,
            distance_m=sum(graph[u][v]["length_m"] for u, v in pairwise(nodes)),
            loaded=loaded,
        )
        self._cache[key] = route
        return route

    def travel_time_s(self, origin: NodeId, destination: NodeId, *, loaded: bool) -> float:
        return self.route(origin, destination, loaded=loaded).travel_time_s

    def warm(self, mine: Mine) -> None:
        """Precompute every load zone ↔ dump zone pair in both haul conditions."""
        for load_zone in mine.load_zones.values():
            for dump_zone in mine.dump_zones.values():
                self.route(load_zone.node, dump_zone.node, loaded=True)
                self.route(dump_zone.node, load_zone.node, loaded=False)
