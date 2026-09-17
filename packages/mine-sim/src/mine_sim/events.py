from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum


class EventKind(StrEnum):
    ASSIGNED = "assigned"
    STANDBY = "standby"
    ARRIVE_SHOVEL = "arrive_shovel"
    SPOT_START = "spot_start"
    LOAD_START = "load_start"
    LOAD_END = "load_end"
    ARRIVE_DUMP = "arrive_dump"
    DUMP_START = "dump_start"
    DUMP_END = "dump_end"


@dataclass(frozen=True, slots=True)
class Event:
    time_s: float
    kind: EventKind
    truck_id: str
    shovel_id: str | None = None
    dump_id: str | None = None
    payload_t: float = 0.0
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ShovelKpis:
    shovel_id: str
    loads: int
    tonnes: float
    engaged_time_s: float
    idle_time_s: float

    @property
    def utilisation_pct(self) -> float:
        span_s = self.engaged_time_s + self.idle_time_s
        return 100.0 * self.engaged_time_s / span_s if span_s else 0.0


@dataclass(frozen=True, slots=True)
class Kpis:
    horizon_s: float
    tonnes_total: float
    tonnes_by_dump: dict[str, float]
    cycles: int
    avg_cycle_time_s: float
    truck_queue_time_s: float
    dump_queue_time_s: float
    standby_events: int
    shovels: tuple[ShovelKpis, ...]

    @property
    def tonnes_per_hour(self) -> float:
        return self.tonnes_total / (self.horizon_s / 3600.0) if self.horizon_s else 0.0


@dataclass(slots=True)
class EventLog:
    events: list[Event] = field(default_factory=list)

    def record(
        self,
        time_s: float,
        kind: EventKind,
        truck_id: str,
        *,
        shovel_id: str | None = None,
        dump_id: str | None = None,
        payload_t: float = 0.0,
        detail: str = "",
    ) -> None:
        self.events.append(
            Event(
                time_s=time_s,
                kind=kind,
                truck_id=truck_id,
                shovel_id=shovel_id,
                dump_id=dump_id,
                payload_t=payload_t,
                detail=detail,
            )
        )

    def kpis(self, *, horizon_s: float, shovel_ids: list[str]) -> Kpis:
        tonnes_by_dump: dict[str, float] = defaultdict(float)
        tonnes_by_shovel: dict[str, float] = defaultdict(float)
        loads_by_shovel: dict[str, int] = defaultdict(int)
        engaged_by_shovel: dict[str, float] = defaultdict(float)
        assigned_at: dict[str, float] = {}
        arrive_shovel_at: dict[str, float] = {}
        arrive_dump_at: dict[str, float] = {}
        spotting_since: dict[str, tuple[str, float]] = {}
        cycle_times_s: list[float] = []
        truck_queue_time_s = 0.0
        dump_queue_time_s = 0.0
        standby_events = 0

        for event in self.events:
            if event.kind is EventKind.ASSIGNED:
                assigned_at[event.truck_id] = event.time_s
            elif event.kind is EventKind.STANDBY:
                standby_events += 1
            elif event.kind is EventKind.ARRIVE_SHOVEL:
                arrive_shovel_at[event.truck_id] = event.time_s
            elif event.kind is EventKind.SPOT_START:
                assert event.shovel_id is not None
                spotting_since[event.truck_id] = (event.shovel_id, event.time_s)
                truck_queue_time_s += event.time_s - arrive_shovel_at.pop(event.truck_id)
            elif event.kind is EventKind.LOAD_END:
                shovel_id, engaged_since_s = spotting_since.pop(event.truck_id)
                # The shovel is tied up from the moment the truck starts spotting,
                # not only while the bucket swings.
                engaged_by_shovel[shovel_id] += event.time_s - engaged_since_s
                tonnes_by_shovel[shovel_id] += event.payload_t
                loads_by_shovel[shovel_id] += 1
            elif event.kind is EventKind.ARRIVE_DUMP:
                arrive_dump_at[event.truck_id] = event.time_s
            elif event.kind is EventKind.DUMP_START:
                dump_queue_time_s += event.time_s - arrive_dump_at.pop(event.truck_id)
            elif event.kind is EventKind.DUMP_END:
                assert event.dump_id is not None
                tonnes_by_dump[event.dump_id] += event.payload_t
                cycle_times_s.append(event.time_s - assigned_at.pop(event.truck_id))

        return Kpis(
            horizon_s=horizon_s,
            tonnes_total=sum(tonnes_by_dump.values()),
            tonnes_by_dump=dict(tonnes_by_dump),
            cycles=len(cycle_times_s),
            avg_cycle_time_s=sum(cycle_times_s) / len(cycle_times_s) if cycle_times_s else 0.0,
            truck_queue_time_s=truck_queue_time_s,
            dump_queue_time_s=dump_queue_time_s,
            standby_events=standby_events,
            shovels=tuple(
                ShovelKpis(
                    shovel_id=shovel_id,
                    loads=loads_by_shovel[shovel_id],
                    tonnes=tonnes_by_shovel[shovel_id],
                    engaged_time_s=engaged_by_shovel[shovel_id],
                    idle_time_s=max(0.0, horizon_s - engaged_by_shovel[shovel_id]),
                )
                for shovel_id in shovel_ids
            ),
        )
