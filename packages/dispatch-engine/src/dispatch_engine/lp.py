from __future__ import annotations

from dataclasses import dataclass, field

from ortools.linear_solver import pywraplp

from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import Shovel, ShovelId
from dispatch_engine.domain.mine import DumpZone, LoadZone, Mine, ZoneId


class InfeasiblePlanError(RuntimeError):
    """No flow assignment satisfies the capacities, floors and blend limits given."""


@dataclass(frozen=True, slots=True)
class FleetType:
    name: str
    trucks: int
    payload_t: float


@dataclass(frozen=True, slots=True)
class BlendTarget:
    """Grade window the blend delivered to a destination has to stay inside.

    A value-maximising LP parks its solution on whichever constraints bind, so
    whenever this window is one of them the plan lands exactly on the limit and
    leaves the operation no room to drift before it is out of spec. `margin`
    shrinks the window the plan is solved against, while the window itself stays
    the spec the delivered blend is judged by.
    """

    dump_zone_id: ZoneId
    element: str
    min_grade: float | None = None
    max_grade: float | None = None
    margin: float = 0.0

    def planning_limits(self) -> tuple[float | None, float | None]:
        return (
            None if self.min_grade is None else self.min_grade + self.margin,
            None if self.max_grade is None else self.max_grade - self.margin,
        )


