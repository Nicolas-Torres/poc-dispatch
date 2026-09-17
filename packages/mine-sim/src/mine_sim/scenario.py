from __future__ import annotations

from dataclasses import dataclass

from dispatch_engine.domain.equipment import Shovel, ShovelId, Truck, TruckId
from dispatch_engine.domain.mine import (
    DumpZone,
    Edge,
    LoadZone,
    Material,
    Mine,
    NodeId,
    RoadNetwork,
)
from dispatch_engine.production_plan import StaticProductionPlan
from pydantic import BaseModel, Field, model_validator


class EdgeSpec(BaseModel):
    source: str
    target: str
    length_m: float = Field(gt=0)
    speed_limit_kph: float = Field(default=40.0, gt=0)
    grade_pct: float = 0.0
    bidirectional: bool = True


class MaterialSpec(BaseModel):
    name: str
    is_ore: bool = True
    grades: dict[str, float] = Field(default_factory=dict)


class LoadZoneSpec(BaseModel):
    id: str
    node: str
    material: MaterialSpec


class ShovelSpec(BaseModel):
    id: str
    zone: str
    load_rate_tph: float = Field(gt=0)
    target_rate_tph: float = Field(ge=0)
    priority: int = 0


class DumpZoneSpec(BaseModel):
    id: str
    node: str
    accepts_ore: bool
    tipping_bays: int = Field(default=1, ge=1)
    dump_time_s: float = Field(default=60.0, gt=0)


class TruckSpec(BaseModel):
    id: str
    payload_t: float = Field(gt=0)
    start_node: str
    fleet_type: str = "default"


@dataclass(frozen=True, slots=True)
class Scenario:
    name: str
    mine: Mine
    shovels: dict[ShovelId, Shovel]
    trucks: tuple[Truck, ...]
    start_nodes: dict[TruckId, NodeId]
    plan: StaticProductionPlan
    spot_time_s: float


class ScenarioSpec(BaseModel):
    """Declarative mine definition: layout, fleet and cycle timings.

    Validated up front because a dangling node or a material with nowhere to be
    tipped only shows up deep inside the simulation otherwise.
    """

    name: str
    edges: list[EdgeSpec]
    load_zones: list[LoadZoneSpec]
    shovels: list[ShovelSpec]
    dump_zones: list[DumpZoneSpec]
    trucks: list[TruckSpec]
    spot_time_s: float = Field(default=40.0, ge=0)

    @model_validator(mode="after")
    def _check_references(self) -> ScenarioSpec:
        nodes = {node for edge in self.edges for node in (edge.source, edge.target)}
        zones = {zone.id: zone for zone in self.load_zones}

        for zone in self.load_zones:
            if zone.node not in nodes:
                raise ValueError(f"load zone {zone.id!r} sits on unknown node {zone.node!r}")
        for dump in self.dump_zones:
            if dump.node not in nodes:
                raise ValueError(f"dump zone {dump.id!r} sits on unknown node {dump.node!r}")
        for truck in self.trucks:
            if truck.start_node not in nodes:
                raise ValueError(f"truck {truck.id!r} starts on unknown node {truck.start_node!r}")
        for shovel in self.shovels:
            if shovel.zone not in zones:
                raise ValueError(f"shovel {shovel.id!r} digs unknown load zone {shovel.zone!r}")

        ore_accepted = {dump.accepts_ore for dump in self.dump_zones}
        for zone in self.load_zones:
            if zone.material.is_ore not in ore_accepted:
                raise ValueError(
                    f"material {zone.material.name!r} of load zone {zone.id!r} has no dump zone"
                )
        return self

    def build(self) -> Scenario:
        edges: list[Edge] = []
        for spec in self.edges:
            edges.append(
                Edge(
                    source=spec.source,
                    target=spec.target,
                    length_m=spec.length_m,
                    speed_limit_kph=spec.speed_limit_kph,
                    grade_pct=spec.grade_pct,
                )
            )
            if spec.bidirectional:
                # The return leg climbs whatever the outbound leg descends.
                edges.append(
                    Edge(
                        source=spec.target,
                        target=spec.source,
                        length_m=spec.length_m,
                        speed_limit_kph=spec.speed_limit_kph,
                        grade_pct=-spec.grade_pct,
                    )
                )

        mine = Mine(
            network=RoadNetwork(edges=tuple(edges)),
            load_zones={
                zone.id: LoadZone(
                    id=zone.id,
                    node=zone.node,
                    material=Material(
                        name=zone.material.name,
                        is_ore=zone.material.is_ore,
                        grades=dict(zone.material.grades),
                    ),
                )
                for zone in self.load_zones
            },
            dump_zones={
                dump.id: DumpZone(
                    id=dump.id,
                    node=dump.node,
                    accepts_ore=dump.accepts_ore,
                    tipping_bays=dump.tipping_bays,
                    dump_time_s=dump.dump_time_s,
                )
                for dump in self.dump_zones
            },
        )

        return Scenario(
            name=self.name,
            mine=mine,
            shovels={
                shovel.id: Shovel(
                    id=shovel.id,
                    zone=shovel.zone,
                    load_rate_tph=shovel.load_rate_tph,
                    priority=shovel.priority,
                )
                for shovel in self.shovels
            },
            trucks=tuple(
                Truck(id=truck.id, payload_t=truck.payload_t, fleet_type=truck.fleet_type)
                for truck in self.trucks
            ),
            start_nodes={truck.id: truck.start_node for truck in self.trucks},
            plan=StaticProductionPlan(
                targets_tph={shovel.id: shovel.target_rate_tph for shovel in self.shovels}
            ),
            spot_time_s=self.spot_time_s,
        )


