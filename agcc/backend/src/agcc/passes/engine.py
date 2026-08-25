"""Pass engine: coarse sample → bisect rise/set → peak → filter → sort.

Algorithm
---------
1. Coarse-sample elevation every 60 s for each station over the horizon.
2. Detect threshold crossings (sign changes of elevation − min_elevation_deg).
3. Bisect each crossing to <= 1 s accuracy.
4. Find peak elevation within each [rise, set] interval by 1-second sampling.
5. Reject windows where usable_duration_s <= 0 (setup + teardown >= duration).
6. Compute azimuth at rise/peak/set and slant range at peak.
7. Build deterministic pass ID from satellite_id, station_id, rise ISO string,
   orbit_model_version via SHA-256 (first 16 hex chars) prefixed "pass_".
8. Collect and sort by start_at then station_id.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Sequence

from agcc.domain.orbit import CustomCircularOrbit
from agcc.domain.planning import CandidatePass
from agcc.domain.stations import GroundStation, StationCatalog
from agcc.orbit.propagator import CircularKeplerPropagator, OrbitPropagator
from agcc.passes.geometry import (
    azimuth_deg,
    elevation_deg,
    slant_range_km,
    station_ecef_km,
)
from agcc.passes.screener import DefaultStationScreener, StationScreener

_COARSE_STEP_S: int = 60
_BISECT_TOL_S: float = 1.0
_PEAK_STEP_S: int = 1

_ORBIT_MODEL_VERSION = "circular_kepler_v1"


# ---------------------------------------------------------------------------
# Pass ID
# ---------------------------------------------------------------------------


def _pass_id(
    satellite_id: str,
    station_id: str,
    rise_iso: str,
    orbit_model_version: str,
) -> str:
    raw = f"{satellite_id}|{station_id}|{rise_iso}|{orbit_model_version}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:16]
    return f"pass_{digest}"


# ---------------------------------------------------------------------------
# Elevation helper at a datetime
# ---------------------------------------------------------------------------


def _elev_at(
    orbit: CustomCircularOrbit,
    station: GroundStation,
    sta_ecef: tuple[float, float, float],
    at: datetime,
    propagator: OrbitPropagator,
) -> float:
    pos_ecef = propagator.state_at(orbit, at).pos_ecef_km
    return elevation_deg(pos_ecef, sta_ecef, station.latitude_deg, station.longitude_deg)


# ---------------------------------------------------------------------------
# Bisection to find threshold crossing
# ---------------------------------------------------------------------------


def _bisect_crossing(
    orbit: CustomCircularOrbit,
    station: GroundStation,
    sta_ecef: tuple[float, float, float],
    t_lo: datetime,
    t_hi: datetime,
    min_elev: float,
    propagator: OrbitPropagator,
) -> datetime:
    """Bisect to find the time when elevation crosses min_elev.

    Precondition: elevation at t_lo and t_hi are on opposite sides of min_elev.
    Returns the crossing time within ±_BISECT_TOL_S seconds.
    """
    lo_ts = t_lo.timestamp()
    hi_ts = t_hi.timestamp()
    f_lo = _elev_at(orbit, station, sta_ecef, t_lo, propagator) - min_elev

    while hi_ts - lo_ts > _BISECT_TOL_S:
        mid_ts = (lo_ts + hi_ts) / 2.0
        mid_t = datetime.fromtimestamp(mid_ts, tz=timezone.utc)
        f_mid = _elev_at(orbit, station, sta_ecef, mid_t, propagator) - min_elev
        if f_lo * f_mid <= 0.0:
            hi_ts = mid_ts
        else:
            lo_ts = mid_ts
            f_lo = f_mid

    return datetime.fromtimestamp((lo_ts + hi_ts) / 2.0, tz=timezone.utc)


# ---------------------------------------------------------------------------
# Peak finder (1-second sampling within window)
# ---------------------------------------------------------------------------


def _find_peak(
    orbit: CustomCircularOrbit,
    station: GroundStation,
    sta_ecef: tuple[float, float, float],
    rise: datetime,
    set_: datetime,
    propagator: OrbitPropagator,
) -> tuple[datetime, float]:
    """Return the highest strict-interior 1-second sample.

    A planning horizon may begin or end in the middle of an existing pass. In
    that truncated window the mathematical maximum can be exactly on a
    boundary, while CandidatePass deliberately requires start < peak < end for
    safe interpolation. The closest interior sample is therefore authoritative
    for the represented opportunity.
    """
    duration_s = (set_ - rise).total_seconds()
    if duration_s <= 0.0:
        raise ValueError("Pass peak requires a positive-duration window")
    inset_s = min(float(_PEAK_STEP_S), duration_s / 2.0)
    first_t = rise + timedelta(seconds=inset_s)
    best_t = first_t
    best_el = _elev_at(orbit, station, sta_ecef, first_t, propagator)

    t = first_t + timedelta(seconds=_PEAK_STEP_S)
    end_ts = set_.timestamp()
    while t.timestamp() < end_ts:
        el = _elev_at(orbit, station, sta_ecef, t, propagator)
        if el > best_el:
            best_el = el
            best_t = t
        t = t + timedelta(seconds=_PEAK_STEP_S)

    return best_t, best_el


# ---------------------------------------------------------------------------
# Per-station pass computation
# ---------------------------------------------------------------------------


def _compute_passes_for_station(
    orbit: CustomCircularOrbit,
    station: GroundStation,
    start: datetime,
    end: datetime,
    orbit_model_version: str,
    station_catalog_version: str,
    scenario_id: str,
    satellite_id: str,
    propagator: OrbitPropagator,
) -> list[CandidatePass]:
    sta_ecef = station_ecef_km(
        station.latitude_deg,
        station.longitude_deg,
        station.altitude_m,
    )
    min_elev = station.minimum_elevation_deg

    # --- Step 1: coarse sample ---
    samples: list[tuple[datetime, float]] = []
    t = start
    end_ts = end.timestamp()
    while t.timestamp() <= end_ts:
        el = _elev_at(orbit, station, sta_ecef, t, propagator)
        samples.append((t, el))
        t = t + timedelta(seconds=_COARSE_STEP_S)
    # include end exactly if not already there
    if samples and samples[-1][0] < end:
        el = _elev_at(orbit, station, sta_ecef, end, propagator)
        samples.append((end, el))

    # --- Step 2 & 3: detect threshold crossings, bisect ---
    passes: list[CandidatePass] = []
    in_pass = False
    rise_time: datetime | None = None

    for i in range(len(samples) - 1):
        t0, el0 = samples[i]
        t1, el1 = samples[i + 1]
        above0 = el0 >= min_elev
        above1 = el1 >= min_elev

        if not in_pass and not above0 and above1:
            # Rising crossing
            rise_time = _bisect_crossing(orbit, station, sta_ecef, t0, t1, min_elev, propagator)
            in_pass = True
        elif not in_pass and above0 and i == 0:
            # Starts already above threshold
            rise_time = t0
            in_pass = True
        elif in_pass and above0 and not above1:
            # Setting crossing
            set_time = _bisect_crossing(orbit, station, sta_ecef, t0, t1, min_elev, propagator)
            if rise_time is not None:
                cp = _build_pass(
                    orbit,
                    station,
                    sta_ecef,
                    rise_time,
                    set_time,
                    orbit_model_version,
                    station_catalog_version,
                    scenario_id,
                    satellite_id,
                    propagator,
                )
                if cp is not None:
                    passes.append(cp)
            in_pass = False
            rise_time = None

    # Window ends while still above threshold
    if in_pass and rise_time is not None:
        set_time = end
        cp = _build_pass(
            orbit,
            station,
            sta_ecef,
            rise_time,
            set_time,
            orbit_model_version,
            station_catalog_version,
            scenario_id,
            satellite_id,
            propagator,
        )
        if cp is not None:
            passes.append(cp)

    return passes


def _build_pass(
    orbit: CustomCircularOrbit,
    station: GroundStation,
    sta_ecef: tuple[float, float, float],
    rise: datetime,
    set_: datetime,
    orbit_model_version: str,
    station_catalog_version: str,
    scenario_id: str,
    satellite_id: str,
    propagator: OrbitPropagator,
) -> CandidatePass | None:
    """Build a CandidatePass; returns None if usable_duration_s <= 0."""
    duration_s = (set_ - rise).total_seconds()
    usable_s = duration_s - station.setup_s - station.teardown_s
    if usable_s <= 0.0:
        return None

    # Peak
    peak_t, peak_el = _find_peak(orbit, station, sta_ecef, rise, set_, propagator)

    # Geometry at key times
    pos_start = propagator.state_at(orbit, rise).pos_ecef_km
    pos_peak = propagator.state_at(orbit, peak_t).pos_ecef_km
    pos_end = propagator.state_at(orbit, set_).pos_ecef_km

    az_start = azimuth_deg(pos_start, sta_ecef, station.latitude_deg, station.longitude_deg)
    az_peak = azimuth_deg(pos_peak, sta_ecef, station.latitude_deg, station.longitude_deg)
    az_end = azimuth_deg(pos_end, sta_ecef, station.latitude_deg, station.longitude_deg)
    slant = slant_range_km(pos_peak, sta_ecef)

    rise_iso = rise.strftime("%Y%m%dT%H%M%SZ")
    pid = _pass_id(satellite_id, station.station_id, rise_iso, orbit_model_version)

    return CandidatePass(
        pass_id=pid,
        scenario_id=scenario_id,
        satellite_id=satellite_id,
        station_id=station.station_id,
        start_at=rise,
        peak_at=peak_t,
        end_at=set_,
        duration_s=duration_s,
        usable_duration_s=usable_s,
        max_elevation_deg=min(peak_el, 90.0),
        azimuth_start_deg=az_start,
        azimuth_peak_deg=az_peak,
        azimuth_end_deg=az_end,
        slant_range_peak_km=slant,
        minimum_elevation_deg=station.minimum_elevation_deg,
        orbit_model_version=orbit_model_version,
        station_catalog_version=station_catalog_version,
    )


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------


class PassEngine:
    """Computes CandidatePass windows for a satellite over authorized stations."""

    def __init__(
        self,
        propagator: OrbitPropagator | None = None,
        screener: StationScreener | None = None,
        orbit_model_version: str = _ORBIT_MODEL_VERSION,
    ) -> None:
        self._propagator: OrbitPropagator = propagator or CircularKeplerPropagator()
        self._screener: StationScreener = screener or DefaultStationScreener()
        self._orbit_model_version = orbit_model_version

    def compute_passes(
        self,
        orbit: CustomCircularOrbit,
        satellite_id: str,
        stations: Sequence[GroundStation],
        horizon_start: datetime,
        horizon_end: datetime,
        scenario_id: str,
        station_catalog_version: str,
    ) -> list[CandidatePass]:
        """Return sorted candidate passes over all screened stations.

        Sorted by start_at then station_id.

        Raises ValueError for:
        - Naive (timezone-unaware) horizon datetimes
        - horizon_end <= horizon_start
        - satellite_id not starting with "sat_"
        - scenario_id not starting with "scenario_"
        - Empty station_catalog_version
        - Duplicate station IDs
        """
        # Validate timezone awareness
        if horizon_start.tzinfo is None:
            raise ValueError("horizon_start must be timezone-aware")
        if horizon_end.tzinfo is None:
            raise ValueError("horizon_end must be timezone-aware")
        # Validate horizon order
        if horizon_end <= horizon_start:
            raise ValueError("horizon_end must be after horizon_start")
        # Validate satellite_id
        if not satellite_id.startswith("sat_"):
            raise ValueError(f"satellite_id must start with 'sat_', got '{satellite_id}'")
        # Validate scenario_id
        if not scenario_id.startswith("scenario_"):
            raise ValueError(f"scenario_id must start with 'scenario_', got '{scenario_id}'")
        # Validate station_catalog_version
        if not station_catalog_version:
            raise ValueError("station_catalog_version must be non-empty")
        # Validate unique station IDs
        station_ids = [s.station_id for s in stations]
        if len(station_ids) != len(set(station_ids)):
            raise ValueError("Duplicate station IDs in stations list")

        # Normalize to UTC
        start = horizon_start.astimezone(timezone.utc)
        end = horizon_end.astimezone(timezone.utc)

        screened = self._screener.screen(orbit, list(stations))

        all_passes: list[CandidatePass] = []
        for station in screened:
            station_passes = _compute_passes_for_station(
                orbit=orbit,
                station=station,
                start=start,
                end=end,
                orbit_model_version=self._orbit_model_version,
                station_catalog_version=station_catalog_version,
                scenario_id=scenario_id,
                satellite_id=satellite_id,
                propagator=self._propagator,
            )
            all_passes.extend(station_passes)

        # Sort deterministically: start_at then station_id
        all_passes.sort(key=lambda p: (p.start_at, p.station_id))
        return all_passes

    def compute_passes_from_catalog(
        self,
        orbit: CustomCircularOrbit,
        satellite_id: str,
        catalog: StationCatalog,
        selected_stations: Sequence[GroundStation],
        horizon_start: datetime,
        horizon_end: datetime,
        scenario_id: str,
    ) -> list[CandidatePass]:
        """Return sorted candidate passes using station_catalog_version from catalog.

        Delegates to compute_passes() with station_catalog_version=catalog.catalog_version.
        """
        return self.compute_passes(
            orbit=orbit,
            satellite_id=satellite_id,
            stations=selected_stations,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
            scenario_id=scenario_id,
            station_catalog_version=catalog.catalog_version,
        )