@dataclass(frozen=True, slots=True)
class PlanInputs:
    """Planning levers that are not properties of the equipment itself."""

    # c_r of the patent formulation: what a tonne off this shovel is worth.
    values_per_tonne: dict[ShovelId, float] = field(default_factory=dict)
    # Multiplier on that value depending on where the tonne is tipped: the same
    # ore is worth less on a stockpile than fed through the crusher.
    dump_values_per_tonne: dict[ZoneId, float] = field(default_factory=dict)
    # Production floors, e.g. the stripping commitment on a waste shovel.
    min_rates_tph: dict[ShovelId, float] = field(default_factory=dict)
    blend_targets: tuple[BlendTarget, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteFlow:
    """Flow x_r on one DISPATCH route: load zone, dump zone, haul and fleet type."""

    shovel_id: ShovelId
    load_zone_id: ZoneId
    dump_zone_id: ZoneId
    fleet_type: str
    cycle_time_s: float
    rate_tph: float


@dataclass(frozen=True, slots=True)
class LpProductionPlan:
    """Stage 2 result: the ideal flow rate on every route the plan uses."""

    flows: tuple[RouteFlow, ...]

    def required_rate_tph(self, shovel_id: ShovelId) -> float:
        return sum(flow.rate_tph for flow in self.flows if flow.shovel_id == shovel_id)

    def required_haulage_t(self, shovel_id: ShovelId) -> float:
        # Little's law: sustaining x t/h on a route whose cycle lasts c hours needs
        # x*c tonnes in flight on it. This is what makes a distant shovel ask for
        # more trucks than a near one running at the same rate.
        return sum(
            flow.rate_tph * flow.cycle_time_s / 3600.0
            for flow in self.flows
            if flow.shovel_id == shovel_id
        )

    def rate_by_dump_tph(self, dump_zone_id: ZoneId) -> float:
        return sum(flow.rate_tph for flow in self.flows if flow.dump_zone_id == dump_zone_id)

    def destination_rates_tph(self, load_zone_id: ZoneId) -> dict[ZoneId, float]:
        rates: dict[ZoneId, float] = {}
        for flow in self.flows:
            if flow.load_zone_id == load_zone_id:
                rates[flow.dump_zone_id] = rates.get(flow.dump_zone_id, 0.0) + flow.rate_tph
        return rates


@dataclass(frozen=True, slots=True)
class _Candidate:
    shovel: Shovel
    zone: LoadZone
    dump: DumpZone
    fleet: FleetType
    cycle_time_s: float
    shovel_hours_per_tonne: float
    bay_hours_per_tonne: float
    truck_hours_per_tonne: float
    value_per_tonne: float


def _candidates(
    mine: Mine,
    shovels: dict[ShovelId, Shovel],
    fleets: tuple[FleetType, ...],
    best_path: BestPath,
    spot_time_s: float,
    inputs: PlanInputs,
) -> list[_Candidate]:
    candidates = []
    for shovel in shovels.values():
        zone = mine.load_zones[shovel.zone]
        for dump in mine.dump_zones.values():
            if not dump.accepts(zone.material):
                continue
            for fleet in fleets:
                load_time_s = fleet.payload_t / shovel.load_rate_tph * 3600.0
                cycle_time_s = (
                    best_path.travel_time_s(dump.node, zone.node, loaded=False)
                    + spot_time_s
                    + load_time_s
                    + best_path.travel_time_s(zone.node, dump.node, loaded=True)
                    + dump.dump_time_s
                )
                # Every constraint is expressed as the fraction of a resource a
                # tonne per hour consumes, so mixed payloads and fleet types
                # compose without special cases.
                tonne_hours = fleet.payload_t * 3600.0
                candidates.append(
                    _Candidate(
                        shovel=shovel,
                        zone=zone,
                        dump=dump,
                        fleet=fleet,
                        cycle_time_s=cycle_time_s,
                        shovel_hours_per_tonne=(spot_time_s + load_time_s) / tonne_hours,
                        bay_hours_per_tonne=dump.dump_time_s / tonne_hours,
                        truck_hours_per_tonne=cycle_time_s / tonne_hours,
                        value_per_tonne=(
                            inputs.values_per_tonne.get(shovel.id, 1.0)
                            * inputs.dump_values_per_tonne.get(dump.id, 1.0)
                        ),
                    )
                )
    return candidates


def solve_production_plan(
    *,
    mine: Mine,
    shovels: dict[ShovelId, Shovel],
    fleets: tuple[FleetType, ...],
    best_path: BestPath,
    spot_time_s: float = 0.0,
    inputs: PlanInputs | None = None,
) -> LpProductionPlan:
    """Macro optimisation: max cx subject to Hx = b, x >= 0 (US 11,187,547).

    Solves the ideal flow rate on every load zone → dump zone → fleet type route,
    limited by digging capacity, destination intake, tipping bays, fleet size and
    blend windows. Re-solve it when conditions change — a shovel going down, a
    material change, trucks entering or leaving — never per truck request.
    """
    inputs = inputs if inputs is not None else PlanInputs()
    candidates = _candidates(mine, shovels, fleets, best_path, spot_time_s, inputs)

    solver = pywraplp.Solver.CreateSolver("GLOP")
    routes = [
        (solver.NumVar(0.0, solver.infinity(), f"x_{index}"), candidate)
        for index, candidate in enumerate(candidates)
    ]

    for shovel_id in shovels:
        digging = solver.Constraint(0.0, 1.0)
        production = solver.Constraint(inputs.min_rates_tph.get(shovel_id, 0.0), solver.infinity())
        for var, candidate in routes:
            if candidate.shovel.id == shovel_id:
                digging.SetCoefficient(var, candidate.shovel_hours_per_tonne)
                production.SetCoefficient(var, 1.0)

    for dump in mine.dump_zones.values():
        tipping = solver.Constraint(0.0, float(dump.tipping_bays))
        intake = solver.Constraint(
            0.0, dump.capacity_tph if dump.capacity_tph is not None else solver.infinity()
        )
        for var, candidate in routes:
            if candidate.dump.id == dump.id:
                tipping.SetCoefficient(var, candidate.bay_hours_per_tonne)
                intake.SetCoefficient(var, 1.0)

    for fleet in fleets:
        availability = solver.Constraint(0.0, float(fleet.trucks))
        for var, candidate in routes:
            if candidate.fleet.name == fleet.name:
                availability.SetCoefficient(var, candidate.truck_hours_per_tonne)

    for target in inputs.blend_targets:
        _add_blend_constraints(solver, routes, target)

    objective = solver.Objective()
    for var, candidate in routes:
        objective.SetCoefficient(var, candidate.value_per_tonne)
    objective.SetMaximization()

    if solver.Solve() != pywraplp.Solver.OPTIMAL:
        raise InfeasiblePlanError(
            "no optimal production plan: check shovel floors, destination capacity and blend limits"
        )

    return LpProductionPlan(
        flows=tuple(
            RouteFlow(
                shovel_id=candidate.shovel.id,
                load_zone_id=candidate.zone.id,
                dump_zone_id=candidate.dump.id,
                fleet_type=candidate.fleet.name,
                cycle_time_s=candidate.cycle_time_s,
                rate_tph=var.solution_value(),
            )
            for var, candidate in routes
            # Vertex solutions leave numerical dust on the routes they do not use.
            if var.solution_value() > 1e-6
        )
    )


def _add_blend_constraints(
    solver: pywraplp.Solver,
    routes: list[tuple[pywraplp.Variable, _Candidate]],
    target: BlendTarget,
) -> None:
    """Grade limits as linear constraints on the mix arriving at a destination.

    sum_r (g_r - max) x_r <= 0 keeps the blend under the ceiling and
    sum_r (min - g_r) x_r <= 0 keeps it over the floor, without dividing by a
    total flow that may be zero.
    """
    feeding = [
        (var, candidate) for var, candidate in routes if candidate.dump.id == target.dump_zone_id
    ]
    min_grade, max_grade = target.planning_limits()

    if max_grade is not None:
        ceiling = solver.Constraint(-solver.infinity(), 0.0)
        for var, candidate in feeding:
            grade = candidate.zone.material.grades.get(target.element, 0.0)
            ceiling.SetCoefficient(var, grade - max_grade)

    if min_grade is not None:
        floor = solver.Constraint(-solver.infinity(), 0.0)
        for var, candidate in feeding:
            grade = candidate.zone.material.grades.get(target.element, 0.0)
            floor.SetCoefficient(var, min_grade - grade)
