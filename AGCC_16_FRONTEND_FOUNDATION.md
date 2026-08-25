# Task 16 — Frontend foundation and synchronized mission setup

## Objective

Build the typed frontend shell, session lifecycle, setup workflow, and synchronized orbit form/ring state. Do not build the final visualization or simulation animation.

## Fixed routes

```text
/setup/orbit
/setup/communications
/setup/stations
/setup/mission
/mission
```

The setup uses progressive steps but the product center is `/mission`.

## Session lifecycle

- Create backend session on first app load.
- Save session ID and current draft scenario in `sessionStorage`.
- Reloading the tab restores it.
- Register `pagehide` cleanup using `navigator.sendBeacon` where possible; backend TTL is the fallback.
- Never use `localStorage`.

## API client

Generate or hand-write one typed client based strictly on backend OpenAPI. No frontend recreation of formulas, eligibility, planning, or status logic.

## Setup screens

Orbit page:

- Main canvas placeholder for orbit ring.
- Side form for preset, altitude, inclination, RAAN, phase, and epoch.
- Two-way state transformations call shared TypeScript equivalents of Task 03 contracts only; no propagation.

Communications page:

- Band.
- Exact carrier frequency.
- Maximum downlink rate.
- Protocol efficiency under Advanced.

Stations page:

- Catalog table/map placeholder.
- Filter by provider, band, cost availability, and planner eligibility.
- Allow all eligible or explicit selection.
- Show field provenance and assumption badges.

Mission page:

- Required data volume.
- Deadline.
- Maximum budget and currency.
- Planning preference.

## Validation

Backend validation is authoritative. Frontend duplicates only range checks for immediate feedback and displays backend errors verbatim in a structured panel.

## Acceptance

Test session restore, route progression, two-way orbit editor state, station selection, submission payloads, and no `localStorage` use. Return the completion report and stop.

