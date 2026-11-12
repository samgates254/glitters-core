"""
Construction Activities, Execution Modes, Resource Pool Items, and Project Graph Representation.
Supports both renewable daily capacities and non-renewable material inventories/delivery schedules.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import datetime
from typing import Dict, List, Optional, Set, Tuple
from .dependencies import (
    DependencyType,
    KenyanWorkCalendar,
    MeasurementStatus,
    PrecedenceDependency,
    ProjectArchetype,
    RateProvenanceStatus,
    ResourceCategory,
)
from .engineering_specs import EngineeringSpecification
from .money import MoneyKES


@dataclass(frozen=True)
class ExecutionMode:
    mode_id: str
    duration_days: int
    direct_cost_kes: float
    renewable_demands: Dict[str, float] = field(default_factory=dict)
    nonrenewable_demands: Dict[str, float] = field(default_factory=dict)
    concrete_mpa_offered: Optional[float] = None
    steel_mpa_offered: Optional[float] = None

    @property
    def direct_cost_money(self) -> MoneyKES:
        return MoneyKES.from_kes(self.direct_cost_kes)


@dataclass
class Activity:
    activity_id: str
    name: str
    wbs_code: str
    duration_days: int
    optimistic_days: Optional[float] = None
    most_likely_days: Optional[float] = None
    pessimistic_days: Optional[float] = None
    crash_duration_days: Optional[int] = None
    normal_cost_kes: float = 0.0
    crash_cost_kes: Optional[float] = None
    resource_demands: Dict[str, float] = field(default_factory=dict)
    material_demands: Dict[str, float] = field(default_factory=dict)
    modes: List[ExecutionMode] = field(default_factory=list)
    engineering_spec: Optional[EngineeringSpecification] = None
    concrete_mpa_designed: Optional[float] = None
    steel_mpa_designed: Optional[float] = None
    is_milestone: bool = False
    earliest_start_window: int = 0
    latest_finish_window: Optional[int] = None

    def __post_init__(self) -> None:
        if self.duration_days < 0:
            raise ValueError(f"Activity {self.activity_id} cannot have negative duration ({self.duration_days}).")
        if self.is_milestone and self.duration_days != 0:
            raise ValueError(f"Milestone {self.activity_id} must have 0 duration.")
        if not self.modes:
            self.modes = [
                ExecutionMode(
                    mode_id="NORMAL",
                    duration_days=self.duration_days,
                    direct_cost_kes=self.normal_cost_kes,
                    renewable_demands=dict(self.resource_demands),
                    nonrenewable_demands=dict(self.material_demands),
                    concrete_mpa_offered=self.concrete_mpa_designed,
                    steel_mpa_offered=self.steel_mpa_designed,
                )
            ]
            if (
                self.crash_duration_days is not None
                and self.crash_cost_kes is not None
                and self.crash_duration_days < self.duration_days
            ):
                self.modes.append(
                    ExecutionMode(
                        mode_id="CRASH",
                        duration_days=self.crash_duration_days,
                        direct_cost_kes=self.crash_cost_kes,
                        renewable_demands={k: v * 1.25 for k, v in self.resource_demands.items()},
                        nonrenewable_demands=dict(self.material_demands),
                        concrete_mpa_offered=self.concrete_mpa_designed,
                        steel_mpa_offered=self.steel_mpa_designed,
                    )
                )

    @property
    def pert_mean(self) -> float:
        if (
            self.optimistic_days is not None
            and self.most_likely_days is not None
            and self.pessimistic_days is not None
        ):
            return (self.optimistic_days + 4.0 * self.most_likely_days + self.pessimistic_days) / 6.0
        return float(self.duration_days)

    @property
    def pert_variance(self) -> float:
        if self.optimistic_days is not None and self.pessimistic_days is not None:
            return ((self.pessimistic_days - self.optimistic_days) / 6.0) ** 2
        return 0.0

    @property
    def crash_slope_kes_per_day(self) -> float:
        if (
            self.crash_duration_days is not None
            and self.crash_cost_kes is not None
            and self.duration_days > self.crash_duration_days
        ):
            return (self.crash_cost_kes - self.normal_cost_kes) / float(self.duration_days - self.crash_duration_days)
        return float("inf")


@dataclass(frozen=True)
class ResourcePoolItem:
    resource_id: str
    name: str
    category: ResourceCategory
    role_or_type: str
    unit: str
    max_capacity_per_day: float
    unit_cost_kes: float
    overtime_cost_kes: Optional[float] = None
    mobilization_cost_kes: float = 0.0
    available_from_day: int = 0
    available_until_day: int = 10000
    county_location: str = "Nairobi"
    productivity_rate_per_day: float = 1.0
    cost_source_status: MeasurementStatus = MeasurementStatus.SYNTHETIC_BENCHMARK
    rate_provenance: RateProvenanceStatus = RateProvenanceStatus.SYNTHETIC_BENCHMARK_RATE


@dataclass
class ConstructionProject:
    """
    Complete mathematical representation of a construction project P.
    Includes renewable resource pools, non-renewable material inventory caps,
    time-phased material delivery schedules, and Kenyan county work calendars.
    """
    project_id: str
    name: str
    archetype: ProjectArchetype
    county: str
    budget_ceiling_kes: float
    contractual_deadline_days: int
    project_start_date: datetime.date = datetime.date(2026, 11, 16)
    contractual_completion_date: Optional[datetime.date] = None
    daily_overhead_kes: float = 25000.0
    liquidated_damages_per_day_kes: float = 50000.0
    early_completion_bonus_per_day_kes: float = 0.0
    financing_annual_interest_rate: float = 0.16
    calendar: KenyanWorkCalendar = field(default_factory=lambda: KenyanWorkCalendar("DEFAULT_KE", "Nairobi"))
    activities: Dict[str, Activity] = field(default_factory=dict)
    dependencies: List[PrecedenceDependency] = field(default_factory=list)
    resources: Dict[str, ResourcePoolItem] = field(default_factory=dict)
    # Non-renewable material supply constraints (Remediates CRITICAL 5):
    # nonrenewable_material_caps[mat_id] = max total available units across project
    nonrenewable_material_caps: Dict[str, float] = field(default_factory=dict)
    # scheduled_material_deliveries[mat_id] = [(delivery_day_index, quantity_delivered), ...]
    scheduled_material_deliveries: Dict[str, List[Tuple[int, float]]] = field(default_factory=dict)
    priority_weight: float = 1.0

    def add_activity(self, activity: Activity) -> None:
        if activity.activity_id in self.activities:
            raise ValueError(f"Duplicate activity ID: {activity.activity_id}")
        self.activities[activity.activity_id] = activity

    def add_dependency(self, dep: PrecedenceDependency) -> None:
        if dep.predecessor_id not in self.activities:
            raise KeyError(f"Unknown predecessor activity '{dep.predecessor_id}'")
        if dep.successor_id not in self.activities:
            raise KeyError(f"Unknown successor activity '{dep.successor_id}'")
        if dep.predecessor_id == dep.successor_id:
            raise ValueError(f"Self-dependency detected on activity '{dep.predecessor_id}'")
        self.dependencies.append(dep)

    def to_activity_on_node_graph(self) -> Dict[str, List[Tuple[str, DependencyType, int]]]:
        adj: Dict[str, List[Tuple[str, DependencyType, int]]] = {aid: [] for aid in self.activities}
        for dep in self.dependencies:
            adj[dep.predecessor_id].append((dep.successor_id, dep.dep_type, dep.lag_days))
        return adj

    def to_activity_on_arrow_network(self) -> Dict[str, object]:
        events: Set[str] = set()
        real_arrows: List[Dict[str, object]] = []
        dummy_arrows: List[Dict[str, object]] = []
        for aid, act in self.activities.items():
            u_ev = f"E_{aid}_START"
            v_ev = f"E_{aid}_FINISH"
            events.add(u_ev)
            events.add(v_ev)
            real_arrows.append({
                "from_event": u_ev,
                "to_event": v_ev,
                "activity_id": aid,
                "duration_days": act.duration_days,
                "is_dummy": False,
            })
        for idx, dep in enumerate(self.dependencies):
            if dep.dep_type == DependencyType.FS:
                from_ev, to_ev = f"E_{dep.predecessor_id}_FINISH", f"E_{dep.successor_id}_START"
            elif dep.dep_type == DependencyType.SS:
                from_ev, to_ev = f"E_{dep.predecessor_id}_START", f"E_{dep.successor_id}_START"
            elif dep.dep_type == DependencyType.FF:
                from_ev, to_ev = f"E_{dep.predecessor_id}_FINISH", f"E_{dep.successor_id}_FINISH"
            else:
                from_ev, to_ev = f"E_{dep.predecessor_id}_START", f"E_{dep.successor_id}_FINISH"
            dummy_arrows.append({
                "from_event": from_ev,
                "to_event": to_ev,
                "dummy_id": f"DUMMY_{idx}_{dep.predecessor_id}_{dep.successor_id}",
                "lag_days": dep.lag_days,
                "is_dummy": True,
            })
        return {
            "events": sorted(events),
            "real_arrows": real_arrows,
            "dummy_arrows": dummy_arrows,
            "num_events": len(events),
            "num_real_activities": len(real_arrows),
            "num_dummy_activities": len(dummy_arrows),
        }
