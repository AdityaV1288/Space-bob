# Task 03 — Custom orbit input and synchronized editor contract

## Objective

Define the authoritative circular-LEO orbit representation and pure conversions used by both the numeric form and future 3D orbit ring. Do not create a TLE and do not render the UI.

## Canonical model

`CustomCircularOrbit` fields:

```text
altitude_km: float, 200..2000
inclination_deg: float, 0..180
raan_deg: float, normalized to [0,360)
phase_deg: float, normalized to [0,360)
epoch: UTC datetime
direction: PROGRADE if inclination <= 90 else RETROGRADE
eccentricity: fixed literal 0.0
argument_of_perigee_deg: fixed literal 0.0
earth_radius_km: fixed 6378.137
mu_km3_s2: fixed 398600.4418
```

`direction` is derived and must not be accepted independently from the user.

## Derived values

Implement pure functions:

```text
semi_major_axis_km = earth_radius_km + altitude_km
mean_motion_rad_s = sqrt(mu_km3_s2 / semi_major_axis_km^3)
period_s = 2*pi / mean_motion_rad_s
revolutions_per_day = 86400 / period_s
```

Implement `OrbitRingTransform` with ring radius in normalized scene units, plane quaternion, and satellite phase radians. Provide deterministic `orbit_to_ring()` and `ring_to_orbit()` functions. Round-trip error for inclination, RAAN, and phase must be below `1e-6` degrees, excluding normalized wraparound.

## Presets

Provide fixed presets only:

- `EQUATORIAL_550`: 550 km, 0°.
- `MID_INCLINATION_550`: 550 km, 53°.
- `POLAR_550`: 550 km, 90°.
- `RETROGRADE_DEMO_550`: 550 km, 97.6°.

Do not label the last preset sun-synchronous.

## Exclusions

- No synthetic TLE.
- No drag, J2, maneuver, propulsion, or eccentricity controls.
- No satellite hardware fields.
- No frontend components.

## Acceptance

Test range validation, angle normalization, preset values, period calculation, and bidirectional ring round trips. Return the completion report and stop.

