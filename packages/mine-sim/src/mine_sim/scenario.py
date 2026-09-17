from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from dispatch_engine.domain.equipment import Shovel, ShovelId, StatusCode, Truck, TruckId
from dispatch_engine.domain.mine import (
    DumpZone,
    Edge,
    LoadZone,
    Material,
    Mine,
    NodeId,
    RoadNetwork,
)
from dispatch_engine.domain.snapshot import Overrides, TruckRestriction
from dispatch_engine.lp import BlendTarget, FleetType, PlanInputs
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
    # Only read by StaticProductionPlan; the LP derives its own rates.
    target_rate_tph: float = Field(default=0.0, ge=0)
    # Only read by the LP: what a tonne off this shovel is worth, and any
    # production floor it has to meet (a stripping commitment, typically).
    value_per_tonne: float = Field(default=1.0, ge=0)
    min_rate_tph: float = Field(default=0.0, ge=0)
    priority: int = 0
    # Shovels are individual assets, so each one carries its own reliability.
    reliability: ReliabilitySpec | None = None


class DumpZoneSpec(BaseModel):
    id: str
    node: str
    accepts_ore: bool
    tipping_bays: int = Field(default=1, ge=1)
    dump_time_s: float = Field(default=60.0, gt=0)
    capacity_tph: float | None = Field(default=None, gt=0)
    # Only read by the LP: what a tonne is worth once tipped here, relative to
    # its value at the shovel.
    value_per_tonne: float = Field(default=1.0, ge=0)


class BlendTargetSpec(BaseModel):
    dump_zone: str
    element: str
    min_grade: float | None = None
    max_grade: float | None = None
    # Shrinks the window the plan is solved against, so execution drift still
    # lands inside the spec. Zero means the plan sits right on the limit.
    margin: float = Field(default=0.0, ge=0)


class VariabilitySpec(BaseModel):
    """Dispersion of cycle times, as a coefficient of variation (sigma / mu).

    All zero by default, which reproduces the deterministic twin exactly.
    """

    load_cv: float = Field(default=0.0, ge=0)
    travel_cv: float = Field(default=0.0, ge=0)
    dump_cv: float = Field(default=0.0, ge=0)

    @property
    def enabled(self) -> bool:
        return max(self.load_cv, self.travel_cv, self.dump_cv) > 0.0


class ReliabilitySpec(BaseModel):
    """Random breakdowns: mean time between failures and mean time to repair."""

    mtbf_h: float = Field(gt=0)
    mttr_h: float = Field(gt=0)

    @property
    def availability(self) -> float:
        return self.mtbf_h / (self.mtbf_h + self.mttr_h)


@dataclass(frozen=True, slots=True)
class Variability:
    load_cv: float = 0.0
    travel_cv: float = 0.0
    dump_cv: float = 0.0


@dataclass(frozen=True, slots=True)
class Reliability:
    mtbf_s: float
    mttr_s: float


class DisruptionSpec(BaseModel):
    """A scheduled outage: the shovel stops reporting as operating for a while."""

    shovel: str
    start_min: float = Field(ge=0)
    duration_min: float = Field(gt=0)
    status: Literal["down", "delay", "standby"] = "down"


@dataclass(frozen=True, slots=True)
class Disruption:
    shovel_id: ShovelId
    start_s: float
    duration_s: float
    status: StatusCode


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
    fleets: tuple[FleetType, ...]
    plan_inputs: PlanInputs
    disruptions: tuple[Disruption, ...]
    variability: Variability
    truck_reliability: Reliability | None
    shovel_reliability: dict[ShovelId, Reliability]
    overrides: Overrides


class RestrictionSpec(BaseModel):
    """An operational restriction the dispatcher has put on one truck."""

    truck: str
    short_hauls_only: bool = False
    speed_factor: float = Field(default=1.0, gt=0.0, le=1.0)
    load_factor: float = Field(default=1.0, gt=0.0, le=1.0)


