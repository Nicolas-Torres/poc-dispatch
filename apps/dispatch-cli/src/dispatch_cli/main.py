from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from statistics import fmean
from typing import Annotated, Any

import typer
from dispatch_engine.best_path import BestPath
from dispatch_engine.lp import BlendTarget
from dispatch_engine.policies.earliest_shovel import EarliestShovelPolicy
from dispatch_engine.policies.longest_waiting_shovel import LongestWaitingShovelPolicy
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.policy import DispatchPolicy
from dispatch_engine.production_plan import ProductionPlan
from mine_sim.events import Kpis
from mine_sim.planning import PlanConditions, solve_scenario_plan
from mine_sim.replicas import seeds, spread
from mine_sim.scenario import (
    SCENARIOS,
    Scenario,
    ScenarioSpec,
    dump_scenario_spec,
    load_scenario_spec,
)
from mine_sim.simulation import PlanProvider, PolicyFactory, Simulation
from pydantic import ValidationError

app = typer.Typer(add_completion=False, no_args_is_help=True)

POLICIES = ("neediest", "earliest", "longest")

PolicyOption = Annotated[
    str,
    typer.Option(
        help=(
            "Assignment strategy: 'neediest' follows the plan; 'earliest' and 'longest' are the "
            "plan-blind baselines from the literature."
        )
    ),
]

PlanOption = Annotated[
    str, typer.Option("--plan", help="Production plan: 'lp' solves stage 2, 'static' uses targets.")
]
ScenarioOption = Annotated[
    str, typer.Option(help="Built-in scenario name, or the path to a YAML/JSON mine of your own.")
]
HorizonOption = Annotated[
    float, typer.Option(help="Window the static plan measures required haulage over.")
]
SeedOption = Annotated[int, typer.Option(help="Seed of the first replica; a run is reproducible.")]
ReplicasOption = Annotated[
    int, typer.Option(min=1, help="Runs to average over. With variability, one run says little.")
]


@app.callback()
def main() -> None:
    """Run DISPATCH-style scenarios on the mine digital twin."""


@app.command("scenarios")
def list_scenarios() -> None:
    """List the built-in scenarios."""
    for name in SCENARIOS:
        typer.echo(name)


@app.command("export-scenario")
def export_scenario(
    out: Annotated[Path, typer.Option(help="Where to write the mine definition.")],
    scenario: ScenarioOption = "toy",
) -> None:
    """Write a scenario out as YAML or JSON, to start your own mine from it."""
    try:
        dump_scenario_spec(_spec(scenario), out)
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"wrote {out}")


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
    policy: PolicyOption = "neediest",
    horizon_min: HorizonOption = 30.0,
    shovel_idle_weight: Annotated[
        float, typer.Option(help="Weight of shovel idle time against truck queueing.")
    ] = 1.0,
    seed: SeedOption = 1,
    replicas: ReplicasOption = 1,
    export_events: Annotated[
        Path | None, typer.Option(help="Write the cycle event log to this CSV file.")
    ] = None,
) -> None:
    """Run a scenario and report haulage KPIs."""
    built = _scenario(scenario)
    best_path = BestPath(built.mine.network)
    provider = _plan_provider(plan, built, best_path, horizon_min)
    factory = _policy_factory(policy, best_path, shovel_idle_weight)

    simulations = []
    runs = []
    for run_seed in seeds(seed, replicas):
        simulation = Simulation(
            built, factory, plan_provider=provider, best_path=best_path, seed=run_seed
        )
        runs.append(simulation.run(until_s=hours * 3600.0))
        simulations.append(simulation)

    setup = f"{plan} plan, {policy} policy"
    if replicas > 1:
        setup += f", {replicas} replicas from seed {seed}"
    _report(built, runs, simulations[-1].plan, setup)

    if export_events is not None:
        log = simulations[0].log
        log.to_csv(export_events)
        typer.echo(f"\nwrote {len(log.events):,} events to {export_events}")


def _spec(name: str) -> ScenarioSpec:
    """A built-in scenario name, or the path to a mine of your own."""
    if name in SCENARIOS:
        return SCENARIOS[name]()

    path = Path(name)
    if not path.is_file():
        raise typer.BadParameter(
            f"{name!r} is neither a built-in scenario ({', '.join(SCENARIOS)}) nor a file"
        )
    try:
        return load_scenario_spec(path)
    except ValidationError as error:
        # The raw pydantic dump repeats the whole document back at you; the
        # messages alone are what tells someone which line to fix.
        problems = "\n".join(f"  - {issue['msg']}" for issue in error.errors())
        raise typer.BadParameter(f"{path} is not a valid scenario:\n{problems}") from error
    except ValueError as error:
        raise typer.BadParameter(str(error)) from error


def _scenario(name: str) -> Scenario:
    return _spec(name).build()


