# Task 05 — Ground-station catalog and authorization

## Objective

Implement catalog loading, field-level provenance, filtering, and per-scenario authorization. Do not fabricate real-station facts and do not fetch a catalog in this task.

## GroundStation fields

```text
id, name, provider_id
latitude_deg, longitude_deg, altitude_m
supported_bands: set[Band]
max_downlink_rate_mbps
minimum_elevation_deg
setup_s, teardown_s
cost_model: NONE | PER_MINUTE | PER_CONTACT_PLUS_MINUTE
booking_cost, cost_per_minute, currency
simultaneous_contacts: fixed 1 for MVP
provenance: field-level source/assumption mapping
enabled
```

Coordinates and supported bands must be sourced facts or explicitly marked simulation assumptions. A catalog entry missing coordinates is invalid. A catalog entry missing supported bands or rate is viewable but `planner_eligible=false`.

## Catalog files

Create:

```text
data/catalogs/stations.template.json
data/catalogs/stations.demo.json
backend/src/agcc/stations/catalog.py
backend/src/agcc/stations/filtering.py
```

The demo catalog must use fictional provider/station names and plausible synthetic values. Do not use real company names. Include at least 12 stations distributed across longitude and both hemispheres so later tests yield multiple opportunities.

## Authorization

`StationSelection` fields:

```text
allow_all_eligible: bool
authorized_station_ids: set[str]
authorized_provider_ids: set[str]
excluded_station_ids: set[str]
```

Filtering order is fixed: enabled → complete planner data → band compatibility later → explicit exclusions → allow-all or authorized set.

## External placeholder

Define `StationCatalogProvider` and `NotConfiguredStationCatalogProvider`. The latter raises `ExternalDataUnavailable` with code `STATION_CATALOG_NOT_CONFIGURED`. Do not implement network access.

## Acceptance

Test invalid coordinates, missing eligibility data, provenance preservation, filter order, deterministic catalog sorting, and placeholder failure. Return the completion report and stop.

