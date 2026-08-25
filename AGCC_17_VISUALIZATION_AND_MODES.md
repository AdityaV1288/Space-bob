# Task 17 — Mission dashboard, orbit visualization, and simulation modes

## Objective

Implement the main mission dashboard and three clearly separated views: Prediction, Live Estimate, and Anomaly Branch.

## Dashboard layout

```text
Header: mission, satellite, simulation mode, plan version, clock controls
Main: interactive 3D Earth, orbit ring, satellite, ground track, stations, active link
Right: current contact, modeled rate, weather, delivered/remaining target
Bottom: all candidate passes and selected contact sequence
Lower panel: event stream, warnings, pending approvals
```

## Orbit view

- Earth at origin.
- Orbit ring derived from canonical orbit transform.
- Satellite position comes from backend simulation/orbit samples.
- Selected stations and currently visible stations have distinct styles.
- Active contact displays a data beam whose animation speed reflects modeled estimated rate.
- Weather visualization is decorative context; numerical station weather comes from backend snapshots.

## Modes

### Prediction

Uses forecast/recorded environment data and the original plan. User may run at 1x, 5x, 10x, or 20x. It does not modify the live-estimate state.

### Live Estimate

Uses the configured live provider when available; otherwise this tab shows `LIVE DATA NOT CONFIGURED` and cannot pretend fixture data is live. The displayed rate is labeled `Modeled current rate`, never measured throughput.

### Anomaly Branch

Clones the prediction scenario state at a selected simulation time. Anomalies and replans occur only in the branch. The original prediction remains unchanged. Support 1x through 1000x.

## Contact opportunity display

Show all candidate passes. Color-code ineligible, eligible-unused, planned, committed, executed, and failed. Clicking any item displays exact selection or rejection reasons.

## Target display

Always show:

```text
Required
Delivered
Remaining
Predicted final delivery under current approved plan
Predicted shortfall
Planned completion time
Current approved cost
```

Do not display a confidence percentage.

## Acceptance

Visual component tests cover mode separation, labels, all-pass display, selection reasons, approval banner, rate labeling, branch isolation, and target completion. Return the completion report and stop.

