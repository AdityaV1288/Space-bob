"""Contact planner — deterministic contact selection and data allocation (Task 10).

Architecture
------------
PlannedContact   — one merged contact with selection reasons and allocated volume.
ContactPlan      — full planner output for a scenario + mission.
ContactPlanner   — stateless; call plan() to produce a ContactPlan.

Slice model
-----------
Each eligible pass is split into 60-second slices (final partial slice allowed).
Adjacent selected slices for the same pass merge into one PlannedContact.
Every contact must have at least 60 usable seconds.
One station per satellite per time slot.

Preference strategies (lexicographic after hard constraints)
------------------------------------------------------------
FASTEST:      earliest completion → lowest cost → fewest contacts → station ID
LOWEST_COST:  lowest cost → earliest completion → fewest contacts → station ID
BALANCED:     min(0.6 * norm_time + 0.4 * norm_cost) → fewest contacts → station ID
              normalize time by mission window; cost by maximum budget

Cost formula  (Decimal, never binary float)
-------------------------------------------
    billable_minutes = ceil(contact_duration_s / 60)
    usage_cost       = billable_minutes * cost_per_minute
    contact_cost     = booking_cost + usage_cost
"""

from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, Field, field_validator

from agcc.domain.common import _require_utc, _validate_id
from agcc.domain.enums import ContactCommitment
from agcc.domain.mission import PlanningPreference
from agcc.domain.stations import GroundStation
from agcc.feasibility import EligiblePassRecord

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALGORITHM_VERSION = "planner_v1"
_SLICE_S: float = 60.0
_ZERO = Decimal("0")
_CENT = Decimal("0.0001")


# ---------------------------------------------------------------------------
# Plan status
# ---------------------------------------------------------------------------


class PlanStatus(str, Enum):
    FEASIBLE = "feasible"
    NO_FEASIBLE_PLAN_FOUND = "no_feasible_plan_found"


# ---------------------------------------------------------------------------
# PlannedContact
# ---------------------------------------------------------------------------