class DispatcherSpec(BaseModel):
    """Manual intervention, declared with the mine instead of written in Python.

    A real dispatcher pins a truck to a shovel, parks a machine or keeps a
    damaged one working on worse terms. All of that already travelled in the
    snapshot; none of it could be reached from a scenario file.
    """

    locked: dict[str, str] = Field(default_factory=dict)
    excluded_trucks: list[str] = Field(default_factory=list)
    excluded_shovels: list[str] = Field(default_factory=list)
    restrictions: list[RestrictionSpec] = Field(default_factory=list)


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
    blend_targets: list[BlendTargetSpec] = Field(default_factory=list)
    disruptions: list[DisruptionSpec] = Field(default_factory=list)
    variability: VariabilitySpec = Field(default_factory=VariabilitySpec)
    # One profile for the whole fleet: the trucks are interchangeable.
    truck_reliability: ReliabilitySpec | None = None
    dispatcher: DispatcherSpec = Field(default_factory=DispatcherSpec)

    @model_validator(mode="after")
    def _check_references(self) -> ScenarioSpec:
        nodes = {node for edge in self.edges for node in (edge.source, edge.target)}
        zones = {zone.id: zone for zone in self.load_zones}
        dumps = {dump.id for dump in self.dump_zones}

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

        shovel_ids = {shovel.id for shovel in self.shovels}
        truck_ids = {truck.id for truck in self.trucks}

        for truck_id, shovel_id in self.dispatcher.locked.items():
            if truck_id not in truck_ids:
                raise ValueError(f"dispatcher locks unknown truck {truck_id!r}")
            if shovel_id not in shovel_ids:
                raise ValueError(f"dispatcher locks {truck_id!r} to unknown shovel {shovel_id!r}")
        for truck_id in self.dispatcher.excluded_trucks:
            if truck_id not in truck_ids:
                raise ValueError(f"dispatcher excludes unknown truck {truck_id!r}")
        for shovel_id in self.dispatcher.excluded_shovels:
            if shovel_id not in shovel_ids:
                raise ValueError(f"dispatcher excludes unknown shovel {shovel_id!r}")
        for restriction in self.dispatcher.restrictions:
            if restriction.truck not in truck_ids:
                raise ValueError(f"restriction points at unknown truck {restriction.truck!r}")

        for disruption in self.disruptions:
            if disruption.shovel not in shovel_ids:
                raise ValueError(f"disruption points at unknown shovel {disruption.shovel!r}")

        for blend in self.blend_targets:
            if blend.dump_zone not in dumps:
                raise ValueError(f"blend target points at unknown dump zone {blend.dump_zone!r}")
            if blend.min_grade is None and blend.max_grade is None:
                raise ValueError(
                    f"blend target on {blend.dump_zone!r} sets neither a floor nor a ceiling"
                )
            if (
                blend.min_grade is not None
                and blend.max_grade is not None
                and blend.min_grade + blend.margin > blend.max_grade - blend.margin
            ):
                raise ValueError(
                    f"blend margin {blend.margin} leaves no room inside the window on "
                    f"{blend.dump_zone!r}"
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
                    capacity_tph=dump.capacity_tph,
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
            disruptions=tuple(
                Disruption(
                    shovel_id=item.shovel,
                    start_s=item.start_min * 60.0,
                    duration_s=item.duration_min * 60.0,
                    status=StatusCode(item.status),
                )
                for item in self.disruptions
            ),
            variability=Variability(
                load_cv=self.variability.load_cv,
                travel_cv=self.variability.travel_cv,
                dump_cv=self.variability.dump_cv,
            ),
            truck_reliability=_reliability(self.truck_reliability),
            shovel_reliability={
                shovel.id: reliability
                for shovel in self.shovels
                if (reliability := _reliability(shovel.reliability)) is not None
            },
            fleets=self._fleets(),
            overrides=Overrides(
                locked=dict(self.dispatcher.locked),
                excluded_trucks=frozenset(self.dispatcher.excluded_trucks),
                excluded_shovels=frozenset(self.dispatcher.excluded_shovels),
                restrictions={
                    item.truck: TruckRestriction(
                        short_hauls_only=item.short_hauls_only,
                        speed_factor=item.speed_factor,
                        load_factor=item.load_factor,
                    )
                    for item in self.dispatcher.restrictions
                },
            ),
            plan_inputs=PlanInputs(
                values_per_tonne={shovel.id: shovel.value_per_tonne for shovel in self.shovels},
                min_rates_tph={shovel.id: shovel.min_rate_tph for shovel in self.shovels},
                dump_values_per_tonne={dump.id: dump.value_per_tonne for dump in self.dump_zones},
                blend_targets=tuple(
                    BlendTarget(
                        dump_zone_id=blend.dump_zone,
                        element=blend.element,
                        min_grade=blend.min_grade,
                        max_grade=blend.max_grade,
                        margin=blend.margin,
                    )
                    for blend in self.blend_targets
                ),
            ),
        )

    def _fleets(self) -> tuple[FleetType, ...]:
        """Trucks grouped by fleet type, which is the unit the LP reasons about."""
        payloads: dict[str, list[float]] = defaultdict(list)
        for truck in self.trucks:
            payloads[truck.fleet_type].append(truck.payload_t)
        return tuple(
            FleetType(name=name, trucks=len(group), payload_t=sum(group) / len(group))
            for name, group in payloads.items()
        )