@app.command()
def compare(
    scenario: ScenarioOption = "toy-stockpile",
    hours: Annotated[float, typer.Option(help="Simulated hours to run.")] = 4.0,
    seed: SeedOption = 1,
    # Defaulted above one because comparing two policies on a single noisy run
    # invites conclusions the data does not support.
    replicas: ReplicasOption = 5,
) -> None:
    """Run every policy on the same mine and fleet, and compare what they achieve."""
    built = _scenario(scenario)
    best_path = BestPath(built.mine.network)
    provider = _plan_provider("lp", built, best_path, horizon_min=30.0)

    results: dict[str, list[Kpis]] = {}
    for name in POLICIES:
        factory = _policy_factory(name, best_path, shovel_idle_weight=1.0)
        # The same seeds for every policy: they are compared on the same worlds,
        # not on different ones that happen to share a distribution.
        results[name] = [
            Simulation(
                built, factory, plan_provider=provider, best_path=best_path, seed=run_seed
            ).run(until_s=hours * 3600.0)
            for run_seed in seeds(seed, replicas)
        ]

    replica_note = f", {replicas} replicas from seed {seed}" if replicas > 1 else ""
    typer.echo(f"scenario {built.name} - {hours:.1f} h - lp plan{replica_note}\n")
    typer.echo("  metric                " + "".join(f"{name:>18}" for name in POLICIES))
    _compare_row("tonnes moved", results, lambda kpis: kpis.tonnes_moved)
    _compare_row("plan value", results, lambda kpis: _realised_value(built, kpis))
    _compare_row("cycles", results, lambda kpis: kpis.cycles)
    _compare_row("truck queueing (min)", results, lambda kpis: kpis.truck_queue_time_s / 60, 1)
    _compare_row(
        "shovel idle (min)",
        results,
        lambda kpis: sum(shovel.idle_time_s for shovel in kpis.shovels) / 60,
    )
    for target in built.plan_inputs.blend_targets:
        _compare_row(
            f"{target.dump_zone_id} {target.element}",
            results,
            lambda kpis, target=target: _delivered_grade(built, kpis, target),
            3,
        )
        low = "-" if target.min_grade is None else f"{target.min_grade:.2f}"
        high = "-" if target.max_grade is None else f"{target.max_grade:.2f}"
        typer.echo(f"  {'  window':<22}{low + ' - ' + high:>18}")


def _compare_row(
    label: str,
    results: dict[str, list[Kpis]],
    metric: Callable[[Kpis], float],
    precision: int = 0,
) -> None:
    cells = "".join(f"{spread(results[name], metric).format(precision):>18}" for name in POLICIES)
    typer.echo(f"  {label:<22}{cells}")


def _realised_value(scenario: Scenario, kpis: Kpis) -> float:
    """What the run actually earned under the objective the LP maximises."""
    shovel_by_zone = {shovel.zone: shovel_id for shovel_id, shovel in scenario.shovels.items()}
    inputs = scenario.plan_inputs
    return sum(
        tonnes
        * inputs.values_per_tonne.get(shovel_by_zone[zone_id], 1.0)
        * inputs.dump_values_per_tonne.get(dump_id, 1.0)
        for (zone_id, dump_id), tonnes in kpis.tonnes_by_route.items()
    )


def _delivered_grade(scenario: Scenario, kpis: Kpis, target: BlendTarget) -> float:
    tonnes = 0.0
    graded = 0.0
    for (zone_id, dump_id), route_t in kpis.tonnes_by_route.items():
        if dump_id != target.dump_zone_id:
            continue
        tonnes += route_t
        graded += route_t * scenario.mine.load_zones[zone_id].material.grades.get(
            target.element, 0.0
        )
    return graded / tonnes if tonnes else 0.0


def _policy_factory(kind: str, best_path: BestPath, shovel_idle_weight: float) -> PolicyFactory:
    if kind == "neediest":

        def dispatch_like(plan: ProductionPlan) -> DispatchPolicy:
            return NeediestShovelPolicy(
                best_path=best_path, plan=plan, shovel_idle_weight=shovel_idle_weight
            )

        return dispatch_like

    if kind == "earliest":

        def myopic(plan: ProductionPlan) -> DispatchPolicy:
            return EarliestShovelPolicy(best_path=best_path, plan=plan)

        return myopic

    if kind == "longest":

        def even(plan: ProductionPlan) -> DispatchPolicy:
            return LongestWaitingShovelPolicy(best_path=best_path, plan=plan)

        return even

    raise typer.BadParameter(f"unknown policy {kind!r}, try: {', '.join(POLICIES)}")


def _plan_provider(
    kind: str, scenario: Scenario, best_path: BestPath, horizon_min: float
) -> PlanProvider:
    if kind == "lp":

        def solved(conditions: PlanConditions) -> ProductionPlan:
            return solve_scenario_plan(scenario, best_path, conditions)

        return solved

    if kind == "static":
        # Fixed targets cannot react to a shovel going down; that is the point of
        # keeping this option around to compare against.
        targets = replace(scenario.plan, horizon_s=horizon_min * 60.0)

        def fixed(_conditions: PlanConditions) -> ProductionPlan:
            return targets

        return fixed

    raise typer.BadParameter(f"unknown plan {kind!r}, try: lp, static")


