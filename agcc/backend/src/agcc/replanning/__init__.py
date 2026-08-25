"""Forward-only replanning, immutable proposals, and approvals (Task 14)."""

from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from agcc.domain.enums import ContactCommitment, ProposalStatus
from agcc.feasibility import RelaxationSuggestions
from agcc.planner import ContactPlan


class ForwardPlanDiff(BaseModel):
    added_contact_ids: list[str] = Field(default_factory=list)
    removed_contact_ids: list[str] = Field(default_factory=list)
    modified_contact_ids: list[str] = Field(default_factory=list)
    preserved_contact_ids: list[str] = Field(default_factory=list)
    cost_delta: str
    deadline_delta_s: float


class ReplanAlternative(BaseModel):
    kind: str
    calculated_value: str | float | list[str]
    requires_approval: bool = True


class ForwardReplanProposal(BaseModel):
    proposal_id: str
    old_plan: ContactPlan
    proposed_plan: ContactPlan | None
    diff: ForwardPlanDiff | None
    trigger: Literal["predicted_shortfall", "future_contact_unavailable", "user_requested"]
    predicted_shortfall_before_mb: float = Field(ge=0.0)
    predicted_shortfall_after_mb: float = Field(ge=0.0)
    approval_reasons: list[str] = Field(default_factory=list)
    alternatives: list[ReplanAlternative] = Field(default_factory=list)
    status: ProposalStatus = ProposalStatus.PENDING
    created_at: datetime


class ReplanDecision(BaseModel):
    proposal: ForwardReplanProposal
    active_plan: ContactPlan


class ForwardReplanner:
    def __init__(self) -> None:
        self.proposals: dict[str, ForwardReplanProposal] = {}
        self.plan_history: dict[str, ContactPlan] = {}

    def propose(
        self,
        *,
        current_plan: ContactPlan,
        candidate_plan: ContactPlan | None,
        now: datetime,
        predicted_shortfall_mb: float,
        authorized_station_ids: set[str],
        suggestions: RelaxationSuggestions | None = None,
        trigger: Literal[
            "predicted_shortfall", "future_contact_unavailable", "user_requested"
        ] = "predicted_shortfall",
    ) -> ForwardReplanProposal | None:
        if trigger == "predicted_shortfall" and predicted_shortfall_mb <= 0.0:
            return None
        self.plan_history[current_plan.plan_id] = current_plan.model_copy(deep=True)
        alternatives = _alternatives(suggestions)
        diff = None
        approval_reasons: list[str] = []
        shortfall_after = predicted_shortfall_mb
        if candidate_plan is not None:
            _validate_forward_only(current_plan, candidate_plan, now)
            diff = _diff(current_plan, candidate_plan)
            approval_reasons = _approval_reasons(
                current_plan, candidate_plan, diff, authorized_station_ids
            )
            if candidate_plan.planned_volume_mb >= candidate_plan.required_volume_mb:
                shortfall_after = 0.0
        digest = hashlib.sha256(
            f"{current_plan.plan_id}|{now.isoformat()}|{predicted_shortfall_mb}".encode()
        ).hexdigest()[:16]
        proposal = ForwardReplanProposal(
            proposal_id=f"proposal_{digest}",
            old_plan=current_plan.model_copy(deep=True),
            proposed_plan=candidate_plan.model_copy(deep=True) if candidate_plan else None,
            diff=diff,
            trigger=trigger,
            predicted_shortfall_before_mb=predicted_shortfall_mb,
            predicted_shortfall_after_mb=shortfall_after,
            approval_reasons=approval_reasons,
            alternatives=alternatives,
            created_at=now,
        )
        self.proposals[proposal.proposal_id] = proposal
        return proposal

    def approve(self, proposal_id: str) -> ReplanDecision:
        proposal = self.proposals[proposal_id]
        if proposal.status != ProposalStatus.PENDING or proposal.proposed_plan is None:
            raise ValueError("Only a pending proposal with a plan may be approved")
        old = proposal.old_plan
        candidate = proposal.proposed_plan
        active = candidate.model_copy(
            update={
                "version": old.version + 1,
                "parent_plan_id": old.plan_id,
                "contacts": [
                    contact.model_copy(update={"commitment": ContactCommitment.COMMITTED})
                    for contact in candidate.contacts
                ],
            }
        )
        decided = proposal.model_copy(
            update={"status": ProposalStatus.APPROVED, "proposed_plan": active}
        )
        self.proposals[proposal_id] = decided
        self.plan_history[active.plan_id] = active.model_copy(deep=True)
        return ReplanDecision(proposal=decided, active_plan=active)

    def reject(self, proposal_id: str) -> ReplanDecision:
        proposal = self.proposals[proposal_id]
        if proposal.status != ProposalStatus.PENDING:
            raise ValueError("Only a pending proposal may be rejected")
        decided = proposal.model_copy(update={"status": ProposalStatus.REJECTED})
        self.proposals[proposal_id] = decided
        return ReplanDecision(proposal=decided, active_plan=proposal.old_plan)


