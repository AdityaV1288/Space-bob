"""Mission domain contracts."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import _require_utc, _validate_id
from agcc.domain.enums import MissionStatus
from agcc.domain.stations import StationSelection


class PlanningPreference(str, Enum):
    """Planning optimization preference — does not relax hard constraints."""

    FASTEST = "fastest"
    LOWEST_COST = "lowest_cost"
    BALANCED = "balanced"


class DownlinkMission(BaseModel):
    """A fixed-volume downlink mission with a hard deadline (Task 09)."""

    model_config = {"frozen": True}

    mission_id: str = Field(description="Unique mission ID (prefix: mission_)")
    name: str = Field(min_length=1)
    required_volume_mb: float = Field(
        gt=0.0, description="Required data volume in decimal megabytes"
    )
    release_at: datetime = Field(description="Earliest time data may be downlinked (UTC)")
    deadline_at: datetime = Field(
        description="Hard deadline — data must be downlinked by this time (UTC)"
    )
    status: MissionStatus = MissionStatus.PENDING

    @field_validator("mission_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("mission_", v)

    @field_validator("release_at", "deadline_at", mode="before")
    @classmethod
    def _check_utc(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _check_release_before_deadline(self) -> "DownlinkMission":
        if self.release_at >= self.deadline_at:
            raise ValueError(
                f"release_at ({self.release_at}) must be earlier than "
                f"deadline_at ({self.deadline_at})"
            )
        return self


class ScenarioConstraints(BaseModel):
    """Hard constraints that govern planning for a scenario (Task 09)."""

    model_config = {"frozen": True}

    maximum_budget: Decimal = Field(
        ge=Decimal("0"),
        description="Maximum allowable cost (Decimal, same currency as scenario)",
    )
    currency: str = Field(min_length=1, description="Fixed currency code for this scenario")
    station_selection: StationSelection = Field(
        default_factory=StationSelection,
        description="Authorized station selection for this scenario",
    )
    planning_preference: PlanningPreference = PlanningPreference.BALANCED
    allow_additional_contact_proposals: bool = False


class Scenario(BaseModel):
    """Root aggregate binding satellite, stations, mission, and constraints."""

    model_config = {"frozen": True}

    scenario_id: str = Field(description="Unique scenario ID (prefix: scenario_)")
    name: str = Field(min_length=1)
    satellite_id: str
    station_ids: list[str] = Field(min_length=1)
    mission_id: str
    constraints: ScenarioConstraints

    @field_validator("scenario_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("scenario_", v)