def _reliability(spec: ReliabilitySpec | None) -> Reliability | None:
    if spec is None:
        return None
    return Reliability(mtbf_s=spec.mtbf_h * 3600.0, mttr_s=spec.mttr_h * 3600.0)


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
            ShovelSpec(
                id="SH01",
                zone="zone_n",
                load_rate_tph=3000,
                target_rate_tph=1400,
                value_per_tonne=4.0,
            ),
            ShovelSpec(
                id="SH02",
                zone="zone_s",
                load_rate_tph=2400,
                target_rate_tph=1000,
                value_per_tonne=3.0,
            ),
            ShovelSpec(
                id="SH03",
                zone="zone_w",
                load_rate_tph=3600,
                target_rate_tph=1600,
                value_per_tonne=1.0,
                # Stripping commitment: without a floor the plan would move no
                # waste at all, since ore is worth more per tonne.
                min_rate_tph=800,
            ),
        ],
        dump_zones=[
            DumpZoneSpec(
                id="crusher",
                node="crusher",
                accepts_ore=True,
                tipping_bays=2,
                dump_time_s=60,
                capacity_tph=2200,
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
        # Forces the plan to mix both ore benches: neither grade sits inside the
        # window on its own. The margin is not slack for drift — it moves the plan
        # off the ceiling to a split the discrete fleet can actually execute. On
        # the ceiling the plan asks for 3:1, which leaves SH02 needing 1.2 trucks
        # out of six; the fleet delivers 5.7:1 and the ore arrives out of spec.
        blend_targets=[
            BlendTargetSpec(
                dump_zone="crusher", element="cu", min_grade=0.6, max_grade=0.8, margin=0.05
            )
        ],
    )


def toy_mine_with_failure() -> ScenarioSpec:
    """The same pit, with the high grade shovel down for 40 minutes.

    The blend window needs both ore benches, so losing one forces the plan to
    stop feeding the crusher altogether and put the fleet on waste until it is
    back — a change no fixed set of targets would make on its own.
    """
    spec = toy_mine().model_dump()
    spec["name"] = "toy-failure"
    spec["disruptions"] = [
        DisruptionSpec(shovel="SH01", start_min=30, duration_min=40).model_dump()
    ]
    # Rebuilt through validation so the disruption's references are checked too.
    return ScenarioSpec.model_validate(spec)


def toy_mine_with_stockpile() -> ScenarioSpec:
    """The same pit with a second ore destination, so the plan has to split.

    The stockpile sits at the top of the ramp — a much shorter haul than the
    crusher — but a tonne left there is worth less than a tonne fed to the
    plant. With the crusher capped below what the benches can produce, the plan
    has to decide how much ore takes the short cheap trip and how much earns
    full value.
    """
    spec = toy_mine().model_dump()
    spec["name"] = "toy-stockpile"
    spec["edges"].append(
        EdgeSpec(
            source="ramp_top", target="stockpile", length_m=300, speed_limit_kph=40
        ).model_dump()
    )
    spec["dump_zones"].append(
        DumpZoneSpec(
            id="stockpile",
            node="stockpile",
            accepts_ore=True,
            tipping_bays=2,
            dump_time_s=50,
            value_per_tonne=0.6,
        ).model_dump()
    )
    for dump in spec["dump_zones"]:
        if dump["id"] == "crusher":
            dump["capacity_tph"] = 1400
    return ScenarioSpec.model_validate(spec)


def toy_mine_variable() -> ScenarioSpec:
    """The same pit, but the world stops being perfect.

    Cycle times get realistic dispersion and both trucks and shovels break down
    at random, at roughly 95 % availability each. The nominal times the engine
    plans with do not change — only what actually happens does.
    """
    spec = toy_mine().model_dump()
    spec["name"] = "toy-variable"
    spec["variability"] = VariabilitySpec(load_cv=0.15, travel_cv=0.10, dump_cv=0.10).model_dump()
    spec["truck_reliability"] = ReliabilitySpec(mtbf_h=40, mttr_h=2).model_dump()
    for shovel in spec["shovels"]:
        shovel["reliability"] = ReliabilitySpec(mtbf_h=60, mttr_h=3).model_dump()
    return ScenarioSpec.model_validate(spec)


def toy_mine_restricted() -> ScenarioSpec:
    """The same pit with half the fleet damaged but still working.

    A real dispatcher rarely parks a machine outright. A cracked tray becomes a
    load restriction, a failing engine a speed restriction, a bad transmission a
    short-haul restriction — the truck keeps earning on worse terms. This is the
    scenario that exercises the operational restrictions of US 11,187,547.
    """
    spec = toy_mine().model_dump()
    spec["name"] = "toy-restricted"
    spec["dispatcher"] = DispatcherSpec(
        restrictions=[
            RestrictionSpec(truck="CAT01", load_factor=0.6),
            RestrictionSpec(truck="CAT02", speed_factor=0.7),
            RestrictionSpec(truck="CAT03", short_hauls_only=True),
        ]
    ).model_dump()
    return ScenarioSpec.model_validate(spec)


SCENARIOS = {
    "toy": toy_mine,
    "toy-failure": toy_mine_with_failure,
    "toy-stockpile": toy_mine_with_stockpile,
    "toy-variable": toy_mine_variable,
    "toy-restricted": toy_mine_restricted,
}


def load_scenario_spec(path: Path) -> ScenarioSpec:
    """Read a mine definition from a YAML or JSON file.

    The file is validated by the same model the built-in scenarios go through,
    so a mistake in a hand-written mine is reported the same way.
    """
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        document = json.loads(text)
    elif path.suffix.lower() in {".yaml", ".yml"}:
        document = yaml.safe_load(text)
    else:
        raise ValueError(f"unsupported scenario format {path.suffix!r}, use .yaml or .json")
    return ScenarioSpec.model_validate(document)


def dump_scenario_spec(spec: ScenarioSpec, path: Path) -> None:
    """Write a mine definition out, so a built-in can seed a hand-edited one."""
    document = spec.model_dump(exclude_defaults=True)
    if path.suffix.lower() == ".json":
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    elif path.suffix.lower() in {".yaml", ".yml"}:
        path.write_text(yaml.safe_dump(document, sort_keys=False), encoding="utf-8")
    else:
        raise ValueError(f"unsupported scenario format {path.suffix!r}, use .yaml or .json")
