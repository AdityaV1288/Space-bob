"""Open-Meteo normalization, caching, and failure-boundary tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agcc.api.service import AgccApplicationService
from agcc.domain.enums import Band, SourceKind, SourceQuality, SourceType
from agcc.domain.errors import external_data_unavailable
from agcc.domain.stations import FieldProvenance, GroundStation
from agcc.environment.provider import (
    NotConfiguredLiveWeatherProvider,
    OpenMeteoWeatherProvider,
    WeatherUnavailable,
    weather_provider_from_environment,
)


def station() -> GroundStation:
    assumptions = [
        "latitude_deg", "longitude_deg", "altitude_m", "supported_bands",
        "max_downlink_rate_mbps", "minimum_elevation_deg", "setup_s",
        "teardown_s", "cost_model", "booking_cost", "cost_per_minute", "currency",
    ]
    return GroundStation(
        station_id="station_openmeteo_test",
        name="Open-Meteo test",
        provider_id="provider_test",
        latitude_deg=12.9716,
        longitude_deg=77.5946,
        altitude_m=920.0,
        supported_bands=frozenset({Band.X}),
        max_downlink_rate_mbps=50.0,
        minimum_elevation_deg=5.0,
        setup_s=60,
        teardown_s=30,
        booking_cost=0.0,
        cost_per_minute=1.0,
        field_provenance=FieldProvenance(assumptions=assumptions),
    )


def test_normalizes_hourly_liquid_rain_and_uses_station_geometry() -> None:
    hour = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/v1/forecast"
        assert request.url.params["latitude"] == "12.9716"
        assert request.url.params["longitude"] == "77.5946"
        assert request.url.params["elevation"] == "920.0"
        assert request.url.params["timezone"] == "GMT"
        assert request.url.params["timeformat"] == "unixtime"
        assert request.url.params["wind_speed_unit"] == "ms"
        assert "rain" in request.url.params["hourly"]
        assert "showers" in request.url.params["hourly"]
        return httpx.Response(200, json={
            "hourly": {
                "time": [int(hour.timestamp())],
                "temperature_2m": [24.5],
                "relative_humidity_2m": [81],
                "rain": [2.0],
                "showers": [1.2],
                "cloud_cover": [72],
                "wind_speed_10m": [5.0],
            }
        })

    async def run() -> list[object]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenMeteoWeatherProvider(
                "https://api.open-meteo.com/v1/forecast", client=client
            )
            first = await provider.snapshots_for(station(), hour, hour + timedelta(hours=1))
            second = await provider.snapshots_for(station(), hour, hour + timedelta(hours=1))
            assert second == first
            return first

    snapshots = asyncio.run(run())
    assert calls == 1
    assert len(snapshots) == 1
    snapshot = snapshots[0]
    assert snapshot.precipitation_mm_per_hr == 3.2  # type: ignore[attr-defined]
    assert snapshot.wind_speed_mps == 5.0  # type: ignore[attr-defined]
    assert snapshot.source_kind == SourceKind.LIVE  # type: ignore[attr-defined]
    assert snapshot.source_quality == SourceQuality.VERIFIED  # type: ignore[attr-defined]
    assert snapshot.provenance.source_type == SourceType.FORECAST  # type: ignore[attr-defined]
    assert "hourly liquid-rain mean" in snapshot.provenance.assumption_fields[0]  # type: ignore[attr-defined]


def test_rejects_inconsistent_arrays() -> None:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    payload = {
        "hourly": {
            "time": [int(now.timestamp())], "temperature_2m": [],
            "relative_humidity_2m": [50], "rain": [0], "showers": [0],
            "cloud_cover": [0], "wind_speed_10m": [0],
        }
    }

    async def run() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenMeteoWeatherProvider(
                "https://api.open-meteo.com/v1/forecast", client=client
            )
            with pytest.raises(WeatherUnavailable):
                await provider.snapshots_for(station(), now, now + timedelta(hours=1))

    asyncio.run(run())


def test_provider_errors_are_normalized() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(429, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = OpenMeteoWeatherProvider(
                "https://api.open-meteo.com/v1/forecast", client=client
            )
            now = datetime.now(UTC)
            with pytest.raises(WeatherUnavailable):
                await provider.snapshots_for(station(), now, now + timedelta(hours=1))

    asyncio.run(run())


def test_environment_factory_requires_url_but_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGCC_WEATHER_API_URL", raising=False)
    monkeypatch.delenv("AGCC_WEATHER_API_KEY", raising=False)
    assert isinstance(weather_provider_from_environment(), NotConfiguredLiveWeatherProvider)
    monkeypatch.setenv(
        "AGCC_WEATHER_API_URL", "https://api.open-meteo.com/v1/forecast"
    )
    assert isinstance(weather_provider_from_environment(), OpenMeteoWeatherProvider)


def test_retries_temporary_rate_limit_then_succeeds() -> None:
    now = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"})
        return httpx.Response(200, json={
            "hourly": {
                "time": [int(now.timestamp())], "temperature_2m": [20],
                "relative_humidity_2m": [50], "rain": [0], "showers": [0],
                "cloud_cover": [10], "wind_speed_10m": [2],
            }
        })

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = OpenMeteoWeatherProvider(
                "https://api.open-meteo.com/v1/forecast", client=client
            )
            assert await provider.snapshots_for(
                station(), now, now + timedelta(hours=1)
            )

    asyncio.run(run())
    assert calls == 2


def test_station_failure_is_isolated_when_another_station_succeeds() -> None:
    good = station()
    bad = station().model_copy(update={"station_id": "station_openmeteo_bad"})

    class PartialProvider:
        async def snapshots_for(
            self, selected: GroundStation, start: datetime, end: datetime
        ) -> list[object]:
            del start, end
            if selected.station_id == bad.station_id:
                raise WeatherUnavailable(external_data_unavailable("TEST_FAILURE"))
            return []

    service = AgccApplicationService(fixture_mode=True, weather_provider=PartialProvider())
    now = datetime.now(UTC)
    result = service._fetch_weather(  # noqa: SLF001
        {good.station_id: good, bad.station_id: bad}, now, now + timedelta(hours=1)
    )
    assert result == {good.station_id: []}
