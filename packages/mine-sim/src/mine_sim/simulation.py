from __future__ import annotations

from collections.abc import Callable, Generator
from dataclasses import dataclass

import simpy
from dispatch_engine.best_path import BestPath
from dispatch_engine.domain.equipment import CycleState, ShovelId, StatusCode, Truck
from dispatch_engine.domain.mine import NodeId, ZoneId
from dispatch_engine.domain.snapshot import MineSnapshot, Overrides, ShovelStatus, TruckStatus
from dispatch_engine.policies.neediest_shovel import NeediestShovelPolicy
from dispatch_engine.policy import DispatchPolicy
from dispatch_engine.production_plan import ProductionPlan

from mine_sim.events import EventKind, EventLog, Kpis
from mine_sim.scenario import Disruption, Scenario

type PolicyFactory = Callable[[ProductionPlan], DispatchPolicy]
type PlanProvider = Callable[[frozenset[ShovelId]], ProductionPlan]

# Priority for the outage process when it takes a shovel out of service. Below
# the trucks' own priority so it jumps their queue: the shovel stops once it
# finishes the load it is on, instead of serving everyone already waiting.
_OUTAGE_PRIORITY = -1
_TRUCK_PRIORITY = 0


@dataclass(slots=True)
class _TruckRuntime:
    truck: Truck
    state: CycleState
    status: StatusCode
    free_at_node: NodeId
    free_at_s: float = 0.0
    assigned_shovel: ShovelId | None = None
    arrive_at_shovel_s: float = 0.0
    origin_zone: ZoneId | None = None
    assigned_dump: ZoneId | None = None


