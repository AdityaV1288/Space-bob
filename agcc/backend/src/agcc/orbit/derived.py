"""Pure orbit derivation functions — no side effects, no I/O."""

from __future__ import annotations

import math

from agcc.domain.orbit import CustomCircularOrbit


def semi_major_axis_km(orbit: CustomCircularOrbit) -> float:
    """Return semi-major axis in kilometers."""
    return orbit.earth_radius_km + orbit.altitude_km


def mean_motion_rad_s(orbit: CustomCircularOrbit) -> float:
    """Return mean motion in radians per second."""
    a = semi_major_axis_km(orbit)
    return float(math.sqrt(orbit.mu_km3_s2 / (a**3)))


def period_s(orbit: CustomCircularOrbit) -> float:
    """Return orbital period in seconds."""
    return (2.0 * math.pi) / mean_motion_rad_s(orbit)


def revolutions_per_day(orbit: CustomCircularOrbit) -> float:
    """Return number of complete revolutions per day."""
    return 86400.0 / period_s(orbit)
