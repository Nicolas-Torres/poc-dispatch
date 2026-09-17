from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dispatch_engine.domain.equipment import ShovelId


class ProductionPlan(Protocol):
    """Stage 2 output, as consumed by the real-time assignment stage."""

    def required_rate_tph(self, shovel_id: ShovelId) -> float:
        """Target haulage rate the shovel should sustain, in tonnes per hour."""
        ...


@dataclass(frozen=True, slots=True)
class StaticProductionPlan:
    """Fixed per-shovel targets taken from the scenario.

    Stands in for the LP until the macro optimisation stage is built. Stage 3
    only ever sees this interface, so swapping in the solver later leaves the
    assignment code untouched.
    """

    targets_tph: dict[ShovelId, float]

    def required_rate_tph(self, shovel_id: ShovelId) -> float:
        return self.targets_tph.get(shovel_id, 0.0)