class Simulation:
    """Digital twin of the haul cycle, driven by a dispatch policy.

    Shovels and dump zones are SimPy resources, so queues emerge from
    contention rather than being modelled explicitly. The policy is asked for a
    destination at the only point a real system asks: when a truck is free after
    dumping.

    Conditions change during the run: a scheduled outage takes a shovel out of
    service, which re-solves the production plan over the shovels still digging
    and rebuilds the policy around it.
    """

    def __init__(
        self,
        scenario: Scenario,
        policy_factory: PolicyFactory | None = None,
        *,
        plan_provider: PlanProvider | None = None,
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

        self._plan_provider = (
            plan_provider if plan_provider is not None else lambda _unavailable: scenario.plan
        )
        self._policy_factory = (
            policy_factory
            if policy_factory is not None
            else lambda plan: NeediestShovelPolicy(best_path=self.best_path, plan=plan)
        )
        self._unavailable: set[ShovelId] = set()
        self.plan = self._plan_provider(frozenset())
        self.policy = self._policy_factory(self.plan)

        self._shovel_status = {shovel_id: StatusCode.OPERATING for shovel_id in scenario.shovels}
        self._shovel_resources = {
            shovel_id: simpy.PriorityResource(self.env, capacity=1)
            for shovel_id in scenario.shovels
        }
        self._dump_resources = {
            dump.id: simpy.Resource(self.env, capacity=dump.tipping_bays)
            for dump in scenario.mine.dump_zones.values()
        }
        self._delivered_t: dict[tuple[ZoneId, ZoneId], float] = {}
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
        for disruption in self.scenario.disruptions:
            self.env.process(self._disruption_process(disruption))
        self.env.run(until=until_s)
        return self.log.kpis(horizon_s=until_s, shovel_ids=list(self.scenario.shovels))

    def _is_available(self, shovel_id: ShovelId) -> bool:
        return (
            self._shovel_status[shovel_id] is StatusCode.OPERATING
            and shovel_id not in self.overrides.excluded_shovels
        )

    def _set_shovel_status(self, shovel_id: ShovelId, status: StatusCode) -> None:
        self._shovel_status[shovel_id] = status
        if status is StatusCode.OPERATING:
            self._unavailable.discard(shovel_id)
        else:
            self._unavailable.add(shovel_id)

        self.plan = self._plan_provider(frozenset(self._unavailable))
        self.policy = self._policy_factory(self.plan)
        self.log.record(
            self.env.now,
            EventKind.REPLAN,
            shovel_id=shovel_id,
            detail=f"{len(self._unavailable)} shovel(s) out of service",
        )

    def _snapshot(self) -> MineSnapshot:
        now_s = self.env.now
        return MineSnapshot(
            now_s=now_s,
            mine=self.scenario.mine,
            shovels={
                shovel_id: ShovelStatus(shovel=shovel, status=self._shovel_status[shovel_id])
                for shovel_id, shovel in self.scenario.shovels.items()
            },
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
                    origin_zone=runtime.origin_zone,
                    assigned_dump=runtime.assigned_dump,
                )
                for runtime in self._trucks.values()
            },
            overrides=self.overrides,
            delivered_t=self._delivered_t,
        )

    def _disruption_process(self, disruption: Disruption) -> Generator[simpy.Event, None, None]:
        env = self.env
        yield env.timeout(disruption.start_s)

        # Reported first so no further trucks are sent there and the plan reacts,
        # then the shovel physically stops once it is free.
        self._set_shovel_status(disruption.shovel_id, disruption.status)
        self.log.record(
            env.now,
            EventKind.SHOVEL_DOWN,
            shovel_id=disruption.shovel_id,
            detail=disruption.status,
        )

        resource = self._shovel_resources[disruption.shovel_id]
        with resource.request(priority=_OUTAGE_PRIORITY) as request:
            yield request
            yield env.timeout(disruption.duration_s)

        self._set_shovel_status(disruption.shovel_id, StatusCode.OPERATING)
        self.log.record(env.now, EventKind.SHOVEL_UP, shovel_id=disruption.shovel_id)

    def _truck_process(self, runtime: _TruckRuntime) -> Generator[simpy.Event, None, None]:
        env = self.env
        truck = runtime.truck
        while True:
            assignment = self.policy.assign(self._snapshot(), truck.id)
            if assignment is None:
                runtime.status = StatusCode.STANDBY
                self.log.record(env.now, EventKind.STANDBY, truck_id=truck.id)
                yield env.timeout(self.standby_retry_s)
                runtime.status = StatusCode.OPERATING
                continue

            shovel = self.scenario.shovels[assignment.shovel_id]
            zone = self.scenario.mine.load_zones[assignment.zone_id]
            self.log.record(
                env.now,
                EventKind.ASSIGNED,
                truck_id=truck.id,
                shovel_id=shovel.id,
                detail=assignment.reason,
            )

            runtime.state = CycleState.TRAVEL_EMPTY
            runtime.assigned_shovel = shovel.id
            runtime.arrive_at_shovel_s = env.now + assignment.route.travel_time_s
            runtime.free_at_node = zone.node
            yield env.timeout(assignment.route.travel_time_s)

            if not self._is_available(shovel.id):
                # The shovel went out of service while the truck was on its way.
                # It is already at the bench, so dispatch simply sends it to
                # another one from here.
                runtime.assigned_shovel = None
                runtime.free_at_s = env.now
                self.log.record(
                    env.now,
                    EventKind.REASSIGNED,
                    truck_id=truck.id,
                    shovel_id=shovel.id,
                    detail="shovel out of service on arrival",
                )
                continue

            runtime.state = CycleState.QUEUE_AT_SHOVEL
            self.log.record(
                env.now, EventKind.ARRIVE_SHOVEL, truck_id=truck.id, shovel_id=shovel.id
            )
            with self._shovel_resources[shovel.id].request(priority=_TRUCK_PRIORITY) as request:
                yield request
                runtime.state = CycleState.SPOTTING
                self.log.record(
                    env.now, EventKind.SPOT_START, truck_id=truck.id, shovel_id=shovel.id
                )
                yield env.timeout(self.scenario.spot_time_s)

                runtime.state = CycleState.LOADING
                self.log.record(
                    env.now, EventKind.LOAD_START, truck_id=truck.id, shovel_id=shovel.id
                )
                yield env.timeout(shovel.load_time_s(truck))
                self.log.record(
                    env.now,
                    EventKind.LOAD_END,
                    truck_id=truck.id,
                    shovel_id=shovel.id,
                    payload_t=truck.payload_t,
                )

            destination = self.policy.choose_destination(self._snapshot(), truck.id, zone.id)
            dump = self.scenario.mine.dump_zones[destination.dump_zone_id]
            haul = destination.route
            runtime.state = CycleState.TRAVEL_LOADED
            # Leaving the shovel puts the truck back into T': it no longer counts
            # towards that shovel's committed haulage, and it will need a new
            # destination once it has tipped.
            runtime.assigned_shovel = None
            runtime.origin_zone = zone.id
            runtime.assigned_dump = dump.id
            runtime.free_at_node = dump.node
            runtime.free_at_s = env.now + haul.travel_time_s + dump.dump_time_s
            yield env.timeout(haul.travel_time_s)

            runtime.state = CycleState.QUEUE_AT_DUMP
            self.log.record(
                env.now,
                EventKind.ARRIVE_DUMP,
                truck_id=truck.id,
                dump_id=dump.id,
                zone_id=zone.id,
            )
            with self._dump_resources[dump.id].request() as request:
                yield request
                runtime.state = CycleState.DUMPING
                self.log.record(env.now, EventKind.DUMP_START, truck_id=truck.id, dump_id=dump.id)
                yield env.timeout(dump.dump_time_s)
                self.log.record(
                    env.now,
                    EventKind.DUMP_END,
                    truck_id=truck.id,
                    dump_id=dump.id,
                    zone_id=zone.id,
                    payload_t=truck.payload_t,
                    detail=destination.reason,
                )

            route = (zone.id, dump.id)
            self._delivered_t[route] = self._delivered_t.get(route, 0.0) + truck.payload_t
            runtime.origin_zone = None
            runtime.assigned_dump = None
            runtime.free_at_s = env.now
