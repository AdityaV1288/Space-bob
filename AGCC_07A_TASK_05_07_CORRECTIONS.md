# Task 07A — Mandatory corrections for Tasks 05–07

## Governing instruction

This corrective task must be completed before Task 08. Read `AGCC_00_GOVERNING_CONTEXT.md` and this file only. Inspect the existing repository, implement exactly the changes below, run every required check, return the mandatory completion report, and stop.

Do not implement capacity calculations, mission feasibility, planning, APIs, UI, persistence, or live external providers.

## Objective

Correct the Tasks 05–07 implementation so that:

- Incomplete station records remain viewable without becoming planner-eligible.
- All synthetic station facts are explicitly labeled.
- Station catalogs carry authoritative versions and provenance.
- Candidate-pass objects cannot represent inconsistent geometry or timing.
- The pass engine uses its injected propagator instead of a private implementation helper.
- Pass computation validates its public inputs.
- Weather and space-weather fixtures remain separate data families.
- Recorded environmental payload hashes are present and verifiable.
- External-provider configuration names use the AGCC namespace.

Do not change any rule not named in this file.

---

# Part A — Establish a clean baseline

Before modifying files, run from `agcc/backend`:

```powershell
py -3.12 -m pytest
py -3.12 -m ruff check .
py -3.12 -m mypy src/agcc
```

If the Python launcher differs, use the existing project environment. Record the exact commands and baseline results.

If tests cannot start because dependencies are missing, install only the dependencies already declared in `backend/pyproject.toml`. Do not add or substitute packages.

Continue with the corrections even if a baseline test fails, but list every baseline failure in the completion report.

---

# Part B — Correct the ground-station contract

## File

`backend/src/agcc/domain/stations.py`

## Required GroundStation fields

Retain existing fields and apply these exact amendments:

```python
supported_bands: frozenset[Band] | None = None
max_downlink_rate_mbps: float | None = Field(default=None, gt=0.0)
longitude_deg: float = Field(ge=-180.0, lt=180.0)
```

`supported_bands=frozenset()` is permitted but is planner-ineligible. A missing value and an empty value both mean that verified compatibility information is unavailable.

Keep `minimum_elevation_deg`, `setup_s`, `teardown_s`, and commercial fields required for now. Do not make other fields optional.

## Planner eligibility

Replace the current property with exactly this behavior:

```python
@property
def planner_eligible(self) -> bool:
    return (
        self.enabled
        and self.supported_bands is not None
        and len(self.supported_bands) > 0
        and self.max_downlink_rate_mbps is not None
        and self.max_downlink_rate_mbps > 0.0
    )
```

Do not store `planner_eligible` in JSON. It remains derived.

## Field provenance

Retain `FieldProvenance`, but validate the following fields:

```text
latitude_deg
longitude_deg
altitude_m
supported_bands
max_downlink_rate_mbps
minimum_elevation_deg
setup_s
teardown_s
cost_model
booking_cost
cost_per_minute
currency
```

Every populated field in that list must appear in exactly one of:

- `field_provenance.sources`
- `field_provenance.assumptions`

A field appearing in both is invalid. A populated field appearing in neither is invalid.

For optional fields that are `None`, provenance is not required. An empty `supported_bands` value is populated and therefore requires provenance.

## StationCatalog

Replace the unversioned catalog contract with:

```python
class StationCatalog(BaseModel):
    model_config = {"frozen": True}

    catalog_id: str
    schema_version: str
    catalog_version: str
    generated_at: datetime
    provenance: Provenance
    stations: list[GroundStation] = Field(default_factory=list)
```

Validation rules:

- `catalog_id` starts with `catalog_` and contains text after the prefix.
- `schema_version` is non-empty.
- `catalog_version` is non-empty.
- `generated_at` is timezone-aware and normalized to UTC.
- Station IDs are unique.
- Returned station ordering is deterministic by `station_id`.

Do not add `catalog_` to the global entity-ID registry unless needed by the shared validation approach. A local validator is acceptable.

## Catalog loader

Update `backend/src/agcc/stations/catalog.py` so `load_catalog_from_file()` validates and preserves the complete catalog record. It must not discard version or provenance fields.

The loader must sort stations by ID before constructing the final frozen `StationCatalog`.

## Catalog filter

Keep the existing order:

```text
enabled
→ planner eligible
→ explicit exclusions
→ allow-all or authorized set
```

The filter must safely handle `None` bands and rates through the derived property. Do not perform band compatibility in this module.

---

# Part C — Migrate catalog JSON files

## Files

```text
data/catalogs/stations.template.json
data/catalogs/stations.demo.json
```

Both files must use this root shape:

```json
{
  "catalog_id": "catalog_demo_v1",
  "schema_version": "1.0.0",
  "catalog_version": "2026.08.1",
  "generated_at": "2026-08-20T00:00:00Z",
  "provenance": {
    "source_type": "fixture",
    "source_name": "AGCC demo station catalog",
    "source_uri": null,
    "source_version": "2026.08.1",
    "observed_at": null,
    "fetched_at": "2026-08-20T00:00:00Z",
    "assumption_fields": []
  },
  "stations": []
}
```

