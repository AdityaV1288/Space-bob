"""OrbitRingTransform — bidirectional mapping between orbit elements and 3-D scene units.

The scene uses a right-handed coordinate system where:
  - Y axis points to the celestial north pole (Earth spin axis).
  - The scene unit sphere has radius 1.0.
  - Ring radius is proportional to altitude relative to a reference altitude.

All angles in function signatures are in degrees (contracts); radians only inside.

Quaternion convention: q = (w, x, y, z), unit quaternion.

Factorization used:
  The orbital plane is obtained by:
    1. Rotating the equatorial plane around Y by -RAAN  (q_raan: rotation about Y)
    2. Then tilting around the new X axis by inclination (q_inc: rotation about X)

  q = q_raan * q_inc  where
    q_inc  = (cos(i/2), sin(i/2), 0, 0)
    q_raan = (cos(-r/2), 0, sin(-r/2), 0)   [Y-axis, angle = -RAAN]

  Product q = q_raan * q_inc:
    qw =  cos(r/2)*cos(i/2)
    qx =  cos(r/2)*sin(i/2)
    qy = -sin(r/2)*cos(i/2)
    qz =  sin(r/2)*sin(i/2)

  Inverse:
    half_i  = atan2( sqrt(qx²+qz²), sqrt(qw²+qy²) )  — gives i/2 ∈ [0, π/2]
    But we need to recover sign of inclination.
    Since inclination ∈ [0°,180°]:  i/2 ∈ [0°,90°], sin(i/2) >= 0 always.
    So:  i/2 = atan2(||(qx,qz)||, ||(qw,qy)||)  (always non-negative, correct)
    And: r/2 from atan2(-qy, qw)  =>  r = -2*atan2(-qy, qw) = 2*atan2(qy, -qw)
    But more robustly:  atan2(-qy, qw) = atan2(sin(r/2), cos(r/2)) = r/2
    So RAAN = 2 * atan2(-qy, qw),  then normalize to [0,360).
"""

from __future__ import annotations

import math

from pydantic import BaseModel, Field

from agcc.domain.orbit import CustomCircularOrbit

# Reference altitude for scene scaling: 550 km maps to ring_radius = 1.0
_REF_ALTITUDE_KM: float = 550.0
_REF_RADIUS: float = 1.0


class OrbitRingTransform(BaseModel):
    """Scene-space representation of an orbit ring."""

    model_config = {"frozen": True}

    ring_radius: float = Field(gt=0.0, description="Ring radius in normalized scene units")
    # Quaternion (w, x, y, z) describing the orbital plane orientation
    qw: float
    qx: float
    qy: float
    qz: float
    satellite_phase_rad: float = Field(
        description="Satellite position along ring in radians [0, 2π)"
    )


def orbit_to_ring(orbit: CustomCircularOrbit) -> OrbitRingTransform:
    """Convert a CustomCircularOrbit to scene-space OrbitRingTransform."""
    inc_rad = math.radians(orbit.inclination_deg)
    raan_rad = math.radians(orbit.raan_deg)
    phase_rad = math.radians(orbit.phase_deg) % (2.0 * math.pi)

    ring_radius = _REF_RADIUS * (orbit.altitude_km / _REF_ALTITUDE_KM)

    # q_inc  = (cos(i/2), sin(i/2), 0, 0)
    ci = math.cos(inc_rad / 2.0)
    si = math.sin(inc_rad / 2.0)

    # q_raan = (cos(-r/2), 0, sin(-r/2), 0)
    cr = math.cos(-raan_rad / 2.0)  # = cos(raan/2)
    sr = math.sin(-raan_rad / 2.0)  # = -sin(raan/2)

    # q = q_raan * q_inc
    # (w1,x1,y1,z1)*(w2,x2,y2,z2) =
    #   (w1w2-x1x2-y1y2-z1z2,  w1x2+x1w2+y1z2-z1y2,
    #    w1y2-x1z2+y1w2+z1x2,  w1z2+x1y2-y1x2+z1w2)
    # q_raan=(cr, 0, sr, 0), q_inc=(ci, si, 0, 0)
    qw = cr * ci - 0.0 * si - sr * 0.0 - 0.0 * 0.0
    qx = cr * si + 0.0 * ci + sr * 0.0 - 0.0 * 0.0
    qy = cr * 0.0 - 0.0 * 0.0 + sr * ci + 0.0 * si
    qz = cr * 0.0 + 0.0 * 0.0 - sr * si + 0.0 * ci

    # Simplified:
    qw = cr * ci
    qx = cr * si
    qy = sr * ci
    qz = -sr * si

    # Normalize (guard float drift)
    norm = math.sqrt(qw**2 + qx**2 + qy**2 + qz**2)
    qw /= norm
    qx /= norm
    qy /= norm
    qz /= norm

    return OrbitRingTransform(
        ring_radius=ring_radius,
        qw=qw,
        qx=qx,
        qy=qy,
        qz=qz,
        satellite_phase_rad=phase_rad,
    )


def ring_to_orbit(
    ring: OrbitRingTransform,
    altitude_km: float | None = None,
) -> tuple[float, float, float, float]:
    """Recover (inclination_deg, raan_deg, phase_deg, altitude_km) from a ring transform.

    Returns a 4-tuple: (inclination_deg, raan_deg, phase_deg, altitude_km).
    altitude_km may be supplied directly; if None it is derived from ring_radius.
    """
    alt_km = altitude_km if altitude_km is not None else ring.ring_radius * _REF_ALTITUDE_KM

    w, x, y, z = ring.qw, ring.qx, ring.qy, ring.qz

    # Recover inclination:
    # i/2 = atan2(sqrt(qx²+qz²), sqrt(qw²+qy²))
    # Since i ∈ [0°,180°], i/2 ∈ [0°,90°], result is always non-negative.
    half_inc = math.atan2(math.sqrt(x**2 + z**2), math.sqrt(w**2 + y**2))
    inclination_deg = math.degrees(2.0 * half_inc)

    # Recover RAAN:
    # From qw = cr*ci and qy = sr*ci:  atan2(qy, qw) = atan2(sr, cr) = -raan/2
    # So: raan/2 = -atan2(qy, qw)  =>  raan = -2*atan2(qy, qw)
    half_raan = -math.atan2(y, w)
    raan_deg = math.degrees(2.0 * half_raan) % 360.0

    phase_deg = math.degrees(ring.satellite_phase_rad) % 360.0

    return inclination_deg, raan_deg, phase_deg, alt_km
