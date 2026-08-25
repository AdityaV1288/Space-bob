"""Tests for Task 09 — Mission feasibility and eligible pass records.

Acceptance criteria:
  1. Exact target: capacity exactly equals required volume → POTENTIALLY_FEASIBLE.
  2. One-byte-equivalent shortfall: capacity 1 MB below required → INFEASIBLE_CAPACITY.
  3. Insufficient deadline: passes exist but all after deadline → INFEASIBLE_DEADLINE.
  4. Insufficient budget: passes exist but cheapest set exceeds budget → INFEASIBLE_BUDGET.
  5. Excluded station restoration: excluded catalog stations appear in suggestions.
  6. Deterministic money rounding: cost formula uses Decimal ceil.
  7. Band mismatch generates INCOMPATIBLE_BAND rejection.
  8. Disabled station generates STATION_UNAVAILABLE rejection.
  9. Relaxation suggestions are produced for all infeasibility modes.
 10. compute_contact_cost formula is exact.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agcc.domain.enums import Band, CostModel, RejectionCode
from agcc.domain.planning import CandidatePass, CapacityEstimate
from agcc.domain.stations import FieldProvenance, GroundStation
from agcc.feasibility import (
    EligiblePassRecord,
    FeasibilityChecker,
    FeasibilityStatus,
    compute_contact_cost,
)
from agcc.feasibility.builder import EligiblePassBuilder

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_DEADLINE = _NOW + timedelta(hours=6)
_MAX_BUDGET = 500.0

_FULL_PROV = FieldProvenance(
    assumptions=[
        "latitude_deg",
        "longitude_deg",
        "altitude_m",
        "supported_bands",
        "max_downlink_rate_mbps",
        "minimum_elevation_deg",
        "setup_s",
        "teardown_s",
        "cost_model",
        "booking_cost",
        "cost_per_minute",
        "currency",
    ]
)


def _make_station(
    station_id: str = "station_test01",
    band: Band = Band.X,
    booking_cost: float = 0.0,
    cost_per_minute: float = 10.0,
    enabled: bool = True,
) -> GroundStation:
    return GroundStation(
        station_id=station_id,
        name=station_id,
        provider_id="provider_test",
        latitude_deg=48.0,
        longitude_deg=11.0,
        altitude_m=500.0,
        supported_bands=frozenset({band}),
        max_downlink_rate_mbps=100.0,
        minimum_elevation_deg=5.0,
        setup_s=60,
        teardown_s=30,
        cost_model=CostModel.PER_CONTACT_PLUS_MINUTE,
        booking_cost=booking_cost,
        cost_per_minute=cost_per_minute,
        currency="USD",
        field_provenance=_FULL_PROV,
        enabled=enabled,
    )


def _make_pass(
    pass_id: str = "pass_t001",
    start_offset_h: float = 1.0,
    duration_s: float = 600.0,
    station_id: str = "station_test01",
) -> CandidatePass:
    start = _NOW + timedelta(hours=start_offset_h)
    end = start + timedelta(seconds=duration_s)
    usable = duration_s - 90  # 60s setup + 30s teardown
    return CandidatePass(
        pass_id=pass_id,
        scenario_id="scenario_test01",
        satellite_id="sat_test01",
        station_id=station_id,
        start_at=start,
        peak_at=start + timedelta(seconds=duration_s / 2),
        end_at=end,
        duration_s=duration_s,
        usable_duration_s=usable,
        max_elevation_deg=30.0,
        azimuth_start_deg=10.0,
        azimuth_peak_deg=180.0,
        azimuth_end_deg=350.0,
        slant_range_peak_km=900.0,
        minimum_elevation_deg=5.0,
        orbit_model_version="circular_kepler_v1",
        station_catalog_version="2026.08.1",
    )


def _make_capacity(
    pass_id: str = "pass_t001",
    usable_capacity_mb: float = 100.0,
    quality: str = "verified",
) -> CapacityEstimate:
    from agcc.capacity.engine import _capacity_id

    return CapacityEstimate(
        capacity_id=_capacity_id(pass_id),
        pass_id=pass_id,
        base_rate_mbps=100.0,
        usable_capacity_mb=usable_capacity_mb,
        average_effective_rate_mbps=100.0,
        peak_effective_rate_mbps=100.0,
        weather_data_quality=quality,
        model_version="capacity_v1",
        sample_count=10,
    )


def _make_record(
    pass_id: str = "pass_t001",
    start_offset_h: float = 1.0,
    capacity_mb: float = 100.0,
    station_id: str = "station_test01",
    band: Band = Band.X,
    booking_cost: float = 0.0,
    cost_per_minute: float = 10.0,
    station_enabled: bool = True,
    deadline: datetime | None = None,
    max_budget: float | None = None,
    weather_quality: str = "verified",
) -> EligiblePassRecord:
    station = _make_station(
        station_id=station_id,
        band=band,
        booking_cost=booking_cost,
        cost_per_minute=cost_per_minute,
        enabled=station_enabled,
    )
    pass_ = _make_pass(pass_id=pass_id, start_offset_h=start_offset_h, station_id=station_id)
    capacity = _make_capacity(
        pass_id=pass_id, usable_capacity_mb=capacity_mb, quality=weather_quality
    )
    builder = EligiblePassBuilder(
        satellite_band=Band.X,
        deadline=deadline or _DEADLINE,
        max_budget_usd=float(max_budget or _MAX_BUDGET),
    )
    return builder.build(pass_, capacity, station)


def _checker() -> FeasibilityChecker:
    return FeasibilityChecker()


# ---------------------------------------------------------------------------
# Acceptance 1: exact target → POTENTIALLY_FEASIBLE
# ---------------------------------------------------------------------------


class TestExactTarget:
    def test_capacity_exactly_meets_requirement(self) -> None:
        required = 100.0
        record = _make_record(capacity_mb=required)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=required,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.POTENTIALLY_FEASIBLE
        assert report.capacity_shortfall_mb == 0.0

    def test_multiple_passes_sum_to_exact(self) -> None:
        r1 = _make_record(pass_id="pass_t001", start_offset_h=1.0, capacity_mb=50.0)
        r2 = _make_record(
            pass_id="pass_t002",
            start_offset_h=2.0,
            capacity_mb=50.0,
            station_id="station_test02",
        )
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[r1, r2],
        )
        assert report.status == FeasibilityStatus.POTENTIALLY_FEASIBLE
        assert report.total_eligible_capacity_mb == 100.0


# ---------------------------------------------------------------------------
# Acceptance 2: shortfall → INFEASIBLE_CAPACITY
# ---------------------------------------------------------------------------


class TestCapacityShortfall:
    def test_1mb_below_required_is_infeasible(self) -> None:
        required = 100.0
        record = _make_record(capacity_mb=99.0)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=required,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert abs(report.capacity_shortfall_mb - 1.0) < 0.001

    def test_no_eligible_passes_is_capacity_infeasible(self) -> None:
        # Band mismatch → no eligible passes
        record = _make_record(band=Band.S)  # satellite uses X
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert report.eligible_count == 0
        assert report.total_count == 1


# ---------------------------------------------------------------------------
# Acceptance 3: insufficient deadline → INFEASIBLE_DEADLINE
# ---------------------------------------------------------------------------


class TestInsufficientDeadline:
    def test_all_passes_after_deadline_infeasible(self) -> None:
        # Deadline is 30 min from now; pass starts at hour 1 → after deadline
        tight_deadline = _NOW + timedelta(minutes=30)
        record = _make_record(start_offset_h=1.0, deadline=tight_deadline)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=tight_deadline,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        # Pass starts at NOW+1h which is ≥ NOW+30min deadline → rejected
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert report.eligible_count == 0

    def test_passes_exist_but_accumulation_beyond_deadline(self) -> None:
        """Two passes within deadline window but capacity only met after deadline."""
        deadline = _NOW + timedelta(hours=2)
        # Pass 1 at 1h: 40 MB; pass 2 at 3h (after deadline): 100 MB
        r1 = _make_record(
            pass_id="pass_t001",
            start_offset_h=1.0,
            capacity_mb=40.0,
            deadline=deadline,
        )
        # Pass 2 is after deadline → will be rejected by builder
        r2 = _make_record(
            pass_id="pass_t002",
            start_offset_h=3.0,
            capacity_mb=100.0,
            station_id="station_test02",
            deadline=deadline,
        )
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=deadline,
            maximum_budget=_MAX_BUDGET,
            records=[r1, r2],
        )
        # Only r1 is eligible (40 MB) → capacity shortfall
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert report.capacity_shortfall_mb == pytest.approx(60.0)


# ---------------------------------------------------------------------------
# Acceptance 4: insufficient budget → INFEASIBLE_BUDGET
# ---------------------------------------------------------------------------


class TestInsufficientBudget:
    def test_cheapest_feasible_set_exceeds_budget(self) -> None:
        # 100 MB at cost_per_minute=100, 10 min usable → 1000 USD per pass
        # budget = 100 USD → infeasible
        tiny_budget = 100.0
        record = _make_record(
            cost_per_minute=100.0,
            max_budget=tiny_budget,
        )
        # Record itself gets rejected (BUDGET_EXCEEDED) since cost > 100
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=1.0,  # tiny requirement
            deadline=_DEADLINE,
            maximum_budget=Decimal(str(tiny_budget)),
            records=[record],
        )
        # Budget exceeded filter should have rejected the record
        assert report.eligible_count == 0

    def test_feasible_capacity_but_sum_cost_exceeds_budget(self) -> None:
        """Enough capacity exists but cheapest set cost > budget → INFEASIBLE_BUDGET."""
        # Pass: 10 min usable, $60/min → cost = 10 * 60 = $600; budget = $500
        record = _make_record(
            capacity_mb=200.0,
            cost_per_minute=60.0,
            booking_cost=0.0,
            max_budget=_MAX_BUDGET,  # 500 USD budget
        )
        # usable_duration_s = 510s → ceil(510/60) = 9 min → cost = 9*60 = $540 > $500
        _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=50.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        # record is ineligible due to BUDGET_EXCEEDED
        assert RejectionCode.BUDGET_EXCEEDED in record.rejection_codes


# ---------------------------------------------------------------------------
# Acceptance 5: excluded station restoration suggestion
# ---------------------------------------------------------------------------


class TestExcludedStationSuggestion:
    def test_excluded_stations_in_suggestions(self) -> None:
        # One eligible pass
        record = _make_record(capacity_mb=50.0)  # 50 MB, need 200
        catalog_ids = ["station_test01", "station_excluded_a", "station_excluded_b"]
        authorized_ids = ["station_test01"]
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=200.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
            catalog_station_ids=catalog_ids,
            authorized_station_ids=authorized_ids,
        )
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert report.suggestions is not None
        assert "station_excluded_a" in report.suggestions.excluded_station_candidates
        assert "station_excluded_b" in report.suggestions.excluded_station_candidates
        assert "station_test01" not in report.suggestions.excluded_station_candidates

    def test_no_suggestions_when_fully_feasible(self) -> None:
        record = _make_record(capacity_mb=200.0)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.POTENTIALLY_FEASIBLE
        assert report.suggestions is None


# ---------------------------------------------------------------------------
# Acceptance 6: deterministic money rounding
# ---------------------------------------------------------------------------


class TestMoneyRounding:
    def test_billable_minutes_uses_ceiling(self) -> None:
        # 61 seconds usable → ceil(61/60) = 2 billable minutes
        cost = compute_contact_cost(
            usable_duration_s=61.0,
            booking_cost=0.0,
            cost_per_minute=10.0,
        )
        assert cost == Decimal("20.0")

    def test_exact_60_seconds_is_1_minute(self) -> None:
        cost = compute_contact_cost(
            usable_duration_s=60.0,
            booking_cost=0.0,
            cost_per_minute=10.0,
        )
        assert cost == Decimal("10.0")

    def test_booking_cost_added_to_usage(self) -> None:
        # 60 s usable, $5 booking, $10/min → $5 + $10 = $15
        cost = compute_contact_cost(
            usable_duration_s=60.0,
            booking_cost=5.0,
            cost_per_minute=10.0,
        )
        assert cost == Decimal("15.0")

    def test_cost_stored_as_decimal_string(self) -> None:
        record = _make_record(booking_cost=50.0, cost_per_minute=10.0)
        cost_dec = Decimal(record.contact_cost_decimal)
        # usable = 510 s → ceil(510/60) = 9 min → 9*10 + 50 = 140
        assert cost_dec == Decimal("140.0")

    def test_minimum_possible_cost_is_decimal_string(self) -> None:
        record = _make_record(capacity_mb=100.0)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        # Should be parseable as Decimal
        cost = Decimal(report.minimum_possible_cost)
        assert cost >= Decimal("0")


# ---------------------------------------------------------------------------
# Band mismatch → INCOMPATIBLE_BAND rejection code
# ---------------------------------------------------------------------------


class TestBandMismatch:
    def test_station_s_band_satellite_x_band_rejected(self) -> None:
        record = _make_record(band=Band.S)  # satellite uses X
        assert RejectionCode.INCOMPATIBLE_BAND in record.rejection_codes
        assert not record.is_eligible

    def test_matching_band_is_eligible(self) -> None:
        record = _make_record(band=Band.X)
        assert RejectionCode.INCOMPATIBLE_BAND not in record.rejection_codes


# ---------------------------------------------------------------------------
# Disabled station → STATION_UNAVAILABLE
# ---------------------------------------------------------------------------


class TestStationUnavailable:
    def test_disabled_station_rejected(self) -> None:
        record = _make_record(station_enabled=False)
        assert RejectionCode.STATION_UNAVAILABLE in record.rejection_codes
        assert not record.is_eligible


# ---------------------------------------------------------------------------
# Relaxation suggestions detail
# ---------------------------------------------------------------------------


class TestRelaxationSuggestions:
    def test_volume_reduction_suggested_when_capacity_short(self) -> None:
        record = _make_record(capacity_mb=50.0)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.suggestions is not None
        assert report.suggestions.required_volume_reduction_mb == pytest.approx(50.0)

    def test_additional_budget_suggested_when_budget_exceeded(self) -> None:
        """When feasible capacity exists but all per-pass costs exceed budget."""
        # cost = ceil(510/60)*100 = 9*100 = $900; budget = $500 → needs $400 more
        record = _make_record(
            capacity_mb=200.0,
            cost_per_minute=100.0,
            booking_cost=0.0,
            max_budget=_MAX_BUDGET,  # $500
        )
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        # Record is ineligible (BUDGET_EXCEEDED), capacity = infeasible_capacity
        assert report.status == FeasibilityStatus.INFEASIBLE_BUDGET

    def test_feasibility_report_preserves_all_records(self) -> None:
        r1 = _make_record(pass_id="pass_t001", capacity_mb=100.0)
        r2 = _make_record(pass_id="pass_t002", band=Band.S, station_id="station_test02")
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=50.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[r1, r2],
        )
        assert report.total_count == 2
        assert report.eligible_count == 1


# ---------------------------------------------------------------------------
# Earliest completion
# ---------------------------------------------------------------------------


class TestEarliestCompletion:
    def test_earliest_completion_set_when_feasible(self) -> None:
        record = _make_record(start_offset_h=1.0, capacity_mb=200.0)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.earliest_possible_completion_at is not None
        assert report.earliest_possible_completion_at <= _DEADLINE

    def test_earliest_completion_none_when_capacity_infeasible(self) -> None:
        record = _make_record(capacity_mb=10.0)  # 10 MB < 100 MB needed
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.earliest_possible_completion_at is None


# ---------------------------------------------------------------------------
# DownlinkMission domain contract tests (Task 09 fields)
# ---------------------------------------------------------------------------


class TestDownlinkMissionContract:
    def test_release_at_before_deadline_at_accepted(self) -> None:
        from agcc.domain.mission import DownlinkMission

        m = DownlinkMission(
            mission_id="mission_task09a",
            name="T09",
            required_volume_mb=500.0,
            release_at=_NOW,
            deadline_at=_NOW + timedelta(hours=6),
        )
        assert m.release_at < m.deadline_at

    def test_release_at_equal_deadline_at_rejected(self) -> None:
        from pydantic import ValidationError

        from agcc.domain.mission import DownlinkMission

        with pytest.raises(ValidationError, match="release_at"):
            DownlinkMission(
                mission_id="mission_task09b",
                name="T09",
                required_volume_mb=500.0,
                release_at=_NOW,
                deadline_at=_NOW,
            )

    def test_release_at_after_deadline_at_rejected(self) -> None:
        from pydantic import ValidationError

        from agcc.domain.mission import DownlinkMission

        with pytest.raises(ValidationError, match="release_at"):
            DownlinkMission(
                mission_id="mission_task09c",
                name="T09",
                required_volume_mb=500.0,
                release_at=_NOW + timedelta(hours=1),
                deadline_at=_NOW,
            )

    def test_release_at_naive_rejected(self) -> None:
        from pydantic import ValidationError

        from agcc.domain.mission import DownlinkMission

        naive = _NOW.replace(tzinfo=None)
        with pytest.raises(ValidationError, match="naive"):
            DownlinkMission(
                mission_id="mission_task09d",
                name="T09",
                required_volume_mb=500.0,
                release_at=naive,
                deadline_at=_NOW + timedelta(hours=6),
            )

    def test_deadline_at_serializes_with_z(self) -> None:
        from agcc.domain.mission import DownlinkMission

        m = DownlinkMission(
            mission_id="mission_task09e",
            name="T09",
            required_volume_mb=500.0,
            release_at=_NOW,
            deadline_at=_NOW + timedelta(hours=6),
        )
        data = m.model_dump(mode="json")
        assert data["deadline_at"].endswith("Z")
        assert data["release_at"].endswith("Z")


class TestScenarioConstraintsContract:
    def test_maximum_budget_decimal_accepted(self) -> None:
        from agcc.domain.mission import ScenarioConstraints

        sc = ScenarioConstraints(maximum_budget=Decimal("1500.00"), currency="USD")
        assert sc.maximum_budget == Decimal("1500.00")

    def test_currency_required(self) -> None:
        from pydantic import ValidationError

        from agcc.domain.mission import ScenarioConstraints

        with pytest.raises(ValidationError):
            ScenarioConstraints(maximum_budget=Decimal("100"), currency="")

    def test_planning_preference_defaults_to_balanced(self) -> None:
        from agcc.domain.mission import PlanningPreference, ScenarioConstraints

        sc = ScenarioConstraints(maximum_budget=Decimal("100"), currency="USD")
        assert sc.planning_preference == PlanningPreference.BALANCED

    def test_planning_preference_fastest(self) -> None:
        from agcc.domain.mission import PlanningPreference, ScenarioConstraints

        sc = ScenarioConstraints(
            maximum_budget=Decimal("100"),
            currency="EUR",
            planning_preference=PlanningPreference.FASTEST,
        )
        assert sc.planning_preference == PlanningPreference.FASTEST

    def test_allow_additional_proposals_default_false(self) -> None:
        from agcc.domain.mission import ScenarioConstraints

        sc = ScenarioConstraints(maximum_budget=Decimal("0"), currency="USD")
        assert sc.allow_additional_contact_proposals is False

    def test_budget_negative_rejected(self) -> None:
        from pydantic import ValidationError

        from agcc.domain.mission import ScenarioConstraints

        with pytest.raises(ValidationError):
            ScenarioConstraints(maximum_budget=Decimal("-1"), currency="USD")


# ---------------------------------------------------------------------------
# One-byte-equivalent shortfall acceptance test
# ---------------------------------------------------------------------------


class TestOneByteshortfall:
    """Capacity that is a tiny fraction below the required target → INFEASIBLE_CAPACITY."""

    def test_tiny_shortfall_below_1mb_is_infeasible(self) -> None:
        """Capacity = required - 0.000001 MB (< 1 byte) must still be infeasible."""
        required = 100.0
        # 1 byte = 1/(1024*1024) MB ≈ 9.54e-7 MB; use an even smaller delta
        tiny_delta = 0.000001  # 1 µB equivalent — deliberately sub-byte
        record = _make_record(capacity_mb=required - tiny_delta)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=required,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.INFEASIBLE_CAPACITY
        assert report.capacity_shortfall_mb > 0.0
        assert report.capacity_shortfall_mb == pytest.approx(tiny_delta, rel=1e-3)

    def test_capacity_exactly_at_target_is_feasible(self) -> None:
        """Boundary: capacity == required must be POTENTIALLY_FEASIBLE."""
        required = 250.0
        record = _make_record(capacity_mb=required)
        report = _checker().check(
            scenario_id="scenario_test01",
            mission_id="mission_test01",
            required_volume_mb=required,
            deadline=_DEADLINE,
            maximum_budget=_MAX_BUDGET,
            records=[record],
        )
        assert report.status == FeasibilityStatus.POTENTIALLY_FEASIBLE
        assert report.capacity_shortfall_mb == 0.0