class PlannedContact(BaseModel):
    """One merged contact selected by the planner."""

    model_config = {"frozen": True}

    contact_id: str = Field(description="Unique contact ID (prefix: contact_)")
    pass_id: str
    station_id: str

    start_at: datetime = Field(description="Start of the merged contact interval (UTC)")
    end_at: datetime = Field(description="End of the merged contact interval (UTC)")
    duration_s: float = Field(ge=0.0, description="Total selected contact duration in seconds")

    # Volume allocated to this contact (trimmed on the last contact)
    allocated_volume_mb: float = Field(ge=0.0)
    reserved_capacity_mb: float | None = Field(
        default=None,
        ge=0.0,
        description="Physical capacity inside the approved contact reservation",
    )

    # Billable cost for this contact (Decimal string)
    contact_cost_decimal: str = Field(description="Contact cost as Decimal string")

    # Selection rationale
    selection_reasons: list[str] = Field(default_factory=list)
    commitment: ContactCommitment = ContactCommitment.PLANNED

    @field_validator("contact_id", mode="before")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id("contact_", v)

    @field_validator("start_at", "end_at", mode="before")
    @classmethod
    def _check_utc(cls, v: object) -> object:
        return _require_utc(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# ContactPlan
# ---------------------------------------------------------------------------


class ContactPlan(BaseModel):
    """Full planner output for a scenario + mission."""

    model_config = {"frozen": True}

    plan_id: str = Field(description="Unique plan ID (prefix: plan_)")
    version: int = Field(default=1, ge=1)
    parent_plan_id: str | None = None

    mission_id: str
    scenario_id: str
    created_at: datetime

    preference: PlanningPreference
    status: PlanStatus

    contacts: list[PlannedContact] = Field(default_factory=list)

    required_volume_mb: float = Field(ge=0.0)
    # planned_volume_mb == required_volume_mb when feasible
    planned_volume_mb: float = Field(ge=0.0)
    estimated_total_cost: str = Field(description="Total cost as Decimal string")

    planned_completion_at: datetime | None = None

    unused_opportunity_ids: list[str] = Field(default_factory=list)
    rejected_opportunity_records: list[EligiblePassRecord] = Field(default_factory=list)

    algorithm_version: str = ALGORITHM_VERSION
    validation_violations: list[str] = Field(default_factory=list)

    @field_validator("plan_id", mode="before")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id("plan_", v)

    @field_validator("created_at", mode="before")
    @classmethod
    def _check_created_at(cls, v: object) -> object:
        return _require_utc(v)  # type: ignore[arg-type]

    @field_validator("planned_completion_at", mode="before")
    @classmethod
    def _check_completion_utc(cls, v: object) -> object:
        if v is None:
            return v
        return _require_utc(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Internal slice representation
# ---------------------------------------------------------------------------


class _Slice:
    """One 60-second window within an eligible pass."""

    __slots__ = (
        "pass_id",
        "station_id",
        "start_at",
        "end_at",
        "duration_s",
        "capacity_mb",
        "record",
        "station",
        "slice_index",
    )

    def __init__(
        self,
        *,
        pass_id: str,
        station_id: str,
        start_at: datetime,
        end_at: datetime,
        duration_s: float,
        capacity_mb: float,
        record: EligiblePassRecord,
        station: GroundStation,
        slice_index: int,
    ) -> None:
        self.pass_id = pass_id
        self.station_id = station_id
        self.start_at = start_at
        self.end_at = end_at
        self.duration_s = duration_s
        self.capacity_mb = capacity_mb
        self.record = record
        self.station = station
        self.slice_index = slice_index


# ---------------------------------------------------------------------------
# Slice construction
# ---------------------------------------------------------------------------


def _build_slices(record: EligiblePassRecord, station: GroundStation) -> list[_Slice]:
    """Split a pass into ≤60-second slices.  Minimum contact = 60 usable seconds."""
    pass_ = record.pass_
    usable_s = pass_.usable_duration_s
    if usable_s < _SLICE_S:
        return []  # cannot form even one full 60-second slice

    total_cap = record.capacity.usable_capacity_mb
    slices: list[_Slice] = []

    # Task 10 slices only the usable reservation.  CandidatePass carries the
    # usable duration, while the station owns the setup/teardown policy.
    usable_start = pass_.start_at + timedelta(seconds=station.setup_s)
    usable_end = pass_.end_at - timedelta(seconds=station.teardown_s)
    actual_usable_s = (usable_end - usable_start).total_seconds()
    if actual_usable_s < _SLICE_S:
        return []
    # Reject inconsistent upstream records instead of multiplying capacity.
    usable_s = min(usable_s, actual_usable_s)
    usable_end = usable_start + timedelta(seconds=usable_s)
    t = usable_start.timestamp()
    end_ts = usable_end.timestamp()
    idx = 0

    while t < end_ts:
        s_end_ts = min(t + _SLICE_S, end_ts)
        dur = s_end_ts - t
        frac = dur / usable_s
        cap = total_cap * frac

        slices.append(
            _Slice(
                pass_id=pass_.pass_id,
                station_id=pass_.station_id,
                start_at=datetime.fromtimestamp(t, tz=timezone.utc),
                end_at=datetime.fromtimestamp(s_end_ts, tz=timezone.utc),
                duration_s=dur,
                capacity_mb=cap,
                record=record,
                station=station,
                slice_index=idx,
            )
        )
        t = s_end_ts
        idx += 1

    return slices


# ---------------------------------------------------------------------------
# Cost helpers
# ---------------------------------------------------------------------------


def _contact_cost(duration_s: float, station: GroundStation) -> Decimal:
    """Billable-minute cost for a contact of given duration."""
    billable = Decimal(math.ceil(duration_s / 60))
    usage = billable * Decimal(str(station.cost_per_minute))
    return Decimal(str(station.booking_cost)) + usage


def _contact_id(pass_id: str, first_slice_index: int) -> str:
    digest = hashlib.sha256(f"contact|{pass_id}|{first_slice_index}".encode()).hexdigest()[:16]
    return f"contact_{digest}"


# ---------------------------------------------------------------------------
# Normalisation (BALANCED)
# ---------------------------------------------------------------------------


def _norm_time(ts: float, release_ts: float, deadline_ts: float) -> float:
    window = deadline_ts - release_ts
    if window <= 0.0:
        return 0.0
    return max(0.0, min(1.0, (ts - release_ts) / window))


def _norm_cost(cost: Decimal, max_budget: Decimal) -> float:
    if max_budget <= _ZERO:
        return 0.0
    return max(0.0, min(1.0, float(cost / max_budget)))


# ---------------------------------------------------------------------------
# Greedy selection core
# ---------------------------------------------------------------------------


def _greedy_select(
    *,
    slices: list[_Slice],
    required_volume_mb: float,
    deadline: datetime,
    maximum_budget: Decimal,
    mission_window_start: datetime,
    score_key: object,
    static_order: bool = False,
) -> tuple[list[_Slice], bool]:
    """Generic greedy selection loop used by all three strategies.

    ``score_key(slice, running_cost) -> tuple`` — lower is better.
    One satellite time-slot may only serve one station.
    """
    from typing import Any, Callable

    key_fn: Callable[[_Slice, Decimal, list[_Slice]], Any] = score_key  # type: ignore[assignment]

    running_cost = _ZERO
    selected: list[_Slice] = []
    remaining_candidates = list(slices)

    remaining = required_volume_mb
    deadline_ts = deadline.timestamp()

    if static_order:
        ranked_once = sorted(
            remaining_candidates,
            key=lambda candidate: key_fn(candidate, running_cost, selected),
        )
        for s in ranked_once:
            if remaining <= 0.0:
                break
            if s.start_at < mission_window_start or s.end_at.timestamp() > deadline_ts:
                continue
            same_pass = [x for x in selected if x.pass_id == s.pass_id]
            if same_pass:
                indexes = {x.slice_index for x in same_pass}
                if s.slice_index - 1 not in indexes and s.slice_index + 1 not in indexes:
                    continue
            elif s.duration_s < _SLICE_S:
                continue
            if _time_overlaps(s, selected):
                continue
            inc = _incremental_cost(s, selected, running_cost)
            if running_cost + inc > maximum_budget:
                continue
            selected.append(s)
            running_cost += inc
            remaining -= s.capacity_mb
        return selected, remaining <= 0.0

    while remaining > 0.0 and remaining_candidates:
        # Marginal cost changes whenever a slice extends an existing contact,
        # therefore ranking must be recomputed after every choice.
        ranked = sorted(
            remaining_candidates,
            key=lambda candidate: key_fn(candidate, running_cost, selected),
        )
        chosen: _Slice | None = None
        chosen_inc = _ZERO
        for s in ranked:
            # Deadline: slice must end by deadline
            if s.start_at < mission_window_start or s.end_at.timestamp() > deadline_ts:
                continue
            same_pass = [x for x in selected if x.pass_id == s.pass_id]
            if same_pass:
                indexes = {x.slice_index for x in same_pass}
                if s.slice_index - 1 not in indexes and s.slice_index + 1 not in indexes:
                    continue
            elif s.duration_s < _SLICE_S:
                continue
            # Satellite single-station constraint: only one station per time slot.
            # Slices from the same pass are allowed (they extend the same contact).
            # But two different passes cannot overlap in time.
            # We enforce: if any already-selected pass overlaps this slice's time,
            # skip it — unless it's the same pass.
            # Always check against other selected passes. A previously committed
            # pass can be extended into the time occupied by a different station.
            if _time_overlaps(s, selected):
                continue

            # Budget check
            inc = _incremental_cost(s, selected, running_cost)
            if running_cost + inc > maximum_budget:
                continue
            chosen = s
            chosen_inc = inc
            break
        if chosen is None:
            break
        selected.append(chosen)
        remaining_candidates.remove(chosen)
        running_cost += chosen_inc
        remaining -= chosen.capacity_mb

    success = remaining <= 0.0
    return selected, success


def _time_overlaps(candidate: _Slice, selected: list[_Slice]) -> bool:
    """Return True if candidate overlaps in time with any already-selected different-pass slice."""
    c_start = candidate.start_at.timestamp()
    c_end = candidate.end_at.timestamp()
    for s in selected:
        if s.pass_id == candidate.pass_id:
            continue
        if s.end_at.timestamp() > c_start and s.start_at.timestamp() < c_end:
            return True
    return False


def _incremental_cost(
    new_slice: _Slice,
    selected: list[_Slice],
    running_cost: Decimal,
) -> Decimal:
    """Return the cost delta of adding new_slice to the current selection."""
    same_pass_slices = [s for s in selected if s.pass_id == new_slice.pass_id]
    station = new_slice.station

    if not same_pass_slices:
        # Brand-new contact: pay full first-slice cost
        return _contact_cost(new_slice.duration_s, station)
    else:
        # Extending existing contact: pay only for extra billable minutes
        old_dur = sum(s.duration_s for s in same_pass_slices)
        new_dur = old_dur + new_slice.duration_s
        old_cost = _contact_cost(old_dur, station)
        new_cost = _contact_cost(new_dur, station)
        return new_cost - old_cost


# ---------------------------------------------------------------------------
# Merge slices → PlannedContact list with volume trimming
# ---------------------------------------------------------------------------


def _build_contacts(
    selected: list[_Slice],
    required_volume_mb: float,
) -> tuple[list[PlannedContact], Decimal, datetime | None]:
    """Merge adjacent slices per pass; trim last allocation; build PlannedContact list."""
    # Contiguous runs are separate contacts.  This prevents gaps from being
    # hidden inside one billed interval.
    by_pass: dict[str, list[_Slice]] = defaultdict(list)
    for s in selected:
        by_pass[s.pass_id].append(s)
    groups: list[list[_Slice]] = []
    for pass_slices in by_pass.values():
        ordered = sorted(pass_slices, key=lambda item: item.slice_index)
        run = [ordered[0]]
        for item in ordered[1:]:
            if item.slice_index == run[-1].slice_index + 1:
                run.append(item)
            else:
                groups.append(run)
                run = [item]
        groups.append(run)
    groups.sort(key=lambda group: (group[0].start_at, group[0].station_id))

    contacts: list[PlannedContact] = []
    total_cost = _ZERO
    remaining = required_volume_mb
    completion_at: datetime | None = None

    for grp in groups:
        if remaining <= 0.0:
            break
        pid = grp[0].pass_id
        station = grp[0].station

        vol = sum(s.capacity_mb for s in grp)
        allocated = min(vol, remaining)

        dur_s = sum(s.duration_s for s in grp)
        start_at = grp[0].start_at
        end_at = grp[-1].end_at
        cost = _contact_cost(dur_s, station)

        cid = _contact_id(pid, grp[0].slice_index)

        contacts.append(
            PlannedContact(
                contact_id=cid,
                pass_id=pid,
                station_id=grp[0].station_id,
                start_at=start_at,
                end_at=end_at,
                duration_s=dur_s,
                allocated_volume_mb=allocated,
                reserved_capacity_mb=vol,
                contact_cost_decimal=str(cost),
                selection_reasons=[
                    "eligible_pass",
                    "preference_ranked",
                    "hard_constraints_validated",
                ],
            )
        )
        total_cost += cost
        remaining -= allocated
        completion_at = max(completion_at, end_at) if completion_at else end_at

    return contacts, total_cost, completion_at


def _final_plan_violations(
    *,
    contacts: list[PlannedContact],
    required_volume_mb: float,
    mission_window_start: datetime,
    deadline: datetime,
    maximum_budget: Decimal,
) -> list[str]:
    """Validate the merged ledger that execution will actually consume."""
    violations: list[str] = []
    allocated = sum(item.allocated_volume_mb for item in contacts)
    if abs(allocated - required_volume_mb) > 1e-6:
        violations.append(
            f"allocated_volume={allocated:.6f} != required_volume={required_volume_mb:.6f}"
        )
    for contact in contacts:
        frozen_capacity = (
            contact.reserved_capacity_mb
            if contact.reserved_capacity_mb is not None
            else contact.allocated_volume_mb
        )
        if contact.allocated_volume_mb > frozen_capacity + 1e-6:
            violations.append(f"{contact.contact_id}: allocation exceeds frozen capacity")
        if contact.start_at < mission_window_start or contact.end_at > deadline:
            violations.append(f"{contact.contact_id}: contact outside mission window")
    ordered = sorted(contacts, key=lambda item: item.start_at)
    for previous, current in zip(ordered, ordered[1:], strict=False):
        if current.start_at < previous.end_at:
            violations.append(
                f"{previous.contact_id}/{current.contact_id}: overlapping contacts"
            )
    total_cost = sum(Decimal(item.contact_cost_decimal) for item in contacts)
    if total_cost > maximum_budget:
        violations.append(
            f"plan_cost={total_cost} exceeds maximum_budget={maximum_budget}"
        )
    return violations


# ---------------------------------------------------------------------------
# ContactPlanner
# ---------------------------------------------------------------------------


class ContactPlanner:
    """Deterministic rule-based contact planner."""

    def plan(
        self,
        *,
        plan_id: str,
        scenario_id: str,
        mission_id: str,
        required_volume_mb: float,
        deadline: datetime,
        mission_window_start: datetime,
        maximum_budget: Decimal,
        preference: PlanningPreference,
        eligible_records: Sequence[EligiblePassRecord],
        station_map: dict[str, GroundStation],
        created_at: datetime | None = None,
    ) -> ContactPlan:
        """Produce a ContactPlan.

        Parameters
        ----------
        station_map:
            Maps station_id → GroundStation for every eligible pass so costs
            can be re-computed per selected duration.
        created_at:
            Override plan creation timestamp (for deterministic tests).
        """
        if created_at is None:
            created_at = datetime.now(tz=timezone.utc)

        only_eligible = [r for r in eligible_records if r.is_eligible]
        rejected = [r for r in eligible_records if not r.is_eligible]

        # Build slices from eligible passes
        all_slices: list[_Slice] = []
        for record in only_eligible:
            sid = record.pass_.station_id
            station = station_map.get(sid)
            if station is None:
                continue
            all_slices.extend(_build_slices(record, station))

        # Preference controls the first deterministic search, but is not a hard
        # constraint. Try the other orderings before declaring infeasibility.
        strategies = list(dict.fromkeys([
            preference,
            PlanningPreference.FASTEST,
            PlanningPreference.LOWEST_COST,
            PlanningPreference.BALANCED,
        ]))
        selected: list[_Slice] = []
        contacts: list[PlannedContact] = []
        total_cost = _ZERO
        completion_at: datetime | None = None
        violations: list[str] = []
        best_partial: list[_Slice] = []
        best_partial_volume = 0.0
        best_partial_cost = _ZERO
        for strategy in strategies:
            candidate, success = self._dispatch(
                slices=all_slices,
                required_volume_mb=required_volume_mb,
                deadline=deadline,
                mission_window_start=mission_window_start,
                maximum_budget=maximum_budget,
                preference=strategy,
            )
            if not success:
                candidate_contacts, candidate_cost, _ = _build_contacts(
                    candidate, required_volume_mb
                )
                candidate_volume = sum(
                    item.allocated_volume_mb for item in candidate_contacts
                )
                if (
                    candidate_volume > best_partial_volume + 1e-9
                    or (
                        abs(candidate_volume - best_partial_volume) <= 1e-9
                        and (not best_partial or candidate_cost < best_partial_cost)
                    )
                ):
                    best_partial = candidate
                    best_partial_volume = candidate_volume
                    best_partial_cost = candidate_cost
                continue
            candidate_contacts, candidate_cost, candidate_completion = _build_contacts(
                candidate, required_volume_mb
            )
            candidate_violations = _final_plan_violations(
                contacts=candidate_contacts,
                required_volume_mb=required_volume_mb,
                mission_window_start=mission_window_start,
                deadline=deadline,
                maximum_budget=maximum_budget,
            )
            if candidate_violations:
                violations = candidate_violations
                continue
            selected = candidate
            contacts = candidate_contacts
            total_cost = candidate_cost
            completion_at = candidate_completion
            violations = []
            break

        if not contacts:
            # An infeasible mission still needs a truthful best-case ledger.
            # Returning zero discarded useful work selected before the greedy
            # search hit the budget/deadline boundary and misled the UI.
            partial_contacts, partial_cost, partial_completion = _build_contacts(
                best_partial, required_volume_mb
            )
            selected_partial_ids = {item.pass_id for item in best_partial}
            unused_ids = [
                r.pass_.pass_id
                for r in only_eligible
                if r.pass_.pass_id not in selected_partial_ids
            ]
            partial_volume = sum(
                item.allocated_volume_mb for item in partial_contacts
            )
            return ContactPlan(
                plan_id=plan_id,
                mission_id=mission_id,
                scenario_id=scenario_id,
                created_at=created_at,
                preference=preference,
                status=PlanStatus.NO_FEASIBLE_PLAN_FOUND,
                contacts=partial_contacts,
                required_volume_mb=required_volume_mb,
                planned_volume_mb=partial_volume,
                estimated_total_cost=str(partial_cost),
                planned_completion_at=partial_completion,
                unused_opportunity_ids=unused_ids,
                rejected_opportunity_records=rejected,
                algorithm_version=ALGORITHM_VERSION,
                validation_violations=violations,
            )

        planned_vol = sum(c.allocated_volume_mb for c in contacts)
        selected_pass_ids = {s.pass_id for s in selected}
        unused_ids = [
            r.pass_.pass_id for r in only_eligible if r.pass_.pass_id not in selected_pass_ids
        ]

        return ContactPlan(
            plan_id=plan_id,
            mission_id=mission_id,
            scenario_id=scenario_id,
            created_at=created_at,
            preference=preference,
            status=PlanStatus.FEASIBLE,
            contacts=contacts,
            required_volume_mb=required_volume_mb,
            planned_volume_mb=planned_vol,
            estimated_total_cost=str(total_cost),
            planned_completion_at=completion_at,
            unused_opportunity_ids=unused_ids,
            rejected_opportunity_records=rejected,
            algorithm_version=ALGORITHM_VERSION,
            validation_violations=violations,
        )

    def _dispatch(
        self,
        *,
        slices: list[_Slice],
        required_volume_mb: float,
        deadline: datetime,
        mission_window_start: datetime,
        maximum_budget: Decimal,
        preference: PlanningPreference,
    ) -> tuple[list[_Slice], bool]:
        release_ts = mission_window_start.timestamp()
        deadline_ts = deadline.timestamp()

        if preference == PlanningPreference.FASTEST:

            def score(
                s: _Slice, running: Decimal, selected: list[_Slice]
            ) -> tuple[float, float, float, str]:
                inc = _incremental_cost(s, selected, running)
                return (s.end_at.timestamp(), -s.capacity_mb, float(inc), s.station_id)

        elif preference == PlanningPreference.LOWEST_COST:

            def score(
                s: _Slice, running: Decimal, selected: list[_Slice]
            ) -> tuple[float, float, float, str]:
                inc = _incremental_cost(s, selected, running)
                cap = s.capacity_mb if s.capacity_mb > 0 else 1e-9
                return (float(inc) / cap, s.end_at.timestamp(), 0.0, s.station_id)

        else:  # BALANCED

            def score(
                s: _Slice, running: Decimal, selected: list[_Slice]
            ) -> tuple[float, float, float, str]:
                inc = _incremental_cost(s, selected, running)
                n_time = _norm_time(s.end_at.timestamp(), release_ts, deadline_ts)
                n_cost = _norm_cost(running + inc, maximum_budget)
                return (
                    0.6 * n_time + 0.4 * n_cost,
                    s.end_at.timestamp(),
                    0.0,
                    s.station_id,
                )

        return _greedy_select(
            slices=slices,
            required_volume_mb=required_volume_mb,
            deadline=deadline,
            maximum_budget=maximum_budget,
            mission_window_start=mission_window_start,
            score_key=score,
            static_order=preference == PlanningPreference.FASTEST,
        )
