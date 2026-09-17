from __future__ import annotations

from typing import Annotated

import typer
from dispatch_engine.best_path import BestPath
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from mine_sim.events import Kpis
from mine_sim.scenario import SCENARIOS
from mine_sim.simulation import Simulation

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.callback()
def main() -> None:
    """Run DISPATCH-style scenarios on the mine digital twin."""


@app.command("scenarios")
def list_scenarios() -> None:
    """List the built-in scenarios."""
    for name in SCENARIOS:
        typer.echo(name)


@app.command()
def run(
    scenario: Annotated[str, typer.Option(help="Scenario to simulate.")] = "toy",
    hours: Annotated[float, typer.Option(help="Simulated hours to run.")] = 2.0,
    horizon_min: Annotated[
        float, typer.Option(help="Window the policy measures required haulage over.")
    ] = 30.0,
    shovel_idle_weight: Annotated[
        float, typer.Option(help="Weight of shovel idle time against truck queueing.")
    ] = 1.0,
) -> None:
    """Run a scenario and report haulage KPIs."""
    if scenario not in SCENARIOS:
        raise typer.BadParameter(f"unknown scenario {scenario!r}, try: {', '.join(SCENARIOS)}")

    built = SCENARIOS[scenario]().build()
    best_path = BestPath(built.mine.network)
    policy = NeediestShovelPolicy(
        best_path=best_path,
        plan=built.plan,
        horizon_s=horizon_min * 60.0,
        shovel_idle_weight=shovel_idle_weight,
    )
    simulation = Simulation(built, policy, best_path=best_path)
    _report(built.name, simulation.run(until_s=hours * 3600.0))


def _report(scenario_name: str, kpis: Kpis) -> None:
    # Plain ASCII only: Windows consoles default to cp1252 and mangle dashes.
    typer.echo(f"scenario {scenario_name} - {kpis.horizon_s / 3600:.1f} h simulated")
    typer.echo(
        f"  tonnes moved    {kpis.tonnes_total:>10,.0f} t  ({kpis.tonnes_per_hour:,.0f} t/h)"
    )
    typer.echo(f"  cycles          {kpis.cycles:>10}")
    typer.echo(f"  avg cycle time  {kpis.avg_cycle_time_s / 60:>10.1f} min")
    typer.echo(f"  truck queueing  {kpis.truck_queue_time_s / 60:>10.1f} min at shovels")
    typer.echo(f"  dump queueing   {kpis.dump_queue_time_s / 60:>10.1f} min")
    typer.echo(f"  standby events  {kpis.standby_events:>10}")

    typer.echo("\n  destination            tonnes")
    for dump_id, tonnes in sorted(kpis.tonnes_by_dump.items()):
        typer.echo(f"  {dump_id:<20} {tonnes:>8,.0f}")

    typer.echo("\n  shovel   loads    tonnes   engaged   idle    util")
    for shovel in kpis.shovels:
        typer.echo(
            f"  {shovel.shovel_id:<8} {shovel.loads:>5} {shovel.tonnes:>9,.0f}"
            f" {shovel.engaged_time_s / 60:>8.1f}m {shovel.idle_time_s / 60:>6.1f}m"
            f" {shovel.utilisation_pct:>6.0f}%"
        )
