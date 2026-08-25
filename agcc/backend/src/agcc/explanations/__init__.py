"""Grounded, read-only IBM Granite explanation boundary."""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, field_validator, model_validator

ExplanationTask = Literal[
    "explain_selection",
    "explain_rejection",
    "explain_redispatch",
    "explain_replan",
    "summarize_anomaly",
    "generate_operator_brief",
]

FORBIDDEN_FACT_KEYS = {"raw_user_prose", "user_prompt", "operator_prompt", "instructions"}
CONTROL_CLAIMS = (
    "station was booked",
    "station is booked",
    "schedule was changed",
    "schedule has been changed",
    "approved the re-plan",
    "approved the replan",
    "overrode the constraint",
)


def canonical_payload(facts: dict[str, Any], constraints: dict[str, Any]) -> str:
    return json.dumps(
        {"constraints": constraints, "verified_facts": facts},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def compute_fact_payload_hash(facts: dict[str, Any], constraints: dict[str, Any]) -> str:
    return hashlib.sha256(canonical_payload(facts, constraints).encode()).hexdigest()


def _assert_verified_shape(value: Any, path: str = "verified_facts") -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in FORBIDDEN_FACT_KEYS:
                raise ValueError(f"{path}.{key} is unverified prose and is not permitted")
            _assert_verified_shape(nested, f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _assert_verified_shape(nested, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise ValueError(f"{path} contains a non-serializable fact value")


class ExplanationRequest(BaseModel):
    schema_version: str = "explanation-request.v1"
    request_id: str = Field(min_length=1)
    task: ExplanationTask
    verified_facts: dict[str, Any]
    plan_id: str = Field(min_length=1)
    proposal_id: str | None = None
    constraints: dict[str, Any]
    fact_payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    @field_validator("verified_facts", "constraints")
    @classmethod
    def verified_payload_only(cls, value: dict[str, Any]) -> dict[str, Any]:
        _assert_verified_shape(value)
        return value

    @model_validator(mode="after")
    def hash_matches_payload(self) -> ExplanationRequest:
        expected = compute_fact_payload_hash(self.verified_facts, self.constraints)
        if self.fact_payload_hash != expected:
            raise ValueError("fact_payload_hash does not match the structured fact payload")
        return self


class ExplanationResponse(BaseModel):
    schema_version: str = "explanation-response.v1"
    request_id: str
    text: str = Field(min_length=1, max_length=1800)
    model_id: str
    generated_at: datetime
    fact_payload_hash: str
    fallback_used: bool
    referenced_entity_ids: list[str]


class ExplanationRecord(BaseModel):
    response: ExplanationResponse
    plan_id: str
    proposal_id: str | None
    verified_facts: dict[str, Any]
    constraints: dict[str, Any]
    validation_flags: list[str] = Field(default_factory=list)


class ExplanationStore:
    def __init__(self) -> None:
        self._records: dict[str, ExplanationRecord] = {}

    def put(self, record: ExplanationRecord) -> None:
        self._records[record.response.request_id] = record.model_copy(deep=True)

    def get(self, request_id: str) -> ExplanationRecord:
        return self._records[request_id].model_copy(deep=True)


class TextGenerator(Protocol):
    model_id: str

    def generate(self, prompt: str) -> str: ...


class WatsonxGraniteAdapter:
    """Minimal watsonx REST adapter. It returns text only and owns no domain state."""

    def __init__(self) -> None:
        self.api_key = os.getenv("AGCC_GRANITE_API_KEY")
        self.project_id = os.getenv("AGCC_GRANITE_PROJECT_ID")
        self.base_url = os.getenv("AGCC_GRANITE_BASE_URL")
        self.model_id = os.getenv("AGCC_GRANITE_MODEL_ID") or "not-configured"
        self.timeout_s = 20.0

    @property
    def configured(self) -> bool:
        return bool(
            self.api_key
            and self.project_id
            and self.base_url
            and self.model_id != "not-configured"
        )

    def generate(self, prompt: str) -> str:
        if not self.configured:
            raise RuntimeError("watsonx credentials are not configured")
        assert self.base_url is not None
        response = httpx.post(
            self.base_url,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model_id": self.model_id,
                "project_id": self.project_id,
                "input": prompt,
                "parameters": {
                    "decoding_method": "greedy",
                    "max_new_tokens": 300,
                    "temperature": 0,
                },
            },
            timeout=self.timeout_s,
        )
        response.raise_for_status()
        return str(response.json()["results"][0]["generated_text"]).strip()


class ExplanationService:
    def __init__(
        self,
        generator: TextGenerator | None = None,
        store: ExplanationStore | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.generator = generator or WatsonxGraniteAdapter()
        self.store = store or ExplanationStore()
        self.clock = clock or (lambda: datetime.now(UTC))

    def explain(self, request: ExplanationRequest) -> ExplanationResponse:
        fallback_used = False
        flags: list[str] = []
        entity_ids = _entity_ids(request)
        try:
            text = self.generator.generate(_grounded_prompt(request))
            flags = _validation_flags(text, request, entity_ids)
            if flags:
                raise ValueError("; ".join(flags))
            model_id = self.generator.model_id
        except Exception:
            fallback_used = True
            text = _template(request)
            model_id = "agcc/deterministic-template-v1"
        response = ExplanationResponse(
            request_id=request.request_id,
            text=text,
            model_id=model_id,
            generated_at=self.clock(),
            fact_payload_hash=request.fact_payload_hash,
            fallback_used=fallback_used,
            referenced_entity_ids=entity_ids,
        )
        self.store.put(ExplanationRecord(
            response=response,
            plan_id=request.plan_id,
            proposal_id=request.proposal_id,
            verified_facts=request.verified_facts,
            constraints=request.constraints,
            validation_flags=flags,
        ))
        return response


def _entity_ids(request: ExplanationRequest) -> list[str]:
    ids = [request.plan_id]
    if request.proposal_id:
        ids.append(request.proposal_id)
    for key, value in request.verified_facts.items():
        if (key == "id" or key.endswith("_id") or key.endswith("_ids")):
            values = value if isinstance(value, list) else [value]
            ids.extend(str(item) for item in values if item is not None)
    return list(dict.fromkeys(ids))


def _grounded_prompt(request: ExplanationRequest) -> str:
    return (
        "You are a read-only explanation layer. Explain only the JSON facts below. "
        "Do not calculate, select, schedule, approve, book, or invent numbers. "
        "Never claim a real station is booked. Keep the response under 180 words.\n"
        f"Task: {request.task}\nPlan: {request.plan_id}\nProposal: {request.proposal_id}\n"
        f"Facts and constraints: {canonical_payload(request.verified_facts, request.constraints)}"
    )


def _numbers(text: str) -> set[str]:
    return set(re.findall(r"(?<![A-Za-z_])-?\d+(?:\.\d+)?", text))


def _validation_flags(text: str, request: ExplanationRequest, entity_ids: list[str]) -> list[str]:
    flags: list[str] = []
    lowered = text.lower()
    if len(text) > 1800:
        flags.append("response_too_long")
    if any(claim in lowered for claim in CONTROL_CLAIMS):
        flags.append("unsupported_control_or_booking_claim")
    allowed_numbers = _numbers(canonical_payload(request.verified_facts, request.constraints))
    if _numbers(text) - allowed_numbers:
        flags.append("unverified_numeric_claim")
    entity_pattern = r"\b(?:plan|proposal|contact|pass|station)[-_][A-Za-z0-9_-]+\b"
    mentioned_ids = set(re.findall(entity_pattern, text))
    if mentioned_ids - set(entity_ids):
        flags.append("unverified_entity_reference")
    return flags


def _template(request: ExplanationRequest) -> str:
    facts = request.verified_facts
    reason = str(
        facts.get("reason")
        or facts.get("decision_reason")
        or "the verified constraints and recorded decision"
    )
    subject = str(
        facts.get("contact_id")
        or facts.get("pass_id")
        or request.proposal_id
        or request.plan_id
    )
    labels = {
        "explain_selection": "was selected because",
        "explain_rejection": "was rejected because",
        "explain_redispatch": "was redistributed because",
        "explain_replan": "was recommended because",
        "summarize_anomaly": "was affected because",
        "generate_operator_brief": "is summarized by",
    }
    return (
        f"{subject} {labels[request.task]} {reason}. "
        "This explanation reports verified records only; "
        "it does not change or approve the plan."
    )


__all__ = [
    "ExplanationRecord", "ExplanationRequest", "ExplanationResponse", "ExplanationService",
    "ExplanationStore", "WatsonxGraniteAdapter", "compute_fact_payload_hash",
]
