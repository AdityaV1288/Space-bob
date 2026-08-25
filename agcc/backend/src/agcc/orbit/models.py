"""Output models for orbit propagation — no domain contracts changed."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from agcc.domain.common import _require_utc


class OrbitState(BaseModel):
    """Full propagated orbit state at a single instant."""

    model_config = {"frozen": True}

    time: datetime = Field(description="UTC time of this state")

    # Inertial (ECI) position in km, J2000-aligned axes
    pos_eci_km: tuple[float, float, float] = Field(
        description="Inertial (ECI) position vector in km (x, y, z)"
    )
    # Inertial (ECI) velocity in km/s
    vel_eci_km_s: tuple[float, float, float] = Field(
        description="Inertial (ECI) velocity vector in km/s (x, y, z)"
    )

    # Earth-fixed (ECEF) position in km
    pos_ecef_km: tuple[float, float, float] = Field(
        description="Earth-fixed (ECEF) position vector in km (x, y, z)"
    )

    # Geodetic coordinates
    latitude_deg: float = Field(ge=-90.0, le=90.0, description="Geodetic latitude in degrees")
    longitude_deg: float = Field(
        ge=-180.0, lt=180.0, description="Geodetic longitude in degrees [-180,180)"
    )
    altitude_km: float = Field(ge=0.0, description="Altitude above WGS-84 reference sphere in km")

    @field_validator("time", mode="before")
    @classmethod
    def _check_time(cls, v: Any) -> Any:
        return _require_utc(v)


class GroundTrackPoint(BaseModel):
    """A single Earth-fixed point on the satellite ground track."""

    model_config = {"frozen": True}

    time: datetime = Field(description="UTC time of this point")
    latitude_deg: float = Field(ge=-90.0, le=90.0)
    longitude_deg: float = Field(ge=-180.0, lt=180.0)
    altitude_km: float = Field(ge=0.0)

    @field_validator("time", mode="before")
    @classmethod
    def _check_time(cls, v: Any) -> Any:
        return _require_utc(v)
