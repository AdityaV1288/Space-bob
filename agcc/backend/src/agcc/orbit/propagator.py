"""Circular Kepler propagator and OrbitPropagator protocol.

Algorithm (documented convention)
----------------------------------
Step 1 — Mean motion:
    n = sqrt(mu / a^3),  a = R_earth + altitude_km   (from orbit.derived)

Step 2 — Phase advance:
    delta_t = (at - epoch).total_seconds()
    theta   = phase_deg_rad + n * delta_t             (radians, epoch true anomaly)

Step 3 — Circular orbital-plane position (perifocal frame, e=0, omega=0):
    r_pqw = [a*cos(theta),  a*sin(theta),  0]         (km)

Step 4 — Rotate to ECI using inclination (i) and RAAN (Omega):
    Apply Rz(-Omega) · Rx(-i) to r_pqw.
    Using the standard 3-1-3 Euler angle chain for Omega, i, omega=0:
        x = cos(Omega)*cos(theta) - sin(Omega)*sin(theta)*cos(i)
        y = sin(Omega)*cos(theta) + cos(Omega)*sin(theta)*cos(i)
        z = sin(theta)*sin(i)
    multiplied by a.

    Velocity vector (circular orbit):
        v_pqw = [-a*n*sin(theta),  a*n*cos(theta),  0]
    rotated by the same matrix.

Step 5 — ECI → ECEF Greenwich rotation (simplified, no precession/nutation):
    The Greenwich Sidereal Angle (GST) is approximated from the J2000 epoch.
    Convention (Vallado, "Fundamentals of Astrodynamics and Applications", 4th ed.):
        JD_UT1  = Julian date of at
        T_UT1   = (JD_UT1 - 2451545.0) / 36525      (Julian centuries from J2000)
        theta_GMST (deg) = 280.46061837
                         + 360.98564736629 * (JD_UT1 - 2451545.0)
                         + 0.000387933 * T_UT1^2
                         - T_UT1^3 / 38710000
        theta_GMST is normalised to [0, 360).
    This is the IAU 1982 GMST model — a planning approximation with ~0.1 s accuracy.

    ECEF: rotate ECI by -theta_GMST around Z:
        x_ecef =  x_eci * cos(gst) + y_eci * sin(gst)
        y_ecef = -x_eci * sin(gst) + y_eci * cos(gst)
        z_ecef =  z_eci

Step 6 — ECEF → geodetic (spherical approximation, consistent with earth_radius_km):
    r_xy   = sqrt(x^2 + y^2)
    lat    = atan2(z, r_xy)          (geocentric, equal to geodetic for sphere)
    lon    = atan2(y, x)
    alt_km = sqrt(x^2+y^2+z^2) - earth_radius_km

    Longitude is normalised to [-180, 180).
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Protocol

from agcc.domain.orbit import CustomCircularOrbit
from agcc.orbit.derived import mean_motion_rad_s, semi_major_axis_km
from agcc.orbit.models import GroundTrackPoint, OrbitState

# ---------------------------------------------------------------------------
# J2000 Julian date
# ---------------------------------------------------------------------------

_J2000_JD: float = 2451545.0
_SECONDS_PER_DAY: float = 86400.0


def _julian_date(dt: datetime) -> float:
    """Return the Julian date for a UTC datetime.

    Reference: Meeus, "Astronomical Algorithms", 2nd ed., ch. 7.
    """
    dt_utc = dt.astimezone(timezone.utc)
    y, m, d = dt_utc.year, dt_utc.month, dt_utc.day
    frac = (dt_utc.hour + dt_utc.minute / 60.0 + dt_utc.second / 3600.0) / 24.0
    if m <= 2:
        y -= 1
        m += 12
    a = int(y / 100)
    b = 2 - a + int(a / 4)
    jd = int(365.25 * (y + 4716)) + int(30.6001 * (m + 1)) + d + frac + b - 1524.5
    return jd


def _gmst_rad(dt: datetime) -> float:
    """Return Greenwich Mean Sidereal Time in radians for a UTC datetime.

    Convention: IAU 1982 GMST model (Vallado, 4th ed., eq. 3-45).
    This is a planning approximation — not suitable for operational use.
    """
    jd = _julian_date(dt)
    t_ut1 = (jd - _J2000_JD) / 36525.0
    gmst_deg = (
        280.46061837
        + 360.98564736629 * (jd - _J2000_JD)
        + 0.000387933 * t_ut1**2
        - (t_ut1**3) / 38710000.0
    )
    gmst_deg = gmst_deg % 360.0
    return math.radians(gmst_deg)


# ---------------------------------------------------------------------------
# Rotation helper
# ---------------------------------------------------------------------------


def _eci_to_ecef(
    pos_eci: tuple[float, float, float],
    vel_eci: tuple[float, float, float],
    gst: float,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Rotate ECI position and velocity to ECEF by the Greenwich sidereal angle."""
    c, s = math.cos(gst), math.sin(gst)
    x, y, z = pos_eci
    pos_ecef = (
        x * c + y * s,
        -x * s + y * c,
        z,
    )
    vx, vy, vz = vel_eci
    vel_ecef = (
        vx * c + vy * s,
        -vx * s + vy * c,
        vz,
    )
    return pos_ecef, vel_ecef


