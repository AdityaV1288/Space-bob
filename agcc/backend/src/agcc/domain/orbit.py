"""Orbit domain contracts."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import Provenance, _require_utc, _validate_id
from agcc.domain.enums import Band, LinkPolarization, OrbitInputMode

# ---------------------------------------------------------------------------
# Band ↔ carrier-frequency validation table (fixed; not user-configurable)
# ---------------------------------------------------------------------------

# Maps Band → (min_ghz, max_ghz) inclusive range.
BAND_FREQUENCY_RANGES_GHZ: dict[Band, tuple[float, float]] = {
    Band.VHF: (0.030, 0.300),
    Band.UHF: (0.300, 1.000),
    Band.S: (2.000, 4.000),
    Band.X: (8.000, 12.000),
    Band.KA: (26.500, 40.000),
}

# Fixed physical constants — not user-configurable
_ECCENTRICITY: float = 0.0
_ARGUMENT_OF_PERIGEE_DEG: float = 0.0
_EARTH_RADIUS_KM: float = 6378.137
_MU_KM3_S2: float = 398600.4418


class CustomCircularOrbit(BaseModel):
    """Canonical circular-LEO orbit representation."""

    model_config = {"frozen": True}

    altitude_km: float = Field(
        ge=200.0, le=2000.0, description="Orbital altitude in kilometers (200–2000)"
    )
    inclination_deg: float = Field(ge=0.0, le=180.0, description="Inclination in degrees")
    raan_deg: float = Field(
        ge=0.0, lt=360.0, description="Right ascension of ascending node in degrees [0,360)"
    )
    phase_deg: float = Field(
        ge=0.0, lt=360.0, description="Satellite phase (true anomaly) in degrees [0,360)"
    )
    epoch: datetime = Field(description="Epoch of orbital elements (UTC)")

    # Derived — set automatically; must not be supplied independently
    direction: str = Field(default="", description="PROGRADE or RETROGRADE (derived)")

    # Fixed — always equal to the module constants above
    eccentricity: float = Field(default=_ECCENTRICITY, frozen=True)
    argument_of_perigee_deg: float = Field(default=_ARGUMENT_OF_PERIGEE_DEG, frozen=True)
    earth_radius_km: float = Field(default=_EARTH_RADIUS_KM, frozen=True)
    mu_km3_s2: float = Field(default=_MU_KM3_S2, frozen=True)

    input_mode: OrbitInputMode = OrbitInputMode.CUSTOM_CIRCULAR

    @field_validator("epoch", mode="before")
    @classmethod
    def _check_epoch(cls, v: Any) -> Any:
        return _require_utc(v)

    @model_validator(mode="after")
    def _derive_direction(self) -> CustomCircularOrbit:
        derived = "PROGRADE" if self.inclination_deg <= 90.0 else "RETROGRADE"
        object.__setattr__(self, "direction", derived)
        return self


class SatelliteCommunications(BaseModel):
    """Communication system parameters for the satellite (Task 08 amendment)."""

    model_config = {"frozen": True}

    band: Band = Field(description="Downlink frequency band")
    carrier_frequency_ghz: float = Field(gt=0.0, description="Carrier frequency in GHz")
    max_downlink_rate_mbps: float = Field(gt=0.0, description="Maximum downlink rate in Mbit/s")
    protocol_efficiency: float = Field(
        gt=0.0, le=1.0, description="Protocol efficiency factor in (0, 1]"
    )
    polarization: LinkPolarization = Field(
        description="Required downlink polarization for the ITU-R P.838 rain model"
    )
    min_elevation_deg: float = Field(
        ge=0.0, le=90.0, description="Minimum elevation angle for contact in degrees"
    )

    @model_validator(mode="after")
    def _check_frequency_in_band_range(self) -> "SatelliteCommunications":
        lo, hi = BAND_FREQUENCY_RANGES_GHZ[self.band]
        if not (lo <= self.carrier_frequency_ghz <= hi):
            raise ValueError(
                f"carrier_frequency_ghz {self.carrier_frequency_ghz} GHz is outside the "
                f"valid range [{lo}, {hi}] GHz for band {self.band.value}"
            )
        return self


class CustomSatellite(BaseModel):
    """Top-level satellite definition."""

    model_config = {"frozen": True}

    satellite_id: str = Field(description="Unique satellite ID (prefix: sat_)")
    name: str = Field(min_length=1)
    orbit: CustomCircularOrbit
    comms: SatelliteCommunications
    provenance: Provenance

    @field_validator("satellite_id", mode="before")
    @classmethod
    def _check_id(cls, v: Any) -> Any:
        return _validate_id("sat_", v)
