from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

type NodeId = str
type ZoneId = str

LOADED_SPEED_FACTOR = 0.7
GRADE_PENALTY_PER_PCT = 0.06
MIN_GRADE_FACTOR = 0.25


class ZoneKind(StrEnum):
    LOAD = "load"
    DUMP = "dump"


@dataclass(frozen=True, slots=True)
class Edge:
    source: NodeId
    target: NodeId
    length_m: float
    speed_limit_kph: float
    grade_pct: float = 0.0

    def effective_speed_kph(self, *, loaded: bool) -> float:
        speed = self.speed_limit_kph
        if loaded:
            speed *= LOADED_SPEED_FACTOR
        # Uphill costs speed but downhill grants no bonus: descending trucks are
        # capped by braking/retarder limits, not by available power.
        if self.grade_pct > 0:
            speed *= max(MIN_GRADE_FACTOR, 1.0 - GRADE_PENALTY_PER_PCT * self.grade_pct)
        return speed

    def travel_time_s(self, *, loaded: bool) -> float:
        return self.length_m / (self.effective_speed_kph(loaded=loaded) / 3.6)


@dataclass(frozen=True, slots=True)
class Material:
    name: str
    is_ore: bool
    grades: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadZone:
    id: ZoneId
    node: NodeId
    material: Material


@dataclass(frozen=True, slots=True)
class DumpZone:
    id: ZoneId
    node: NodeId
    accepts_ore: bool
    tipping_bays: int = 1
    dump_time_s: float = 60.0

    def accepts(self, material: Material) -> bool:
        return material.is_ore == self.accepts_ore


@dataclass(frozen=True, slots=True)
class RoadNetwork:
    edges: tuple[Edge, ...]
    # Bumped whenever the topology changes (road closed, ramp opened); invalidates
    # cached Best Path routes, which is the only reason the stage re-runs.
    version: int = 0

    @property
    def nodes(self) -> frozenset[NodeId]:
        return frozenset(n for e in self.edges for n in (e.source, e.target))


@dataclass(frozen=True, slots=True)
class Mine:
    network: RoadNetwork
    load_zones: dict[ZoneId, LoadZone]
    dump_zones: dict[ZoneId, DumpZone]
