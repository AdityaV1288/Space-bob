"""Topocentric geometry functions: elevation, azimuth, and slant range.

Convention (Vallado, "Fundamentals of Astrodynamics and Applications", 4th ed.):
  - Station ECEF position derived from geodetic coords using spherical Earth.
  - Topocentric vector = satellite ECEF − station ECEF.
  - SEZ (South-East-Zenith) frame used for elevation and azimuth.
  - Elevation: angle above horizon = atan2(Z_sez, sqrt(S²+E²)).
  - Azimuth: measured clockwise from North = atan2(E_sez, -S_sez), normalised [0,360).
  - Slant range: magnitude of topocentric vector.
"""

from __future__ import annotations

import math


def station_ecef_km(
    lat_deg: float,
    lon_deg: float,
    alt_m: float,
    earth_radius_km: float = 6378.137,
) -> tuple[float, float, float]:
    """Return ECEF position of a ground station in km.

    Uses the spherical-Earth approximation consistent with the propagator.
    Altitude is converted from meters to km.
    """
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    r = earth_radius_km + alt_m / 1000.0
    x = r * math.cos(lat) * math.cos(lon)
    y = r * math.cos(lat) * math.sin(lon)
    z = r * math.sin(lat)
    return x, y, z


def topocentric_sez(
    sat_ecef: tuple[float, float, float],
    sta_ecef: tuple[float, float, float],
    lat_deg: float,
    lon_deg: float,
) -> tuple[float, float, float]:
    """Return the topocentric (South, East, Zenith) components in km.

    The SEZ frame is centred at the station:
      S = south component (negative = north)
      E = east component
      Z = zenith component (positive = above horizon)

    Rotation from ECEF difference to SEZ:
      [ S ]   [ sin(lat)*cos(lon)  sin(lat)*sin(lon)  -cos(lat) ] [ dx ]
      [ E ] = [     -sin(lon)          cos(lon)            0    ] [ dy ]
      [ Z ]   [ cos(lat)*cos(lon)  cos(lat)*sin(lon)   sin(lat) ] [ dz ]
    """
    dx = sat_ecef[0] - sta_ecef[0]
    dy = sat_ecef[1] - sta_ecef[1]
    dz = sat_ecef[2] - sta_ecef[2]

    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sl, cl = math.sin(lat), math.cos(lat)
    slo, clo = math.sin(lon), math.cos(lon)

    s = sl * clo * dx + sl * slo * dy - cl * dz
    e = -slo * dx + clo * dy
    z = cl * clo * dx + cl * slo * dy + sl * dz

    return s, e, z


def elevation_deg(
    sat_ecef: tuple[float, float, float],
    sta_ecef: tuple[float, float, float],
    lat_deg: float,
    lon_deg: float,
) -> float:
    """Return topocentric elevation in degrees."""
    s, e, z = topocentric_sez(sat_ecef, sta_ecef, lat_deg, lon_deg)
    rho_horiz = math.sqrt(s**2 + e**2)
    return math.degrees(math.atan2(z, rho_horiz))


def azimuth_deg(
    sat_ecef: tuple[float, float, float],
    sta_ecef: tuple[float, float, float],
    lat_deg: float,
    lon_deg: float,
) -> float:
    """Return topocentric azimuth in degrees, measured clockwise from North [0, 360)."""
    s, e, _ = topocentric_sez(sat_ecef, sta_ecef, lat_deg, lon_deg)
    az = math.degrees(math.atan2(e, -s))
    return az % 360.0


def slant_range_km(
    sat_ecef: tuple[float, float, float],
    sta_ecef: tuple[float, float, float],
) -> float:
    """Return slant range between satellite and station in km."""
    return math.sqrt(
        (sat_ecef[0] - sta_ecef[0]) ** 2
        + (sat_ecef[1] - sta_ecef[1]) ** 2
        + (sat_ecef[2] - sta_ecef[2]) ** 2
    )
