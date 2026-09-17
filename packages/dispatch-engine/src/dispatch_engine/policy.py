from __future__ import annotations

from typing import Protocol

from dispatch_engine.domain.assignment import Assignment, Destination
from dispatch_engine.domain.equipment import TruckId
from dispatch_engine.domain.mine import ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot


class DispatchPolicy(Protocol):
    """Stage 3: decides which shovel a truck is sent to.

    The simulation depends on this protocol and never on a concrete policy, so
    assignment strategies stay interchangeable.
    """

    def assign(self, snapshot: MineSnapshot, truck_id: TruckId) -> Assignment | None:
        """Shovel for `truck_id`, or None when none can take it."""
        ...

    def choose_destination(
        self, snapshot: MineSnapshot, truck_id: TruckId, load_zone_id: ZoneId
    ) -> Destination:
        """Where the truck tips what it just loaded.

        A route in DISPATCH terms is load zone and dump zone together, so the
        same policy decides both legs.
        """
        ...
