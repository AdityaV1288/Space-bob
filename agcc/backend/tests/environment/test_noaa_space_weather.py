"""NOAA SWPC 1-minute planetary K-index normalization tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from agcc.domain.enums import SourceKind, SourceQuality, SourceType
from agcc.environment.space_weather import (
    NoaaSwpcSpaceWeatherProvider,
    SpaceWeatherUnavailable,
)


def test_normalizes_estimated_kp_and_caches_feed() -> None:
    observed = datetime.now(UTC).replace(second=0, microsecond=0)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json=[{
            "time_tag": observed.replace(tzinfo=None).isoformat(),
            "kp_index": 3,
            "estimated_kp": 3.33,
            "kp": "3P",
        }])

    async def run() -> tuple[list[object], list[object]]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            provider = NoaaSwpcSpaceWeatherProvider(
                "https://services.swpc.noaa.gov/json/planetary_k_index_1m.json",
                client=client,
                clock=lambda: 10.0,
            )
            first = await provider.snapshots_for(
                observed - timedelta(minutes=1), observed + timedelta(minutes=2)
            )
            second = await provider.snapshots_for(
                observed - timedelta(minutes=1), observed + timedelta(minutes=2)
            )
            return list(first), list(second)

    first, second = asyncio.run(run())
    assert calls == 1
    assert first == second
    snapshot = first[0]
    assert snapshot.kp_index == 3.33  # type: ignore[attr-defined]
    assert snapshot.source_kind == SourceKind.LIVE  # type: ignore[attr-defined]
    assert snapshot.source_quality == SourceQuality.VERIFIED  # type: ignore[attr-defined]
    assert snapshot.provenance.source_type == SourceType.DERIVED  # type: ignore[attr-defined]
    assert not hasattr(snapshot, "capacity_multiplier")


def test_integer_kp_is_fallback_and_old_record_is_stale() -> None:
    observed = datetime.now(UTC) - timedelta(hours=1)
    transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=[{
        "time_tag": observed.replace(tzinfo=None).isoformat(), "kp_index": 5, "kp": "5Z"
    }]))

    async def run() -> list[object]:
        async with httpx.AsyncClient(transport=transport) as client:
            provider = NoaaSwpcSpaceWeatherProvider("https://noaa.example/kp.json", client=client)
            return list(await provider.snapshots_for(
                observed - timedelta(minutes=1), observed + timedelta(minutes=2)
            ))

    snapshot = asyncio.run(run())[0]
    assert snapshot.kp_index == 5.0  # type: ignore[attr-defined]
    assert snapshot.source_quality == SourceQuality.STALE  # type: ignore[attr-defined]


def test_http_failure_is_normalized() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(lambda _request: httpx.Response(503))
        async with httpx.AsyncClient(transport=transport) as client:
            provider = NoaaSwpcSpaceWeatherProvider("https://noaa.example/kp.json", client=client)
            now = datetime.now(UTC)
            with pytest.raises(SpaceWeatherUnavailable):
                await provider.snapshots_for(now - timedelta(hours=1), now)

    asyncio.run(run())