Use the actual enum serialization value already defined for the fixture source type. Do not change the enum merely to match the example casing.

## Demo-catalog provenance

All demo stations and providers are fictional. Preserve the existing station values, but list every populated synthetic field below in `field_provenance.assumptions`:

```text
latitude_deg
longitude_deg
altitude_m
supported_bands
max_downlink_rate_mbps
minimum_elevation_deg
setup_s
teardown_s
cost_model
booking_cost
cost_per_minute
currency
```

Do not label any demo value as a sourced real-world fact.

## Incomplete template entry

Add at least one valid template/example station with:

```text
supported_bands = null
max_downlink_rate_mbps = null
enabled = true
```

It must load successfully, remain present in the catalog, and have `planner_eligible == false`.

Do not add the incomplete template entry to the demo catalog used by pass-generation integration tests.

---

# Part D — Strengthen CandidatePass invariants

## File

`backend/src/agcc/domain/planning.py`

Add one Pydantic `model_validator(mode="after")` to `CandidatePass`.

It must enforce:

```text
start_at < peak_at < end_at
duration_s > 0
abs(duration_s - (end_at - start_at).total_seconds()) <= 1.0
0 < usable_duration_s <= duration_s
max_elevation_deg >= minimum_elevation_deg
scenario_id is non-empty
satellite_id starts with "sat_"
station_id starts with "station_"
orbit_model_version is non-empty
station_catalog_version is non-empty
```

Keep the existing `pass_` validation.

Do not silently correct inconsistent values. Reject the model with a validation error.

## CapacityEstimate

Do not modify `CapacityEstimate` during this task. Task 08 owns its amendment.

---

# Part E — Make propagator injection real

## Files

```text
backend/src/agcc/passes/engine.py
backend/src/agcc/orbit/propagator.py
```

## Required behavior

The pass engine must obtain every satellite position through the injected `OrbitPropagator` interface or the public `CircularKeplerPropagator.state_at()` method.

Remove this dependency from the pass engine:

```python
from agcc.orbit.propagator import _propagate_one
```

The pass engine must not call `_propagate_one()` directly anywhere.

Pass the propagator into internal elevation, crossing, peak, and pass-building helpers. Do not create a new propagator inside those helpers.

Use the public orbit-state result to obtain Earth-fixed position.

## Public PassEngine interface

Retain the constructor injection:

```python
PassEngine(
    propagator: OrbitPropagator | None = None,
    screener: StationScreener | None = None,
    orbit_model_version: str = "circular_kepler_v1",
)
```

If the existing protocol cannot be imported at runtime, make it a runtime-safe `Protocol` without changing its method signatures.

Add a test double propagator whose returned position is observably different from the default. Prove that `PassEngine` calls the injected object. The test must fail if the engine calls `_propagate_one()` directly.

---

# Part F — Validate pass-engine public inputs

## File

`backend/src/agcc/passes/engine.py`

At the start of `compute_passes()`, validate:

```text
horizon_start and horizon_end are timezone-aware
horizon_end > horizon_start
satellite_id starts with "sat_"
scenario_id starts with "scenario_"
station_catalog_version is non-empty
orbit_model_version is non-empty
station IDs are unique
```

Normalize aware horizon datetimes to UTC before calculations.

Invalid public inputs must raise the existing structured validation-domain error if that is already the repository convention. Otherwise raise `ValueError` consistently. Do not return an empty pass list for invalid input.

## Catalog integration

Add a convenience method or amend the interface so the authoritative catalog version comes from `StationCatalog`, not from a user-typed optional string.

Required public method:

```python
def compute_passes_from_catalog(
    self,
    orbit: CustomCircularOrbit,
    satellite_id: str,
    catalog: StationCatalog,
    selected_stations: Sequence[GroundStation],
    horizon_start: datetime,
    horizon_end: datetime,
    scenario_id: str,
) -> list[CandidatePass]:
    ...
```

It must call the core computation with `catalog.catalog_version`.

Keep the lower-level `compute_passes()` only if existing tests or later code require it, but remove default empty values for `scenario_id` and `station_catalog_version`.

---

# Part G — Separate space-weather fixtures

Move:

```text
data/fixtures/weather/space_weather_fixture.json
```

to:

```text
data/fixtures/space_weather/space_weather_fixture.json
```

Update every test and reference. Do not leave a duplicate in the weather directory.

Required fixture tree:

```text
data/fixtures/weather/weather_clear.json
data/fixtures/weather/weather_moderate_rain.json
data/fixtures/weather/weather_heavy_rain.json
data/fixtures/weather/weather_missing_data.json
data/fixtures/space_weather/space_weather_fixture.json
```

---

# Part H — Complete environmental payload hashing

## Domain amendment

In `backend/src/agcc/domain/environment.py`, add to `SpaceWeatherSnapshot`:

```python
raw_payload_hash: str = Field(min_length=64, max_length=71)
```

Hash format is either:

