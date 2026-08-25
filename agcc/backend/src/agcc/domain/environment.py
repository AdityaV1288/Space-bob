"""Environment observation domain contracts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import Provenance, _require_utc, _validate_id
from agcc.domain.enums import SourceKind, SourceQuality


class WeatherSnapshot(BaseModel):
    """Normalized tropospheric / weather observation for a station."""

    model_config = {"frozen": True}

    snapshot_id: str = Field(description="Unique snapshot ID (prefix: event_)")
    station_id: str

    # Time coverage
    valid_from: datetime = Field(description="Start of validity window (UTC)")
    valid_until: datetime = Field(description="End of validity window (UTC)")
    observed_at: datetime = Field(description="Time the observation was made (UTC)")
    fetched_at: datetime = Field(description="Time the data was fetched (UTC)")

    # Meteorological fields
    precipitation_mm_per_hr: float = Field(ge=0.0, description="Precipitation rate mm/hr")
    temperature_c: float = Field(description="Air temperature in Celsius")
    relative_humidity_pct: float = Field(ge=0.0, le=100.0, description="Relative humidity %")
    cloud_cover_pct: float = Field(ge=0.0, le=100.0, description="Cloud cover %")
    wind_speed_mps: float = Field(ge=0.0, description="Wind speed m/s")

    # Provenance
    source_kind: SourceKind
    source_quality: SourceQuality
    raw_payload_hash: str = Field(
        min_length=64,
        max_length=71,
        description="SHA-256 hex digest of the raw source payload",
    )
    provenance: Provenance

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("event_", v)

    @field_validator("valid_from", "valid_until", "observed_at", "fetched_at", mode="before")
    @classmethod
    def _check_utc(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _check_window_order(self) -> WeatherSnapshot:
        if self.valid_from >= self.valid_until:
            raise ValueError("valid_from must be before valid_until")
        return self

    def covers(self, t: datetime) -> bool:
        """Return True if t falls within [valid_from, valid_until)."""
        return self.valid_from <= t < self.valid_until


class SpaceWeatherSnapshot(BaseModel):
    """Normalized space-weather observation."""

    model_config = {"frozen": True}

    snapshot_id: str = Field(description="Unique snapshot ID (prefix: event_)")

    # Time coverage
    valid_from: datetime = Field(description="Start of validity window (UTC)")
    valid_until: datetime = Field(description="End of validity window (UTC)")
    observed_at: datetime = Field(description="Time the observation was made (UTC)")
    fetched_at: datetime = Field(description="Time the data was fetched (UTC)")

    # Indices
    kp_index: float = Field(ge=0.0, le=9.0, description="Planetary K-index (0–9)")
    dst_index_nt: float | None = Field(default=None, description="Dst index in nT")
    f107_sfu: float | None = Field(default=None, description="Solar flux F10.7 in SFU")

    # Provenance
    source_kind: SourceKind
    source_quality: SourceQuality
    raw_payload_hash: str = Field(
        min_length=64,
        max_length=71,
        description="SHA-256 hex digest of the raw source payload",
    )
    provenance: Provenance

    @field_validator("snapshot_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("event_", v)

    @field_validator("valid_from", "valid_until", "observed_at", "fetched_at", mode="before")
    @classmethod
    def _check_utc(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _check_window_order(self) -> SpaceWeatherSnapshot:
        if self.valid_from >= self.valid_until:
            raise ValueError("valid_from must be before valid_until")
        return self


def payload_hash(raw: str | bytes) -> str:
    """Return the SHA-256 hex digest of a raw payload string or bytes."""
    if isinstance(raw, str):
        raw = raw.encode()
    return hashlib.sha256(raw).hexdigest()


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """Compute a canonical SHA-256 hash of a payload dict.

    The hash is computed over a copy of the dict with 'raw_payload_hash' removed,
    serialised as compact sorted-keys JSON (UTF-8).
    """
    copy = dict(payload)
    copy.pop("raw_payload_hash", None)
    serialized = json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()
