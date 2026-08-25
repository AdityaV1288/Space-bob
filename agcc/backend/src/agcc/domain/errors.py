"""Structured domain errors."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from agcc.domain.enums import RejectionCode


class DomainError(BaseModel):
    """Serializable structured domain error."""

    model_config = {"frozen": True}

    code: str = Field(min_length=1)
    message: str = Field(min_length=1)
    entity_refs: dict[str, str] = Field(default_factory=dict)
    details: dict[str, Any] = Field(default_factory=dict)

    def model_dump_sorted(self) -> dict[str, Any]:
        """Return a deterministically sorted dictionary representation."""
        raw = self.model_dump()
        return {k: raw[k] for k in sorted(raw)}


# ---------------------------------------------------------------------------
# Specific constructors
# ---------------------------------------------------------------------------


def validation_error(message: str, entity_refs: dict[str, str] | None = None) -> DomainError:
    return DomainError(
        code="VALIDATION_ERROR",
        message=message,
        entity_refs=entity_refs or {},
    )


def missing_configuration(field: str, entity_refs: dict[str, str] | None = None) -> DomainError:
    return DomainError(
        code="MISSING_CONFIGURATION",
        message=f"Required configuration is not set: {field}",
        entity_refs=entity_refs or {},
        details={"field": field},
    )


def infeasible_mission(
    reason: RejectionCode,
    entity_refs: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> DomainError:
    return DomainError(
        code="INFEASIBLE_MISSION",
        message=f"Mission is infeasible: {reason.value}",
        entity_refs=entity_refs or {},
        details={"rejection_code": reason.value, **(details or {})},
    )


def external_data_unavailable(
    source: str,
    entity_refs: dict[str, str] | None = None,
) -> DomainError:
    return DomainError(
        code="EXTERNAL_DATA_UNAVAILABLE",
        message=f"External data source is unavailable: {source}",
        entity_refs=entity_refs or {},
        details={"source": source},
    )
