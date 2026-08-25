"""Simulation domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agcc.domain.common import _require_utc, _validate_id
from agcc.domain.enums import AnomalyType, EventType, ProposalStatus, SimulationMode


class DataFragment(BaseModel):
    """A portion of the mission data volume queued for downlink."""

    model_config = {"frozen": True}

    fragment_id: str = Field(description="Unique fragment ID (prefix: fragment_)")
    mission_id: str
    volume_mb: float = Field(gt=0.0, description="Fragment data volume in decimal MB")
    contact_id: str | None = None

    @field_validator("fragment_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("fragment_", v)


class SimulationEvent(BaseModel):
    """An append-only record of something that occurred during simulation."""

    model_config = {"frozen": True}

    event_id: str = Field(description="Unique event ID (prefix: event_)")
    event_type: EventType
    occurred_at: datetime
    contact_id: str | None = None
    fragment_id: str | None = None
    delivered_volume_mb: float | None = Field(default=None, ge=0.0)
    description: str = ""

    @field_validator("event_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("event_", v)

    @field_validator("occurred_at", mode="before")
    @classmethod
    def _check_time(cls, v: Any) -> Any:
        return _require_utc(v)


class AnomalyProposal(BaseModel):
    """A Granite-parsed proposal arising from a detected anomaly."""

    model_config = {"frozen": True}

    proposal_id: str = Field(description="Unique proposal ID (prefix: proposal_)")
    anomaly_type: AnomalyType
    anomaly_text: str
    status: ProposalStatus = ProposalStatus.PENDING
    affected_contact_ids: list[str] = Field(default_factory=list)
    proposed_contact_ids: list[str] = Field(default_factory=list)
    created_at: datetime

    @field_validator("proposal_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("proposal_", v)

    @field_validator("created_at", mode="before")
    @classmethod
    def _check_time(cls, v: Any) -> Any:
        return _require_utc(v)


class ReplanProposal(BaseModel):
    """A proposal to adopt a new plan version; requires human approval."""

    model_config = {"frozen": True}

    proposal_id: str = Field(description="Unique proposal ID (prefix: proposal_)")
    plan_id: str
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime
    simulation_mode: SimulationMode = SimulationMode.NOMINAL

    @field_validator("proposal_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("proposal_", v)

    @field_validator("created_at", mode="before")
    @classmethod
    def _check_time(cls, v: Any) -> Any:
        return _require_utc(v)
