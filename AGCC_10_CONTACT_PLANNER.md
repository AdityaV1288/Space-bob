# Task 10 — Deterministic contact planner

## Objective

Select a set of eligible contacts and data allocations that reaches the exact mission target before the deadline and within budget. Optimize according to fixed lexicographic rules.

## Planning representation

Split each eligible pass into 60-second slices after setup and before teardown. The final partial slice is permitted. Each slice has exact capacity from Task 08 integration samples and proportional usage cost, subject to whole-contact billing after selected slices are merged.

Adjacent selected slices at the same station and pass merge into one `PlannedContact`. Every contact must have at least 60 usable seconds. A satellite can use only one station per slice.

## Fixed objective hierarchy

Hard requirements first:

1. Deliver at least `required_volume_mb` by deadline.
2. Do not exceed maximum budget.

Then optimize lexicographically:

- `FASTEST`: earliest completion, then lowest cost, then fewest contacts, then station ID.
- `LOWEST_COST`: lowest cost, then earliest completion, then fewest contacts, then station ID.
- `BALANCED`: minimize normalized `0.6 * completion_time + 0.4 * cost`, then fewest contacts, then station ID. Normalize time by mission window and cost by maximum budget.

Do not add reliability or confidence terms.

## Deterministic heuristic

Implement candidate-plan construction for each preference:

- FASTEST: evaluate slices chronologically, choosing greatest useful capacity per slice; ties use lower incremental contact cost then station ID.
- LOWEST_COST: rank marginal capacity by incremental cost per MB while respecting time; zero-cost contacts first; recompute contact billing increments after each selection.
- BALANCED: rank by the defined normalized marginal score.

After reaching the target, trim the last allocation to exactly the remaining required volume. Validate the complete plan against all constraints. If the heuristic fails despite pre-feasibility, return `PLANNER_NO_FEASIBLE_PLAN_FOUND`, not a partial successful plan.

## Outputs

`ContactPlan` includes:

```text
id, version=1, parent_plan_id=None
mission_id, scenario_id, created_at
preference, status
contacts
required_volume_mb
planned_volume_mb exactly equal to required when feasible
estimated_total_cost
planned_completion_at
unused_opportunity_ids
rejected opportunity records
algorithm_version
```

Each contact includes selection reasons and allocation volume.

## Acceptance

- Never returns success below target.
- Never allocates more than the target.
- Never exceeds budget/deadline.
- Overlapping stations do not both occupy the satellite.
- Preference fixtures yield the expected deterministic plan.
- Same input yields identical plan except allowed creation timestamp; normalized hash is identical.

Return the completion report and stop.

