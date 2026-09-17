from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.domain.equipment import ShovelId, TruckId
from dispatch_engine.domain.mine import ZoneId
from dispatch_engine.domain.routing import Route


@dataclass(frozen=True, slots=True)
class Assignment:
    """The destination handed back to a truck, with the reasoning behind it.

    `reason` and `penalty_s` exist so a dispatcher can be told why the engine
    chose this shovel — an unexplainable assignment is one an operation will not
    trust.
    """

    truck_id: TruckId
    shovel_id: ShovelId
    zone_id: ZoneId
    route: Route
    reason: str
    penalty_s: float = 0.0
