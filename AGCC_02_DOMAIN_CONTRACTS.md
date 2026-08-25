# Task 02 — Domain contracts

## Objective

Implement the canonical Pydantic models, stable IDs, UTC validation, enums, provenance, and structured errors. Do not implement calculations.

## Files

```text
backend/src/agcc/domain/enums.py
backend/src/agcc/domain/common.py
backend/src/agcc/domain/orbit.py
backend/src/agcc/domain/stations.py
backend/src/agcc/domain/environment.py
backend/src/agcc/domain/mission.py
backend/src/agcc/domain/planning.py
backend/src/agcc/domain/simulation.py
backend/src/agcc/domain/errors.py
backend/tests/domain/
```

## Common contracts

`Provenance`: `source_type`, `source_name`, `source_uri`, `source_version`, `observed_at`, `fetched_at`, `assumption_fields`.

`ModelRef`: `id`, `version`.

IDs are non-empty strings with prefixes: `scenario_`, `sat_`, `station_`, `pass_`, `capacity_`, `mission_`, `plan_`, `contact_`, `fragment_`, `event_`, `proposal_`.

All datetimes must reject naive values and normalize to UTC.

## Required enums

`OrbitInputMode`, `Band`, `PassStatus`, `ContactCommitment`, `MissionStatus`, `EventType`, `AnomalyType`, `ProposalStatus`, `SourceType`, `RejectionCode`, `SimulationMode`.

## Required top-level models

- `CustomCircularOrbit`
- `SatelliteCommunications`
- `CustomSatellite`
- `GroundStation`
- `StationCatalog`
- `WeatherSnapshot`
- `SpaceWeatherSnapshot`
- `CandidatePass`
- `CapacityEstimate`
- `DownlinkMission`
- `ScenarioConstraints`
- `Scenario`
- `FeasibilityResult`
- `PlannedAllocation`
- `ContactPlan`
- `DataFragment`
- `SimulationEvent`
- `AnomalyProposal`
- `ReplanProposal`
- `PlanDiff`

Use the field definitions in subsequent task files as additive contract amendments. For this task, create fields that are already unambiguous from the governing context and tests proving units, ID prefixes, and UTC validation. Do not add speculative RF or persistence fields.

## Errors

Implement `DomainError(code, message, entity_refs, details)` and specific constructors for validation, missing configuration, infeasible mission, and external data unavailable. Errors must serialize deterministically.

## JSON Schema

Add `backend/scripts/export_schemas.py` to export every top-level model into root `schemas/`. Output must be deterministic and sorted.

## Acceptance

- Invalid ID prefixes fail.
- Naive datetimes fail.
- Unit-bearing fields reject negative values.
- Model serialization produces UTC `Z` timestamps.
- Schema export succeeds twice without a diff.
- Full backend checks pass.

Return the completion report and stop.

