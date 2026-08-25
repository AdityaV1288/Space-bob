# AGCC governing context

## Product definition

AGCC is a custom-satellite ground-contact planning and interactive simulation prototype. A user defines one proposed circular-LEO satellite, authorizes a set of ground stations, specifies one fixed-volume downlink mission, a deadline, and a maximum budget. AGCC computes contact opportunities, creates a plan that meets the exact data target when feasible, simulates transfer execution, redistributes undelivered data over approved future contacts, and proposes additional contact commitments when the existing plan becomes infeasible. Material commitment changes require human approval.

AGCC does not control a satellite, book a real station, or claim certified RF performance.

## Non-negotiable MVP scope

- Exactly one satellite per scenario.
- Exactly one fixed-volume downlink mission per scenario.
- Custom circular LEO is the primary orbit input.
- Multiple ground stations and multiple passes are supported.
- Required data volume is a hard target and is never silently reduced.
- Deadline and maximum budget are hard constraints.
- The planner is deterministic and rule-based.
- Data may be fragmented across contacts.
- Data redistribution among already approved future contacts is automatic.
- A new provider, new cost, or committed-contact replacement requires approval.
- Past execution is immutable; replanning is forward-only.
- Granite explains verified results and parses anomaly text into a proposal; it never schedules or assigns numerical effects itself.
- Runtime state lasts for the browser-tab session only.

## Authoritative terminology

- `CandidatePass`: geometrically visible satellite-station interval.
- `EligiblePass`: candidate satisfying compatibility and availability constraints.
- `PlannedContact`: eligible interval selected by a plan.
- `CommittedContact`: approved planned contact protected by commitment rules.
- `ExecutionSlice`: simulated portion of a contact.
- `UnusedOpportunity`: eligible pass not selected.
- `PlanFeasible`: conservative planned capacity reaches the target by the deadline and within budget.
- `ExecutionComplete`: actual simulated delivered volume reaches the target.

## Fixed technical stack

- Python 3.12.
- FastAPI backend.
- Pydantic v2 domain contracts.
- Pytest tests.
- Ruff formatting/linting.
- MyPy type checking.
- Skyfield for time, coordinate, and satellite pass operations where applicable.
- React 18 + TypeScript + Vite frontend.
- Three.js through React Three Fiber for the globe/orbit view.
- Zustand for client session state.
- Browser `sessionStorage` for tab-session persistence.
- HTTP REST for commands/queries; Server-Sent Events for simulation events.
- No database in the MVP.

If a dependency is unavailable, Bob must report the missing dependency and stop. Bob must not silently substitute a library.

## Units and time

- Rates: megabits per second, suffix `_mbps`.
- Data: decimal megabytes, suffix `_mb`.
- Distance: kilometers except station altitude in meters.
- Angles: degrees at contracts; radians only locally inside numerical functions.
- Time: timezone-aware UTC. JSON uses ISO-8601 ending in `Z`.
- Conversion: `capacity_mb = rate_mbps * seconds / 8` before dimensionless factors.

## Architecture ownership

- Domain owns contracts and validation.
- Orbit owns orbit state and ground-track samples.
- Pass engine owns geometric visibility.
- Environment owns normalized external observations/forecasts.
- Capacity owns estimated physical transfer amounts.
- Feasibility owns hard-constraint decisions.
- Planner owns contact selection and planned allocations.
- Dispatch owns fragment queues and remaining data.
- Simulation owns execution state and append-only events.
- Replanner owns future plan versions and diffs.
- Approval layer owns whether a proposed commitment becomes active.
- Granite owns prose only.
- UI never recomputes backend decisions.

## Bob operating contract

Bob is an implementation worker. For every task:

1. Read this file and the single active task.
2. Inspect existing files relevant to that task.
3. Implement only the named files and behavior.
4. Do not anticipate later tasks or create their modules.
5. Do not change public contracts defined by earlier tasks.
6. Do not add a database, authentication, deployment, cloud service, live provider, or extra framework.
7. Do not invent missing station, weather, RF, price, or provider facts.
8. Use explicit `NotConfigured` adapters/placeholders where instructed.
9. Run every command in the task acceptance section.
10. Stop and return the exact completion report.

Bob may choose local variable names and private helper decomposition only. Bob may not choose formulas, public fields, dependencies, defaults, user behavior, or architecture.

## Mandatory completion report

```text
TASK:
STATUS: PASS | FAIL | BLOCKED
FILES CREATED:
FILES MODIFIED:
PUBLIC INTERFACES ADDED:
TEST COMMANDS:
TEST RESULTS:
ASSUMPTIONS MADE: must be NONE unless explicitly permitted
UNRESOLVED INPUTS:
OUT-OF-SCOPE WORK NOT PERFORMED:
```

After the report, stop.

