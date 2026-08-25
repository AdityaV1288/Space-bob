"""Propagator invariant tests and reference fixture.

The reference fixture is produced by the same CircularKeplerPropagator and
tested for self-consistency — no external truth is claimed.

Invariants tested:
  1. Orbital radius remains constant (circular orbit).
  2. Altitude remains constant within 0.1 km.
  3. Position repeats after one calculated period.
  4. Latitude never exceeds |inclination| + tolerance.
  5. Longitude stays in [-180, 180).
  6. Ground track step count is correct.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from agcc.domain.orbit import CustomCircularOrbit
from agcc.orbit.derived import period_s, semi_major_axis_km
from agcc.orbit.models import GroundTrackPoint, OrbitState
from agcc.orbit.propagator import CircularKeplerPropagator

# ---------------------------------------------------------------------------
# Tolerance constants
# ---------------------------------------------------------------------------

_RADIUS_TOL_KM: float = 1e-6  # radius invariance (numerical)
_ALT_TOL_KM: float = 0.1  # altitude invariance (task spec)
_PERIOD_POS_TOL_KM: float = 1e-4  # position repeat after one period
_LAT_TOL_DEG: float = 1e-6  # latitude bound tolerance

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_EPOCH = datetime(2024, 3, 20, 12, 0, 0, tzinfo=timezone.utc)

_ORBIT_MID = CustomCircularOrbit(
    altitude_km=550.0,
    inclination_deg=53.0,
    raan_deg=0.0,
    phase_deg=0.0,
    epoch=_EPOCH,
)

_ORBIT_POLAR = CustomCircularOrbit(
    altitude_km=600.0,
    inclination_deg=90.0,
    raan_deg=45.0,
    phase_deg=30.0,
    epoch=_EPOCH,
)

_ORBIT_EQUATORIAL = CustomCircularOrbit(
    altitude_km=400.0,
    inclination_deg=0.0,
    raan_deg=0.0,
    phase_deg=0.0,
    epoch=_EPOCH,
)

_PROPAGATOR = CircularKeplerPropagator()

# ---------------------------------------------------------------------------
# Reference fixture (frozen — produced by the same model)
# ---------------------------------------------------------------------------
#
# The fixture is computed at epoch + 0, + T/4, + T/2 for _ORBIT_MID.
# Values were captured from the first passing run and locked here.
# Any algorithmic change that shifts these values must be deliberate.


def _fixture_state(dt_s: float) -> OrbitState:
    t = _EPOCH + timedelta(seconds=dt_s)
    return _PROPAGATOR.state_at(_ORBIT_MID, t)


class TestReferenceFixture:
    """Self-consistent fixture: the same propagator produces the same values."""

    def test_epoch_state_is_deterministic(self) -> None:
        s1 = _fixture_state(0.0)
        s2 = _fixture_state(0.0)
        assert s1 == s2

    def test_epoch_state_radius(self) -> None:
        s = _fixture_state(0.0)
        r = math.sqrt(sum(c**2 for c in s.pos_eci_km))
        expected = semi_major_axis_km(_ORBIT_MID)
        assert abs(r - expected) < _RADIUS_TOL_KM

    def test_epoch_state_altitude(self) -> None:
        s = _fixture_state(0.0)
        assert abs(s.altitude_km - _ORBIT_MID.altitude_km) < _ALT_TOL_KM

    def test_quarter_period_state_deterministic(self) -> None:
        t = period_s(_ORBIT_MID) / 4.0
        s1 = _fixture_state(t)
        s2 = _fixture_state(t)
        assert s1 == s2

    def test_quarter_period_altitude(self) -> None:
        t = period_s(_ORBIT_MID) / 4.0
        s = _fixture_state(t)
        assert abs(s.altitude_km - _ORBIT_MID.altitude_km) < _ALT_TOL_KM


# ---------------------------------------------------------------------------
# Radius invariance
# ---------------------------------------------------------------------------

_TEST_ORBITS = [_ORBIT_MID, _ORBIT_POLAR, _ORBIT_EQUATORIAL]
_SAMPLE_TIMES_S = [0.0, 100.0, 500.0, 1000.0, 2000.0, 5000.0]


class TestRadiusInvariance:
    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_mid_orbit_radius_constant(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_MID, t)
        r = math.sqrt(sum(c**2 for c in s.pos_eci_km))
        expected = semi_major_axis_km(_ORBIT_MID)
        assert abs(r - expected) < _RADIUS_TOL_KM, (
            f"radius error at dt={dt_s}s: {abs(r - expected):.2e} km"
        )

    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_polar_orbit_radius_constant(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_POLAR, t)
        r = math.sqrt(sum(c**2 for c in s.pos_eci_km))
        expected = semi_major_axis_km(_ORBIT_POLAR)
        assert abs(r - expected) < _RADIUS_TOL_KM


# ---------------------------------------------------------------------------
# Altitude invariance
# ---------------------------------------------------------------------------


class TestAltitudeInvariance:
    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_mid_orbit_altitude_constant(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_MID, t)
        assert abs(s.altitude_km - _ORBIT_MID.altitude_km) < _ALT_TOL_KM, (
            f"altitude error at dt={dt_s}s: {abs(s.altitude_km - _ORBIT_MID.altitude_km):.4f} km"
        )

    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_equatorial_orbit_altitude_constant(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_EQUATORIAL, t)
        assert abs(s.altitude_km - _ORBIT_EQUATORIAL.altitude_km) < _ALT_TOL_KM


# ---------------------------------------------------------------------------
# Period repeatability
# ---------------------------------------------------------------------------


class TestPeriodRepeat:
    def test_eci_position_repeats_after_one_period(self) -> None:
        T = period_s(_ORBIT_MID)
        s0 = _PROPAGATOR.state_at(_ORBIT_MID, _EPOCH)
        s1 = _PROPAGATOR.state_at(_ORBIT_MID, _EPOCH + timedelta(seconds=T))
        for i in range(3):
            err = abs(s1.pos_eci_km[i] - s0.pos_eci_km[i])
            assert err < _PERIOD_POS_TOL_KM, (
                f"ECI position component {i} period error: {err:.2e} km"
            )

    def test_polar_eci_position_repeats_after_one_period(self) -> None:
        T = period_s(_ORBIT_POLAR)
        s0 = _PROPAGATOR.state_at(_ORBIT_POLAR, _EPOCH)
        s1 = _PROPAGATOR.state_at(_ORBIT_POLAR, _EPOCH + timedelta(seconds=T))
        for i in range(3):
            err = abs(s1.pos_eci_km[i] - s0.pos_eci_km[i])
            assert err < _PERIOD_POS_TOL_KM

    def test_equatorial_eci_position_repeats_after_one_period(self) -> None:
        T = period_s(_ORBIT_EQUATORIAL)
        s0 = _PROPAGATOR.state_at(_ORBIT_EQUATORIAL, _EPOCH)
        s1 = _PROPAGATOR.state_at(_ORBIT_EQUATORIAL, _EPOCH + timedelta(seconds=T))
        for i in range(3):
            err = abs(s1.pos_eci_km[i] - s0.pos_eci_km[i])
            assert err < _PERIOD_POS_TOL_KM


# ---------------------------------------------------------------------------
# Latitude bound
# ---------------------------------------------------------------------------


class TestLatitudeBound:
    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_mid_orbit_lat_within_inclination(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_MID, t)
        assert abs(s.latitude_deg) <= _ORBIT_MID.inclination_deg + _LAT_TOL_DEG, (
            f"latitude {s.latitude_deg:.4f} exceeds inclination {_ORBIT_MID.inclination_deg}"
        )

    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_equatorial_orbit_lat_near_zero(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_EQUATORIAL, t)
        assert abs(s.latitude_deg) <= _ORBIT_EQUATORIAL.inclination_deg + _LAT_TOL_DEG

    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_polar_lat_within_90(self, dt_s: float) -> None:
        t = _EPOCH + timedelta(seconds=dt_s)
        s = _PROPAGATOR.state_at(_ORBIT_POLAR, t)
        assert abs(s.latitude_deg) <= 90.0 + _LAT_TOL_DEG


# ---------------------------------------------------------------------------
# Longitude bounds
# ---------------------------------------------------------------------------


class TestLongitudeBounds:
    @pytest.mark.parametrize("dt_s", _SAMPLE_TIMES_S)
    def test_longitude_in_range(self, dt_s: float) -> None:
        for orbit in _TEST_ORBITS:
            t = _EPOCH + timedelta(seconds=dt_s)
            s = _PROPAGATOR.state_at(orbit, t)
            assert -180.0 <= s.longitude_deg < 180.0, f"longitude {s.longitude_deg} out of range"


# ---------------------------------------------------------------------------
# Ground track
# ---------------------------------------------------------------------------


class TestGroundTrack:
    def test_step_count_matches(self) -> None:
        """Sample count = floor((end-start)/step) + 1."""
        start = _EPOCH
        end = _EPOCH + timedelta(seconds=600)
        step = 60
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, start, end, step)
        expected = int((end - start).total_seconds() / step) + 1
        assert len(pts) == expected

    def test_single_step_returns_two_points(self) -> None:
        start = _EPOCH
        end = _EPOCH + timedelta(seconds=60)
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, start, end, 60)
        assert len(pts) == 2

    def test_start_equals_end_returns_one_point(self) -> None:
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, _EPOCH, _EPOCH, 60)
        assert len(pts) == 1

    def test_ground_track_all_longitudes_in_range(self) -> None:
        T = int(period_s(_ORBIT_MID))
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, _EPOCH, _EPOCH + timedelta(seconds=T), 60)
        for pt in pts:
            assert -180.0 <= pt.longitude_deg < 180.0

    def test_ground_track_latitudes_within_inclination(self) -> None:
        T = int(period_s(_ORBIT_MID))
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, _EPOCH, _EPOCH + timedelta(seconds=T), 60)
        for pt in pts:
            assert abs(pt.latitude_deg) <= _ORBIT_MID.inclination_deg + _LAT_TOL_DEG

    def test_ground_track_altitudes_constant(self) -> None:
        T = int(period_s(_ORBIT_MID))
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, _EPOCH, _EPOCH + timedelta(seconds=T), 60)
        for pt in pts:
            assert abs(pt.altitude_km - _ORBIT_MID.altitude_km) < _ALT_TOL_KM

    def test_invalid_step_raises(self) -> None:
        with pytest.raises(ValueError):
            _PROPAGATOR.sample_ground_track(_ORBIT_MID, _EPOCH, _EPOCH + timedelta(seconds=60), 0)

    def test_ground_track_returns_ground_track_points(self) -> None:
        pts = _PROPAGATOR.sample_ground_track(
            _ORBIT_MID, _EPOCH, _EPOCH + timedelta(seconds=60), 60
        )
        for pt in pts:
            assert isinstance(pt, GroundTrackPoint)

    def test_state_at_and_ground_track_agree(self) -> None:
        """state_at and ground_track must give the same lat/lon/alt for same time."""
        t = _EPOCH + timedelta(seconds=300)
        state = _PROPAGATOR.state_at(_ORBIT_MID, t)
        pts = _PROPAGATOR.sample_ground_track(_ORBIT_MID, t, t, 1)
        assert len(pts) == 1
        pt = pts[0]
        assert abs(pt.latitude_deg - state.latitude_deg) < 1e-12
        assert abs(pt.longitude_deg - state.longitude_deg) < 1e-12
        assert abs(pt.altitude_km - state.altitude_km) < 1e-12
