"""Space-weather provider protocol and implementations.

SpaceWeatherSnapshot is stored for display and anomaly context only.
This module must not assign capacity multipliers.
"""

from __future__ import annotations

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
from agcc.domain.environment import (
    SpaceWeatherSnapshot,
    canonical_payload_hash,
    payload_hash,
)
from agcc.domain.errors import DomainError, external_data_unavailable

_SPACE_WEATHER_LIVE_NOT_CONFIGURED = "SPACE_WEATHER_LIVE_NOT_CONFIGURED"


class SpaceWeatherUnavailable(Exception):
    """Raised when space-weather data cannot be obtained."""

    def __init__(self, error: DomainError) -> None:
        super().__init__(error.message)
        self.error = error


class SpaceWeatherProvider(Protocol):
    """Abstract interface for space-weather data providers."""

    async def snapshots_for(
        self,
        start: datetime,
        end: datetime,
    ) -> list[SpaceWeatherSnapshot]:
        """Return all snapshots covering [start, end)."""
        ...


def _verify_space_weather_hash(record: dict[str, Any]) -> None:
    """Verify that record['raw_payload_hash'] matches canonical_payload_hash(record)."""
    stored = record.get("raw_payload_hash", "")
    expected = canonical_payload_hash(record)
    if stored != expected:
        snapshot_id = record.get("snapshot_id", "<unknown>")
        raise ValueError(
            f"raw_payload_hash mismatch for space-weather snapshot '{snapshot_id}': "
            f"stored='{stored}', expected='{expected}'"
        )


class FixtureSpaceWeatherProvider:
    """Returns deterministic fixture snapshots from a JSON file."""

    def __init__(self, fixture_path: Path) -> None:
        raw = json.loads(fixture_path.read_text(encoding="utf-8"))
        self._data: list[dict[str, Any]] = raw.get("snapshots", [])

    async def snapshots_for(
        self,
        start: datetime,
        end: datetime,
    ) -> list[SpaceWeatherSnapshot]:
        result: list[SpaceWeatherSnapshot] = []
        for raw in self._data:
            _verify_space_weather_hash(raw)
            snap = SpaceWeatherSnapshot.model_validate(raw)
            if snap.valid_until <= start or snap.valid_from >= end:
                continue
            result.append(snap)
        result.sort(key=lambda s: s.valid_from)
        return result


class RecordedSpaceWeatherProvider:
    """Loads normalised snapshots from a recorded-data JSON file."""

    def __init__(self, recorded_path: Path) -> None:
        raw = json.loads(recorded_path.read_text(encoding="utf-8"))
        self._data: list[dict[str, Any]] = raw.get("snapshots", [])

    async def snapshots_for(
        self,
        start: datetime,
        end: datetime,
    ) -> list[SpaceWeatherSnapshot]:
        result: list[SpaceWeatherSnapshot] = []
        for raw in self._data:
            _verify_space_weather_hash(raw)
            snap = SpaceWeatherSnapshot.model_validate(raw)
            if snap.valid_until <= start or snap.valid_from >= end:
                continue
            result.append(snap)
        result.sort(key=lambda s: s.valid_from)
        return result


class NotConfiguredLiveSpaceWeatherProvider:
    """Placeholder that always raises SpaceWeatherUnavailable.

    Required configuration (not supplied here):
      - AGCC_SPACE_WEATHER_API_URL: endpoint for live space-weather data
      NOAA's public endpoint does not require an API key.
    """

    REQUIRED_CONFIG_NAMES: tuple[str, ...] = ("AGCC_SPACE_WEATHER_API_URL",)

    async def snapshots_for(
        self,
        start: datetime,
        end: datetime,
    ) -> list[SpaceWeatherSnapshot]:
        raise SpaceWeatherUnavailable(external_data_unavailable(_SPACE_WEATHER_LIVE_NOT_CONFIGURED))


class NoaaSwpcSpaceWeatherProvider:
    """Normalize NOAA SWPC's rolling 1-minute planetary K-index feed."""

    def __init__(
        self,
        url: str,
        *,
        client: httpx.AsyncClient | None = None,
        cache_ttl_s: float = 60.0,
        stale_after_s: float = 900.0,
        clock: Any = time.monotonic,
    ) -> None:
        if not url.strip():
            raise ValueError("NOAA SWPC URL is required")
        self._url = url
        self._client = client
        self._cache_ttl_s = cache_ttl_s
        self._stale_after_s = stale_after_s
        self._clock = clock
        self._cached_at: float | None = None
        self._cached: list[SpaceWeatherSnapshot] = []

    async def snapshots_for(
        self, start: datetime, end: datetime
    ) -> list[SpaceWeatherSnapshot]:
        if start >= end:
            raise ValueError("space-weather request start must be before end")
        now_mono = float(self._clock())
        if self._cached_at is None or now_mono - self._cached_at >= self._cache_ttl_s:
            self._cached = await self._fetch()
            self._cached_at = now_mono
        return [
            item for item in self._cached
            if item.valid_until > start and item.valid_from < end
        ]

    async def _fetch(self) -> list[SpaceWeatherSnapshot]:
        fetched_at = datetime.now(UTC)
        owns_client = self._client is None
        client = self._client or httpx.AsyncClient(timeout=15.0)
        try:
            response = await client.get(self._url)
            response.raise_for_status()
            raw_payload = response.json()
            if not isinstance(raw_payload, list):
                raise ValueError("NOAA payload must be a list")
            result = [self._normalize(item, fetched_at) for item in raw_payload]
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            raise SpaceWeatherUnavailable(
                external_data_unavailable("NOAA_SWPC_REQUEST_FAILED")
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
        result.sort(key=lambda item: item.valid_from)
        return result

    def _normalize(
        self, record: dict[str, Any], fetched_at: datetime
    ) -> SpaceWeatherSnapshot:
        observed_at = datetime.fromisoformat(str(record["time_tag"])).replace(tzinfo=UTC)
        value = record.get("estimated_kp", record.get("kp_index"))
        if value is None:
            raise ValueError("NOAA record has no K-index value")
        quality = (
            SourceQuality.STALE
            if (fetched_at - observed_at).total_seconds() > self._stale_after_s
            else SourceQuality.VERIFIED
        )
        raw = json.dumps(record, sort_keys=True, separators=(",", ":"))
        suffix = hashlib.sha256(raw.encode()).hexdigest()[:16]
        return SpaceWeatherSnapshot(
            snapshot_id=f"event_noaa_kp_{suffix}",
            valid_from=observed_at,
            valid_until=observed_at + timedelta(minutes=1),
            observed_at=observed_at,
            fetched_at=fetched_at,
            kp_index=float(value),
            dst_index_nt=None,
            f107_sfu=None,
            source_kind=SourceKind.LIVE,
            source_quality=quality,
            raw_payload_hash=payload_hash(raw),
            provenance=Provenance(
                source_type=SourceType.DERIVED,
                source_name="NOAA Space Weather Prediction Center",
                source_uri=self._url,
                observed_at=observed_at,
                fetched_at=fetched_at,
            ),
        )


def space_weather_provider_from_environment() -> SpaceWeatherProvider:
    url = os.getenv("AGCC_SPACE_WEATHER_API_URL")
    if not url:
        return NotConfiguredLiveSpaceWeatherProvider()
    return NoaaSwpcSpaceWeatherProvider(url)
