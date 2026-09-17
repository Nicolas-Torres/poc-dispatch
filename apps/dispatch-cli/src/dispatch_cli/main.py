from __future__ import annotations

from dataclasses import replace
from typing import Annotated

import typer
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import ShovelId
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.policy import DispatchPolicy
from dispatch_engine.production_plan import ProductionPlan
from mine_sim.events import Kpis
from mine_sim.planning import solve_scenario_plan
from mine_sim.scenario import SCENARIOS, Scenario
from mine_sim.simulation import PlanProvider, PolicyFactory, Simulation

app = typer.Typer(add_completion=False, no_args_is_help=True)

PlanOption = Annotated[
    str, typer.Option("--plan", help="Production plan: 'lp' solves stage 2, 'static' uses targets.")
]
ScenarioOption = Annotated[str, typer.Option(help="Scenario to simulate.")]
HorizonOption = Annotated[
    float, typer.Option(help="Window the static plan measures required haulage over.")
]


@app.callback()
def main() -> None:
    """Run DISPATCH-style scenarios on the mine digital twin."""


@app.command("scenarios")
def list_scenarios() -> None:
    """List the built-in scenarios."""
    for name in SCENARIOS:
        typer.echo(name)


@app.command("plan")
def show_plan(scenario: ScenarioOption = "toy") -> None:
    """Solve the production plan (stage 2) and print the flow on every route."""
    built = _scenario(scenario)
    solved = solve_scenario_plan(built, BestPath(built.mine.network))

    typer.echo("  shovel   zone      destination      t/h    cycle   in flight")
    for flow in sorted(solved.flows, key=lambda item: item.shovel_id):
        typer.echo(
            f"  {flow.shovel_id:<8} {flow.load_zone_id:<9} {flow.dump_zone_id:<14}"
            f" {flow.rate_tph:>7,.0f} {flow.cycle_time_s / 60:>7.1f}m"
            f" {flow.rate_tph * flow.cycle_time_s / 3600:>9,.0f} t"
        )
    total = sum(flow.rate_tph for flow in solved.flows)
    typer.echo(f"\n  total {total:,.0f} t/h")


@app.command()
def run(
    scenario: ScenarioOption = "toy",
    hours: Annotated[float, typer.Option(help="Simulated hours to run.")] = 2.0,
    plan: PlanOption = "lp",
    horizon_min: HorizonOption = 30.0,
    shovel_idle_weight: Annotated[
        float, typer.Option(help="Weight of shovel idle time against truck queueing.")
    ] = 1.0,
) -> None:
    """Run a scenario and report haulage KPIs."""
    built = _scenario(scenario)
    best_path = BestPath(built.mine.network)

    simulation = Simulation(
        built,
        _policy_factory(best_path, shovel_idle_weight),
        plan_provider=_plan_provider(plan, built, best_path, horizon_min),
        best_path=best_path,
    )
    kpis = simulation.run(until_s=hours * 3600.0)
    _report(built, kpis, simulation.plan, plan)


def _scenario(name: str) -> Scenario:
    if name not in SCENARIOS:
        raise typer.BadParameter(f"unknown scenario {name!r}, try: {', '.join(SCENARIOS)}")
    return SCENARIOS[name]().build()


def _policy_factory(best_path: BestPath, shovel_idle_weight: float) -> PolicyFactory:
    def build(plan: ProductionPlan) -> DispatchPolicy:
        return NeediestShovelPolicy(
            best_path=best_path, plan=plan, shovel_idle_weight=shovel_idle_weight
        )

    return build


def _plan_provider(
    kind: str, scenario: Scenario, best_path: BestPath, horizon_min: float
) -> PlanProvider:
    if kind == "lp":

        def solved(unavailable: frozenset[ShovelId]) -> ProductionPlan:
            return solve_scenario_plan(scenario, best_path, unavailable=unavailable)

        return solved

    if kind == "static":
        # Fixed targets cannot react to a shovel going down; that is the point of
        # keeping this option around to compare against.
        targets = replace(scenario.plan, horizon_s=horizon_min * 60.0)

        def fixed(_unavailable: frozenset[ShovelId]) -> ProductionPlan:
            return targets

        return fixed

    raise typer.BadParameter(f"unknown plan {kind!r}, try: lp, static")


def _report(scenario: Scenario, kpis: Kpis, plan: ProductionPlan, plan_kind: str) -> None:
    # Plain ASCII only: Windows consoles default to cp1252 and mangle dashes.
    typer.echo(f"scenario {scenario.name} - {kpis.horizon_s / 3600:.1f} h - {plan_kind} plan")
    typer.echo(
        f"  tonnes moved    {kpis.tonnes_total:>10,.0f} t  ({kpis.tonnes_per_hour:,.0f} t/h)"
    )
    typer.echo(f"  cycles          {kpis.cycles:>10}")
    typer.echo(f"  avg cycle time  {kpis.avg_cycle_time_s / 60:>10.1f} min")
    typer.echo(f"  truck queueing  {kpis.truck_queue_time_s / 60:>10.1f} min at shovels")
    typer.echo(f"  dump queueing   {kpis.dump_queue_time_s / 60:>10.1f} min")
    typer.echo(f"  standby events  {kpis.standby_events:>10}")
    if kpis.replans or kpis.reassignments:
        typer.echo(f"  replans         {kpis.replans:>10}")
        typer.echo(f"  reassignments   {kpis.reassignments:>10}")

    typer.echo("\n  destination            tonnes")
    for dump_id, tonnes in sorted(kpis.tonnes_by_dump.items()):
        typer.echo(f"  {dump_id:<20} {tonnes:>8,.0f}")

    hours = kpis.horizon_s / 3600.0
    typer.echo("\n  shovel   loads    tonnes      t/h   plan t/h   util")
    for shovel in kpis.shovels:
        typer.echo(
            f"  {shovel.shovel_id:<8} {shovel.loads:>5} {shovel.tonnes:>9,.0f}"
            f" {shovel.tonnes / hours:>8,.0f} {plan.required_rate_tph(shovel.shovel_id):>10,.0f}"
            f" {shovel.utilisation_pct:>6.0f}%"
        )
