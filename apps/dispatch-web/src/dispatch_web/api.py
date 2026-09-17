from __future__ import annotations

from pathlib import Path
from typing import Any

from dispatch_engine.best_path import BestPath
from dispatch_engine.policies.earliest_shovel import EarliestShovelPolicy
from dispatch_engine.policies.longest_waiting_shovel import LongestWaitingShovelPolicy
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.production_plan import ProductionPlan
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from mine_sim.events import Kpis
from mine_sim.planning import solve_scenario_plan
from mine_sim.replicas import seeds, spread
from mine_sim.scenario import SCENARIOS, Scenario
from mine_sim.simulation import Simulation
from pydantic import BaseModel, Field

from dispatch_web import replay

STATIC = Path(__file__).parent / "static"

POLICIES = {
    "neediest": NeediestShovelPolicy,
    "earliest": EarliestShovelPolicy,
    "longest": LongestWaitingShovelPolicy,
}

DEMOS = [
    {
        "id": "shift",
        "title": "Un turno normal",
        "blurb": "La mina operando con el plan del LP. Mirá la ley entregada al chancador.",
        "scenario": "toy",
        "policy": "neediest",
        "hours": 2.0,
    },
    {
        "id": "failure",
        "title": "Se cae una pala",
        "blurb": "SH01 falla 40 minutos. El plan se recalcula solo y la flota se reorienta.",
        "scenario": "toy-failure",
        "policy": "neediest",
        "hours": 2.0,
    },
    {
        "id": "stockpile",
        "title": "Dos destinos para el mineral",
        "blurb": "Chancador o stockpile: el plan decide el reparto, no la cercanía.",
        "scenario": "toy-stockpile",
        "policy": "neediest",
        "hours": 4.0,
    },
    {
        "id": "messy",
        "title": "El mundo real",
        "blurb": "Ciclos con dispersión y equipos que se rompen. Las colas aparecen de verdad.",
        "scenario": "toy-variable",
        "policy": "neediest",
        "hours": 4.0,
    },
    {
        "id": "myopic",
        "title": "Sin plan: la heurística miope",
        "blurb": "La misma mina despachando al más cercano. Mueve más roca y vale menos.",
        "scenario": "toy",
        "policy": "earliest",
        "hours": 2.0,
    },
]


class RunRequest(BaseModel):
    scenario: str = "toy"
    policy: str = "neediest"
    hours: float = Field(default=2.0, gt=0, le=72)
    seed: int = 1


class CompareRequest(BaseModel):
    scenario: str = "toy"
    hours: float = Field(default=4.0, gt=0, le=72)
    seed: int = 1
    replicas: int = Field(default=3, ge=1, le=20)


def _scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise HTTPException(404, f"unknown scenario {name!r}")
    return SCENARIOS[name]().build()


def _simulate(built: Scenario, policy: str, hours: float, seed: int):
    if policy not in POLICIES:
        raise HTTPException(404, f"unknown policy {policy!r}")
    best_path = BestPath(built.mine.network)
    policy_class = POLICIES[policy]
    simulation = Simulation(
        built,
        lambda plan: policy_class(best_path=best_path, plan=plan),
        plan_provider=lambda conditions: solve_scenario_plan(built, best_path, conditions),
        best_path=best_path,
        seed=seed,
    )
    return simulation, simulation.run(until_s=hours * 3600.0), best_path


def _blends(built: Scenario, kpis: Kpis) -> list[dict[str, Any]]:
    rows = []
    for target in built.plan_inputs.blend_targets:
        feeding = {
            zone: tonnes
            for (zone, dump), tonnes in kpis.tonnes_by_route.items()
            if dump == target.dump_zone_id
        }
        total = sum(feeding.values())
        if not total:
            continue
        grade = (
            sum(
                tonnes * built.mine.load_zones[zone].material.grades.get(target.element, 0.0)
                for zone, tonnes in feeding.items()
            )
            / total
        )
        rows.append(
            {
                "dump": target.dump_zone_id,
                "element": target.element,
                "delivered": grade,
                "min": target.min_grade,
                "max": target.max_grade,
                "in_spec": (target.min_grade is None or grade >= target.min_grade - 1e-9)
                and (target.max_grade is None or grade <= target.max_grade + 1e-9),
            }
        )
    return rows


