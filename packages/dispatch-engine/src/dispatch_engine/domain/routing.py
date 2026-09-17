from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.domain.mine import NodeId


@dataclass(frozen=True, slots=True)
class Route:
    """A concrete minimum-cost path through the road network.

    Narrower than the DISPATCH notion of a "route" (load zone + dump zone + path
    + material grade + fleet type), which belongs to the production plan stage.
    """

    origin: NodeId
    destination: NodeId
    nodes: tuple[NodeId, ...]
    travel_time_s: float
    distance_m: float
    loaded: bool
