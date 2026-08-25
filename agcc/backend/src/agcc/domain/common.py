"""Shared primitives: IDs, datetimes, provenance, and model references."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any

from pydantic import BaseModel, Field, field_validator

from agcc.domain.enums import SourceType

# ---------------------------------------------------------------------------
# ID prefix registry
# ---------------------------------------------------------------------------

_ID_PREFIXES: dict[str, str] = {
    "ScenarioId": "scenario_",
    "SatelliteId": "sat_",
    "StationId": "station_",
    "PassId": "pass_",
    "CapacityId": "capacity_",
    "MissionId": "mission_",
    "PlanId": "plan_",
    "ContactId": "contact_",
    "FragmentId": "fragment_",
    "EventId": "event_",
    "ProposalId": "proposal_",
}


def _make_id_type(prefix: str) -> type:
    """Return an annotated str type that enforces a specific ID prefix."""

    def _check(value: str) -> str:
        if not value.startswith(prefix):
            raise ValueError(f"ID must start with '{prefix}', got '{value}'")
        if len(value) <= len(prefix):
            raise ValueError(f"ID must have content after prefix '{prefix}'")
        return value

    return Annotated[  # type: ignore[return-value]
        str, Field(min_length=len(prefix) + 1), field_validator("__all__", mode="before")
    ]


# We define concrete ID types via simple annotated aliases with a validator
# embedded at the field level. The actual enforcement happens in each model
# via a shared helper.


def _validate_id(prefix: str, value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("ID must be a string")
    if not value.startswith(prefix):
        raise ValueError(f"ID must start with '{prefix}', got '{value}'")
    if len(value) <= len(prefix):
        raise ValueError(f"ID must have content after prefix '{prefix}'")
    return value


# ---------------------------------------------------------------------------
# UTC datetime enforcement
# ---------------------------------------------------------------------------


def _require_utc(value: datetime | str) -> datetime:
    """Reject naive datetimes; convert any aware datetime to UTC.

    Accepts ISO-8601 strings (from JSON parsing) in addition to datetime objects.
    """
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError(
            "Datetime must be timezone-aware (UTC required); naive datetimes are rejected"
        )
    return value.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# Provenance
# ---------------------------------------------------------------------------


class Provenance(BaseModel):
    """Records origin and currency of externally derived data."""

    model_config = {"frozen": True}

    source_type: SourceType
    source_name: str
    source_uri: str | None = None
    source_version: str | None = None
    observed_at: datetime | None = None
    fetched_at: datetime
    assumption_fields: list[str] = Field(default_factory=list)

    @field_validator("observed_at", mode="before")
    @classmethod
    def _check_observed_at(cls, v: Any) -> Any:
        if v is None:
            return v
        return _require_utc(v)

    @field_validator("fetched_at", mode="before")
    @classmethod
    def _check_fetched_at(cls, v: Any) -> Any:
        return _require_utc(v)


# ---------------------------------------------------------------------------
# ModelRef
# ---------------------------------------------------------------------------


class ModelRef(BaseModel):
    """Lightweight pointer to a versioned model instance."""

    model_config = {"frozen": True}

    id: str
    version: int = Field(ge=0)
