# Task 12 — Simulation clock, execution, and event store

## Objective

Execute a contact plan in interactive simulation time, produce append-only events, and maintain current predicted completion after every transfer tick.

## Clock

`SimulationClock` supports `PAUSED`, `1x`, `5x`, `10x`, `20x`, `60x`, and `1000x`. Simulation time is authoritative. Wall-clock time is used only to advance simulation time.

Transfer tick: 1 simulated second. Rendering is not part of this task.

## Execution rate

At every tick during a contact, query the capacity model with current orbit geometry and the active environment snapshot. The result is an estimated/simulated rate, not measured telemetry.

Actual anomaly multipliers, when later present, are applied after the capacity model:

```text
realized_rate_mbps = modeled_rate_mbps * active_anomaly_multiplier
delivered_mb_tick = min(remaining_fragment_mb, realized_rate_mbps * tick_s / 8)
```

## Events

Append events with monotonically increasing sequence numbers:

- `SIMULATION_STARTED`
- `CONTACT_STARTED`
- `RATE_UPDATED`
- `FRAGMENT_STARTED`
- `FRAGMENT_PARTIAL`
- `FRAGMENT_DELIVERED`
- `CONTACT_ENDED`
- `SHORTFALL_PREDICTED`
- `MISSION_COMPLETED`
- `MISSION_DEADLINE_MISSED`
- `SIMULATION_PAUSED`

## Forecast after every tick

```text
predicted_final_delivery_mb = delivered_mb + capacity remaining in approved future contacts
predicted_shortfall_mb = max(0, required_mb - predicted_final_delivery_mb)
```

Emit `SHORTFALL_PREDICTED` only on transition from zero to positive shortfall or when shortfall increases by at least 1 MB. Do not globally replan in this task.

## Event store

Implement in-memory append-only `SessionEventStore`. No filesystem or database writes.

## Acceptance

Test clock speeds without real sleeping, event order, tick conversion, pause/resume, contact transitions, target completion, and predicted-shortfall transition. Return the completion report and stop.