def _ecef_to_geodetic(
    pos_ecef: tuple[float, float, float],
    earth_radius_km: float,
) -> tuple[float, float, float]:
    """Convert ECEF position to geodetic (lat_deg, lon_deg, alt_km).

    Uses the spherical approximation: geocentric latitude equals geodetic
    latitude. This is consistent with the earth_radius_km constant from the
    orbit model (spherical Earth).

    Reference: Montenbruck & Gill, "Satellite Orbits", sec. 5.3.
    """
    x, y, z = pos_ecef
    r_xy = math.sqrt(x**2 + y**2)
    lat_rad = math.atan2(z, r_xy)
    lon_rad = math.atan2(y, x)
    alt_km = math.sqrt(x**2 + y**2 + z**2) - earth_radius_km

    lat_deg = math.degrees(lat_rad)
    lon_deg = math.degrees(lon_rad)
    # Normalise longitude to [-180, 180)
    lon_deg = ((lon_deg + 180.0) % 360.0) - 180.0

    return lat_deg, lon_deg, alt_km


# ---------------------------------------------------------------------------
# Propagation core
# ---------------------------------------------------------------------------


def _propagate_one(
    orbit: CustomCircularOrbit,
    at: datetime,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    float,
    float,
    float,
]:
    """Return (pos_eci, vel_eci, pos_ecef, lat_deg, lon_deg, alt_km) at `at`."""
    # Step 1 — mean motion and semi-major axis
    n = mean_motion_rad_s(orbit)
    a = semi_major_axis_km(orbit)

    # Step 2 — advance phase
    delta_s = (at - orbit.epoch).total_seconds()
    theta = math.radians(orbit.phase_deg) + n * delta_s  # radians

    # Step 3 — perifocal position and velocity (circular, omega=0)
    cos_t = math.cos(theta)
    sin_t = math.sin(theta)
    r_pqw = (a * cos_t, a * sin_t, 0.0)
    v_pqw = (-a * n * sin_t, a * n * cos_t, 0.0)

    # Step 4 — rotate to ECI
    inc = math.radians(orbit.inclination_deg)
    raan = math.radians(orbit.raan_deg)
    cos_i = math.cos(inc)
    sin_i = math.sin(inc)
    cos_o = math.cos(raan)
    sin_o = math.sin(raan)

    # Standard 3-1-3 rotation matrix columns:
    #   [cos_o*cos_t - sin_o*sin_t*cos_i,  -cos_o*sin_t - sin_o*cos_t*cos_i,  sin_o*sin_i]
    #   [sin_o*cos_t + cos_o*sin_t*cos_i,  -sin_o*sin_t + cos_o*cos_t*cos_i, -cos_o*sin_i]
    #   [             sin_t*sin_i,                       cos_t*sin_i,               cos_i ]
    # Applied to perifocal vector (p, q, 0):
    px, py, _ = r_pqw
    vx_p, vy_p, _ = v_pqw

    x_eci = cos_o * px - sin_o * py * cos_i
    y_eci = sin_o * px + cos_o * py * cos_i
    z_eci = py * sin_i

    vx_eci = cos_o * vx_p - sin_o * vy_p * cos_i
    vy_eci = sin_o * vx_p + cos_o * vy_p * cos_i
    vz_eci = vy_p * sin_i

    pos_eci = (x_eci, y_eci, z_eci)
    vel_eci = (vx_eci, vy_eci, vz_eci)

    # Step 5 — ECI → ECEF
    gst = _gmst_rad(at)
    pos_ecef, _ = _eci_to_ecef(pos_eci, vel_eci, gst)

    # Step 6 — ECEF → geodetic
    lat_deg, lon_deg, alt_km = _ecef_to_geodetic(pos_ecef, orbit.earth_radius_km)

    return pos_eci, vel_eci, pos_ecef, lat_deg, lon_deg, alt_km


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class OrbitPropagator(Protocol):
    """Abstract interface for orbit propagators."""

    def state_at(self, orbit: CustomCircularOrbit, at: datetime) -> OrbitState:
        """Return the full orbit state at the given UTC time."""
        ...

    def sample_ground_track(
        self,
        orbit: CustomCircularOrbit,
        start: datetime,
        end: datetime,
        step_s: int,
    ) -> list[GroundTrackPoint]:
        """Return ground-track samples from start to end (inclusive) at step_s intervals."""
        ...


# ---------------------------------------------------------------------------
# Concrete implementation
# ---------------------------------------------------------------------------


class CircularKeplerPropagator:
    """Deterministic two-body circular Kepler propagator.

    Implements the fixed algorithm documented in the module docstring.
    No perturbations, no atmospheric drag, no J2, no TLE.
    """

    def state_at(self, orbit: CustomCircularOrbit, at: datetime) -> OrbitState:
        """Return OrbitState at the given UTC time."""
        pos_eci, vel_eci, pos_ecef, lat_deg, lon_deg, alt_km = _propagate_one(orbit, at)
        return OrbitState(
            time=at,
            pos_eci_km=pos_eci,
            vel_eci_km_s=vel_eci,
            pos_ecef_km=pos_ecef,
            latitude_deg=lat_deg,
            longitude_deg=lon_deg,
            altitude_km=alt_km,
        )

    def sample_ground_track(
        self,
        orbit: CustomCircularOrbit,
        start: datetime,
        end: datetime,
        step_s: int,
    ) -> list[GroundTrackPoint]:
        """Return ground-track samples from start to end (inclusive) at step_s intervals."""
        if step_s <= 0:
            raise ValueError(f"step_s must be positive, got {step_s}")
        points: list[GroundTrackPoint] = []
        t = start
        end_ts = end.timestamp()
        while t.timestamp() <= end_ts:
            _, _, pos_ecef, lat_deg, lon_deg, alt_km = _propagate_one(orbit, t)
            points.append(
                GroundTrackPoint(
                    time=t,
                    latitude_deg=lat_deg,
                    longitude_deg=lon_deg,
                    altitude_km=alt_km,
                )
            )
            from datetime import timedelta  # local import to avoid top-level cycle

            t = t + timedelta(seconds=step_s)
        return points
