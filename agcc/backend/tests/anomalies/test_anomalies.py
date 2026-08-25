from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta

import pytest

from agcc.anomalies import (
    AnomalyContext,
    AnomalyService,
    GraniteAnomalyNotConfigured,
    NotConfiguredGraniteAnomalyParser,
    ParsedAnomalyIntent,
    StructuredAnomalyInput,
)
from agcc.domain.enums import AnomalyType, ProposalStatus

NOW = datetime(2026, 8, 21, tzinfo=UTC)


class KeywordTestParser:
    """Deterministic parser used only by Task 13 tests."""

    async def parse(self, text: str, context: AnomalyContext) -> ParsedAnomalyIntent:
        lowered = text.lower()
        anomaly_type = None
        if "outage" in lowered or "offline" in lowered:
            anomaly_type = AnomalyType.STATION_OUTAGE
        elif "reduction" in lowered or "degradation" in lowered:
            anomaly_type = AnomalyType.RATE_DEGRADATION
        elif "rain" in lowered:
            anomaly_type = AnomalyType.HEAVY_RAIN_SCENARIO
        station = next((item for item in context.station_ids if item.lower() in lowered), None)
        match = re.search(r"(\d+(?:\.\d+)?)\s*%", text)
        return ParsedAnomalyIntent(
            anomaly_type=anomaly_type,
            station_id=station,
            explicit_reduction_pct=float(match.group(1)) if match else None,
        )


def context() -> AnomalyContext:
    return AnomalyContext(
        scenario_id="scenario_test",
        station_ids=["station_alpha"],
        contact_ids=["contact_1"],
        simulation_time=NOW,
    )


def test_explicit_outage_requires_confirmation_before_mutation() -> None:
    service = AnomalyService()
    proposal = service.propose_structured(
        "scenario_test",
        StructuredAnomalyInput(
            anomaly_type=AnomalyType.STATION_OUTAGE,
            station_id="station_alpha",
            starts_at=NOW,
            ends_at=NOW + timedelta(minutes=10),
            source_text="Station outage",
        ),
        context(),
        NOW,
    )
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.rate_multiplier == 0.0
    assert service.active == {}
    active = service.confirm(proposal.proposal_id, NOW)
    assert active.rate_multiplier == 0.0


def test_explicit_sixty_percent_reduction_is_copied_not_invented() -> None:
    service = AnomalyService()
    proposal = service.propose_structured(
        "scenario_test",
        StructuredAnomalyInput(
            anomaly_type=AnomalyType.RATE_DEGRADATION,
            contact_id="contact_1",
            explicit_reduction_pct=60,
            source_text="Reduce rate by 60%",
        ),
        context(),
        NOW,
    )
    assert proposal.rate_multiplier == pytest.approx(0.4)


def test_llm_suggested_multiplier_and_interval_are_preserved_for_confirmation() -> None:
    service = AnomalyService()
    intent = ParsedAnomalyIntent(
        anomaly_type=AnomalyType.RATE_DEGRADATION,
        station_id="station_alpha",
        suggested_multiplier=0.58,
        confidence=0.71,
        cause="wildfire smoke",
        assumptions=["station remains operational"],
        starts_at=NOW,
        ends_at=NOW + timedelta(hours=2),
    )

    class Parser:
        async def parse(self, text: str, context: AnomalyContext) -> ParsedAnomalyIntent:
            del text, context
            return intent

    proposal = asyncio.run(
        service.propose_text("scenario_test", "wildfire smoke", context(), Parser(), NOW)
    )
    assert proposal.status == ProposalStatus.PENDING
    assert proposal.rate_multiplier == pytest.approx(0.58)
    active = service.confirm(proposal.proposal_id, NOW)
    assert active.ends_at == NOW + timedelta(hours=2)
    assert active.cause == "wildfire smoke"


def test_zero_llm_degradation_is_rejected_instead_of_becoming_an_outage() -> None:
    service = AnomalyService()

    class Parser:
        async def parse(self, text: str, context: AnomalyContext) -> ParsedAnomalyIntent:
            del text, context
            return ParsedAnomalyIntent(
                anomaly_type=AnomalyType.RATE_DEGRADATION,
                station_id="station_alpha",
                suggested_multiplier=0.0,
            )

    proposal = asyncio.run(
        service.propose_text("scenario_test", "heavy rain", context(), Parser(), NOW)
    )
    assert proposal.status == ProposalStatus.NEEDS_CLARIFICATION
    assert proposal.rate_multiplier is None


def test_vague_station_weather_text_needs_clarification() -> None:
    service = AnomalyService()
    proposal = asyncio.run(
        service.propose_text(
            "scenario_test", "bad weather near a station", context(), KeywordTestParser(), NOW
        )
    )
    assert proposal.status == ProposalStatus.NEEDS_CLARIFICATION
    assert proposal.rate_multiplier is None
    assert service.active == {}


def test_disabled_heavy_rain_policy_does_not_assign_coefficient() -> None:
    service = AnomalyService()
    proposal = asyncio.run(
        service.propose_text(
            "scenario_test", "heavy rain", context(), KeywordTestParser(), NOW
        )
    )
    assert proposal.status == ProposalStatus.NEEDS_CLARIFICATION
    assert proposal.rate_multiplier is None
    assert "disabled pending approved data" in proposal.clarification_questions[0]


def test_confirmation_rejects_incomplete_proposal() -> None:
    service = AnomalyService()
    proposal = service.propose_structured(
        "scenario_test",
        StructuredAnomalyInput(
            anomaly_type=AnomalyType.RATE_DEGRADATION,
            source_text="some degradation",
        ),
        context(),
        NOW,
    )
    with pytest.raises(ValueError, match="complete pending"):
        service.confirm(proposal.proposal_id, NOW)


def test_granite_placeholder_failure_is_explicit() -> None:
    with pytest.raises(GraniteAnomalyNotConfigured) as error:
        asyncio.run(NotConfiguredGraniteAnomalyParser().parse("station offline", context()))
    assert error.value.code == "GRANITE_ANOMALY_NOT_CONFIGURED"
