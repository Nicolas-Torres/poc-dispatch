from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from dispatch_engine.domain.equipment import ShovelId
from dispatch_engine.domain.mine import ZoneId


class ProductionPlan(Protocol):
    """Stage 2 output, as consumed by the real-time assignment stage."""

    def required_rate_tph(self, shovel_id: ShovelId) -> float:
        """Target haulage rate the shovel should sustain, in tonnes per hour."""
        ...

    def required_haulage_t(self, shovel_id: ShovelId) -> float:
        """Tonnes that must be committed to the shovel to sustain that rate."""
        ...

    def destination_rates_tph(self, load_zone_id: ZoneId) -> dict[ZoneId, float]:
        """How the zone's output should split across destinations.

        Empty when the plan has no opinion, which is the signal to fall back to
        whatever destination is nearest.
        """
        ...


@dataclass(frozen=True, slots=True)
class StaticProductionPlan:
    """Fixed per-shovel targets taken from the scenario.

    With no route cycle times there is no honest way to turn a rate into the
    haulage it needs in flight, so it falls back to a fixed window: whatever the
    shovel should move in the next `horizon_s`. That approximation is what biases
    the split towards shovels with short cycles — `LpProductionPlan` derives the
    same number from the cycle time instead.
    """

    targets_tph: dict[ShovelId, float]
    horizon_s: float = 1800.0

    def required_rate_tph(self, shovel_id: ShovelId) -> float:
        return self.targets_tph.get(shovel_id, 0.0)

    def required_haulage_t(self, shovel_id: ShovelId) -> float:
        return self.required_rate_tph(shovel_id) * self.horizon_s / 3600.0

    def destination_rates_tph(self, load_zone_id: ZoneId) -> dict[ZoneId, float]:
        """Fixed targets are per shovel, so they say nothing about destinations."""
        return {}
