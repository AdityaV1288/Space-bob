from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agcc.domain.enums import ContactCommitment, ProposalStatus
from agcc.domain.mission import PlanningPreference
from agcc.feasibility import RelaxationSuggestions
from agcc.planner import ContactPlan, PlannedContact, PlanStatus
from agcc.replanning import ForwardReplanner

NOW = datetime(2026, 8, 21, 2, tzinfo=UTC)


def contact(
    ident: str,
    offset_h: int,
    station: str = "station_authorized",
    volume: float = 100.0,
) -> PlannedContact:
    start = NOW + timedelta(hours=offset_h)
    return PlannedContact(
        contact_id=f"contact_{ident}",
        pass_id=f"pass_{ident}",
        station_id=station,
        start_at=start,
        end_at=start + timedelta(minutes=10),
        duration_s=600,
        allocated_volume_mb=volume,
        reserved_capacity_mb=volume,
        contact_cost_decimal="10.00",
        commitment=ContactCommitment.COMMITTED,
    )


def plan(ident: str, contacts: list[PlannedContact], cost: str = "20.00") -> ContactPlan:
    return ContactPlan(
        plan_id=f"plan_{ident}",
        version=1,
        mission_id="mission_test",
        scenario_id="scenario_test",
        created_at=NOW - timedelta(hours=2),
        preference=PlanningPreference.FASTEST,
        status=PlanStatus.FEASIBLE,
        contacts=contacts,
        required_volume_mb=sum(item.allocated_volume_mb for item in contacts),
        planned_volume_mb=sum(item.allocated_volume_mb for item in contacts),
        estimated_total_cost=cost,
        planned_completion_at=contacts[-1].end_at,
    )


def test_zero_shortfall_does_not_trigger_replanning() -> None:
    old = plan("old", [contact("one", 1)])
    assert ForwardReplanner().propose(
        current_plan=old,
        candidate_plan=old,
        now=NOW,
        predicted_shortfall_mb=0,
        authorized_station_ids={"station_authorized"},
    ) is None


def test_added_contact_proposal_records_exact_approval_reasons() -> None:
    old = plan("old", [contact("one", 1)])
    candidate = plan("new", [old.contacts[0], contact("two", 2)], "30.00")
    proposal = ForwardReplanner().propose(
        current_plan=old,
        candidate_plan=candidate,
        now=NOW,
        predicted_shortfall_mb=20,
        authorized_station_ids={"station_authorized"},
    )
    assert proposal is not None
    assert proposal.diff is not None
    assert proposal.diff.added_contact_ids == ["contact_two"]
    assert proposal.approval_reasons == ["adds_contact", "increases_approved_cost"]


def test_unauthorized_station_is_an_explicit_approval_reason() -> None:
    old = plan("old", [contact("one", 1)])
    candidate = plan("new", [old.contacts[0], contact("two", 2, "station_external")])
    proposal = ForwardReplanner().propose(
        current_plan=old,
        candidate_plan=candidate,
        now=NOW,
        predicted_shortfall_mb=20,
        authorized_station_ids={"station_authorized"},
    )
    assert proposal is not None
    assert "uses_unauthorized_station" in proposal.approval_reasons


def test_past_or_current_contact_cannot_change() -> None:
    past = contact("past", -1)
    old = plan("old", [past, contact("future", 2)])
    changed = past.model_copy(update={"allocated_volume_mb": 99.0})
    candidate = plan("new", [changed, old.contacts[1]])
    with pytest.raises(ValueError, match="immutable"):
        ForwardReplanner().propose(
            current_plan=old,
            candidate_plan=candidate,
            now=NOW,
            predicted_shortfall_mb=1,
            authorized_station_ids={"station_authorized"},
        )


def test_approval_creates_child_version_without_mutating_old_plan() -> None:
    old = plan("old", [contact("one", 1)])
    candidate = plan("new", [old.contacts[0], contact("two", 2)])
    replanner = ForwardReplanner()
    proposal = replanner.propose(
        current_plan=old,
        candidate_plan=candidate,
        now=NOW,
        predicted_shortfall_mb=10,
        authorized_station_ids={"station_authorized"},
    )
    assert proposal is not None
    decision = replanner.approve(proposal.proposal_id)
    assert decision.active_plan.parent_plan_id == old.plan_id
    assert decision.active_plan.version == old.version + 1
    assert replanner.plan_history[old.plan_id] == old
    assert decision.proposal.status == ProposalStatus.APPROVED


def test_rejection_keeps_current_plan_unchanged() -> None:
    old = plan("old", [contact("one", 1)])
    candidate = plan("new", [old.contacts[0], contact("two", 2)])
    replanner = ForwardReplanner()
    proposal = replanner.propose(
        current_plan=old,
        candidate_plan=candidate,
        now=NOW,
        predicted_shortfall_mb=10,
        authorized_station_ids={"station_authorized"},
    )
    assert proposal is not None
    decision = replanner.reject(proposal.proposal_id)
    assert decision.active_plan == old
    assert decision.proposal.status == ProposalStatus.REJECTED


def test_infeasible_proposal_returns_calculated_alternatives() -> None:
    old = plan("old", [contact("one", 1)])
    suggestions = RelaxationSuggestions(
        deadline_extension_s=600,
        additional_budget_needed="12.50",
        excluded_station_candidates=["station_external"],
        required_volume_reduction_mb=5,
    )
    proposal = ForwardReplanner().propose(
        current_plan=old,
        candidate_plan=None,
        now=NOW,
        predicted_shortfall_mb=5,
        authorized_station_ids={"station_authorized"},
        suggestions=suggestions,
    )
    assert proposal is not None
    assert [item.kind for item in proposal.alternatives] == [
        "add_budget", "extend_deadline", "expand_station_authorization", "reduce_target_last"
    ]
