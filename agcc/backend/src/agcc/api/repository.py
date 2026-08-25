"""In-memory runtime repository; no persistence or external side effects."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from agcc.api.contracts import AnomalyImpactData, ProposalData, ScenarioCreateRequest
from agcc.dispatch import DispatchPlan
from agcc.domain.environment import SpaceWeatherSnapshot, WeatherSnapshot
from agcc.domain.planning import CandidatePass, CapacityEstimate
from agcc.feasibility import FeasibilityReport
from agcc.planner import ContactPlan
from agcc.simulation import SessionEventStore, SimulationEngine


@dataclass
class ScenarioRuntime:
    definition: ScenarioCreateRequest
    baseline_snapshot_id: str | None = None
    baseline_plan_id: str | None = None
    baseline_created_at: datetime | None = None
    baseline_weather_hash: str | None = None
    passes: list[CandidatePass] = field(default_factory=list)
    capacities: list[CapacityEstimate] = field(default_factory=list)
    weather_snapshots: list[WeatherSnapshot] = field(default_factory=list)
    space_weather_snapshots: list[SpaceWeatherSnapshot] = field(default_factory=list)
    feasibility: FeasibilityReport | None = None
    plans: dict[str, ContactPlan] = field(default_factory=dict)
    current_plan_id: str | None = None
    dispatch: DispatchPlan | None = None
    simulation: SimulationEngine | None = None
    simulation_paused: bool = False
    simulation_speed: str = "1x"
    simulation_capacity_policy: str = "frozen"
    simulation_wall_anchor: datetime | None = None
    simulation_wall_remainder_s: float = 0.0
    event_store: SessionEventStore = field(default_factory=SessionEventStore)
    anomalies: list[AnomalyImpactData] = field(default_factory=list)
    proposals: dict[str, ProposalData] = field(default_factory=dict)


class InMemoryScenarioRepository:
    def __init__(self) -> None:
        self._items: dict[str, ScenarioRuntime] = {}

    def add(self, runtime: ScenarioRuntime) -> None:
        scenario_id = runtime.definition.scenario.scenario_id
        if scenario_id in self._items:
            raise ValueError(f"Scenario already exists: {scenario_id}")
        self._items[scenario_id] = runtime

    def get(self, scenario_id: str) -> ScenarioRuntime:
        try:
            return self._items[scenario_id]
        except KeyError as exc:
            raise KeyError(f"Scenario not found: {scenario_id}") from exc

    def count(self) -> int:
        return len(self._items)
