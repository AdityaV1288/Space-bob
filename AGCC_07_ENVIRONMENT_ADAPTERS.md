# Task 07 — Weather and space-weather adapters

## Objective

Normalize fixture, recorded, and optional live data behind typed provider interfaces. Core algorithms must never consume raw third-party payloads.

## Weather contract

`WeatherSnapshot` fields:

```text
id, station_id
valid_from, valid_until, observed_at, fetched_at
precipitation_mm_per_hr
temperature_c
relative_humidity_pct
cloud_cover_pct
wind_speed_mps
source_kind: FIXTURE | RECORDED | LIVE
source_quality: VERIFIED | STALE | ASSUMED | UNAVAILABLE
raw_payload_hash
provenance
```

## Provider interface

```python
class WeatherProvider(Protocol):
    async def snapshots_for(
        self, station: GroundStation, start: datetime, end: datetime
    ) -> list[WeatherSnapshot]: ...
```

Implement:

- `FixtureWeatherProvider`
- `RecordedWeatherProvider`
- `NotConfiguredLiveWeatherProvider`

The live placeholder must expose the required configuration names but never contain a key or URL guess. It raises `WEATHER_LIVE_NOT_CONFIGURED`.

## Alignment rule

For a pass, choose the snapshot covering the pass midpoint. If none covers it, use the closest snapshot only when its age is within a configured `max_staleness_s`; otherwise mark environment unavailable. Never silently use neutral weather.

## Space weather

Define and load `SpaceWeatherSnapshot` with time range, indices, source, and quality. Store it for display and anomaly context only. This task must not assign a capacity multiplier.

## Fixtures

Provide deterministic clear, moderate-rain, heavy-rain, and missing-data fixture sets for every demo station over one 24-hour scenario.

## Acceptance

Test time alignment, staleness, missing data, deterministic hashes, recorded payload normalization, and live placeholder failure. Return the completion report and stop.

