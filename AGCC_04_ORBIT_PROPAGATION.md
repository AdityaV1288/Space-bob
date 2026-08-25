# Task 04 — Circular-orbit propagation and ground track

## Objective

Propagate the authoritative custom circular orbit over time and produce deterministic Earth-fixed position and ground-track samples. This is a planning approximation, not an operational ephemeris.

## Interface

```python
class OrbitPropagator(Protocol):
    def state_at(self, orbit: CustomCircularOrbit, at: datetime) -> OrbitState: ...
    def sample_ground_track(
        self, orbit: CustomCircularOrbit, start: datetime, end: datetime, step_s: int
    ) -> list[GroundTrackPoint]: ...
```

Implement `CircularKeplerPropagator` only.

## Fixed algorithm

1. Compute mean motion from Task 03.
2. Advance phase from epoch by `mean_motion_rad_s * delta_seconds`.
3. Create the circular orbital-plane position `[a*cos(theta), a*sin(theta), 0]`.
4. Rotate by inclination and RAAN into the inertial frame.
5. Convert the inertial position to Earth-fixed coordinates using a single documented Greenwich rotation implementation.
6. Convert Earth-fixed coordinates to geodetic latitude, longitude, and altitude.

The conversion must live behind named functions and contain references in docstrings to the mathematical convention, but Bob must not fetch data or create a TLE.

## Models

`OrbitState`: time, inertial position/velocity, Earth-fixed position, latitude, longitude, altitude.

`GroundTrackPoint`: time, latitude, longitude, altitude.

## Validation fixture

Create one frozen reference fixture produced by the same documented model and test invariants rather than claiming external truth:

- Radius remains constant within numerical tolerance.
- Altitude remains constant within 0.1 km.
- Position repeats after one calculated period within tolerance.
- Latitude never exceeds absolute inclination plus tolerance.
- Longitude remains in `[-180,180)`.

## Exclusions

No live satellites, TLE ingestion, SGP4, J2, atmospheric drag, or UI.

## Acceptance

All invariants and backend checks pass. Return the completion report and stop.

