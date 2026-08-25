from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from agcc.anomalies import AnomalyContext, AnomalyService, ParsedAnomalyIntent
from agcc.dispatch import DispatchBuilder, DispatchRedistributor
from agcc.domain.enums import AnomalyType, ContactCommitment, ProposalStatus
from agcc.domain.mission import PlanningPreference
from agcc.feasibility import RelaxationSuggestions
from agcc.planner import ContactPlan, PlannedContact, PlanStatus
from agcc.replanning import ForwardReplanner
from agcc.simulation import SessionEventStore, SimulationEngine

NOW = datetime(2026, 8, 21, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[3] / "data" / "fixtures" / "scenarios"


def fixture(name: str) -> dict[str, object]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def test_fixture_manifest_hashes_and_model_versions_are_current() -> None:
    manifest = fixture("manifest.json")
    hashes = manifest["fixture_sha256"]
    assert isinstance(hashes, dict)
    for name, expected in hashes.items():
        actual = hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest()
        assert actual == expected
    assert manifest["model_versions"] == {
        "planner": "planner_v1",
        "capacity": "capacity_v1",
        "simulation": "task12",
        "anomaly_policy": "anomaly-policies.v1",
    }


def contact(
    ident: str, offset_min: int, allocated: float, reserved: float | None = None
) -> PlannedContact:
    start = NOW + timedelta(minutes=offset_min)
    return PlannedContact(
        contact_id=f"contact_{ident}", pass_id=f"pass_{ident}",
        station_id="station_alpha", start_at=start, end_at=start + timedelta(minutes=10),
        duration_s=600, allocated_volume_mb=allocated,
        reserved_capacity_mb=reserved if reserved is not None else allocated,
        contact_cost_decimal="10.00", commitment=ContactCommitment.COMMITTED,
    )


def plan(ident: str, contacts: list[PlannedContact], required: float = 500) -> ContactPlan:
    total = sum(item.allocated_volume_mb for item in contacts)
    return ContactPlan(
        plan_id=f"plan_{ident}", version=1, mission_id="mission_golden",
        scenario_id="scenario_golden", created_at=NOW,
        preference=PlanningPreference.FASTEST, status=PlanStatus.FEASIBLE,
        contacts=contacts, required_volume_mb=required, planned_volume_mb=total,
        estimated_total_cost=str(Decimal("10") * len(contacts)),
        planned_completion_at=contacts[-1].end_at,
    )


def test_scenario_a_feasible_plan_and_execution_reach_exact_target() -> None:
    expected = fixture("scenario_a_feasible.json")["expected"]
    baseline = plan("a", [contact("a1", 10, 500)])
    store = SessionEventStore()
    engine = SimulationEngine(
        plan=baseline, dispatch=DispatchBuilder().build(baseline), required_volume_mb=500,
        deadline=NOW + timedelta(days=1), base_rate_mbps=0, protocol_efficiency=1,
        store=store, rate_provider=lambda _contact, _at: 8.0,
    )
    engine.start(NOW)
    start = baseline.contacts[0].start_at
    for second in range(501):
        engine.tick(start + timedelta(seconds=second))
        if engine.is_finished:
            break
    assert baseline.planned_volume_mb == expected["planned_mb"]  # type: ignore[index]
    assert engine.delivered_mb == expected["delivered_mb"]  # type: ignore[index]


def test_scenario_b_automatic_redistribution_uses_only_committed_spare() -> None:
    baseline = plan("b", [contact("b1", 10, 200), contact("b2", 30, 300, 350)])
    updated, shortfall = DispatchRedistributor().record_delivery(
        DispatchBuilder().build(baseline), "contact_b1", 150, NOW + timedelta(minutes=20),
        baseline.contacts,
    )
    assert shortfall is None
    assert updated.allocations[1].assigned_volume_mb == 350
    assert [item.contact_id for item in baseline.contacts] == ["contact_b1", "contact_b2"]


def test_scenario_c_plan_unchanged_before_approval_and_v2_after() -> None:
    old = plan("c1", [contact("c1", 10, 450)])
    candidate = plan("c2", [old.contacts[0], contact("c2", 30, 50)])
    replanner = ForwardReplanner()
    proposal = replanner.propose(
        current_plan=old, candidate_plan=candidate, now=NOW,
        predicted_shortfall_mb=50, authorized_station_ids={"station_alpha"},
    )
    assert proposal is not None
    assert replanner.plan_history[old.plan_id] == old
    assert proposal.status == ProposalStatus.PENDING
    approved = replanner.approve(proposal.proposal_id)
    assert approved.active_plan.version == 2
    assert approved.active_plan.parent_plan_id == old.plan_id
    assert approved.active_plan.planned_volume_mb == 500


def test_scenario_d_returns_relaxations_and_never_completes() -> None:
    old = plan("d1", [contact("d1", 10, 450)])
    suggestions = RelaxationSuggestions(
        deadline_extension_s=600, additional_budget_needed="25.00",
        required_volume_reduction_mb=50,
    )
    proposal = ForwardReplanner().propose(
        current_plan=old, candidate_plan=None, now=NOW, predicted_shortfall_mb=50,
        authorized_station_ids={"station_alpha"}, suggestions=suggestions,
    )
    assert proposal is not None and proposal.proposed_plan is None
    assert proposal.predicted_shortfall_after_mb == 50
    assert {item.kind for item in proposal.alternatives} >= {"add_budget", "extend_deadline"}


class OutageTextParser:
    async def parse(self, text: str, context: AnomalyContext) -> ParsedAnomalyIntent:
        return ParsedAnomalyIntent(
            anomaly_type=AnomalyType.STATION_OUTAGE,
            station_id="station_alpha" if "station_alpha" in text else None,
        )


def test_scenario_e_text_proposal_is_inert_until_confirmation() -> None:
    service = AnomalyService()
    context = AnomalyContext(
        scenario_id="scenario_golden", station_ids=["station_alpha"],
        contact_ids=["contact_e1"], simulation_time=NOW,
    )
    proposal = asyncio.run(service.propose_text(
        "scenario_golden", "station_alpha outage", context, OutageTextParser(), NOW
    ))
    assert proposal.status == ProposalStatus.PENDING
    assert service.active == {}
    active = service.confirm(proposal.proposal_id, NOW)
    assert active.rate_multiplier == 0
    assert service.proposals[proposal.proposal_id].status == ProposalStatus.CONFIRMED