- 64 lowercase hexadecimal SHA-256 characters; or
- `sha256:` followed by 64 lowercase hexadecimal characters.

Apply the same validator to `WeatherSnapshot.raw_payload_hash`. Blank hashes are not permitted.

## Canonical record hashing

Implement a public helper:

```python
def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    ...
```

Algorithm:

1. Copy the mapping.
2. Remove `raw_payload_hash` from the copy.
3. Serialize using UTF-8 JSON with sorted keys and separators `(',', ':')`.
4. Compute SHA-256.
5. Return 64 lowercase hexadecimal characters.

This hashes each normalized record, not the entire containing file.

## Provider verification

`FixtureWeatherProvider`, `RecordedWeatherProvider`, `FixtureSpaceWeatherProvider`, and `RecordedSpaceWeatherProvider` must verify every record:

```text
stored raw_payload_hash == canonical_payload_hash(record without hash)
```

On mismatch, reject the record with a structured validation/data-integrity error. Do not silently replace an invalid hash.

Regenerate all fixture hashes using the canonical algorithm. Do not hand-edit hashes.

The recorded provider may retain a whole-file hash as additional metadata, but tests must not access a private field to establish the primary integrity behavior.

---

# Part I — Standardize external configuration names

In the live placeholders, expose exactly:

```text
AGCC_WEATHER_API_URL
AGCC_WEATHER_API_KEY
AGCC_SPACE_WEATHER_API_URL
AGCC_SPACE_WEATHER_API_KEY
```

Do not implement live HTTP requests. Do not add URLs, API keys, provider names, or environment-loading code.

Placeholder calls must continue to raise:

```text
WEATHER_LIVE_NOT_CONFIGURED
SPACE_WEATHER_LIVE_NOT_CONFIGURED
```

Update tests to require the AGCC-prefixed names and reject the old generic names.

---

# Part J — Required tests

## Station tests

Add or update tests proving:

1. A station with `supported_bands=None` loads and is not planner-eligible.
2. A station with an empty band set loads and is not planner-eligible.
3. A station with `max_downlink_rate_mbps=None` loads and is not planner-eligible.
4. A complete enabled station is planner-eligible.
5. Longitude `180.0` is rejected; `-180.0` is accepted.
6. Every populated synthetic field requires provenance.
7. A field in both sources and assumptions is rejected.
8. Duplicate station IDs in a catalog are rejected.
9. Catalog version, schema version, ID, timestamp, and provenance survive loading.
10. Filtering excludes incomplete stations but catalog viewing retains them.
11. The demo catalog contains 12 stations and all synthetic fields are labeled assumptions.

## CandidatePass tests

Test rejection of:

- Peak before start.
- Peak equal to start.
- End before peak.
- Inconsistent duration.
- Zero usable duration.
- Usable duration greater than full duration.
- Maximum elevation below minimum elevation.
- Blank scenario ID.
- Blank model/catalog versions.

## Pass-engine tests

Test:

- Injected propagator is actually called.
- Naive horizons are rejected.
- Reversed/equal horizons are rejected.
- Bad satellite/scenario IDs are rejected.
- Blank catalog version is rejected.
- Duplicate selected station IDs are rejected.
- `compute_passes_from_catalog()` copies the catalog version into every pass.
- Existing geometric invariants still pass.
- Same inputs still produce deterministic output.

## Environment tests

Test:

- Fixture directories are separated.
- All fixture hashes validate.
- Tampering with one weather record causes rejection.
- Tampering with one space-weather record causes rejection.
- Reordered JSON keys produce the same canonical hash.
- Different normalized values produce different hashes.
- Live placeholders expose only AGCC-prefixed names.
- Alignment still never substitutes neutral weather.
- Space weather still has no capacity multiplier.

Do not weaken or delete existing valid tests merely to make the suite pass.

---

# Part K — Final verification

Run from `agcc/backend`:

```powershell
py -3.12 -m pytest
py -3.12 -m ruff check .
py -3.12 -m mypy src/agcc
```

Also run a repository search and verify:

```text
No pass-engine import or call of _propagate_one
No space_weather_fixture.json under data/fixtures/weather
No WEATHER_API_URL without AGCC_ prefix
No SPACE_WEATHER_API_URL without AGCC_ prefix
No blank raw_payload_hash in any environment fixture
```

Do not begin Task 08.

---

# Mandatory completion report

Return exactly:

```text
TASK: 07A — Mandatory corrections for Tasks 05–07
STATUS: PASS | FAIL | BLOCKED
BASELINE TEST RESULTS:
FILES CREATED:
FILES MODIFIED:
FILES MOVED:
PUBLIC INTERFACES ADDED OR AMENDED:
CATALOG MIGRATION RESULT:
FIXTURE HASH MIGRATION RESULT:
FINAL TEST COMMANDS:
FINAL TEST RESULTS:
SEARCH VERIFICATION RESULTS:
ASSUMPTIONS MADE: must be NONE
UNRESOLVED INPUTS:
OUT-OF-SCOPE WORK NOT PERFORMED:
```

After the report, stop and wait for human review.