def _mean_of(runs: list[Kpis], table: Callable[[Kpis], dict[Any, float]]) -> dict[Any, float]:
    totals: dict[Any, float] = defaultdict(float)
    for run in runs:
        for key, value in table(run).items():
            totals[key] += value / len(runs)
    return dict(totals)


def _mean_kpis(runs: list[Kpis]) -> Kpis:
    """A Kpis whose tables hold the average over replicas, for the detail tables."""
    return replace(
        runs[0],
        tonnes_by_dump=_mean_of(runs, lambda run: run.tonnes_by_dump),
        tonnes_by_route=_mean_of(runs, lambda run: run.tonnes_by_route),
        shovels=tuple(
            replace(
                shovel,
                loads=round(fmean([run.shovels[index].loads for run in runs])),
                tonnes=fmean([run.shovels[index].tonnes for run in runs]),
                engaged_time_s=fmean([run.shovels[index].engaged_time_s for run in runs]),
                idle_time_s=fmean([run.shovels[index].idle_time_s for run in runs]),
            )
            for index, shovel in enumerate(runs[0].shovels)
        ),
    )


def _report_blends(scenario: Scenario, kpis: Kpis) -> None:
    """What grade actually arrived, against the window the plan was solved for."""
    if not scenario.plan_inputs.blend_targets:
        return

    typer.echo("\n  destination   element   delivered   window")
    for target in scenario.plan_inputs.blend_targets:
        delivered = _delivered_grade(scenario, kpis, target)
        if delivered == 0.0:
            continue
        low = "-" if target.min_grade is None else f"{target.min_grade:.2f}"
        high = "-" if target.max_grade is None else f"{target.max_grade:.2f}"
        # Flagged rather than left for the reader to spot: a blend a hair over
        # the limit is exactly what the plant rejects.
        out_of_spec = (target.min_grade is not None and delivered < target.min_grade) or (
            target.max_grade is not None and delivered > target.max_grade
        )
        typer.echo(
            f"  {target.dump_zone_id:<13} {target.element:<9} {delivered:>9.3f}   {low} - {high}"
            f"{'   OUT OF SPEC' if out_of_spec else ''}"
        )


def _report(scenario: Scenario, runs: list[Kpis], plan: ProductionPlan, setup: str) -> None:
    kpis = _mean_kpis(runs)

    def line(label: str, metric: Callable[[Kpis], float], precision: int = 0) -> str:
        return f"  {label:<16}{spread(runs, metric).format(precision):>14}"

    # Counts read as whole numbers on a single run; averaged over replicas a
    # fraction is the honest answer.
    counts = 0 if len(runs) == 1 else 1

    # Plain ASCII only: Windows consoles default to cp1252 and mangle dashes.
    typer.echo(f"scenario {scenario.name} - {kpis.horizon_s / 3600:.1f} h - {setup}")
    typer.echo(line("tonnes tipped", lambda k: k.tonnes_total) + " t")
    typer.echo(line("in transit", lambda k: k.tonnes_in_transit) + " t  (not tipped at cut-off)")
    typer.echo(line("cycles", lambda k: k.cycles))
    typer.echo(line("avg cycle time", lambda k: k.avg_cycle_time_s / 60, 1) + " min")
    typer.echo(line("truck queueing", lambda k: k.truck_queue_time_s / 60, 1) + " min at shovels")
    typer.echo(line("dump queueing", lambda k: k.dump_queue_time_s / 60, 1) + " min")
    typer.echo(line("standby events", lambda k: k.standby_events, counts))
    if kpis.replans or kpis.reassignments or kpis.breakdowns:
        typer.echo(line("breakdowns", lambda k: k.breakdowns, counts))
        typer.echo(line("replans", lambda k: k.replans, counts))
        typer.echo(line("reassignments", lambda k: k.reassignments, counts))

    hours = kpis.horizon_s / 3600.0
    typer.echo("\n  route                      tonnes      t/h   plan t/h")
    for (zone_id, dump_id), tonnes in sorted(kpis.tonnes_by_route.items()):
        planned_tph = plan.destination_rates_tph(zone_id).get(dump_id, 0.0)
        typer.echo(
            f"  {f'{zone_id} -> {dump_id}':<24} {tonnes:>8,.0f} {tonnes / hours:>8,.0f}"
            f" {planned_tph:>10,.0f}"
        )

    _report_blends(scenario, kpis)

    typer.echo("\n  shovel   loads    tonnes      t/h   plan t/h   util")
    for shovel in kpis.shovels:
        typer.echo(
            f"  {shovel.shovel_id:<8} {shovel.loads:>5} {shovel.tonnes:>9,.0f}"
            f" {shovel.tonnes / hours:>8,.0f} {plan.required_rate_tph(shovel.shovel_id):>10,.0f}"
            f" {shovel.utilisation_pct:>6.0f}%"
        )
