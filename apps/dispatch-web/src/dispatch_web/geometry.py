from __future__ import annotations

from collections import deque

import networkx as nx
import numpy as np
from dispatch_engine.domain.mine import Mine, NodeId

type Point = tuple[float, float, float]


def layout(mine: Mine) -> dict[NodeId, Point]:
    """Place every node of the road network in space, in metres.

    The domain has no coordinates — nodes are names — but it does carry the two
    things needed to invent honest ones: segment lengths and grades. Elevation is
    integrated from the grades, so the pit is as deep as the ramps say it is, and
    the plan view is laid out to respect road distances.
    """
    graph = nx.Graph()
    for edge in mine.network.edges:
        graph.add_edge(edge.source, edge.target, length=edge.length_m)

    plan = _plan_view(graph)
    elevation = _elevation(mine)
    return {node: (plan[node][0], elevation.get(node, 0.0), plan[node][1]) for node in graph}


def _plan_view(graph: nx.Graph) -> dict[NodeId, tuple[float, float]]:
    """Top-down positions whose separations approximate real road distances.

    Classical multidimensional scaling on the shortest-path distances, done with
    numpy rather than reaching for a layout library: it is a dozen lines, it is
    deterministic, and the output is already in metres instead of a unit box.
    """
    nodes = sorted(graph)
    lengths = dict(nx.all_pairs_dijkstra_path_length(graph, weight="length"))
    distances = np.array([[lengths[a].get(b, 0.0) for b in nodes] for a in nodes], dtype=float)

    size = len(nodes)
    centring = np.eye(size) - np.ones((size, size)) / size
    gram = -0.5 * centring @ (distances**2) @ centring
    values, vectors = np.linalg.eigh(gram)

    # eigh returns ascending order, so the two leading axes are the last columns.
    leading = np.sqrt(np.clip(values[-2:], 0.0, None)) * vectors[:, -2:]
    return {
        node: (float(leading[index, 1]), float(leading[index, 0]))
        for index, node in enumerate(nodes)
    }


def _elevation(mine: Mine) -> dict[NodeId, float]:
    """Integrate grades over the network so ramps actually climb.

    A grade is given in the direction of travel, and `ScenarioSpec.build` emits
    the reverse segment with the sign flipped, so walking the graph from any
    starting point yields one consistent elevation field.
    """
    climb: dict[NodeId, list[tuple[NodeId, float]]] = {}
    for edge in mine.network.edges:
        climb.setdefault(edge.source, []).append(
            (edge.target, edge.length_m * edge.grade_pct / 100.0)
        )

    nodes = sorted(mine.network.nodes)
    if not nodes:
        return {}

    # Start from a dump zone when there is one: the surface is the natural datum.
    start = next((dump.node for dump in mine.dump_zones.values()), nodes[0])
    elevation = {start: 0.0}
    queue = deque([start])
    while queue:
        node = queue.popleft()
        for neighbour, delta in climb.get(node, ()):
            if neighbour not in elevation:
                elevation[neighbour] = elevation[node] + delta
                queue.append(neighbour)

    # Anything the walk could not reach sits on the datum rather than nowhere.
    return {node: elevation.get(node, 0.0) for node in nodes}
