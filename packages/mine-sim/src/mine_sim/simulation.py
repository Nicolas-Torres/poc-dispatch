from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass

import simpy
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import (
    CycleState,
    ShovelId,
    StatusCode,
    Truck,
)
from dispatch_engine.domain.mine import DumpZone, LoadZone, NodeId
from dispatch_engine.domain.snapshot import MineSnapshot, Overrides, TruckStatus
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.policy import DispatchPolicy

from mine_sim.events import EventKind, EventLog, Kpis
from mine_sim.scenario import Scenario


@dataclass(slots=True)
class _TruckRuntime:
    truck: Truck
    state: CycleState
    status: StatusCode
    free_at_node: NodeId
    free_at_s: float = 0.0
    assigned_shovel: ShovelId | None = None
    arrive_at_shovel_s: float = 0.0


class Simulation:
    """Digital twin of the haul cycle, driven by a dispatch policy.

    Shovels and dump zones are SimPy resources, so queues emerge from
    contention rather than being modelled explicitly. The policy is asked for a
    destination at the only point a real system asks: when a truck is free after
    dumping.
    """

    def __init__(
        self,
        scenario: Scenario,
        policy: DispatchPolicy | None = None,
        *,
        best_path: BestPath | None = None,
        overrides: Overrides | None = None,
        standby_retry_s: float = 60.0,
    ) -> None:
        self.scenario = scenario
        self.env = simpy.Environment()
        self.log = EventLog()
        self.overrides = overrides if overrides is not None else Overrides()
        self.standby_retry_s = standby_retry_s

        self.best_path = best_path if best_path is not None else BestPath(scenario.mine.network)
        self.best_path.warm(scenario.mine)
        self.policy = (
            policy
            if policy is not None
            else NeediestShovelPolicy(best_path=self.best_path, plan=scenario.plan)
        )

        self._shovel_resources = {
            shovel_id: simpy.Resource(self.env, capacity=1) for shovel_id in scenario.shovels
        }
        self._dump_resources = {
            dump.id: simpy.Resource(self.env, capacity=dump.tipping_bays)
            for dump in scenario.mine.dump_zones.values()
        }
        self._dump_for_zone = {
            zone.id: self._nearest_dump(zone) for zone in scenario.mine.load_zones.values()
        }
        self._trucks = {
            truck.id: _TruckRuntime(
                truck=truck,
                state=CycleState.TRAVEL_EMPTY,
                status=StatusCode.OPERATING,
                free_at_node=scenario.start_nodes[truck.id],
            )
            for truck in scenario.trucks
        }

    def run(self, until_s: float) -> Kpis:
        for runtime in self._trucks.values():
            self.env.process(self._truck_process(runtime))
        self.env.run(until=until_s)
        return self.log.kpis(horizon_s=until_s, shovel_ids=list(self.scenario.shovels))

    def _nearest_dump(self, zone: LoadZone) -> DumpZone:
        """Destination choice stands in for the LP, which picks it per route."""
        compatible = [
            dump for dump in self.scenario.mine.dump_zones.values() if dump.accepts(zone.material)
        ]
        return min(
            compatible,
            key=lambda dump: self.best_path.travel_time_s(zone.node, dump.node, loaded=True),
        )

    def _snapshot(self) -> MineSnapshot:
        now_s = self.env.now
        return MineSnapshot(
            now_s=now_s,
            mine=self.scenario.mine,
            shovels=self.scenario.shovels,
            trucks={
                runtime.truck.id: TruckStatus(
                    truck=runtime.truck,
                    state=runtime.state,
                    status=runtime.status,
                    free_at_node=runtime.free_at_node,
                    free_in_s=max(0.0, runtime.free_at_s - now_s),
                    assigned_shovel=runtime.assigned_shovel,
                    eta_to_shovel_s=(
                        max(0.0, runtime.arrive_at_shovel_s - now_s)
                        if runtime.assigned_shovel is not None
                        else None
                    ),
                )
                for runtime in self._trucks.values()
            },
            overrides=self.overrides,
        )

    def _truck_process(self, runtime: _TruckRuntime) -> Generator[simpy.Event, None, None]:
        env = self.env
        truck = runtime.truck
        while True:
            assignment = self.policy.assign(self._snapshot(), truck.id)
            if assignment is None:
                runtime.status = StatusCode.STANDBY
                self.log.record(env.now, EventKind.STANDBY, truck.id)
                yield env.timeout(self.standby_retry_s)
                runtime.status = StatusCode.OPERATING
                continue

            shovel = self.scenario.shovels[assignment.shovel_id]
            zone = self.scenario.mine.load_zones[assignment.zone_id]
            self.log.record(
                env.now,
                EventKind.ASSIGNED,
                truck.id,
                shovel_id=shovel.id,
                detail=assignment.reason,
            )

            runtime.state = CycleState.TRAVEL_EMPTY
            runtime.assigned_shovel = shovel.id
            runtime.arrive_at_shovel_s = env.now + assignment.route.travel_time_s
            runtime.free_at_node = zone.node
            yield env.timeout(assignment.route.travel_time_s)

            runtime.state = CycleState.QUEUE_AT_SHOVEL
            self.log.record(env.now, EventKind.ARRIVE_SHOVEL, truck.id, shovel_id=shovel.id)
            with self._shovel_resources[shovel.id].request() as request:
                yield request
                runtime.state = CycleState.SPOTTING
                self.log.record(env.now, EventKind.SPOT_START, truck.id, shovel_id=shovel.id)
                yield env.timeout(self.scenario.spot_time_s)

                runtime.state = CycleState.LOADING
                self.log.record(env.now, EventKind.LOAD_START, truck.id, shovel_id=shovel.id)
                yield env.timeout(shovel.load_time_s(truck))
                self.log.record(
                    env.now,
                    EventKind.LOAD_END,
                    truck.id,
                    shovel_id=shovel.id,
                    payload_t=truck.payload_t,
                )

            dump = self._dump_for_zone[zone.id]
            haul = self.best_path.route(zone.node, dump.node, loaded=True)
            runtime.state = CycleState.TRAVEL_LOADED
            # Leaving the shovel puts the truck back into T': it no longer counts
            # towards that shovel's committed haulage, and it will need a new
            # destination once it has tipped.
            runtime.assigned_shovel = None
            runtime.free_at_node = dump.node
            runtime.free_at_s = env.now + haul.travel_time_s + dump.dump_time_s
            yield env.timeout(haul.travel_time_s)

            runtime.state = CycleState.QUEUE_AT_DUMP
            self.log.record(env.now, EventKind.ARRIVE_DUMP, truck.id, dump_id=dump.id)
            with self._dump_resources[dump.id].request() as request:
                yield request
                runtime.state = CycleState.DUMPING
                self.log.record(env.now, EventKind.DUMP_START, truck.id, dump_id=dump.id)
                yield env.timeout(dump.dump_time_s)
                self.log.record(
                    env.now,
                    EventKind.DUMP_END,
                    truck.id,
                    dump_id=dump.id,
                    payload_t=truck.payload_t,
                )
            runtime.free_at_s = env.now
