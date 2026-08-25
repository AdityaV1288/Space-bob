# Task 11 — Fragment queue and automatic data redistribution

## Objective

Convert a plan into a fragment queue and redistribute undelivered volume among approved future planned contacts without changing contact commitments.

## Fragment rules

- Fixed maximum fragment size: 25 MB.
- Final fragment may be smaller.
- Fragment IDs are deterministic from mission ID and sequence number.
- Fragment state: `QUEUED`, `ASSIGNED`, `TRANSMITTING`, `DELIVERED`, `PARTIAL`, `FAILED`.
- A fragment may be split during execution; preserve parent fragment ID.

## DispatchPlan

Allocate fragments chronologically across planned contacts according to their planned volumes. Total assigned volume must equal mission target for a feasible plan.

## Redistribution algorithm

When contact `k` under-delivers by `r` MB:

1. Record actual delivered volume.
2. Create/requeue exactly `r` MB of remaining fragments.
3. Starting at the next future planned contact, calculate its unused planned capacity.
4. Fill unused capacity chronologically without changing station, contact time, or approved cost.
5. Update allocations and predicted completion.
6. If all planned contacts are filled and remainder > 0, produce `ResidualShortfall` for replanning.

Redistribution must never add a station or contact. It may use spare capacity inside an already planned contact only when that capacity was included in its approved cost/time reservation.

## Invariants

```text
required = delivered + transmitting + assigned + queued
delivered never decreases
past assignments never change
fragment volumes remain positive
```

## Acceptance

Test full delivery, partial contact, failure, multi-contact redistribution, insufficient remaining capacity, and volume conservation to `1e-6 MB`. Return the completion report and stop.

