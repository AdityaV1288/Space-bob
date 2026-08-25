# Task 15 — FastAPI integration

## Objective

Expose the completed deterministic engine through a session-scoped REST API and Server-Sent Events. Do not add persistence, authentication, or business logic to route handlers.

## Session behavior

Frontend creates a session with `POST /api/v1/sessions`. Backend stores state in an in-memory repository keyed by a cryptographically random session ID. No cookie is required; the frontend sends `X-AGCC-Session`.

Deleting the browser session removes the backend session with `DELETE /api/v1/sessions/{id}`. Add a 24-hour inactive-session eviction guard. This is cleanup, not durable persistence.

## Routes

```text
POST   /api/v1/sessions
DELETE /api/v1/sessions/{id}
GET    /api/v1/catalog/stations
POST   /api/v1/scenario
PUT    /api/v1/scenario/orbit
PUT    /api/v1/scenario/communications
PUT    /api/v1/scenario/stations
PUT    /api/v1/scenario/mission
POST   /api/v1/passes/compute
POST   /api/v1/plan
GET    /api/v1/plan/current
POST   /api/v1/simulation/start
POST   /api/v1/simulation/pause
POST   /api/v1/simulation/resume
GET    /api/v1/simulation/state
GET    /api/v1/simulation/events
POST   /api/v1/anomalies/parse
POST   /api/v1/anomalies/confirm
GET    /api/v1/replans/pending
POST   /api/v1/replans/{proposal_id}/approve
POST   /api/v1/replans/{proposal_id}/reject
GET    /api/v1/events/stream
```

## Response rules

- Domain models are response bodies; no duplicate DTO hierarchy.
- Domain errors map to stable JSON: `code`, `message`, `entity_refs`, `details`, `request_id`.
- Validation errors use a normalized `VALIDATION_ERROR` form.
- Route handlers call application services and contain no formulas.
- OpenAPI operation IDs are explicit and stable.

## Application services

Create services for scenario, pass computation, planning, simulation, anomalies, and replanning. Dependencies are injected through an `AppContainer` constructed at startup.

## API placeholders

Live weather and Granite configuration read named environment variables only in adapter factories. If absent, placeholder adapters remain active and return the specified configuration errors.

Do not invent provider URLs or secrets.

## Acceptance

Use FastAPI TestClient to cover the complete fixture flow, session isolation, errors, proposal approval, SSE ordering, and deletion. OpenAPI generation must be deterministic. Return the completion report and stop.

