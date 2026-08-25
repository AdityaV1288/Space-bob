"""Tests for orbit model, derived values, ring round-trips, and presets."""

from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from agcc.domain.orbit import CustomCircularOrbit
from agcc.orbit.derived import (
    mean_motion_rad_s,
    period_s,
    revolutions_per_day,
    semi_major_axis_km,
)
from agcc.orbit.presets import (
    EQUATORIAL_550,
    MID_INCLINATION_550,
    POLAR_550,
    RETROGRADE_DEMO_550,
)
from agcc.orbit.ring import orbit_to_ring, ring_to_orbit

_EPOCH = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

_EARTH_RADIUS_KM = 6378.137
_MU = 398600.4418


def _orbit(
    altitude_km: float = 550.0,
    inclination_deg: float = 53.0,
    raan_deg: float = 30.0,
    phase_deg: float = 45.0,
) -> CustomCircularOrbit:
    return CustomCircularOrbit(
        altitude_km=altitude_km,
        inclination_deg=inclination_deg,
        raan_deg=raan_deg,
        phase_deg=phase_deg,
        epoch=_EPOCH,
    )


# ---------------------------------------------------------------------------
# Range validation
# ---------------------------------------------------------------------------


class TestRangeValidation:
    def test_valid_altitude_lower_bound(self) -> None:
        o = _orbit(altitude_km=200.0)
        assert o.altitude_km == 200.0

    def test_valid_altitude_upper_bound(self) -> None:
        o = _orbit(altitude_km=2000.0)
        assert o.altitude_km == 2000.0

    def test_altitude_below_200_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(altitude_km=199.9)

    def test_altitude_above_2000_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(altitude_km=2000.1)

    def test_inclination_zero_accepted(self) -> None:
        o = _orbit(inclination_deg=0.0)
        assert o.inclination_deg == 0.0

    def test_inclination_180_accepted(self) -> None:
        o = _orbit(inclination_deg=180.0)
        assert o.inclination_deg == 180.0

    def test_inclination_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(inclination_deg=-0.1)

    def test_inclination_above_180_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(inclination_deg=180.1)

    def test_raan_zero_accepted(self) -> None:
        o = _orbit(raan_deg=0.0)
        assert o.raan_deg == 0.0

    def test_raan_359_accepted(self) -> None:
        o = _orbit(raan_deg=359.9)
        assert o.raan_deg == 359.9

    def test_raan_360_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(raan_deg=360.0)

    def test_phase_zero_accepted(self) -> None:
        o = _orbit(phase_deg=0.0)
        assert o.phase_deg == 0.0

    def test_phase_360_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _orbit(phase_deg=360.0)


# ---------------------------------------------------------------------------
# Fixed literals
# ---------------------------------------------------------------------------


class TestFixedLiterals:
    def test_eccentricity_is_zero(self) -> None:
        o = _orbit()
        assert o.eccentricity == 0.0

    def test_argument_of_perigee_is_zero(self) -> None:
        o = _orbit()
        assert o.argument_of_perigee_deg == 0.0

    def test_earth_radius_fixed(self) -> None:
        o = _orbit()
        assert o.earth_radius_km == 6378.137

    def test_mu_fixed(self) -> None:
        o = _orbit()
        assert o.mu_km3_s2 == 398600.4418


# ---------------------------------------------------------------------------
# Direction derivation
# ---------------------------------------------------------------------------


class TestDirectionDerivation:
    def test_prograde_at_zero_inclination(self) -> None:
        o = _orbit(inclination_deg=0.0)
        assert o.direction == "PROGRADE"

    def test_prograde_at_90_deg(self) -> None:
        o = _orbit(inclination_deg=90.0)
        assert o.direction == "PROGRADE"

    def test_retrograde_at_91_deg(self) -> None:
        o = _orbit(inclination_deg=91.0)
        assert o.direction == "RETROGRADE"

    def test_retrograde_at_180_deg(self) -> None:
        o = _orbit(inclination_deg=180.0)
        assert o.direction == "RETROGRADE"


# ---------------------------------------------------------------------------
# Derived value calculations
# ---------------------------------------------------------------------------


class TestDerivedValues:
    def test_semi_major_axis_550(self) -> None:
        o = _orbit(altitude_km=550.0)
        expected = _EARTH_RADIUS_KM + 550.0
        assert abs(semi_major_axis_km(o) - expected) < 1e-9

    def test_period_550_approx_5676s(self) -> None:
        """ISS-like 550 km orbit has period ~95.6 min ≈ 5736 s."""
        o = _orbit(altitude_km=550.0)
        p = period_s(o)
        # Expected: 2π * sqrt(a³/μ)
        a = _EARTH_RADIUS_KM + 550.0
        expected = 2.0 * math.pi * math.sqrt(a**3 / _MU)
        assert abs(p - expected) < 1.0  # within 1 second

    def test_period_positive(self) -> None:
        o = _orbit()
        assert period_s(o) > 0.0

    def test_mean_motion_positive(self) -> None:
        o = _orbit()
        assert mean_motion_rad_s(o) > 0.0

    def test_revolutions_per_day_550(self) -> None:
        o = _orbit(altitude_km=550.0)
        revs = revolutions_per_day(o)
        # Should be roughly 15 rev/day for 550 km
        assert 14.0 < revs < 16.0

    def test_period_increases_with_altitude(self) -> None:
        low = _orbit(altitude_km=300.0)
        high = _orbit(altitude_km=1000.0)
        assert period_s(low) < period_s(high)

    def test_consistency_period_mean_motion(self) -> None:
        o = _orbit()
        n = mean_motion_rad_s(o)
        t = period_s(o)
        assert abs(n * t - 2.0 * math.pi) < 1e-10


