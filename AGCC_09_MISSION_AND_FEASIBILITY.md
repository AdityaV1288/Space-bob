# Task 09 — Mission constraints and feasibility

## Objective

Define one hard-target downlink mission, build eligible pass records, and determine whether any plan can meet the target before scheduling optimization.

## User inputs

`DownlinkMission`:

```text
id, name
required_volume_mb > 0
release_at UTC
deadline_at UTC, later than release
```

`ScenarioConstraints`:

```text
maximum_budget: Decimal >= 0
currency: fixed one currency per scenario
authorized station selection
planning_preference: FASTEST | LOWEST_COST | BALANCED
allow_additional_contact_proposals: bool
```

Budget and deadline are hard constraints. Preferences never relax them.

## Cost calculation

For contact duration in seconds:

```text
billable_minutes = ceil(duration_s / 60)
usage_cost = billable_minutes * cost_per_minute
contact_cost = booking_cost + usage_cost
```

Use `Decimal`, never binary float, for money.

## EligiblePassRecord

Combine pass, capacity, weather quality, compatibility, availability, deadline intersection, and estimated full-pass cost. Preserve every rejected record with one or more `RejectionCode` values.

## Feasibility bounds

Compute:

- `total_eligible_capacity_mb`: sum of useful capacity before deadline.
- `minimum_possible_cost`: greedy cheapest-capacity lower-bound estimate.
- `earliest_possible_completion_at`: greedy chronological capacity accumulation.
- `capacity_shortfall_mb = max(0, required - total_eligible_capacity)`.

This is a pre-planner bound, not the final schedule. Status:

- `POTENTIALLY_FEASIBLE` when bounds do not disprove feasibility.
- `INFEASIBLE_CAPACITY`, `INFEASIBLE_BUDGET`, `INFEASIBLE_DEADLINE`, or `UNVERIFIED_ENVIRONMENT` otherwise.

## Constraint-relaxation suggestions

When infeasible, produce calculations—not prose—for:

- Minimum deadline extension found by scanning later eligible passes.
- Minimum additional budget for a capacity-sufficient set.
- Additional authorized station candidates if available in catalog but excluded.
- Required target reduction as a last option.

Do not apply suggestions automatically.

## Acceptance

Test exact target, one-byte-equivalent shortfall, insufficient deadline, insufficient budget, excluded station restoration, and deterministic money rounding. Return the completion report and stop.

