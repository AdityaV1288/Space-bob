"""HTTP contracts for the Task 13 backend integration boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import _require_utc
from agcc.domain.enums import AnomalyType
from agcc.domain.mission import DownlinkMission, Scenario
from agcc.domain.orbit import CustomSatellite
from agcc.simulation import ClockSpeed

SCHEMA_VERSION = "api.v1"


class ScenarioCreateRequest(BaseModel):
    scenario: Scenario
    satellite: CustomSatellite
    mission: DownlinkMission

    @model_validator(mode="after")
    def validate_references(self) -> ScenarioCreateRequest:
        if self.scenario.satellite_id != self.satellite.satellite_id:
            raise ValueError("scenario.satellite_id does not reference satellite")
        if self.scenario.mission_id != self.mission.mission_id:
            raise ValueError("scenario.mission_id does not reference mission")
        return self


class HorizonRequest(BaseModel):
    start_at: datetime
    end_at: datetime
    step_s: int = Field(default=60, ge=1, le=3600)

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def validate_utc(cls, value: Any) -> Any:
        return _require_utc(value)

    @model_validator(mode="after")
    def validate_order(self) -> HorizonRequest:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be after start_at")
        return self


class CapacityRequest(BaseModel):
    pass_ids: list[str] | None = None


class FeasibilityRequest(BaseModel):
    refresh_capacity: bool = True


class PlanRequest(BaseModel):
    plan_id: str | None = None
    mission_window_start: datetime | None = None

    @field_validator("mission_window_start", mode="before")
    @classmethod
    def validate_window_start(cls, value: Any) -> Any:
        return value if value is None else _require_utc(value)


class SimulationStartRequest(BaseModel):
    plan_id: str | None = None
    sim_start_at: datetime | None = None
    speed: ClockSpeed = ClockSpeed.X1
    capacity_policy: Literal["frozen", "live"] = "frozen"

    @field_validator("sim_start_at", mode="before")
    @classmethod
    def validate_start(cls, value: Any) -> Any:
        return value if value is None else _require_utc(value)


class SimulationStepRequest(BaseModel):
    seconds: int = Field(default=1, ge=1, le=86400)


class SimulationForkRequest(BaseModel):
    """Snapshot values used to create an isolated future simulation branch."""

    sim_time: datetime
    delivered_mb: float = Field(default=0.0, ge=0.0)

    @field_validator("sim_time", mode="before")
    @classmethod
    def validate_sim_time(cls, value: Any) -> Any:
        return _require_utc(value)


class AnomalyRequest(BaseModel):
    anomaly_type: AnomalyType
    station_id: str | None = None
    affected_contact_ids: list[str] = Field(default_factory=list)
    rate_multiplier: float = Field(ge=0.0, le=1.0)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    cause: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)

    @field_validator("starts_at", "ends_at", mode="before")
    @classmethod
    def anomaly_times(cls, value: Any) -> Any:
        return value if value is None else _require_utc(value)

    @model_validator(mode="after")
    def anomaly_time_order(self) -> AnomalyRequest:
        if (
            self.ends_at is not None
            and self.starts_at is not None
            and self.ends_at <= self.starts_at
        ):
            raise ValueError("ends_at must be after starts_at")
        return self


class ReplanRequest(BaseModel):
    reason: str = Field(min_length=1)


class ProposalDecisionRequest(BaseModel):
    reason: str = Field(min_length=1)


class ApiErrorBody(BaseModel):
    code: str
    message: str
    entity_refs: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)


class ApiEnvelope(BaseModel):
    schema_version: str = SCHEMA_VERSION
    request_id: str
    scenario_id: str | None
    current_plan_id: str | None = None
    data: Any = None
    provenance: dict[str, Any] | None = None
    assumptions: list[str] = Field(default_factory=list)
    error: ApiErrorBody | None = None


class DiagnosticsData(BaseModel):
    engine_version: str
    model_versions: dict[str, str]
    adapter_mode: Literal["fixture", "not_configured"]
    dependency_versions: dict[str, str]
    active_scenario_count: int


class ScenarioValidationData(BaseModel):
    valid: bool
    errors: list[ApiErrorBody] = Field(default_factory=list)


class OrbitSummaryData(BaseModel):
    semi_major_axis_km: float
    mean_motion_rad_s: float
    period_s: float
    revolutions_per_day: float
    direction: str


class AnomalyImpactData(BaseModel):
    anomaly_id: str
    anomaly_type: AnomalyType
    affected_contact_ids: list[str]
    station_id: str | None = None
    rate_multiplier: float
    starts_at: datetime
    ends_at: datetime | None = None
    confidence: float | None = None
    cause: str | None = None
    assumptions: list[str] = Field(default_factory=list)
    estimated_capacity_reduction_mb: float
    description: str


class ProposalData(BaseModel):
    proposal_id: str
    scenario_id: str
    current_plan_id: str
    proposed_plan_id: str
    status: Literal["pending", "approved", "rejected"]
    reason: str
    created_at: datetime

    @field_validator("created_at", mode="before")
    @classmethod
    def validate_created(cls, value: Any) -> Any:
        return _require_utc(value)


class EventSubscriptionMessage(BaseModel):
    scenario_id: str
    simulated_time: datetime | None
    current_contact_id: str | None
    current_station_id: str | None
    throughput_mbps: float | None
    delivered_volume_mb: float
    remaining_volume_mb: float
    anomalies: list[AnomalyImpactData]
    notifications: list[str]
    proposals: list[ProposalData]
