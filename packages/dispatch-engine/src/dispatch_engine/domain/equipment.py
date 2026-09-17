from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from dispatch_engine.domain.mine import ZoneId

type TruckId = str
type ShovelId = str


class CycleState(StrEnum):
    """Haul cycle state machine described in docs/contexto (§1.4)."""

    TRAVEL_EMPTY = "travel_empty"
    QUEUE_AT_SHOVEL = "queue_at_shovel"
    SPOTTING = "spotting"
    LOADING = "loading"
    TRAVEL_LOADED = "travel_loaded"
    QUEUE_AT_DUMP = "queue_at_dump"
    DUMPING = "dumping"


class StatusCode(StrEnum):
    OPERATING = "operating"
    DELAY = "delay"
    STANDBY = "standby"
    DOWN = "down"


@dataclass(frozen=True, slots=True)
class Truck:
    id: TruckId
    payload_t: float
    fleet_type: str = "default"


@dataclass(frozen=True, slots=True)
class Shovel:
    id: ShovelId
    zone: ZoneId
    load_rate_tph: float
    priority: int = 0

    def load_time_s(self, truck: Truck) -> float:
        return truck.payload_t / self.load_rate_tph * 3600.0
