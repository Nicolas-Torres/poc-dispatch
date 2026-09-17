from __future__ import annotations

import pytest
from dispatch_engine.best_path import BestPath, NoRouteError
from dispatch_engine.domain.mine import Edge, RoadNetwork


def _network() -> RoadNetwork:
    # The direct road is the same length but much slower than going via the junction.
    return RoadNetwork(
        edges=(
            Edge(source="pit", target="plant", length_m=2000, speed_limit_kph=20),
            Edge(source="pit", target="junction", length_m=1000, speed_limit_kph=60),
            Edge(source="junction", target="plant", length_m=1000, speed_limit_kph=60),
            Edge(source="orphan", target="orphan_end", length_m=100, speed_limit_kph=30),
        )
    )


def test_route_minimises_time_not_distance() -> None:
    best_path = BestPath(_network())

    route = best_path.route("pit", "plant", loaded=False)

    assert route.nodes == ("pit", "junction", "plant")
    assert route.travel_time_s == pytest.approx(120.0)
    assert route.distance_m == pytest.approx(2000.0)


def test_loaded_hauls_are_slower_than_empty_ones() -> None:
    best_path = BestPath(_network())

    empty = best_path.travel_time_s("pit", "plant", loaded=False)
    loaded = best_path.travel_time_s("pit", "plant", loaded=True)

    assert loaded == pytest.approx(empty / 0.7)


def test_uphill_costs_speed_and_downhill_grants_no_bonus() -> None:
    uphill = Edge(source="a", target="b", length_m=1000, speed_limit_kph=40, grade_pct=10)
    downhill = Edge(source="b", target="a", length_m=1000, speed_limit_kph=40, grade_pct=-10)
    flat = Edge(source="a", target="b", length_m=1000, speed_limit_kph=40)

    assert uphill.travel_time_s(loaded=False) > flat.travel_time_s(loaded=False)
    assert downhill.travel_time_s(loaded=False) == pytest.approx(flat.travel_time_s(loaded=False))


def test_routes_are_cached_until_the_network_changes() -> None:
    best_path = BestPath(_network())

    best_path.route("pit", "plant", loaded=False)
    best_path.route("pit", "plant", loaded=False)
    assert best_path.cached_route_count == 1

    best_path.update_network(RoadNetwork(edges=_network().edges, version=1))
    assert best_path.cached_route_count == 0


def test_unreachable_destination_raises() -> None:
    best_path = BestPath(_network())

    with pytest.raises(NoRouteError):
        best_path.route("pit", "orphan_end", loaded=False)

    with pytest.raises(NoRouteError):
        best_path.route("pit", "nowhere", loaded=False)
