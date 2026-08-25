"""Ground station domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import Provenance, _require_utc, _validate_id
from agcc.domain.enums import Band, CostModel

# Fields that always require provenance when populated
_PROVENANCE_REQUIRED_FIELDS = frozenset(
    {
        "latitude_deg",
        "longitude_deg",
        "altitude_m",
        "supported_bands",
        "max_downlink_rate_mbps",
        "minimum_elevation_deg",
        "setup_s",
        "teardown_s",
        "cost_model",
        "booking_cost",
        "cost_per_minute",
        "currency",
    }
)


class FieldProvenance(BaseModel):
    """Field-level provenance: maps field names to source/assumption notes."""

    model_config = {"frozen": True}

    # Maps field name -> short description of source or assumption
    sources: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)


class GroundStation(BaseModel):
    """A ground station capable of receiving satellite downlink."""

    model_config = {"frozen": True}

    station_id: str = Field(description="Unique station ID (prefix: station_)")
    name: str = Field(min_length=1)
    provider_id: str = Field(min_length=1, description="Identifier of the station provider")

    # Coordinates — must be sourced facts or explicitly marked simulation assumptions
    latitude_deg: float = Field(ge=-90.0, le=90.0, description="Geodetic latitude in degrees")
    longitude_deg: float = Field(ge=-180.0, lt=180.0, description="Geodetic longitude in degrees")
    altitude_m: float = Field(ge=0.0, description="Altitude above WGS-84 ellipsoid in meters")

    # RF capability — optional; None means not yet configured
    supported_bands: frozenset[Band] | None = None
    max_downlink_rate_mbps: float | None = Field(
        default=None, gt=0.0, description="Station maximum downlink rate in Mbit/s"
    )
    minimum_elevation_deg: float = Field(
        ge=0.0, le=90.0, description="Station minimum elevation mask in degrees"
    )

    # Contact timing
    setup_s: int = Field(ge=0, description="Setup time in seconds before contact")
    teardown_s: int = Field(ge=0, description="Teardown time in seconds after contact")

    # Cost model
    cost_model: CostModel = CostModel.NONE
    booking_cost: float = Field(ge=0.0, description="Fixed per-contact booking cost")
    cost_per_minute: float = Field(ge=0.0, description="Variable cost per contact minute")
    currency: str = Field(default="USD", min_length=1)

    # MVP constraint
    simultaneous_contacts: int = Field(default=1, ge=1, le=1, description="Fixed at 1 for MVP")

    # Field-level provenance
    field_provenance: FieldProvenance = Field(default_factory=FieldProvenance)

    # Operational flags
    enabled: bool = True

    @field_validator("station_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("station_", v)

    @model_validator(mode="after")
    def _check_field_provenance(self) -> GroundStation:
        """Populated fields must each appear in exactly one of sources or assumptions."""
        sourced = set(self.field_provenance.sources.keys())
        assumed = set(self.field_provenance.assumptions)

        # Check for fields in both sources and assumptions (invalid)
        overlap = sourced & assumed
        if overlap:
            raise ValueError(
                f"Fields appear in both sources and assumptions: {sorted(overlap)}. "
                f"Each field must be in exactly one."
            )

        covered = sourced | assumed

        # Determine which provenance-required fields are "populated"
        populated_fields: set[str] = set()
        for field in _PROVENANCE_REQUIRED_FIELDS:
            val = getattr(self, field)
            if val is None:
                continue  # optional fields that are None don't need provenance
            # supported_bands=frozenset() is populated (empty set counts)
            populated_fields.add(field)

        # Each populated field must be covered
        for field in populated_fields:
            if field not in covered:
                raise ValueError(
                    f"Field '{field}' must be listed in field_provenance.sources "
                    f"or field_provenance.assumptions"
                )

        return self

    @property
    def planner_eligible(self) -> bool:
        """True when the station has sufficient data for the planner."""
        return (
            self.enabled
            and self.supported_bands is not None
            and len(self.supported_bands) > 0
            and self.max_downlink_rate_mbps is not None
            and self.max_downlink_rate_mbps > 0.0
        )


class StationCatalog(BaseModel):
    """An ordered collection of ground stations with metadata."""

    model_config = {"frozen": True}

    catalog_id: str
    schema_version: str
    catalog_version: str
    generated_at: datetime
    provenance: Provenance
    stations: list[GroundStation] = Field(default_factory=list)

    @field_validator("generated_at", mode="before")
    @classmethod
    def _check_generated_at(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _check_catalog_invariants(self) -> StationCatalog:
        # catalog_id must start with "catalog_" and have content after prefix
        if not self.catalog_id.startswith("catalog_") or len(self.catalog_id) <= len("catalog_"):
            raise ValueError(
                "catalog_id must start with 'catalog_' and have content after the prefix"
            )
        if not self.schema_version:
            raise ValueError("schema_version must be non-empty")
        if not self.catalog_version:
            raise ValueError("catalog_version must be non-empty")
        # Station IDs must be unique
        ids = [s.station_id for s in self.stations]
        if len(ids) != len(set(ids)):
            from collections import Counter

            dupes = [sid for sid, cnt in Counter(ids).items() if cnt > 1]
            raise ValueError(f"Duplicate station IDs in catalog: {dupes}")
        return self


class StationSelection(BaseModel):
    """Authorization parameters for ground station selection in a scenario."""

    model_config = {"frozen": True}

    allow_all_eligible: bool = False
    authorized_station_ids: frozenset[str] = Field(default_factory=frozenset)
    authorized_provider_ids: frozenset[str] = Field(default_factory=frozenset)
    excluded_station_ids: frozenset[str] = Field(default_factory=frozenset)