def toy_mine() -> ScenarioSpec:
    """Small pit: two ore benches and a waste bench, a crusher and a waste dump.

    Grades are written for the outbound (descending, empty) direction, so the
    loaded haul out of the pit is the expensive leg — which is what makes the
    assignment decision non-trivial.
    """
    return ScenarioSpec(
        name="toy",
        edges=[
            EdgeSpec(source="crusher", target="plant_junction", length_m=400, speed_limit_kph=40),
            EdgeSpec(
                source="waste_dump",
                target="plant_junction",
                length_m=900,
                speed_limit_kph=40,
                grade_pct=-2,
            ),
            EdgeSpec(source="plant_junction", target="ramp_top", length_m=600, speed_limit_kph=45),
            EdgeSpec(
                source="ramp_top",
                target="ramp_mid",
                length_m=1200,
                speed_limit_kph=30,
                grade_pct=-8,
            ),
            EdgeSpec(
                source="ramp_mid",
                target="pit_junction",
                length_m=800,
                speed_limit_kph=30,
                grade_pct=-6,
            ),
            EdgeSpec(
                source="pit_junction",
                target="bench_n",
                length_m=500,
                speed_limit_kph=30,
                grade_pct=-2,
            ),
            EdgeSpec(
                source="pit_junction",
                target="bench_s",
                length_m=700,
                speed_limit_kph=30,
                grade_pct=-3,
            ),
            EdgeSpec(
                source="pit_junction",
                target="bench_w",
                length_m=450,
                speed_limit_kph=30,
                grade_pct=-1,
            ),
        ],
        load_zones=[
            LoadZoneSpec(
                id="zone_n",
                node="bench_n",
                material=MaterialSpec(name="ore_high", is_ore=True, grades={"cu": 0.9}),
            ),
            LoadZoneSpec(
                id="zone_s",
                node="bench_s",
                material=MaterialSpec(name="ore_low", is_ore=True, grades={"cu": 0.5}),
            ),
            LoadZoneSpec(
                id="zone_w", node="bench_w", material=MaterialSpec(name="waste", is_ore=False)
            ),
        ],
        shovels=[
            ShovelSpec(id="SH01", zone="zone_n", load_rate_tph=3000, target_rate_tph=1400),
            ShovelSpec(id="SH02", zone="zone_s", load_rate_tph=2400, target_rate_tph=1000),
            ShovelSpec(id="SH03", zone="zone_w", load_rate_tph=3600, target_rate_tph=1600),
        ],
        dump_zones=[
            DumpZoneSpec(
                id="crusher", node="crusher", accepts_ore=True, tipping_bays=2, dump_time_s=60
            ),
            DumpZoneSpec(
                id="waste_dump",
                node="waste_dump",
                accepts_ore=False,
                tipping_bays=2,
                dump_time_s=45,
            ),
        ],
        trucks=[
            TruckSpec(id=f"CAT{index:02d}", payload_t=220, start_node="crusher")
            for index in range(1, 7)
        ],
    )


SCENARIOS = {"toy": toy_mine}