def _value(built: Scenario, kpis: Kpis) -> float:
    shovel_by_zone = {shovel.zone: shovel_id for shovel_id, shovel in built.shovels.items()}
    inputs = built.plan_inputs
    return sum(
        tonnes
        * inputs.values_per_tonne.get(shovel_by_zone[zone], 1.0)
        * inputs.dump_values_per_tonne.get(dump, 1.0)
        for (zone, dump), tonnes in kpis.tonnes_by_route.items()
    )


def _kpis(built: Scenario, kpis: Kpis) -> dict[str, Any]:
    return {
        "tonnes_tipped": kpis.tonnes_total,
        "tonnes_in_transit": kpis.tonnes_in_transit,
        "tonnes_per_hour": kpis.tonnes_per_hour,
        "plan_value": _value(built, kpis),
        "cycles": kpis.cycles,
        "avg_cycle_min": kpis.avg_cycle_time_s / 60.0,
        "truck_queue_min": kpis.truck_queue_time_s / 60.0,
        "dump_queue_min": kpis.dump_queue_time_s / 60.0,
        "standby": kpis.standby_events,
        "breakdowns": kpis.breakdowns,
        "replans": kpis.replans,
        "reassignments": kpis.reassignments,
        "by_dump": kpis.tonnes_by_dump,
        "by_route": {f"{zone} -> {dump}": t for (zone, dump), t in kpis.tonnes_by_route.items()},
        "shovels": [
            {
                "id": s.shovel_id,
                "loads": s.loads,
                "tonnes": s.tonnes,
                "utilisation_pct": s.utilisation_pct,
            }
            for s in kpis.shovels
        ],
        "blends": _blends(built, kpis),
    }


def _plan_rows(plan: ProductionPlan, built: Scenario) -> list[dict[str, Any]]:
    rows = []
    for zone_id in built.mine.load_zones:
        for dump_id, rate in plan.destination_rates_tph(zone_id).items():
            rows.append({"zone": zone_id, "dump": dump_id, "rate_tph": rate})
    return rows


def create_app() -> FastAPI:
    app = FastAPI(title="poc-dispatch demo")

    @app.get("/api/demos")
    def demos() -> dict[str, Any]:
        return {"demos": DEMOS, "scenarios": list(SCENARIOS), "policies": list(POLICIES)}

    @app.post("/api/run")
    def run(request: RunRequest) -> dict[str, Any]:
        built = _scenario(request.scenario)
        simulation, kpis, best_path = _simulate(built, request.policy, request.hours, request.seed)
        payload = replay.build(built, simulation.log, best_path, request.hours * 3600.0)
        payload["kpis"] = _kpis(built, kpis)
        payload["plan"] = _plan_rows(simulation.plan, built)
        payload["blend_targets"] = [
            {
                "dump": target.dump_zone_id,
                "element": target.element,
                "min": target.min_grade,
                "max": target.max_grade,
            }
            for target in built.plan_inputs.blend_targets
        ]
        # Enough for the dashboard to price each load as it is tipped, instead of
        # showing the finished total while the run is still going.
        shovel_by_zone = {shovel.zone: shovel_id for shovel_id, shovel in built.shovels.items()}
        payload["values"] = {
            zone: built.plan_inputs.values_per_tonne.get(shovel_by_zone[zone], 1.0)
            for zone in built.mine.load_zones
        }
        payload["dump_values"] = {
            dump: built.plan_inputs.dump_values_per_tonne.get(dump, 1.0)
            for dump in built.mine.dump_zones
        }
        payload["setup"] = {
            "scenario": request.scenario,
            "policy": request.policy,
            "hours": request.hours,
            "seed": request.seed,
        }
        return payload

    @app.post("/api/compare")
    def compare(request: CompareRequest) -> dict[str, Any]:
        built = _scenario(request.scenario)
        rows = {}
        for name in POLICIES:
            runs = [
                _simulate(built, name, request.hours, run_seed)[1]
                for run_seed in seeds(request.seed, request.replicas)
            ]
            rows[name] = {
                "plan_value": spread(runs, lambda k: _value(built, k)).mean,
                "plan_value_sd": spread(runs, lambda k: _value(built, k)).stdev,
                "tonnes": spread(runs, lambda k: k.tonnes_moved).mean,
                "truck_queue_min": spread(runs, lambda k: k.truck_queue_time_s / 60.0).mean,
                "blends": _blends(built, runs[0]),
            }
        return {"scenario": request.scenario, "replicas": request.replicas, "policies": rows}

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
