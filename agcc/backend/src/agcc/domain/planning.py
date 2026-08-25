"""Planning domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import _require_utc, _validate_id
from agcc.domain.enums import ContactCommitment, PassStatus, RejectionCode


class CandidatePass(BaseModel):
    """A geometrically visible satellite-station interval."""

    model_config = {"frozen": True}

    pass_id: str = Field(description="Unique pass ID (prefix: pass_); deterministic hash-derived")
    scenario_id: str
    satellite_id: str
    station_id: str

    # Timing
    start_at: datetime = Field(description="Rise time: elevation first crosses threshold (UTC)")
    peak_at: datetime = Field(description="Time of maximum elevation (UTC)")
    end_at: datetime = Field(description="Set time: elevation falls back below threshold (UTC)")
    duration_s: float = Field(ge=0.0, description="Total pass duration in seconds")
    usable_duration_s: float = Field(
        ge=0.0, description="Usable duration after setup/teardown subtracted in seconds"
    )

    # Geometry
    max_elevation_deg: float = Field(ge=0.0, le=90.0)
    azimuth_start_deg: float = Field(ge=0.0, lt=360.0)
    azimuth_peak_deg: float = Field(ge=0.0, lt=360.0)
    azimuth_end_deg: float = Field(ge=0.0, lt=360.0)
    slant_range_peak_km: float = Field(gt=0.0)
    minimum_elevation_deg: float = Field(ge=0.0, le=90.0)

    # Versioning
    orbit_model_version: str
    station_catalog_version: str

    status: PassStatus = PassStatus.CANDIDATE

    @field_validator("pass_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("pass_", v)

    @field_validator("start_at", "peak_at", "end_at", mode="before")
    @classmethod
    def _check_times(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _check_pass_invariants(self) -> CandidatePass:
        """Enforce all CandidatePass timing and metadata invariants."""
        # Timing order
        if not (self.start_at < self.peak_at < self.end_at):
            raise ValueError(
                f"Timing order violated: start_at ({self.start_at}) < "
                f"peak_at ({self.peak_at}) < end_at ({self.end_at}) required"
            )
        # Positive duration
        if self.duration_s <= 0:
            raise ValueError(f"duration_s must be > 0, got {self.duration_s}")
        # Duration consistency with start/end
        computed_duration = (self.end_at - self.start_at).total_seconds()
        if abs(self.duration_s - computed_duration) > 1.0:
            raise ValueError(
                f"duration_s ({self.duration_s}) inconsistent with "
                f"end_at - start_at ({computed_duration:.3f}s); allowed tolerance 1.0s"
            )
        # Usable duration
        if self.usable_duration_s <= 0:
            raise ValueError(f"usable_duration_s must be > 0, got {self.usable_duration_s}")
        if self.usable_duration_s > self.duration_s:
            raise ValueError(
                f"usable_duration_s ({self.usable_duration_s}) must be <= "
                f"duration_s ({self.duration_s})"
            )
        # Elevation geometry
        if self.max_elevation_deg < self.minimum_elevation_deg:
            raise ValueError(
                f"max_elevation_deg ({self.max_elevation_deg}) must be >= "
                f"minimum_elevation_deg ({self.minimum_elevation_deg})"
            )
        # ID / version fields
        if not self.scenario_id:
            raise ValueError("scenario_id must be non-empty")
        if not self.satellite_id.startswith("sat_"):
            raise ValueError(f"satellite_id must start with 'sat_', got '{self.satellite_id}'")
        if not self.station_id.startswith("station_"):
            raise ValueError(f"station_id must start with 'station_', got '{self.station_id}'")
        if not self.orbit_model_version:
            raise ValueError("orbit_model_version must be non-empty")
        if not self.station_catalog_version:
            raise ValueError("station_catalog_version must be non-empty")
        return self


class CapacityEstimate(BaseModel):
    """Estimated physical transfer capacity for a pass (Task 08)."""

    model_config = {"frozen": True}

    capacity_id: str = Field(description="Unique capacity ID (prefix: capacity_)")
    pass_id: str

    # Rate limits
    base_rate_mbps: float = Field(ge=0.0, description="min(satellite, station) rate in Mbit/s")

    # Capacity summary
    usable_capacity_mb: float = Field(ge=0.0, description="Total usable capacity in decimal MB")
    average_effective_rate_mbps: float = Field(
        ge=0.0, description="Average effective rate across samples in Mbit/s"
    )
    peak_effective_rate_mbps: float = Field(
        ge=0.0, description="Peak per-sample effective rate in Mbit/s"
    )

    # Data quality flag
    weather_data_quality: str = Field(
        description="Source quality of weather data used (verified/stale/unavailable/assumed)"
    )

    # Versioning / assumptions
    model_version: str = Field(description="Capacity model version identifier")
    assumptions: list[str] = Field(
        default_factory=list,
        description="List of assumption labels applied during estimation",
    )

    # Integration metadata
    sample_count: int = Field(ge=0, description="Number of 10-second integration samples")

    @field_validator("capacity_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("capacity_", v)


class FeasibilityResult(BaseModel):
    """Outcome of a feasibility check against hard constraints."""

    model_config = {"frozen": True}

    is_feasible: bool
    planned_volume_mb: float = Field(ge=0.0)
    planned_cost_usd: float = Field(ge=0.0)
    rejection_codes: list[RejectionCode] = Field(default_factory=list)
    message: str = ""


class PlannedAllocation(BaseModel):
    """Assignment of a data fragment to a planned or committed contact."""

    model_config = {"frozen": True}

    contact_id: str = Field(description="Unique contact ID (prefix: contact_)")
    pass_id: str
    allocated_volume_mb: float = Field(ge=0.0)
    commitment: ContactCommitment = ContactCommitment.PLANNED
    cost_usd: float = Field(ge=0.0)

    @field_validator("contact_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("contact_", v)


class ContactPlan(BaseModel):
    """A versioned ordered list of planned allocations for a scenario."""

    model_config = {"frozen": True}

    plan_id: str = Field(description="Unique plan ID (prefix: plan_)")
    scenario_id: str
    version: int = Field(ge=0)
    allocations: list[PlannedAllocation] = Field(default_factory=list)
    feasibility: FeasibilityResult

    @field_validator("plan_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("plan_", v)


class PlanDiff(BaseModel):
    """Describes changes between two plan versions."""

    model_config = {"frozen": True}

    from_version: int = Field(ge=0)
    to_version: int = Field(ge=0)
    added_contact_ids: list[str] = Field(default_factory=list)
    removed_contact_ids: list[str] = Field(default_factory=list)
    modified_contact_ids: list[str] = Field(default_factory=list)
