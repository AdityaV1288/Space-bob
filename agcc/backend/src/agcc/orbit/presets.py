"""Fixed orbit presets."""

from __future__ import annotations

from datetime import datetime, timezone

from agcc.domain.orbit import CustomCircularOrbit

# A stable reference epoch for presets — midnight 2000-01-01 UTC (J2000)
_PRESET_EPOCH = datetime(2000, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _make(altitude_km: float, inclination_deg: float) -> CustomCircularOrbit:
    return CustomCircularOrbit(
        altitude_km=altitude_km,
        inclination_deg=inclination_deg,
        raan_deg=0.0,
        phase_deg=0.0,
        epoch=_PRESET_EPOCH,
    )


EQUATORIAL_550: CustomCircularOrbit = _make(550.0, 0.0)
MID_INCLINATION_550: CustomCircularOrbit = _make(550.0, 53.0)
POLAR_550: CustomCircularOrbit = _make(550.0, 90.0)
RETROGRADE_DEMO_550: CustomCircularOrbit = _make(550.0, 97.6)
