"""Weather provider protocol and implementations.

Providers normalise all external data into WeatherSnapshot objects.
Core algorithms must never consume raw third-party payloads.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from agcc.domain.common import Provenance
from agcc.domain.enums import SourceKind, SourceQuality, SourceType
from agcc.domain.environment import WeatherSnapshot, canonical_payload_hash, payload_hash
from agcc.domain.errors import DomainError, external_data_unavailable
from agcc.domain.stations import GroundStation

_WEATHER_LIVE_NOT_CONFIGURED = "WEATHER_LIVE_NOT_CONFIGURED"

# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class WeatherUnavailable(Exception):
    """Raised when weather data cannot be obtained."""

    def __init__(self, error: DomainError) -> None:
        super().__init__(error.message)
        self.error = error


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WeatherProvider(Protocol):
    """Abstract interface for weather data providers."""

    async def snapshots_for(
        self,
        station: GroundStation,
        start: datetime,
        end: datetime,
    ) -> list[WeatherSnapshot]:
        """Return all snapshots covering [start, end) for the given station."""
        ...


# ---------------------------------------------------------------------------
# Fixture provider — deterministic, in-process
# ---------------------------------------------------------------------------


class FixtureWeatherProvider:
    """Returns deterministic fixture snapshots loaded from a JSON file.

    The fixture file maps station_id → list of snapshot dicts.
    Snapshots whose valid_until <= start or valid_from >= end are filtered out.
    """

    def __init__(self, fixture_path: Path) -> None:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._data: dict[str, list[dict[str, Any]]] = raw.get("stations", {})
        self._path = fixture_path

    async def snapshots_for(
        self,
        station: GroundStation,
        start: datetime,
        end: datetime,
    ) -> list[WeatherSnapshot]:
        raw_list = self._data.get(station.station_id, [])
        result: list[WeatherSnapshot] = []
        for raw in raw_list:
            _verify_weather_hash(raw)
            snap = WeatherSnapshot.model_validate(raw)
            if snap.valid_until <= start or snap.valid_from >= end:
                continue
            result.append(snap)
        result.sort(key=lambda s: s.valid_from)
        return result


# ---------------------------------------------------------------------------
# Recorded provider — load from recorded JSON file
# ---------------------------------------------------------------------------


class RecordedWeatherProvider:
    """Loads normalised snapshots from a recorded-data JSON file.

    The file schema is identical to the fixture format.
    Payloads are hashed at load time to detect changes.
    """

    def __init__(self, recorded_path: Path) -> None:
        raw_text = recorded_path.read_text(encoding="utf-8")
        raw = json.loads(raw_text)
        self._data: dict[str, list[dict[str, Any]]] = raw.get("stations", {})
        self._file_hash = payload_hash(raw_text)

    async def snapshots_for(
        self,
        station: GroundStation,
        start: datetime,
        end: datetime,
    ) -> list[WeatherSnapshot]:
        raw_list = self._data.get(station.station_id, [])
        result: list[WeatherSnapshot] = []
        for raw in raw_list:
            _verify_weather_hash(raw)
            snap = WeatherSnapshot.model_validate(raw)
            if snap.valid_until <= start or snap.valid_from >= end:
                continue
            result.append(snap)
        result.sort(key=lambda s: s.valid_from)
        return result


# ---------------------------------------------------------------------------
# Hash verification helper
# ---------------------------------------------------------------------------


def _verify_weather_hash(record: dict[str, Any]) -> None:
    """Verify that record['raw_payload_hash'] matches canonical_payload_hash(record).

    Raises ValueError on mismatch.
    """
    stored = record.get("raw_payload_hash", "")
    expected = canonical_payload_hash(record)
    if stored != expected:
        snapshot_id = record.get("snapshot_id", "<unknown>")
        raise ValueError(
            f"raw_payload_hash mismatch for snapshot '{snapshot_id}': "
            f"stored='{stored}', expected='{expected}'"
        )


# ---------------------------------------------------------------------------
# Live placeholder — never configured, always raises
# ---------------------------------------------------------------------------


class NotConfiguredLiveWeatherProvider:
    """Placeholder raised when no live weather provider has been wired up.

    Required configuration: ``AGCC_WEATHER_API_URL``.

    Never contains a URL guess or credential.
    """

    # Names of the required configuration keys — exposed for documentation
    REQUIRED_CONFIG_NAMES: tuple[str, ...] = ("AGCC_WEATHER_API_URL",)

    async def snapshots_for(
        self,
        station: GroundStation,
        start: datetime,
        end: datetime,
    ) -> list[WeatherSnapshot]:
        raise WeatherUnavailable(external_data_unavailable(_WEATHER_LIVE_NOT_CONFIGURED))


class OpenMeteoWeatherProvider:
    """Normalize Open-Meteo hourly liquid-rain forecasts.

    Open-Meteo's hourly ``rain`` and ``showers`` values are accumulations over
    the preceding hour. Their sum is therefore normalized as the mean liquid
    rain rate in mm/h for that one-hour validity interval. It is forecast model
    output, not a station observation or instantaneous rain-rate measurement.
    """

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        max_forecast_days: int = 16,
        cache_ttl_s: float = 300.0,
        max_retries: int = 2,
        clock: Any = time.monotonic,
    ) -> None:
        if not base_url.strip():
            raise ValueError("Open-Meteo forecast URL is required")
        if not 1 <= max_forecast_days <= 16:
            raise ValueError("max_forecast_days must be between 1 and 16")
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._max_forecast_days = max_forecast_days
        self._cache_ttl_s = cache_ttl_s
        self._max_retries = max_retries
        self._clock = clock
        self._cache: dict[str, tuple[float, dict[str, Any], datetime]] = {}

    async def snapshots_for(
        self,
        station: GroundStation,
        start: datetime,
        end: datetime,
    ) -> list[WeatherSnapshot]:
        if start >= end:
            raise ValueError("weather request start must be before end")
        fetched_at = datetime.now(UTC)
        requested_days = max(1, (end.date() - fetched_at.date()).days + 1)
        days = min(self._max_forecast_days, requested_days)
        params = {
            "latitude": str(station.latitude_deg),
            "longitude": str(station.longitude_deg),
            "elevation": str(station.altitude_m),
            "hourly": (
                "temperature_2m,relative_humidity_2m,rain,showers,"
                "cloud_cover,wind_speed_10m"
            ),
            "forecast_days": str(days),
            "timezone": "GMT",
            "timeformat": "unixtime",
            "precipitation_unit": "mm",
            "wind_speed_unit": "ms",
            "cell_selection": "land",
        }
        cache_key = "|".join((station.station_id, str(days)))
        cached = self._cache.get(cache_key)
        if cached and self._clock() < cached[0]:
            payload, fetched_at = cached[1], cached[2]
        else:
            owns_client = self._client is None
            client = self._client or httpx.AsyncClient(timeout=15.0)
            try:
                response = await self._get_with_retry(client, params)
                payload = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise WeatherUnavailable(
                    external_data_unavailable("OPEN_METEO_REQUEST_FAILED")
                ) from exc
            finally:
                if owns_client:
                    await client.aclose()
            self._cache[cache_key] = (
                self._clock() + self._cache_ttl_s,
                payload,
                fetched_at,
            )
        try:
            return self._normalize(payload, station, start, end, fetched_at)
        except (KeyError, TypeError, ValueError) as exc:
            raise WeatherUnavailable(
                external_data_unavailable("OPEN_METEO_RESPONSE_INVALID")
            ) from exc

    def _normalize(
        self,
        payload: dict[str, Any],
        station: GroundStation,
        start: datetime,
        end: datetime,
        fetched_at: datetime,
    ) -> list[WeatherSnapshot]:
        hourly = payload["hourly"]
        arrays = {
            key: hourly[key]
            for key in (
                "time", "temperature_2m", "relative_humidity_2m", "rain",
                "showers", "cloud_cover", "wind_speed_10m",
            )
        }
        lengths = {len(values) for values in arrays.values()}
        if len(lengths) != 1:
            raise ValueError("Open-Meteo hourly arrays have inconsistent lengths")
        result: list[WeatherSnapshot] = []
        for index, epoch in enumerate(arrays["time"]):
            valid_from = datetime.fromtimestamp(float(epoch), tz=UTC)
            valid_until = valid_from + timedelta(hours=1)
            if valid_until <= start or valid_from >= end:
                continue
            record = {key: values[index] for key, values in arrays.items()}
            raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
            suffix = hashlib.sha256(
                f"{station.station_id}|{int(valid_from.timestamp())}".encode()
            ).hexdigest()[:16]
            result.append(
                WeatherSnapshot(
                    snapshot_id=f"event_openmeteo_{suffix}",
                    station_id=station.station_id,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    observed_at=fetched_at,
                    fetched_at=fetched_at,
                    precipitation_mm_per_hr=(
                        float(record["rain"]) + float(record["showers"])
                    ),
                    temperature_c=float(record["temperature_2m"]),
                    relative_humidity_pct=float(record["relative_humidity_2m"]),
                    cloud_cover_pct=float(record["cloud_cover"]),
                    wind_speed_mps=float(record["wind_speed_10m"]),
                    source_kind=SourceKind.LIVE,
                    source_quality=SourceQuality.VERIFIED,
                    raw_payload_hash=payload_hash(raw),
                    provenance=Provenance(
                        source_type=SourceType.FORECAST,
                        source_name="Open-Meteo best_match hourly forecast",
                        source_uri=self._base_url,
                        observed_at=fetched_at,
                        fetched_at=fetched_at,
                        assumption_fields=[
                            "precipitation_mm_per_hr: hourly liquid-rain mean"
                        ],
                    ),
                )
            )
        result.sort(key=lambda item: item.valid_from)
        return result

    async def _get_with_retry(
        self, client: httpx.AsyncClient, params: dict[str, str]
    ) -> httpx.Response:
        for attempt in range(self._max_retries + 1):
            response = await client.get(self._base_url, params=params)
            if response.status_code != 429 and response.status_code < 500:
                response.raise_for_status()
                return response
            if attempt == self._max_retries:
                response.raise_for_status()
            retry_after = response.headers.get("Retry-After")
            delay = (
                min(2.0, max(0.0, float(retry_after)))
                if retry_after
                else 0.25 * (2**attempt)
            )
            await asyncio.sleep(delay)
        raise RuntimeError("unreachable Open-Meteo retry state")


def weather_provider_from_environment() -> WeatherProvider:
    """Create Open-Meteo only when its forecast endpoint is configured."""

    base_url = os.getenv("AGCC_WEATHER_API_URL")
    if not base_url:
        return NotConfiguredLiveWeatherProvider()
    return OpenMeteoWeatherProvider(base_url)
