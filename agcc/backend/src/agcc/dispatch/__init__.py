"""Fragment queue and automatic data redistribution (Task 11).

Architecture
------------
FragmentState       — lifecycle states for a data fragment.
Fragment            — one slice of mission data, max 25 MB, deterministic ID.
ContactAllocation   — how much of a contact's planned capacity is assigned to
                      which fragments.
DispatchPlan        — the initial mapping of fragments to contacts, built from
                      a feasible ContactPlan.
ResidualShortfall   — emitted when redistribution exhausts all future capacity
                      and remainder > 0; triggers replanning.
DispatchBuilder     — pure function: ContactPlan → DispatchPlan.
DispatchRedistributor
                    — pure function: records actual delivery for contact k and
                      returns an updated DispatchPlan (plus optional shortfall).

Fragment rules
--------------
- Maximum fragment size: 25 MB.
- Final fragment may be smaller.
- Fragment IDs are deterministic: fragment_{mission_id_body}_{seq:06d}
- Fragment state machine: QUEUED → ASSIGNED → TRANSMITTING → DELIVERED / PARTIAL / FAILED
- A fragment may be split on partial delivery; the remainder requeues with the
  same parent_fragment_id.

Redistribution invariants
-------------------------
    required = delivered + transmitting + assigned + queued
    delivered never decreases
    past assignments never change
    fragment volumes remain positive

Volume conservation design note
--------------------------------
Fragment volume is fixed at creation. Conservation is maintained by state
transitions only — never by creating new volume:

  DELIVERED: counted in delivered_mb
  ASSIGNED / TRANSMITTING: counted in assigned_mb
  QUEUED / PARTIAL: counted in queued_mb

On partial delivery of contact k:
  - Fragments that were fully delivered → DELIVERED
  - Fragments that were only partially delivered → PARTIAL  (still counted in queued_mb)
  - Unstarted fragments assigned to contact k → QUEUED     (still counted in queued_mb)
  The sum queued_mb now contains the remainder for redistribution.
  Redistribution reassigns QUEUED/PARTIAL fragments to future contacts (ASSIGNED).
  No new Fragment objects are created; volumes never change.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, Field, field_validator, model_validator

from agcc.domain.common import _require_utc, _validate_id
from agcc.planner import ContactPlan, PlannedContact

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MAX_FRAGMENT_MB: float = 25.0

# ---------------------------------------------------------------------------
# FragmentState
# ---------------------------------------------------------------------------


class FragmentState(str, Enum):
    QUEUED = "queued"
    ASSIGNED = "assigned"
    TRANSMITTING = "transmitting"
    DELIVERED = "delivered"
    PARTIAL = "partial"
    FAILED = "failed"


# ---------------------------------------------------------------------------
# Fragment
# ---------------------------------------------------------------------------


class Fragment(BaseModel):
    """One slice of mission data queued for downlink.

    fragment_id is deterministic: fragment_{mission_id_body}_{seq:06d}
    parent_fragment_id is set when this fragment is a remainder after a partial
    delivery; it points to the original fragment that was split.
    """

    model_config = {"frozen": True}

    fragment_id: str = Field(description="Unique fragment ID (prefix: fragment_)")
    mission_id: str
    sequence_number: int = Field(ge=0, description="Zero-based sequence within the mission")
    volume_mb: float = Field(gt=0.0, description="Fragment data volume in decimal MB")
    state: FragmentState = FragmentState.QUEUED
    # contact this fragment is assigned/transmitting/delivered to
    assigned_contact_id: str | None = None
    # non-None when this is a remainder produced by a partial delivery
    parent_fragment_id: str | None = None

    @field_validator("fragment_id", mode="before")
    @classmethod
    def _check_id(cls, v: str) -> str:
        return _validate_id("fragment_", v)


# ---------------------------------------------------------------------------
# ID helper
# ---------------------------------------------------------------------------


def _fragment_id(mission_id: str, seq: int) -> str:
    """Deterministic fragment ID from mission ID and sequence number.

    Strips the 'mission_' prefix from mission_id for brevity, then hashes
    mission_id + seq to ensure uniqueness even across long sequences.
    """
    body = mission_id.removeprefix("mission_")
    digest = hashlib.sha256(f"fragment|{mission_id}|{seq}".encode()).hexdigest()[:12]
    return f"fragment_{body}_{seq:06d}_{digest}"


def _split_volume_into_fragments(
    mission_id: str,
    total_mb: float,
    start_seq: int = 0,
    parent_fragment_id: str | None = None,
) -> list[Fragment]:
    """Split total_mb into fragments of at most MAX_FRAGMENT_MB each."""
    fragments: list[Fragment] = []
    remaining = total_mb
    seq = start_seq
    while remaining > 0.0:
        vol = min(remaining, MAX_FRAGMENT_MB)
        fid = _fragment_id(mission_id, seq)
        fragments.append(
            Fragment(
                fragment_id=fid,
                mission_id=mission_id,
                sequence_number=seq,
                volume_mb=vol,
                state=FragmentState.QUEUED,
                parent_fragment_id=parent_fragment_id,
            )
        )
        remaining -= vol
        seq += 1
    return fragments


# ---------------------------------------------------------------------------
# ContactAllocation
# ---------------------------------------------------------------------------


class ContactAllocation(BaseModel):
    """The fragment assignments for a single planned contact.

    planned_volume_mb  — from the ContactPlan; the contact's approved capacity.
    assigned_volume_mb — sum of fragment volumes currently assigned to this contact.
    delivered_volume_mb— actual delivered volume (set during redistribution).
    fragment_ids       — ordered list of fragment IDs assigned to this contact.
    """

    model_config = {"frozen": True}

    contact_id: str
    planned_volume_mb: float = Field(ge=0.0)
    assigned_volume_mb: float = Field(ge=0.0)
    delivered_volume_mb: float = Field(default=0.0, ge=0.0)
    fragment_ids: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# ResidualShortfall
# ---------------------------------------------------------------------------


class ResidualShortfall(BaseModel):
    """Emitted when redistribution exhausts all future planned capacity.

    shortfall_mb > 0 means the mission cannot be completed without new contacts.
    """

    model_config = {"frozen": True}

    mission_id: str
    shortfall_mb: float = Field(gt=0.0)
    after_contact_id: str = Field(
        description="The contact whose under-delivery triggered redistribution"
    )
    produced_at: datetime

    @field_validator("produced_at", mode="before")
    @classmethod
    def _check_utc(cls, v: object) -> object:
        return _require_utc(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# DispatchPlan
# ---------------------------------------------------------------------------


class DispatchPlan(BaseModel):
    """Full dispatch state for a scenario mission.

    Invariant:
        required_volume_mb == delivered_mb + transmitting_mb + assigned_mb + queued_mb
    """

    model_config = {"frozen": True}

    mission_id: str
    plan_id: str
    required_volume_mb: float = Field(ge=0.0)

    # Ordered list of all fragments (initial + remainder fragments from splits)
    fragments: list[Fragment] = Field(default_factory=list)

    # One allocation per contact, in chronological order
    allocations: list[ContactAllocation] = Field(default_factory=list)

    # Running totals (computed, stored for fast access)
    delivered_mb: float = Field(default=0.0, ge=0.0)
    assigned_mb: float = Field(default=0.0, ge=0.0)
    queued_mb: float = Field(default=0.0, ge=0.0)

    # Predicted completion time (end_at of the last contact expected to finish delivery)
    predicted_completion_at: datetime | None = None

    @field_validator("predicted_completion_at", mode="before")
    @classmethod
    def _check_utc(cls, v: object) -> object:
        if v is None:
            return v
        return _require_utc(v)  # type: ignore[arg-type]

    @model_validator(mode="after")
    def _check_volume_conservation(self) -> DispatchPlan:
        total = self.delivered_mb + self.assigned_mb + self.queued_mb
        if abs(total - self.required_volume_mb) > 1e-6:
            raise ValueError(
                f"Volume conservation violated: "
                f"delivered({self.delivered_mb}) + assigned({self.assigned_mb}) + "
                f"queued({self.queued_mb}) = {total} "
                f"!= required({self.required_volume_mb})"
            )
        return self


# ---------------------------------------------------------------------------
# DispatchBuilder — ContactPlan → DispatchPlan
# ---------------------------------------------------------------------------


class DispatchBuilder:
    """Builds the initial DispatchPlan from a feasible ContactPlan.

    Fragments are created (max 25 MB each), then assigned chronologically
    to contacts according to their planned_volume_mb allocations.
    """

    def build(self, plan: ContactPlan) -> DispatchPlan:
        """Convert a feasible ContactPlan into a DispatchPlan.

        Raises
        ------
        ValueError
            If plan.status is not FEASIBLE.
        """
        from agcc.planner import PlanStatus

        if plan.status != PlanStatus.FEASIBLE:
            raise ValueError(
                f"Cannot build DispatchPlan from non-feasible plan (status={plan.status})"
            )

        mission_id = plan.mission_id
        required_mb = plan.required_volume_mb

        # 1. Create fragment queue
        all_fragments = _split_volume_for_contacts(mission_id, required_mb, plan.contacts)

        # 2. Assign fragments to contacts chronologically
        allocations, assigned_fragments = _assign_to_contacts(all_fragments, plan.contacts)

        # Fragments map: fragment_id → Fragment (possibly in ASSIGNED state)
        frag_map = {f.fragment_id: f for f in all_fragments}
        for fid, contact_id in assigned_fragments.items():
            old = frag_map[fid]
            frag_map[fid] = Fragment(
                fragment_id=old.fragment_id,
                mission_id=old.mission_id,
                sequence_number=old.sequence_number,
                volume_mb=old.volume_mb,
                state=FragmentState.ASSIGNED,
                assigned_contact_id=contact_id,
                parent_fragment_id=old.parent_fragment_id,
            )

        fragments = list(frag_map.values())

        # 3. Compute summary totals
        delivered_mb = 0.0
        assigned_mb = sum(f.volume_mb for f in fragments if f.state == FragmentState.ASSIGNED)
        queued_mb = sum(f.volume_mb for f in fragments if f.state == FragmentState.QUEUED)

        # 4. Predicted completion: end_at of last contact that has an allocation
        predicted_completion = _predicted_completion(allocations, plan.contacts)

        return DispatchPlan(
            mission_id=mission_id,
            plan_id=plan.plan_id,
            required_volume_mb=required_mb,
            fragments=fragments,
            allocations=allocations,
            delivered_mb=delivered_mb,
            assigned_mb=assigned_mb,
            queued_mb=queued_mb,
            predicted_completion_at=predicted_completion,
        )


# ---------------------------------------------------------------------------
# DispatchRedistributor — record delivery and redistribute remainder
# ---------------------------------------------------------------------------


class DispatchRedistributor:
    """Records the actual delivered volume for a contact and redistributes.

    Redistribution fills unused capacity in future contacts chronologically,
    without adding contacts or changing approved cost/time reservations.

    Returns (updated_plan, shortfall_or_None).
    """

    def record_delivery(
        self,
        dispatch: DispatchPlan,
        contact_id: str,
        actual_delivered_mb: float,
        delivered_at: datetime,
        contacts_in_order: Sequence[PlannedContact],
    ) -> tuple[DispatchPlan, ResidualShortfall | None]:
        """Record actual delivery for ``contact_id`` and redistribute remainder.

        Parameters
        ----------
        dispatch:
            Current DispatchPlan.
        contact_id:
            The contact that just executed (may have under-delivered).
        actual_delivered_mb:
            How much was actually delivered during this contact (>= 0).
        delivered_at:
            UTC timestamp of delivery completion.
        contacts_in_order:
            All planned contacts in chronological order (used to find future ones).

        Returns
        -------
        (updated_dispatch, shortfall)
            shortfall is None when all remaining volume fits in future contacts.
        """
        # Validate
        if actual_delivered_mb < 0.0:
            raise ValueError("actual_delivered_mb must be >= 0")

        # Find this contact's current allocation
        alloc_index = _find_alloc_index(dispatch, contact_id)
        if alloc_index is None:
            raise ValueError(f"contact_id {contact_id!r} not found in dispatch plan")

        alloc = dispatch.allocations[alloc_index]
        planned_vol = alloc.planned_volume_mb
        # Clamp: cannot deliver more than planned
        actual_delivered_mb = min(actual_delivered_mb, planned_vol)

        # 1. Transition fragments for this contact: DELIVERED / PARTIAL / QUEUED.
        #    Returns the list of undelivered fragments (PARTIAL + QUEUED) for redistribution.
        fragments_mut = {f.fragment_id: f for f in dispatch.fragments}
        undelivered = _settle_contact_fragments(
            alloc=alloc,
            actual_delivered_mb=actual_delivered_mb,
            fragments_mut=fragments_mut,
        )

        # 2. Update this contact's allocation to reflect actual delivery.
        new_alloc = ContactAllocation(
            contact_id=contact_id,
            planned_volume_mb=planned_vol,
            assigned_volume_mb=actual_delivered_mb,
            delivered_volume_mb=actual_delivered_mb,
            fragment_ids=alloc.fragment_ids,
        )
        new_allocations = list(dispatch.allocations)
        new_allocations[alloc_index] = new_alloc

        # 3. Redistribute undelivered fragments into future contacts.
        future_indices = list(range(alloc_index + 1, len(new_allocations)))
        residual, new_allocations, fragments_mut = _redistribute(
            undelivered_frags=undelivered,
            allocations=new_allocations,
            future_indices=future_indices,
            fragments_mut=fragments_mut,
        )

        # 5. Rebuild fragment list and totals
        all_frags = list(fragments_mut.values())
        delivered_mb = sum(f.volume_mb for f in all_frags if f.state == FragmentState.DELIVERED)
        assigned_mb = sum(
            f.volume_mb
            for f in all_frags
            if f.state in (FragmentState.ASSIGNED, FragmentState.TRANSMITTING)
        )
        queued_mb = sum(
            f.volume_mb
            for f in all_frags
            if f.state in (FragmentState.QUEUED, FragmentState.PARTIAL)
        )

        # 6. Predicted completion: end_at of last contact with assigned volume
        predicted = _predicted_completion(new_allocations, list(contacts_in_order))

        shortfall: ResidualShortfall | None = None
        if residual > 1e-9:
            shortfall = ResidualShortfall(
                mission_id=dispatch.mission_id,
                shortfall_mb=residual,
                after_contact_id=contact_id,
                produced_at=delivered_at,
            )

        updated = DispatchPlan(
            mission_id=dispatch.mission_id,
            plan_id=dispatch.plan_id,
            required_volume_mb=dispatch.required_volume_mb,
            fragments=all_frags,
            allocations=new_allocations,
            delivered_mb=delivered_mb,
            assigned_mb=assigned_mb,
            queued_mb=queued_mb,
            predicted_completion_at=predicted,
        )
        return updated, shortfall


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _assign_to_contacts(
    fragments: list[Fragment],
    contacts: list[PlannedContact],
) -> tuple[list[ContactAllocation], dict[str, str]]:
    """Assign fragments to contacts in chronological order.

    Returns
    -------
    allocations:
        One ContactAllocation per contact with fragment_ids filled.
    assigned:
        Dict mapping fragment_id → contact_id for fragments that got assigned.
    """
    allocations: list[ContactAllocation] = []
    assigned: dict[str, str] = {}  # fragment_id → contact_id

    # Fragments are iterated in order; we fill each contact's planned volume
    frag_iter = iter(fragments)
    current_frag: Fragment | None = next(frag_iter, None)
    frag_remaining: float = current_frag.volume_mb if current_frag else 0.0

    for contact in contacts:
        budget = contact.allocated_volume_mb
        filled = 0.0
        contact_frags: list[str] = []

        while current_frag is not None and filled < budget - 1e-9:
            space = budget - filled
            if frag_remaining <= space + 1e-9:
                # Whole fragment fits
                contact_frags.append(current_frag.fragment_id)
                assigned[current_frag.fragment_id] = contact.contact_id
                filled += frag_remaining
                current_frag = next(frag_iter, None)
                frag_remaining = current_frag.volume_mb if current_frag else 0.0
            else:
                raise ValueError(
                    "Fragment crosses a contact boundary; fragmentation is inconsistent"
                )

        allocations.append(
            ContactAllocation(
                contact_id=contact.contact_id,
                planned_volume_mb=(
                    contact.reserved_capacity_mb
                    if contact.reserved_capacity_mb is not None
                    else contact.allocated_volume_mb
                ),
                assigned_volume_mb=min(filled, contact.allocated_volume_mb),
                fragment_ids=contact_frags,
            )
        )

    return allocations, assigned


def _split_volume_for_contacts(
    mission_id: str,
    total_mb: float,
    contacts: Sequence[PlannedContact],
) -> list[Fragment]:
    """Fragment data without crossing an initial contact boundary."""
    fragments: list[Fragment] = []
    remaining = total_mb
    sequence = 0
    for contact in sorted(contacts, key=lambda item: item.start_at):
        contact_remaining = min(contact.allocated_volume_mb, remaining)
        while contact_remaining > 1e-9:
            volume = min(MAX_FRAGMENT_MB, contact_remaining)
            fragments.append(
                Fragment(
                    fragment_id=_fragment_id(mission_id, sequence),
                    mission_id=mission_id,
                    sequence_number=sequence,
                    volume_mb=volume,
                )
            )
            sequence += 1
            contact_remaining -= volume
            remaining -= volume
    if remaining > 1e-6:
        fragments.extend(_split_volume_into_fragments(mission_id, remaining, sequence))
    return fragments


def _find_alloc_index(dispatch: DispatchPlan, contact_id: str) -> int | None:
    for i, alloc in enumerate(dispatch.allocations):
        if alloc.contact_id == contact_id:
            return i
    return None


def _settle_contact_fragments(
    alloc: ContactAllocation,
    actual_delivered_mb: float,
    fragments_mut: dict[str, Fragment],
) -> list[Fragment]:
    """Transition fragments for this contact based on actual delivery.

    - Fragments fully covered → DELIVERED.
    - First underfilled fragment → PARTIAL (stays in queued_mb).
    - Remaining fragments → QUEUED, unassigned (stay in queued_mb).

    Returns the list of undelivered fragments (PARTIAL then remaining QUEUED),
    in original order, ready for redistribution. No new volume is created.
    """
    remaining_delivered = actual_delivered_mb
    undelivered: list[Fragment] = []

    for fid in alloc.fragment_ids:
        frag = fragments_mut.get(fid)
        if frag is None:
            continue
        if frag.state == FragmentState.DELIVERED:
            remaining_delivered = max(0.0, remaining_delivered - frag.volume_mb)
            continue
        if remaining_delivered >= frag.volume_mb - 1e-9:
            # Fully delivered
            fragments_mut[fid] = Fragment(
                fragment_id=frag.fragment_id,
                mission_id=frag.mission_id,
                sequence_number=frag.sequence_number,
                volume_mb=frag.volume_mb,
                state=FragmentState.DELIVERED,
                assigned_contact_id=frag.assigned_contact_id,
                parent_fragment_id=frag.parent_fragment_id,
            )
            remaining_delivered -= frag.volume_mb
        elif remaining_delivered > 1e-9:
            delivered_part = remaining_delivered
            remainder_volume = frag.volume_mb - delivered_part
            fragments_mut[fid] = frag.model_copy(
                update={"volume_mb": delivered_part, "state": FragmentState.DELIVERED}
            )
            next_sequence = max(f.sequence_number for f in fragments_mut.values()) + 1
            remainder = Fragment(
                fragment_id=_fragment_id(frag.mission_id, next_sequence),
                mission_id=frag.mission_id,
                sequence_number=next_sequence,
                volume_mb=remainder_volume,
                state=FragmentState.PARTIAL,
                parent_fragment_id=frag.parent_fragment_id or frag.fragment_id,
            )
            fragments_mut[remainder.fragment_id] = remainder
            undelivered.append(remainder)
            remaining_delivered = 0.0
        else:
            queued = frag.model_copy(
                update={"state": FragmentState.QUEUED, "assigned_contact_id": None}
            )
            fragments_mut[fid] = queued
            undelivered.append(queued)

    return undelivered


def _redistribute(
    undelivered_frags: list[Fragment],
    allocations: list[ContactAllocation],
    future_indices: list[int],
    fragments_mut: dict[str, Fragment],
) -> tuple[float, list[ContactAllocation], dict[str, Fragment]]:
    """Reassign undelivered fragments into spare capacity of future contacts.

    No new volume is created — only state transitions (QUEUED/PARTIAL → ASSIGNED).
    Returns (residual_mb, updated_allocations, updated_fragments_mut).
    residual_mb > 0 means some volume could not be placed (→ ResidualShortfall).
    """
    if not undelivered_frags:
        return 0.0, allocations, fragments_mut

    rem_queue = list(undelivered_frags)
    new_allocations = list(allocations)

    for idx in future_indices:
        if not rem_queue:
            break
        alloc = new_allocations[idx]
        spare = alloc.planned_volume_mb - alloc.assigned_volume_mb

        if spare <= 1e-9:
            continue

        new_frag_ids = list(alloc.fragment_ids)
        added_vol = 0.0
        still_needed: list[Fragment] = []

        for frag in rem_queue:
            if spare <= 1e-9:
                still_needed.append(frag)
                continue
            if frag.volume_mb <= spare + 1e-9:
                # Whole fragment fits — reassign to this future contact
                new_frag_ids.append(frag.fragment_id)
                added_vol += frag.volume_mb
                spare -= frag.volume_mb
                fragments_mut[frag.fragment_id] = Fragment(
                    fragment_id=frag.fragment_id,
                    mission_id=frag.mission_id,
                    sequence_number=frag.sequence_number,
                    volume_mb=frag.volume_mb,
                    state=FragmentState.ASSIGNED,
                    assigned_contact_id=alloc.contact_id,
                    parent_fragment_id=frag.parent_fragment_id,
                )
            else:
                assigned_piece = frag.model_copy(
                    update={
                        "volume_mb": spare,
                        "state": FragmentState.ASSIGNED,
                        "assigned_contact_id": alloc.contact_id,
                    }
                )
                fragments_mut[frag.fragment_id] = assigned_piece
                new_frag_ids.append(frag.fragment_id)
                added_vol += spare
                next_sequence = max(item.sequence_number for item in fragments_mut.values()) + 1
                remainder = Fragment(
                    fragment_id=_fragment_id(frag.mission_id, next_sequence),
                    mission_id=frag.mission_id,
                    sequence_number=next_sequence,
                    volume_mb=frag.volume_mb - spare,
                    state=FragmentState.PARTIAL,
                    parent_fragment_id=frag.parent_fragment_id or frag.fragment_id,
                )
                fragments_mut[remainder.fragment_id] = remainder
                still_needed.append(remainder)
                spare = 0.0

        rem_queue = still_needed

        if added_vol > 1e-9:
            new_allocations[idx] = ContactAllocation(
                contact_id=alloc.contact_id,
                planned_volume_mb=alloc.planned_volume_mb,
                assigned_volume_mb=alloc.assigned_volume_mb + added_vol,
                delivered_volume_mb=alloc.delivered_volume_mb,
                fragment_ids=new_frag_ids,
            )

    residual = sum(f.volume_mb for f in rem_queue)
    return residual, new_allocations, fragments_mut


def _predicted_completion(
    allocations: list[ContactAllocation],
    contacts: list[PlannedContact],
) -> datetime | None:
    """Return end_at of the last contact that has assigned volume > 0."""
    contact_map = {c.contact_id: c for c in contacts}
    last: datetime | None = None
    for alloc in allocations:
        if alloc.assigned_volume_mb > 1e-9:
            c = contact_map.get(alloc.contact_id)
            if c is not None:
                if last is None or c.end_at > last:
                    last = c.end_at
    return last
