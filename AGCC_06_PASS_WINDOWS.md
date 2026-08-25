# Task 06 — Station visibility and pass windows

## Objective

Compute exact-enough rise, peak, and set times against every authorized station using topocentric elevation. Do not use a service-radius circle as the final visibility test.

## Inputs

- `CustomCircularOrbit`
- `GroundStation[]`
- UTC horizon start/end
- `CircularKeplerPropagator`

## Algorithm

1. Coarse-sample the horizon every 60 seconds for each station.
2. Compute topocentric elevation from the station to satellite Earth-fixed position.
3. Detect threshold crossings at the station's `minimum_elevation_deg`.
4. Refine rise and set crossing times by bisection until interval width is <= 1 second.
5. Find peak elevation within each rise/set interval using deterministic bounded scalar search or 1-second sampling. Use one method consistently and test it.
6. Reject windows where usable duration after setup/teardown is <= 0.
7. Produce all candidate passes, sorted by start time then station ID.

## CandidatePass fields

```text
id, scenario_id, satellite_id, station_id
start_at, peak_at, end_at, duration_s, usable_duration_s
max_elevation_deg
azimuth_start_deg, azimuth_peak_deg, azimuth_end_deg
slant_range_peak_km
minimum_elevation_deg
orbit_model_version, station_catalog_version
```

Pass ID is a deterministic hash-derived ID from satellite ID, station ID, rise time, and model version.

## Optional screening

Expose `StationScreener`, but the default MVP implementation returns every authorized station. Do not optimize prematurely.

## Acceptance

- Start < peak < end.
- Threshold elevation at refined boundaries is within numerical tolerance.
- Max elevation meets threshold.
- No overlapping duplicate passes for the same station.
- Multiple demo stations produce multiple passes over 24 hours.
- Same input produces byte-identical normalized output.

Return the completion report and stop.

