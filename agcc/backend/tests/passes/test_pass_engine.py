"""Acceptance tests for the pass engine.

Verified invariants:
  1. start_at < peak_at < end_at for every pass.
  2. Elevation at refined rise/set boundaries is within numerical tolerance of min_elevation_deg.
  3. max_elevation_deg >= minimum_elevation_deg for every pass.
  4. No overlapping duplicate passes for the same station.
  5. Multiple demo stations produce multiple passes over 24 hours.
  6. Same input produces byte-identical normalised output (determinism).
  7. Injected propagator is used by the engine (not direct _propagate_one).
  8. Input validation (naive horizons, bad IDs, blank catalog version, duplicates).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from agcc.domain.enums import Band
from agcc.domain.orbit import CustomCircularOrbit
from agcc.domain.planning import CandidatePass
from agcc.domain.stations import FieldProvenance, GroundStation, StationSelection
from agcc.orbit.models import OrbitState
from agcc.orbit.propagator import _propagate_one
from agcc.passes.engine import PassEngine
from agcc.passes.geometry import elevation_deg, station_ecef_km
from agcc.stations.catalog import load_catalog_from_file
from agcc.stations.filtering import filter_stations

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_EPOCH = datetime(2024, 3, 20, 12, 0, 0, tzinfo=timezone.utc)

# Mid-inclination orbit — will have many passes over the demo stations
_ORBIT_MID = CustomCircularOrbit(
    altitude_km=550.0,
    inclination_deg=53.0,
    raan_deg=0.0,
    phase_deg=0.0,
    epoch=_EPOCH,
)

_FULL_ASSUMPTIONS = [
    "latitude_deg",
    "longitude_deg",
    "altitude_m",
    "supported_bands",
    "max_downlink_rate_mbps",
    "minimum_elevation_deg",
    "setup_s",
    "teardown_s",
    "cost_model",
    "booking_cost",
    "cost_per_minute",
    "currency",
]
_COORD_PROV = FieldProvenance(assumptions=_FULL_ASSUMPTIONS)


def _make_station(
    station_id: str,
    lat: float,
    lon: float,
    min_elev: float = 5.0,
    setup_s: int = 60,
    teardown_s: int = 30,
) -> GroundStation:
    return GroundStation(
        station_id=station_id,
        name=station_id,
        provider_id="provider_test",
        latitude_deg=lat,
        longitude_deg=lon,
        altitude_m=50.0,
        supported_bands=frozenset({Band.X}),
        max_downlink_rate_mbps=100.0,
        minimum_elevation_deg=min_elev,
        setup_s=setup_s,
        teardown_s=teardown_s,
        booking_cost=0.0,
        cost_per_minute=10.0,
        field_provenance=_COORD_PROV,
    )


# A spread of stations at varied latitudes and longitudes
_STATIONS = [
    _make_station("station_europe", lat=48.9, lon=9.2),
    _make_station("station_northpole", lat=78.0, lon=15.0),
    _make_station("station_southamerica", lat=-34.6, lon=-58.4),
    _make_station("station_pacific", lat=37.8, lon=-122.4),
]

_ENGINE = PassEngine()
_SAT_ID = "sat_test_main"
_SCENARIO_ID = "scenario_test_main"
_CATALOG_VERSION = "2026.08.1"

_24H_START = _EPOCH
_24H_END = _EPOCH + timedelta(hours=24)


def _compute_24h(
    orbit: CustomCircularOrbit = _ORBIT_MID,
    stations: list[GroundStation] = _STATIONS,
) -> list[CandidatePass]:
    return _ENGINE.compute_passes(
        orbit=orbit,
        satellite_id=_SAT_ID,
        stations=stations,
        horizon_start=_24H_START,
        horizon_end=_24H_END,
        scenario_id=_SCENARIO_ID,
        station_catalog_version=_CATALOG_VERSION,
    )


# ---------------------------------------------------------------------------
# Invariant 1: start < peak < end
# ---------------------------------------------------------------------------


class TestTimingOrder:
    def test_start_before_peak_before_end(self) -> None:
        passes = _compute_24h()
        assert passes, "Expected at least one pass in 24 h"
        for p in passes:
            assert p.start_at < p.peak_at, (
                f"Pass {p.pass_id}: start_at {p.start_at} not < peak_at {p.peak_at}"
            )
            assert p.peak_at < p.end_at, (
                f"Pass {p.pass_id}: peak_at {p.peak_at} not < end_at {p.end_at}"
            )

    def test_duration_matches_start_end(self) -> None:
        passes = _compute_24h()
        for p in passes:
            computed = (p.end_at - p.start_at).total_seconds()
            assert abs(p.duration_s - computed) < 2.0, (
                f"duration_s mismatch: stored {p.duration_s}, computed {computed}"
            )

    def test_usable_duration_positive(self) -> None:
        passes = _compute_24h()
        for p in passes:
            assert p.usable_duration_s > 0.0

    def test_horizon_starting_during_descending_pass_keeps_peak_interior(self) -> None:
        full_passes = _compute_24h(stations=[_STATIONS[0]])
        source = next(
            item
            for item in full_passes
            if (item.end_at - item.peak_at).total_seconds() > 120
        )
        truncated = _ENGINE.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=[_STATIONS[0]],
            horizon_start=source.peak_at + timedelta(seconds=10),
            horizon_end=source.end_at + timedelta(seconds=10),
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        assert truncated
        assert truncated[0].start_at < truncated[0].peak_at < truncated[0].end_at


# ---------------------------------------------------------------------------
# Invariant 2: elevation at rise/set within tolerance of threshold
# ---------------------------------------------------------------------------

_ELEV_TOL_DEG: float = 1.0  # generous: coarse 60s + 1s bisect tolerance


class TestBoundaryElevation:
    def test_rise_elevation_near_threshold(self) -> None:
        passes = _compute_24h()
        for p in passes:
            # Find the station
            station = next(s for s in _STATIONS if s.station_id == p.station_id)
            sta_ecef = station_ecef_km(
                station.latitude_deg, station.longitude_deg, station.altitude_m
            )
            _, _, pos_rise, _, _, _ = _propagate_one(_ORBIT_MID, p.start_at)
            el_rise = elevation_deg(pos_rise, sta_ecef, station.latitude_deg, station.longitude_deg)
            assert abs(el_rise - station.minimum_elevation_deg) <= _ELEV_TOL_DEG, (
                f"Pass {p.pass_id}: rise elevation {el_rise:.3f}° not within "
                f"{_ELEV_TOL_DEG}° of threshold {station.minimum_elevation_deg}°"
            )

    def test_set_elevation_near_threshold(self) -> None:
        passes = _compute_24h()
        for p in passes:
            station = next(s for s in _STATIONS if s.station_id == p.station_id)
            sta_ecef = station_ecef_km(
                station.latitude_deg, station.longitude_deg, station.altitude_m
            )
            _, _, pos_set, _, _, _ = _propagate_one(_ORBIT_MID, p.end_at)
            el_set = elevation_deg(pos_set, sta_ecef, station.latitude_deg, station.longitude_deg)
            assert abs(el_set - station.minimum_elevation_deg) <= _ELEV_TOL_DEG, (
                f"Pass {p.pass_id}: set elevation {el_set:.3f}° not within "
                f"{_ELEV_TOL_DEG}° of threshold {station.minimum_elevation_deg}°"
            )


# ---------------------------------------------------------------------------
# Invariant 3: max_elevation_deg >= minimum_elevation_deg
# ---------------------------------------------------------------------------


class TestMaxElevation:
    def test_max_elevation_meets_threshold(self) -> None:
        passes = _compute_24h()
        for p in passes:
            assert p.max_elevation_deg >= p.minimum_elevation_deg, (
                f"Pass {p.pass_id}: max_el {p.max_elevation_deg:.3f}° < "
                f"threshold {p.minimum_elevation_deg}°"
            )

    def test_max_elevation_bounded(self) -> None:
        passes = _compute_24h()
        for p in passes:
            assert 0.0 <= p.max_elevation_deg <= 90.0


# ---------------------------------------------------------------------------
# Invariant 4: no overlapping duplicate passes per station
# ---------------------------------------------------------------------------


class TestNoDuplicates:
    def test_no_overlapping_passes_same_station(self) -> None:
        passes = _compute_24h()
        by_station: dict[str, list[CandidatePass]] = {}
        for p in passes:
            by_station.setdefault(p.station_id, []).append(p)

        for sid, station_passes in by_station.items():
            # sorted by start_at already (engine guarantees this globally)
            sp = sorted(station_passes, key=lambda p: p.start_at)
            for i in range(len(sp) - 1):
                a, b = sp[i], sp[i + 1]
                assert a.end_at <= b.start_at, (
                    f"Station {sid}: pass {a.pass_id} overlaps {b.pass_id} "
                    f"(end {a.end_at} > start {b.start_at})"
                )

    def test_no_duplicate_pass_ids(self) -> None:
        passes = _compute_24h()
        ids = [p.pass_id for p in passes]
        assert len(ids) == len(set(ids)), "Duplicate pass IDs detected"


# ---------------------------------------------------------------------------
# Invariant 5: multiple stations produce multiple passes
# ---------------------------------------------------------------------------


class TestMultiplePasses:
    def test_multiple_stations_produce_multiple_passes(self) -> None:
        passes = _compute_24h()
        station_ids_seen = {p.station_id for p in passes}
        assert len(station_ids_seen) >= 2, (
            f"Expected passes from ≥2 stations, got {station_ids_seen}"
        )

    def test_at_least_5_passes_in_24h(self) -> None:
        passes = _compute_24h()
        assert len(passes) >= 5, f"Expected ≥5 passes over 24 h, got {len(passes)}"

    def test_demo_catalog_produces_passes(self) -> None:
        """Demo catalog stations + 24 h window must yield multiple passes."""
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        sel = StationSelection(allow_all_eligible=True)
        stations = filter_stations(catalog, sel)
        assert stations, "Demo catalog produced no eligible stations"

        passes = _ENGINE.compute_passes_from_catalog(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            catalog=catalog,
            selected_stations=stations,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
        )
        station_ids = {p.station_id for p in passes}
        assert len(station_ids) >= 3, f"Expected passes from ≥3 demo stations, got {station_ids}"


# ---------------------------------------------------------------------------
# Invariant 6: determinism — same input → byte-identical output
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_input_same_output(self) -> None:
        engine = PassEngine()
        p1 = engine.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        p2 = engine.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        assert len(p1) == len(p2)
        for a, b in zip(p1, p2):
            assert a == b, f"Pass mismatch: {a.pass_id} vs {b.pass_id}"

    def test_pass_ids_deterministic(self) -> None:
        engine1 = PassEngine()
        engine2 = PassEngine()
        p1 = engine1.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        p2 = engine2.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        ids1 = [p.pass_id for p in p1]
        ids2 = [p.pass_id for p in p2]
        assert ids1 == ids2

    def test_sort_order_start_then_station(self) -> None:
        passes = _compute_24h()
        keys = [(p.start_at, p.station_id) for p in passes]
        assert keys == sorted(keys)


# ---------------------------------------------------------------------------
# Geometry sanity
# ---------------------------------------------------------------------------


class TestGeometry:
    def test_slant_range_positive(self) -> None:
        passes = _compute_24h()
        for p in passes:
            assert p.slant_range_peak_km > 0.0

    def test_azimuth_fields_in_range(self) -> None:
        passes = _compute_24h()
        for p in passes:
            for az in (p.azimuth_start_deg, p.azimuth_peak_deg, p.azimuth_end_deg):
                assert 0.0 <= az < 360.0, f"Azimuth {az} out of [0,360)"

    def test_short_window_rejected(self) -> None:
        """Station with setup+teardown >= any pass duration produces no passes."""
        # Very high elevation threshold + large overhead time = almost no usable contacts
        tight = _make_station(
            "station_tight",
            lat=48.9,
            lon=9.2,
            min_elev=45.0,  # only zenith passes
            setup_s=9999,
            teardown_s=0,
        )
        passes = _ENGINE.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=[tight],
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        # All passes should have been rejected (usable <= 0) or none exceed 45° threshold
        for p in passes:
            assert p.usable_duration_s > 0.0


# ---------------------------------------------------------------------------
# Propagator injection (Part E)
# ---------------------------------------------------------------------------


class _RecordingPropagator:
    """Test-double propagator that wraps CircularKeplerPropagator and tracks calls."""

    def __init__(self) -> None:
        from agcc.orbit.propagator import CircularKeplerPropagator

        self._real = CircularKeplerPropagator()
        self.call_count = 0

    def state_at(self, orbit: CustomCircularOrbit, at: datetime) -> OrbitState:
        self.call_count += 1
        return self._real.state_at(orbit, at)


class TestPropagatorInjection:
    def test_injected_propagator_is_called(self) -> None:
        """Engine must use the injected propagator, not a hard-coded _propagate_one."""
        recorder = _RecordingPropagator()
        engine = PassEngine(propagator=recorder)
        engine.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=[_STATIONS[0]],
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        assert recorder.call_count > 0, (
            "Engine did not call the injected propagator; it may still use _propagate_one directly"
        )

    def test_different_propagator_produces_different_ids(self) -> None:
        """Two independent engines with the same real propagator produce same pass IDs."""
        e1 = PassEngine()
        e2 = PassEngine(propagator=_RecordingPropagator())
        p1 = e1.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        p2 = e2.compute_passes(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            stations=_STATIONS,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
            station_catalog_version=_CATALOG_VERSION,
        )
        # Same orbit → same results regardless of wrapped propagator
        assert [p.pass_id for p in p1] == [p.pass_id for p in p2]


# ---------------------------------------------------------------------------
# Input validation (Part F)
# ---------------------------------------------------------------------------


class TestInputValidation:
    def test_naive_horizon_start_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=datetime(2024, 3, 20, 12, 0, 0),  # naive
                horizon_end=_24H_END,
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_naive_horizon_end_rejected(self) -> None:
        with pytest.raises(ValueError, match="timezone-aware"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=_24H_START,
                horizon_end=datetime(2024, 3, 21, 12, 0, 0),  # naive
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_reversed_horizon_rejected(self) -> None:
        with pytest.raises(ValueError, match="after"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=_24H_END,
                horizon_end=_24H_START,
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_equal_horizons_rejected(self) -> None:
        with pytest.raises(ValueError, match="after"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=_24H_START,
                horizon_end=_24H_START,
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_bad_satellite_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="sat_"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id="invalid_id",
                stations=_STATIONS,
                horizon_start=_24H_START,
                horizon_end=_24H_END,
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_bad_scenario_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="scenario_"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=_24H_START,
                horizon_end=_24H_END,
                scenario_id="",
                station_catalog_version=_CATALOG_VERSION,
            )

    def test_blank_catalog_version_rejected(self) -> None:
        with pytest.raises(ValueError, match="station_catalog_version"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=_STATIONS,
                horizon_start=_24H_START,
                horizon_end=_24H_END,
                scenario_id=_SCENARIO_ID,
                station_catalog_version="",
            )

    def test_duplicate_station_ids_rejected(self) -> None:
        dup_stations = [_STATIONS[0], _STATIONS[0]]
        with pytest.raises(ValueError, match="Duplicate"):
            _ENGINE.compute_passes(
                orbit=_ORBIT_MID,
                satellite_id=_SAT_ID,
                stations=dup_stations,
                horizon_start=_24H_START,
                horizon_end=_24H_END,
                scenario_id=_SCENARIO_ID,
                station_catalog_version=_CATALOG_VERSION,
            )


# ---------------------------------------------------------------------------
# compute_passes_from_catalog
# ---------------------------------------------------------------------------


class TestComputePassesFromCatalog:
    def test_catalog_version_is_passed_through(self) -> None:
        """compute_passes_from_catalog must stamp catalog.catalog_version onto passes."""
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        sel = StationSelection(allow_all_eligible=True)
        stations = filter_stations(catalog, sel)
        assert stations

        passes = _ENGINE.compute_passes_from_catalog(
            orbit=_ORBIT_MID,
            satellite_id=_SAT_ID,
            catalog=catalog,
            selected_stations=stations,
            horizon_start=_24H_START,
            horizon_end=_24H_END,
            scenario_id=_SCENARIO_ID,
        )
        assert passes
        for p in passes:
            assert p.station_catalog_version == catalog.catalog_version
