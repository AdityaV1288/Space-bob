"""Tests for weather providers, alignment, staleness, and space weather."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agcc.domain.common import Provenance
from agcc.domain.enums import Band, SourceKind, SourceQuality, SourceType
from agcc.domain.environment import WeatherSnapshot, payload_hash
from agcc.domain.planning import CandidatePass
from agcc.domain.stations import FieldProvenance, GroundStation
from agcc.environment.alignment import align_to_pass
from agcc.environment.provider import (
    FixtureWeatherProvider,
    NotConfiguredLiveWeatherProvider,
    RecordedWeatherProvider,
    WeatherUnavailable,
)
from agcc.environment.space_weather import (
    FixtureSpaceWeatherProvider,
    NotConfiguredLiveSpaceWeatherProvider,
    SpaceWeatherUnavailable,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BASE = datetime(2024, 3, 20, 12, 0, 0, tzinfo=timezone.utc)
_PROV = Provenance(
    source_type=SourceType.DERIVED,
    source_name="test",
    fetched_at=_BASE,
)
_COORD_PROV = FieldProvenance(
    assumptions=[
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
)
_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures" / "weather"
)
_SPACE_WEATHER_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures" / "space_weather"
)


def _make_station(station_id: str = "station_demo_centraleurope") -> GroundStation:
    return GroundStation(
        station_id=station_id,
        name=station_id,
        provider_id="provider_test",
        latitude_deg=48.9,
        longitude_deg=9.2,
        altitude_m=380.0,
        supported_bands=frozenset({Band.X}),
        max_downlink_rate_mbps=100.0,
        minimum_elevation_deg=5.0,
        setup_s=60,
        teardown_s=30,
        booking_cost=0.0,
        cost_per_minute=10.0,
        field_provenance=_COORD_PROV,
    )


def _canonical_hash(payload: dict) -> str:
    import hashlib
    import json

    copy = dict(payload)
    copy.pop("raw_payload_hash", None)
    serialized = json.dumps(copy, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _make_snap(
    station_id: str = "station_x",
    valid_from: datetime | None = None,
    valid_until: datetime | None = None,
    observed_at: datetime | None = None,
    quality: SourceQuality = SourceQuality.VERIFIED,
    idx: int = 0,
) -> WeatherSnapshot:
    vf = valid_from or _BASE
    vu = valid_until or (_BASE + timedelta(hours=1))
    oa = observed_at or vf

    def _fmt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    payload: dict = dict(
        snapshot_id=f"event_wx_test_{idx:03d}",
        station_id=station_id,
        valid_from=_fmt(vf),
        valid_until=_fmt(vu),
        observed_at=_fmt(oa),
        fetched_at=_fmt(_BASE),
        precipitation_mm_per_hr=0.0,
        temperature_c=15.0,
        relative_humidity_pct=50.0,
        cloud_cover_pct=0.0,
        wind_speed_mps=2.0,
        source_kind="fixture",
        source_quality=quality.value,
        provenance=dict(
            source_type="derived",
            source_name="test",
            fetched_at=_fmt(_BASE),
        ),
    )
    return WeatherSnapshot(
        snapshot_id=f"event_wx_test_{idx:03d}",
        station_id=station_id,
        valid_from=vf,
        valid_until=vu,
        observed_at=oa,
        fetched_at=_BASE,
        precipitation_mm_per_hr=0.0,
        temperature_c=15.0,
        relative_humidity_pct=50.0,
        cloud_cover_pct=0.0,
        wind_speed_mps=2.0,
        source_kind=SourceKind.FIXTURE,
        source_quality=quality,
        raw_payload_hash=_canonical_hash(payload),
        provenance=_PROV,
    )


def _make_pass(
    start: datetime | None = None,
    duration_s: float = 600.0,
) -> CandidatePass:
    s = start or (_BASE + timedelta(hours=1))
    e = s + timedelta(seconds=duration_s)
    return CandidatePass(
        pass_id="pass_test0001",
        scenario_id="scenario_envtest01",
        satellite_id="sat_x",
        station_id="station_x",
        start_at=s,
        peak_at=s + timedelta(seconds=duration_s / 2),
        end_at=e,
        duration_s=duration_s,
        usable_duration_s=duration_s - 90,
        max_elevation_deg=30.0,
        azimuth_start_deg=0.0,
        azimuth_peak_deg=90.0,
        azimuth_end_deg=180.0,
        slant_range_peak_km=900.0,
        minimum_elevation_deg=5.0,
        orbit_model_version="circular_kepler_v1",
        station_catalog_version="2026.08.1",
    )


# ---------------------------------------------------------------------------
# WeatherSnapshot domain model
# ---------------------------------------------------------------------------


class TestWeatherSnapshotModel:
    def test_valid_snapshot(self) -> None:
        s = _make_snap()
        assert s.station_id == "station_x"

    def test_valid_from_before_valid_until(self) -> None:
        with pytest.raises(ValidationError):
            _make_snap(valid_from=_BASE + timedelta(hours=2), valid_until=_BASE)

    def test_equal_valid_from_until_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_snap(valid_from=_BASE, valid_until=_BASE)

    def test_covers_midpoint(self) -> None:
        s = _make_snap(valid_from=_BASE, valid_until=_BASE + timedelta(hours=2))
        assert s.covers(_BASE + timedelta(hours=1))

    def test_does_not_cover_before(self) -> None:
        s = _make_snap(valid_from=_BASE, valid_until=_BASE + timedelta(hours=1))
        assert not s.covers(_BASE - timedelta(seconds=1))

    def test_does_not_cover_at_valid_until(self) -> None:
        s = _make_snap(valid_from=_BASE, valid_until=_BASE + timedelta(hours=1))
        assert not s.covers(_BASE + timedelta(hours=1))

    def test_raw_payload_hash_deterministic(self) -> None:
        h1 = payload_hash("test payload")
        h2 = payload_hash("test payload")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_raw_payload_hash_bytes(self) -> None:
        h = payload_hash(b"bytes payload")
        assert len(h) == 64


# ---------------------------------------------------------------------------
# Fixture provider
# ---------------------------------------------------------------------------


class TestFixtureWeatherProvider:
    def test_loads_clear_fixture(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=24)))
        assert len(snaps) == 24

    def test_fixture_snapshots_sorted_by_valid_from(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=24)))
        times = [s.valid_from for s in snaps]
        assert times == sorted(times)

    def test_clear_fixture_has_zero_precip(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        assert all(s.precipitation_mm_per_hr == 0.0 for s in snaps)

    def test_heavy_rain_fixture_has_high_precip(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_heavy_rain.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        assert all(s.precipitation_mm_per_hr > 10.0 for s in snaps)

    def test_missing_data_fixture_only_six_hours(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_missing_data.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=24)))
        assert len(snaps) == 6

    def test_no_snapshots_for_unknown_station(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_unknown_xyz")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=24)))
        assert snaps == []

    def test_snapshots_outside_window_filtered(self) -> None:
        provider = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        # Request only first 2 hours
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=2)))
        assert all(s.valid_from < _BASE + timedelta(hours=2) for s in snaps)
        assert len(snaps) == 2

    def test_fixture_hashes_deterministic(self) -> None:
        provider1 = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        provider2 = FixtureWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        s1 = asyncio.run(provider1.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        s2 = asyncio.run(provider2.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        assert s1 == s2


# ---------------------------------------------------------------------------
# Recorded provider
# ---------------------------------------------------------------------------


class TestRecordedWeatherProvider:
    def test_loads_same_format_as_fixture(self) -> None:
        # Re-use clear fixture as "recorded" data — same schema
        provider = RecordedWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        station = _make_station("station_demo_centraleurope")
        snaps = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=24)))
        assert len(snaps) == 24

    def test_recorded_payload_normalization(self) -> None:
        """Re-loading the same file must produce identical snapshots."""
        provider = RecordedWeatherProvider(_FIXTURE_DIR / "weather_moderate_rain.json")
        station = _make_station("station_demo_centraleurope")
        s1 = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        s2 = asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        assert s1 == s2

    def test_file_hash_stable(self) -> None:
        p1 = RecordedWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        p2 = RecordedWeatherProvider(_FIXTURE_DIR / "weather_clear.json")
        assert p1._file_hash == p2._file_hash


# ---------------------------------------------------------------------------
# Live placeholder
# ---------------------------------------------------------------------------


class TestNotConfiguredLiveWeatherProvider:
    def test_raises_weather_unavailable(self) -> None:
        provider = NotConfiguredLiveWeatherProvider()
        station = _make_station()
        with pytest.raises(WeatherUnavailable):
            asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))

    def test_error_code(self) -> None:
        provider = NotConfiguredLiveWeatherProvider()
        station = _make_station()
        try:
            asyncio.run(provider.snapshots_for(station, _BASE, _BASE + timedelta(hours=1)))
        except WeatherUnavailable as exc:
            assert exc.error.code == "EXTERNAL_DATA_UNAVAILABLE"
            assert "WEATHER_LIVE_NOT_CONFIGURED" in exc.error.details.get("source", "")
        else:
            pytest.fail("WeatherUnavailable not raised")

    def test_exposes_required_config_names(self) -> None:
        provider = NotConfiguredLiveWeatherProvider()
        assert "AGCC_WEATHER_API_URL" in provider.REQUIRED_CONFIG_NAMES
        assert "AGCC_WEATHER_API_KEY" not in provider.REQUIRED_CONFIG_NAMES


# ---------------------------------------------------------------------------
# Alignment — time alignment
# ---------------------------------------------------------------------------


class TestAlignment:
    def test_exact_coverage_returns_verified(self) -> None:
        pass_ = _make_pass(start=_BASE + timedelta(hours=1))
        snap = _make_snap(
            valid_from=_BASE,
            valid_until=_BASE + timedelta(hours=3),
            quality=SourceQuality.VERIFIED,
        )
        result = align_to_pass(pass_, [snap])
        assert result.available
        assert result.quality == SourceQuality.VERIFIED
        assert result.snapshot is snap

    def test_exact_coverage_midpoint_boundary(self) -> None:
        """Pass midpoint exactly at valid_from — should be covered."""
        midpoint = _BASE + timedelta(hours=2)
        pass_ = _make_pass(start=midpoint - timedelta(seconds=300))  # midpoint at BASE+2h
        snap = _make_snap(
            valid_from=midpoint,
            valid_until=midpoint + timedelta(hours=1),
        )
        result = align_to_pass(pass_, [snap])
        assert result.available

    def test_no_snapshots_returns_unavailable(self) -> None:
        pass_ = _make_pass()
        result = align_to_pass(pass_, [])
        assert not result.available
        assert result.quality == SourceQuality.UNAVAILABLE

    def test_stale_within_max_staleness(self) -> None:
        """No snapshot covers midpoint, but closest is within max_staleness_s."""
        pass_ = _make_pass(start=_BASE + timedelta(hours=5))
        # Snapshot covers hours 0-1, observed at hour 0 → age ~4.5 hours at midpoint
        snap = _make_snap(
            valid_from=_BASE,
            valid_until=_BASE + timedelta(hours=1),
            observed_at=_BASE,
        )
        result = align_to_pass(pass_, [snap], max_staleness_s=86400.0)
        assert result.available
        assert result.quality == SourceQuality.STALE

    def test_stale_beyond_max_staleness_returns_unavailable(self) -> None:
        pass_ = _make_pass(start=_BASE + timedelta(hours=5))
        snap = _make_snap(
            valid_from=_BASE,
            valid_until=_BASE + timedelta(hours=1),
            observed_at=_BASE,
        )
        # max_staleness_s = 1 second → observed_at is ~4.5 hours away → UNAVAILABLE
        result = align_to_pass(pass_, [snap], max_staleness_s=1.0)
        assert not result.available
        assert result.quality == SourceQuality.UNAVAILABLE

    def test_multiple_snapshots_picks_covering_one(self) -> None:
        pass_ = _make_pass(start=_BASE + timedelta(hours=3))
        # midpoint at BASE+3h5min
        snaps = [
            _make_snap(valid_from=_BASE, valid_until=_BASE + timedelta(hours=1), idx=0),
            _make_snap(
                valid_from=_BASE + timedelta(hours=2), valid_until=_BASE + timedelta(hours=4), idx=1
            ),
            _make_snap(
                valid_from=_BASE + timedelta(hours=4), valid_until=_BASE + timedelta(hours=5), idx=2
            ),
        ]
        result = align_to_pass(pass_, snaps)
        assert result.available
        assert result.snapshot is snaps[1]

    def test_does_not_silently_use_neutral_weather(self) -> None:
        """Missing data returns UNAVAILABLE, not a zero-valued snapshot."""
        pass_ = _make_pass(start=_BASE + timedelta(hours=20))
        # Only first 6 hours covered
        snaps = [
            _make_snap(
                valid_from=_BASE + timedelta(hours=i),
                valid_until=_BASE + timedelta(hours=i + 1),
                idx=i,
            )
            for i in range(6)
        ]
        result = align_to_pass(pass_, snaps, max_staleness_s=1.0)
        assert not result.available
        assert result.snapshot is None


# ---------------------------------------------------------------------------
# Space weather
# ---------------------------------------------------------------------------


class TestSpaceWeatherProvider:
    def test_fixture_loads(self) -> None:
        provider = FixtureSpaceWeatherProvider(_SPACE_WEATHER_DIR / "space_weather_fixture.json")
        snaps = asyncio.run(provider.snapshots_for(_BASE, _BASE + timedelta(hours=24)))
        assert len(snaps) == 1
        assert snaps[0].kp_index == 1.0

    def test_not_configured_raises(self) -> None:
        provider = NotConfiguredLiveSpaceWeatherProvider()
        with pytest.raises(SpaceWeatherUnavailable):
            asyncio.run(provider.snapshots_for(_BASE, _BASE + timedelta(hours=1)))

    def test_not_configured_error_code(self) -> None:
        provider = NotConfiguredLiveSpaceWeatherProvider()
        try:
            asyncio.run(provider.snapshots_for(_BASE, _BASE + timedelta(hours=1)))
        except SpaceWeatherUnavailable as exc:
            assert exc.error.code == "EXTERNAL_DATA_UNAVAILABLE"
            assert "SPACE_WEATHER_LIVE_NOT_CONFIGURED" in exc.error.details.get("source", "")
        else:
            pytest.fail("SpaceWeatherUnavailable not raised")

    def test_no_capacity_multiplier_on_snapshot(self) -> None:
        """SpaceWeatherSnapshot must not have a capacity_multiplier field."""
        provider = FixtureSpaceWeatherProvider(_SPACE_WEATHER_DIR / "space_weather_fixture.json")
        snaps = asyncio.run(provider.snapshots_for(_BASE, _BASE + timedelta(hours=24)))
        assert len(snaps) > 0
        assert not hasattr(snaps[0], "capacity_multiplier")