def _validate_forward_only(old: ContactPlan, new: ContactPlan, now: datetime) -> None:
    new_by_id = {item.contact_id: item for item in new.contacts}
    for contact in old.contacts:
        if contact.start_at <= now:
            replacement = new_by_id.get(contact.contact_id)
            if replacement != contact:
                raise ValueError(f"Past or current contact is immutable: {contact.contact_id}")


def _diff(old: ContactPlan, new: ContactPlan) -> ForwardPlanDiff:
    old_by_id = {item.contact_id: item for item in old.contacts}
    new_by_id = {item.contact_id: item for item in new.contacts}
    common = old_by_id.keys() & new_by_id.keys()
    return ForwardPlanDiff(
        added_contact_ids=sorted(new_by_id.keys() - old_by_id.keys()),
        removed_contact_ids=sorted(old_by_id.keys() - new_by_id.keys()),
        modified_contact_ids=sorted(key for key in common if old_by_id[key] != new_by_id[key]),
        preserved_contact_ids=sorted(key for key in common if old_by_id[key] == new_by_id[key]),
        cost_delta=str(Decimal(new.estimated_total_cost) - Decimal(old.estimated_total_cost)),
        deadline_delta_s=(
            (new.planned_completion_at - old.planned_completion_at).total_seconds()
            if new.planned_completion_at and old.planned_completion_at
            else 0.0
        ),
    )


def _approval_reasons(
    old: ContactPlan,
    new: ContactPlan,
    diff: ForwardPlanDiff,
    authorized_station_ids: set[str],
) -> list[str]:
    reasons: list[str] = []
    if diff.added_contact_ids:
        reasons.append("adds_contact")
    if Decimal(new.estimated_total_cost) > Decimal(old.estimated_total_cost):
        reasons.append("increases_approved_cost")
    added = {item.contact_id: item for item in new.contacts}
    if any(added[item].station_id not in authorized_station_ids for item in diff.added_contact_ids):
        reasons.append("uses_unauthorized_station")
    if diff.removed_contact_ids or diff.modified_contact_ids:
        reasons.append("replaces_future_commitment")
    return reasons


def _alternatives(suggestions: RelaxationSuggestions | None) -> list[ReplanAlternative]:
    if suggestions is None:
        return []
    result: list[ReplanAlternative] = []
    if suggestions.additional_budget_needed is not None:
        result.append(ReplanAlternative(
            kind="add_budget", calculated_value=suggestions.additional_budget_needed
        ))
    if suggestions.deadline_extension_s is not None:
        result.append(ReplanAlternative(
            kind="extend_deadline", calculated_value=suggestions.deadline_extension_s
        ))
    if suggestions.excluded_station_candidates:
        result.append(ReplanAlternative(
            kind="expand_station_authorization",
            calculated_value=suggestions.excluded_station_candidates,
        ))
    if suggestions.required_volume_reduction_mb is not None:
        result.append(ReplanAlternative(
            kind="reduce_target_last",
            calculated_value=suggestions.required_volume_reduction_mb,
        ))
    return result


__all__ = [
    "ForwardPlanDiff", "ForwardReplanProposal", "ForwardReplanner", "ReplanAlternative",
    "ReplanDecision",
]