# ---------------------------------------------------------------------------
# Presets
# ---------------------------------------------------------------------------


class TestPresets:
    def test_equatorial_altitude(self) -> None:
        assert EQUATORIAL_550.altitude_km == 550.0

    def test_equatorial_inclination(self) -> None:
        assert EQUATORIAL_550.inclination_deg == 0.0

    def test_equatorial_direction(self) -> None:
        assert EQUATORIAL_550.direction == "PROGRADE"

    def test_mid_inclination_altitude(self) -> None:
        assert MID_INCLINATION_550.altitude_km == 550.0

    def test_mid_inclination_inclination(self) -> None:
        assert MID_INCLINATION_550.inclination_deg == 53.0

    def test_mid_inclination_direction(self) -> None:
        assert MID_INCLINATION_550.direction == "PROGRADE"

    def test_polar_altitude(self) -> None:
        assert POLAR_550.altitude_km == 550.0

    def test_polar_inclination(self) -> None:
        assert POLAR_550.inclination_deg == 90.0

    def test_polar_direction(self) -> None:
        assert POLAR_550.direction == "PROGRADE"

    def test_retrograde_demo_altitude(self) -> None:
        assert RETROGRADE_DEMO_550.altitude_km == 550.0

    def test_retrograde_demo_inclination(self) -> None:
        assert RETROGRADE_DEMO_550.inclination_deg == 97.6

    def test_retrograde_demo_direction(self) -> None:
        assert RETROGRADE_DEMO_550.direction == "RETROGRADE"

    def test_all_presets_frozen(self) -> None:
        """Presets must not be mutated."""
        with pytest.raises(Exception):
            EQUATORIAL_550.altitude_km = 600.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Ring round-trip
# ---------------------------------------------------------------------------

_ROUND_TRIP_CASES = [
    (0.0, 0.0, 0.0, 550.0),
    (53.0, 45.0, 90.0, 550.0),
    (90.0, 180.0, 270.0, 800.0),
    (97.6, 270.0, 30.0, 400.0),
    (0.0, 359.0, 1.0, 2000.0),
    (180.0, 0.0, 0.0, 550.0),
]


class TestRingRoundTrip:
    @pytest.mark.parametrize("inc,raan,phase,alt", _ROUND_TRIP_CASES)
    def test_round_trip_inclination(
        self, inc: float, raan: float, phase: float, alt: float
    ) -> None:
        o = _orbit(altitude_km=alt, inclination_deg=inc, raan_deg=raan, phase_deg=phase)
        ring = orbit_to_ring(o)
        inc_rt, raan_rt, phase_rt, alt_rt = ring_to_orbit(ring)
        assert abs(inc_rt - inc) < 1e-6, f"inclination round-trip error: {abs(inc_rt - inc)}"

    @pytest.mark.parametrize("inc,raan,phase,alt", _ROUND_TRIP_CASES)
    def test_round_trip_raan(self, inc: float, raan: float, phase: float, alt: float) -> None:
        o = _orbit(altitude_km=alt, inclination_deg=inc, raan_deg=raan, phase_deg=phase)
        ring = orbit_to_ring(o)
        inc_rt, raan_rt, phase_rt, alt_rt = ring_to_orbit(ring)
        # Exclude wraparound: if raan is near 0 or 360 both 0 and 360 are correct
        raan_err = min(abs(raan_rt - raan), 360.0 - abs(raan_rt - raan))
        assert raan_err < 1e-6, f"raan round-trip error: {raan_err}"

    @pytest.mark.parametrize("inc,raan,phase,alt", _ROUND_TRIP_CASES)
    def test_round_trip_phase(self, inc: float, raan: float, phase: float, alt: float) -> None:
        o = _orbit(altitude_km=alt, inclination_deg=inc, raan_deg=raan, phase_deg=phase)
        ring = orbit_to_ring(o)
        inc_rt, raan_rt, phase_rt, alt_rt = ring_to_orbit(ring)
        phase_err = min(abs(phase_rt - phase), 360.0 - abs(phase_rt - phase))
        assert phase_err < 1e-6, f"phase round-trip error: {phase_err}"

    @pytest.mark.parametrize("inc,raan,phase,alt", _ROUND_TRIP_CASES)
    def test_round_trip_altitude(self, inc: float, raan: float, phase: float, alt: float) -> None:
        o = _orbit(altitude_km=alt, inclination_deg=inc, raan_deg=raan, phase_deg=phase)
        ring = orbit_to_ring(o)
        inc_rt, raan_rt, phase_rt, alt_rt = ring_to_orbit(ring)
        assert abs(alt_rt - alt) < 1e-6, f"altitude round-trip error: {abs(alt_rt - alt)}"

    def test_ring_radius_scales_with_altitude(self) -> None:
        low = orbit_to_ring(_orbit(altitude_km=400.0))
        high = orbit_to_ring(_orbit(altitude_km=1000.0))
        assert low.ring_radius < high.ring_radius

    def test_ring_radius_positive(self) -> None:
        ring = orbit_to_ring(_orbit())
        assert ring.ring_radius > 0.0

    def test_quaternion_normalized(self) -> None:
        ring = orbit_to_ring(_orbit())
        norm = math.sqrt(ring.qw**2 + ring.qx**2 + ring.qy**2 + ring.qz**2)
        assert abs(norm - 1.0) < 1e-12
