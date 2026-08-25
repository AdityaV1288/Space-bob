# Task 19 — End-to-end verification and controlled demo

## Objective

Add golden scenarios proving the complete AGCC behavior. Do not add new features or change formulas to make tests pass.

## Golden scenarios

### Scenario A — Feasible baseline

- One 550 km, 53° circular-LEO satellite.
- Fictional demo stations.
- Fixed weather fixture and approved test attenuation table.
- 500 MB target.
- Deadline and budget sufficient.
- Plan reaches exactly 500 MB.
- Simulation delivers exactly 500 MB.

### Scenario B — Recoverable under-delivery

- Same initial feasible plan.
- First contact realizes less throughput.
- Automatic redistribution among remaining committed contacts restores 500 MB.
- No new station proposal occurs.

### Scenario C — Approval-required recovery

- Under-delivery causes future committed capacity to be insufficient.
- System predicts shortfall before the final committed contact.
- Additional authorized contact can restore target for extra cost.
- Proposal is created.
- Before approval, plan is unchanged.
- After approval, plan v2 reaches 500 MB.

### Scenario D — Constraint relaxation

- No available contact set can meet the deadline and budget.
- System returns infeasible before simulation.
- It produces minimum calculated deadline extension and additional-budget alternatives.
- It never labels the mission complete.

### Scenario E — Natural-language anomaly

- Explicit outage text becomes a structured proposal.
- Nothing changes before confirmation.
- Confirmation applies deterministic outage policy.
- Replanning follows Scenario C rules.

## Verification artifacts

Create:

```text
backend/tests/golden/
frontend/src/**/*.integration.test.tsx
data/fixtures/scenarios/
docs/verification-report-template.md
```

Golden JSON excludes nondeterministic timestamps or normalizes them. Record model versions and fixture hashes.

## Global acceptance

- Backend unit, integration, and golden tests pass.
- Ruff and MyPy pass.
- Frontend tests and production build pass.
- No secrets are present.
- No real station has fabricated unlabeled facts.
- No confidence percentage appears in planner or UI.
- No successful plan or execution reports below target.
- No anomaly mutates state before confirmation.
- No new-cost contact becomes active before approval.
- Prediction and anomaly branch remain isolated.
- Granite being unconfigured does not block core operation.

Return the completion report and stop. The project is not complete until a human reviews this report.

