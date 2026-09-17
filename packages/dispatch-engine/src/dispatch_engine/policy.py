from __future__ import annotations

from typing import Protocol

from dispatch_engine.domain.assignment import Assignment
from dispatch_engine.domain.equipment import TruckId
from dispatch_engine.domain.snapshot import MineSnapshot


class DispatchPolicy(Protocol):
    """Stage 3: decides which shovel a truck is sent to.

    The simulation depends on this protocol and never on a concrete policy, so
    assignment strategies stay interchangeable.
    """

    def assign(self, snapshot: MineSnapshot, truck_id: TruckId) -> Assignment | None:
        """Destination for `truck_id`, or None when no shovel can take it."""
        ...
