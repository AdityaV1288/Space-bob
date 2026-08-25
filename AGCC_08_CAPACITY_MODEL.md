# Task 08 — Capacity model v1

## Objective

Estimate usable downlink capacity per pass from rate limits, time-varying elevation, exact carrier-frequency configuration, precipitation, and overhead. Keep uncertainty flags separate. Do not implement a certified RF link budget.

## Required communications amendment

`SatelliteCommunications` fields:

```text
band: Band
carrier_frequency_ghz: float > 0
max_downlink_rate_mbps: float > 0
protocol_efficiency: float in (0,1]
```

Band and frequency must agree with a fixed validation range table stored in configuration. Do not infer frequency from band silently; a preset may populate it, but the resolved value is explicit.

## Eligibility

A pass is capacity-eligible only if the station supports the band and both rate limits are positive.

## Fixed calculation

Divide the usable pass interval into 10-second integration samples. For each sample `i`:

```text
base_rate_mbps = min(satellite.max_downlink_rate_mbps, station.max_downlink_rate_mbps)
elevation_factor = clamp(sin(elevation_rad), 0, 1)
weather_factor = WeatherAttenuationModel.factor(frequency_ghz, elevation_deg, precipitation_mm_per_hr)
effective_rate_mbps_i = base_rate_mbps * protocol_efficiency * elevation_factor * weather_factor
sample_capacity_mb_i = effective_rate_mbps_i * sample_duration_s / 8
```

Total usable capacity is the sum of sample capacities. Setup and teardown are excluded before sampling.

## Weather model boundary

Implement `WeatherAttenuationModel` as a protocol plus:

- `ConfiguredWeatherAttenuationModel`, which loads a versioned JSON lookup table keyed by frequency range, rain-rate range, and elevation range.
- `NoWeatherAttenuationModel`, permitted only in tests and marked as an assumption.

Create `data/fixtures/environment/weather_attenuation.template.json`, but do not invent scientific coefficients. Until the team supplies approved coefficients, the production configured model must raise `WEATHER_ATTENUATION_TABLE_MISSING`. Test with a clearly labeled test-only table.

This deliberate block prevents Bob from fabricating physics.

## CapacityEstimate fields

```text
id, pass_id
base_rate_mbps
usable_capacity_mb
average_effective_rate_mbps
peak_effective_rate_mbps
weather_data_quality
model_version
assumptions
sample_count
```

No confidence score and no Kp multiplier.

## Acceptance

- 8 Mbps for 10 seconds with all factors 1 equals 10 MB.
- Capacity never exceeds the rate-limit maximum.
- Higher elevation never lowers elevation factor.
- A table factor outside `[0,1]` is rejected.
- Missing production attenuation configuration blocks calculation explicitly.
- Full backend checks pass.

Return the completion report and stop.

