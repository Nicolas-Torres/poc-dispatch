from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from statistics import fmean, stdev

from mine_sim.events import Kpis


@dataclass(frozen=True, slots=True)
class Spread:
    """A metric across replicas. With one replica the spread is simply zero."""

    mean: float
    stdev: float

    def format(self, precision: int = 0) -> str:
        if self.stdev == 0.0:
            return f"{self.mean:,.{precision}f}"
        return f"{self.mean:,.{precision}f} +/-{self.stdev:,.{precision}f}"


def spread(runs: list[Kpis], metric: Callable[[Kpis], float]) -> Spread:
    values = [metric(run) for run in runs]
    return Spread(mean=fmean(values), stdev=stdev(values) if len(values) > 1 else 0.0)


def seeds(seed: int, replicas: int) -> list[int]:
    """Seeds for a set of replicas, so two policies can be run on the same worlds."""
    return [seed + offset for offset in range(replicas)]
