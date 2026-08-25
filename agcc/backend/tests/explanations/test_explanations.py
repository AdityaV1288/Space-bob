from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from agcc.explanations import (
    ExplanationRequest,
    ExplanationService,
    compute_fact_payload_hash,
)


class FakeGenerator:
    model_id = "ibm/granite-test"

    def __init__(
        self,
        text: str = "contact-7 was selected because its recorded pass is feasible",
    ) -> None:
        self.text = text
        self.prompt = ""

    def generate(self, prompt: str) -> str:
        self.prompt = prompt
        return self.text


class FailingGenerator(FakeGenerator):
    def generate(self, prompt: str) -> str:
        raise RuntimeError("model unavailable")


def request(**changes: object) -> ExplanationRequest:
    facts = changes.pop("verified_facts", {
        "contact_id": "contact-7",
        "reason": "its recorded pass is feasible",
        "delivered_volume_mb": 3000,
    })
    constraints = changes.pop("constraints", {"deadline_met": True})
    values = {
        "request_id": "explain-1",
        "task": "explain_selection",
        "verified_facts": facts,
        "plan_id": "plan-1",
        "proposal_id": None,
        "constraints": constraints,
        "fact_payload_hash": compute_fact_payload_hash(facts, constraints),
        **changes,
    }
    return ExplanationRequest.model_validate(values)


def service(generator: FakeGenerator | None = None) -> ExplanationService:
    return ExplanationService(
        generator=generator or FakeGenerator(),
        clock=lambda: datetime(2026, 8, 21, tzinfo=UTC),
    )


def test_template_fallback_works_without_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGCC_GRANITE_API_KEY", raising=False)
    result = ExplanationService(clock=lambda: datetime(2026, 8, 21, tzinfo=UTC)).explain(request())
    assert result.fallback_used is True
    assert result.model_id == "agcc/deterministic-template-v1"


def test_request_contains_only_verified_facts() -> None:
    facts = {"contact_id": "contact-7", "raw_user_prose": "book it for me"}
    with pytest.raises(ValidationError, match="unverified prose"):
        request(verified_facts=facts)


def test_explanation_cannot_change_plan_state() -> None:
    plan = {"plan_id": "plan-1", "status": "pending", "contacts": ["contact-7"]}
    before = plan.copy(), list(plan["contacts"])
    service().explain(request())
    assert plan == before[0]
    assert plan["contacts"] == before[1]


def test_fact_hash_and_original_facts_are_stored() -> None:
    svc = service()
    req = request()
    result = svc.explain(req)
    record = svc.store.get(req.request_id)
    assert record.response.fact_payload_hash == req.fact_payload_hash == result.fact_payload_hash
    assert record.verified_facts == req.verified_facts


def test_model_failure_falls_back_safely() -> None:
    result = service(FailingGenerator()).explain(request())
    assert result.fallback_used is True
    assert "does not change or approve the plan" in result.text


def test_explanation_references_correct_entity_ids() -> None:
    result = service().explain(request(proposal_id="proposal-2"))
    assert result.referenced_entity_ids == ["plan-1", "proposal-2", "contact-7"]


def test_unsupported_claim_is_flagged_and_excluded() -> None:
    svc = service(FakeGenerator("The station was booked and schedule was changed."))
    result = svc.explain(request())
    record = svc.store.get(result.request_id)
    assert result.fallback_used is True
    assert "booked" not in result.text
    assert "unsupported_control_or_booking_claim" in record.validation_flags


def test_new_numeric_claim_forces_fallback() -> None:
    svc = service(FakeGenerator("contact-7 will deliver 9999 MB"))
    result = svc.explain(request())
    assert result.fallback_used is True
    assert "9999" not in result.text


def test_hash_mismatch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="fact_payload_hash"):
        request(fact_payload_hash="0" * 64)
