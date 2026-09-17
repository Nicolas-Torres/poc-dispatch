"""The domain has no coordinates, so the layout has to invent honest ones."""

from __future__ import annotations

import math

import networkx as nx
import numpy as np
import pytest
from dispatch_web.geometry import layout
from mine_sim.scenario import SCENARIOS


def _built(name: str):
    return SCENARIOS[name]().build()


def test_every_node_gets_a_place() -> None:
    mine = _built("toy").mine
    points = layout(mine)
    assert set(points) == set(mine.network.nodes)
    assert all(len(p) == 3 and all(math.isfinite(c) for c in p) for p in points.values())


def test_the_pit_sits_below_the_dumps() -> None:
    """Elevation is integrated from the road grades, so the ramp has to descend."""
    scenario = _built("toy")
    points = layout(scenario.mine)
    crusher = points[scenario.mine.dump_zones["crusher"].node][1]

    for zone in scenario.mine.load_zones.values():
        assert points[zone.node][1] < crusher - 50.0, f"{zone.node} is not in a pit"


def test_elevation_matches_the_grade_of_every_segment() -> None:
    """The whole point of integrating grades is that the result is consistent."""
    mine = _built("toy").mine
    points = layout(mine)
    for edge in mine.network.edges:
        climb = points[edge.target][1] - points[edge.source][1]
        expected = edge.length_m * edge.grade_pct / 100.0
        assert climb == pytest.approx(expected, abs=1e-6)


def test_plan_view_preserves_road_distances() -> None:
    """MDS is only worth using if the picture is faithful to the haul network."""
    mine = _built("toy").mine
    points = layout(mine)

    graph = nx.Graph()
    for edge in mine.network.edges:
        graph.add_edge(edge.source, edge.target, length=edge.length_m)
    road = dict(nx.all_pairs_dijkstra_path_length(graph, weight="length"))

    nodes = sorted(points)
    drawn, actual = [], []
    for i, a in enumerate(nodes):
        for b in nodes[i + 1 :]:
            drawn.append(math.dist((points[a][0], points[a][2]), (points[b][0], points[b][2])))
            actual.append(road[a][b])

    assert np.corrcoef(drawn, actual)[0, 1] > 0.9


def test_a_third_destination_does_not_break_the_layout() -> None:
    mine = _built("toy-stockpile").mine
    points = layout(mine)
    assert set(points) == set(mine.network.nodes)
    # Distinct nodes must land in distinct places, or the picture is a lie.
    plan = {(round(p[0], 3), round(p[2], 3)) for p in points.values()}
    assert len(plan) == len(points)
