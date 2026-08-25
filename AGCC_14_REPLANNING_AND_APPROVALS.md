# Task 14 — Forward replanning, proposals, and approvals

## Objective

React to predicted shortfall by first redistributing data, then evaluating future authorized opportunities, and finally producing user choices. Never mutate committed plans without approval.

## Commitment states

`OPPORTUNITY → AUTHORIZED → PLANNED → COMMITTED → EXECUTED`.

Initial feasible-plan contacts become `COMMITTED` when the user starts the simulation. Past or started contacts cannot change.

## Trigger

Replanning begins only when:

- `predicted_shortfall_mb > 0` after automatic redistribution;
- a future committed contact becomes unavailable;
- or the user explicitly requests replanning.

Rate changes that preserve zero predicted shortfall do not trigger replanning.

## Forward replanning

1. Freeze executed/current contact history.
2. Compute remaining mission volume.
3. Preserve viable future committed contacts.
4. Run planner over remaining authorized opportunities.
5. Prefer a plan using preserved commitments.
6. If no such plan exists, evaluate additional authorized but uncommitted contacts.
7. If still infeasible, evaluate catalog stations outside the authorized set only as proposals.
8. Produce alternatives: add budget/contact, extend deadline, expand station authorization, or reduce target last.

## Approval rules

Approval is required if a proposal:

- adds a contact;
- adds cost above the current approved plan;
- uses a previously unauthorized station/provider;
- cancels or replaces a future committed contact.

Data redistribution within existing commitments requires no approval.

## ReplanProposal

Include old plan, proposed plan, `PlanDiff`, predicted shortfall before/after, cost delta, deadline delta, added/removed/modified contacts, exact approval reasons, and status `PENDING`, `APPROVED`, `REJECTED`, or `EXPIRED`.

Applying approval creates a new immutable plan version with `parent_plan_id`. Rejection leaves the current plan unchanged.

## Acceptance

Test no-trigger minor degradation, automatic redistribution, added-contact proposal, deadline-extension alternative, unauthorized-station proposal, approval application, rejection, and immutable history. Return the completion report and stop.

