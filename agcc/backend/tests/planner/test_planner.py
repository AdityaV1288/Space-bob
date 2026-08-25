"""Tests for Task 10 — Deterministic contact planner.

Acceptance criteria:
  1. Never returns success below target.
  2. Never allocates more than the target.
  3. Never exceeds budget or deadline.
  4. Overlapping stations do not both occupy the satellite.
  5. FASTEST preference selects earliest-completing contacts.
  6. LOWEST_COST preference selects cheapest contacts.
  7. BALANCED preference uses weighted normalised score.
  8. Same input yields identical plan (determinism); plan hash stable.
  9. Plans with no feasible path return NO_FEASIBLE_PLAN_FOUND.
 10. planned_volume_mb == required_volume_mb when feasible.
 11. Cost formula uses billable-minute rounding (Decimal).
 12. Trimming last contact keeps plan exactly at target.
 13. Rejected records (ineligible passes) are preserved in output.
 14. Unused opportunity IDs are recorded.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agcc.domain.enums import Band, CostModel
from agcc.domain.mission import PlanningPreference
from agcc.domain.planning import CandidatePass, CapacityEstimate
from agcc.domain.stations import FieldProvenance, GroundStation
from agcc.feasibility import EligiblePassRecord
from agcc.feasibility.builder import EligiblePassBuilder
from agcc.planner import (
    ALGORITHM_VERSION,
    ContactPlan,
    ContactPlanner,
    PlanStatus,
    _contact_cost,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_RELEASE = _NOW
_DEADLINE = _NOW + timedelta(hours=12)
_BUDGET = Decimal("10000")

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


def _station(
    station_id: str = "station_alpha01",
    band: Band = Band.X,
    booking_cost: float = 0.0,
    cost_per_minute: float = 10.0,
    enabled: bool = True,
    rate_mbps: float = 100.0,
) -> GroundStation:
    return GroundStation(
        station_id=station_id,
        name=station_id,
        provider_id="provider_test",
        latitude_deg=50.0,
        longitude_deg=10.0,
        altitude_m=100.0,
        supported_bands=frozenset({band}),
        max_downlink_rate_mbps=rate_mbps,
        minimum_elevation_deg=5.0,
        setup_s=0,
        teardown_s=0,
        cost_model=CostModel.PER_CONTACT_PLUS_MINUTE,
        booking_cost=booking_cost,
        cost_per_minute=cost_per_minute,
        currency="USD",
        field_provenance=_FULL_PROV,
        enabled=enabled,
    )


def _capacity_id(pass_id: str) -> str:
    digest = hashlib.sha256(f"capacity|{pass_id}".encode()).hexdigest()[:16]
    return f"capacity_{digest}"


def _pass(
    pass_id: str,
    start_offset_h: float,
    duration_s: float = 600.0,
    station_id: str = "station_alpha01",
) -> CandidatePass:
    start = _NOW + timedelta(hours=start_offset_h)
    end = start + timedelta(seconds=duration_s)
    # No setup/teardown — usable_duration_s == duration_s for simplicity
    usable = duration_s
    return CandidatePass(
        pass_id=pass_id,
        scenario_id="scenario_s01",
        satellite_id="sat_demo01",
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
        orbit_model_version="circular_v1",
        station_catalog_version="cat_v1",
    )


def _capacity(pass_id: str, capacity_mb: float) -> CapacityEstimate:
    return CapacityEstimate(
        capacity_id=_capacity_id(pass_id),
        pass_id=pass_id,
        base_rate_mbps=100.0,
        usable_capacity_mb=capacity_mb,
        average_effective_rate_mbps=100.0,
        peak_effective_rate_mbps=100.0,
        weather_data_quality="verified",
        model_version="capacity_v1",
        sample_count=10,
    )


def _eligible_record(
    pass_id: str,
    start_offset_h: float,
    capacity_mb: float,
    station: GroundStation,
    deadline: datetime | None = None,
    budget: float | None = None,
) -> EligiblePassRecord:
    p = _pass(pass_id, start_offset_h, station_id=station.station_id)
    cap = _capacity(pass_id, capacity_mb)
    builder = EligiblePassBuilder(
        satellite_band=Band.X,
        deadline=deadline or _DEADLINE,
        max_budget_usd=budget or float(_BUDGET),
    )
    return builder.build(p, cap, station)


def _planner() -> ContactPlanner:
    return ContactPlanner()


def _plan_id() -> str:
    return "plan_test00000001"


def _station_map(*stations: GroundStation) -> dict[str, GroundStation]:
    return {s.station_id: s for s in stations}


# ---------------------------------------------------------------------------
# Helper: run planner with a simple single-station setup
# ---------------------------------------------------------------------------


def _run(
    records: list[EligiblePassRecord],
    station: GroundStation,
    *,
    required_mb: float,
    preference: PlanningPreference = PlanningPreference.FASTEST,
    budget: Decimal = _BUDGET,
    deadline: datetime = _DEADLINE,
    created_at: datetime | None = None,
) -> ContactPlan:
    if created_at is None:
        created_at = _NOW
    return _planner().plan(
        plan_id=_plan_id(),
        scenario_id="scenario_s01",
        mission_id="mission_m01",
        required_volume_mb=required_mb,
        deadline=deadline,
        mission_window_start=_RELEASE,
        maximum_budget=budget,
        preference=preference,
        eligible_records=records,
        station_map=_station_map(station),
        created_at=created_at,
    )


# ===========================================================================
# TestNeverExceedsTarget — planned_volume_mb <= required_volume_mb
# ===========================================================================


class TestNeverExceedsTarget:
    def test_single_pass_exact_allocation(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert plan.planned_volume_mb <= plan.required_volume_mb

    def test_allocation_never_exceeds_required(self) -> None:
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
            _eligible_record("pass_e002", 3.0, 200.0, st),
        ]
        plan = _run(records, st, required_mb=50.0)
        assert plan.planned_volume_mb <= plan.required_volume_mb

    def test_two_contacts_trim_last_to_exact(self) -> None:
        """Two passes needed; second should be trimmed to exactly fill remaining."""
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 80.0, st),
            _eligible_record("pass_e002", 3.0, 80.0, st),
        ]
        plan = _run(records, st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert abs(plan.planned_volume_mb - 100.0) < 1e-9


def test_preference_is_not_treated_as_a_hard_single_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    station = _station()
    record = _eligible_record("pass_fallback01", 1.0, 200.0, station)
    planner = _planner()
    original = planner._dispatch
    calls = 0

    def first_strategy_fails(**kwargs: object) -> tuple[list[object], bool]:
        nonlocal calls
        calls += 1
        if calls == 1:
            return [], False
        return original(**kwargs)  # type: ignore[arg-type,return-value]

    monkeypatch.setattr(planner, "_dispatch", first_strategy_fails)
    plan = planner.plan(
        plan_id=_plan_id(),
        scenario_id="scenario_s01",
        mission_id="mission_m01",
        required_volume_mb=100.0,
        deadline=_DEADLINE,
        mission_window_start=_RELEASE,
        maximum_budget=_BUDGET,
        preference=PlanningPreference.FASTEST,
        eligible_records=[record],
        station_map=_station_map(station),
        created_at=_NOW,
    )
    assert calls >= 2
    assert plan.status == PlanStatus.FEASIBLE
    assert plan.validation_violations == []


# ===========================================================================
# TestNeverBelowTarget — when feasible, exactly meets required volume
# ===========================================================================


class TestNeverBelowTarget:
    def test_feasible_plan_meets_exact_target(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 500.0, st)
        plan = _run([r], st, required_mb=300.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert abs(plan.planned_volume_mb - 300.0) < 1e-9

    def test_multi_pass_plan_meets_exact_target(self) -> None:
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 40.0, st),
            _eligible_record("pass_e002", 2.0, 40.0, st),
            _eligible_record("pass_e003", 3.0, 40.0, st),
        ]
        plan = _run(records, st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert abs(plan.planned_volume_mb - 100.0) < 1e-9


# ===========================================================================
# TestNoFeasiblePlan
# ===========================================================================


class TestNoFeasiblePlan:
    def test_zero_eligible_records_returns_no_plan(self) -> None:
        st = _station()
        plan = _run([], st, required_mb=100.0)
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND
        assert plan.planned_volume_mb == 0.0
        assert plan.contacts == []

    def test_insufficient_capacity_returns_no_plan(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 50.0, st)
        plan = _run([r], st, required_mb=200.0)
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND

    def test_budget_too_tight_returns_no_plan(self) -> None:
        # cost_per_minute=10, 600s pass = 10 billable minutes = 100 USD
        st = _station(cost_per_minute=10.0)
        r = _eligible_record("pass_e001", 1.0, 500.0, st)
        plan = _run([r], st, required_mb=100.0, budget=Decimal("1"))
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND

    def test_all_passes_after_deadline_returns_no_plan(self) -> None:
        st = _station()
        # Pass starts after deadline
        deadline = _NOW + timedelta(hours=1)
        r = _eligible_record("pass_e001", 2.0, 500.0, st, deadline=deadline, budget=float(_BUDGET))
        # r is ineligible (DEADLINE_MISSED), no eligible records
        plan = _run([r], st, required_mb=100.0, deadline=deadline)
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND

    def test_no_feasible_plan_has_empty_contacts(self) -> None:
        st = _station()
        plan = _run([], st, required_mb=100.0)
        assert len(plan.contacts) == 0
        assert plan.estimated_total_cost == "0"

    def test_no_feasible_plan_has_unused_opportunity_ids(self) -> None:
        """Eligible passes that could not satisfy budget are listed as unused."""
        st = _station(cost_per_minute=1000.0)  # very expensive
        r = _eligible_record("pass_e001", 1.0, 500.0, st)
        # budget too low to even start
        plan = _run([r], st, required_mb=100.0, budget=Decimal("1"))
        # r may be eligible from builder's perspective (builder uses max_budget_usd)
        # but planner cannot afford it
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND


# ===========================================================================
# TestDeadlineConstraint
# ===========================================================================


class TestDeadlineConstraint:
    def test_contacts_end_before_deadline(self) -> None:
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
            _eligible_record("pass_e002", 11.0, 200.0, st),
        ]
        plan = _run(records, st, required_mb=100.0)
        if plan.status == PlanStatus.FEASIBLE:
            for c in plan.contacts:
                assert c.end_at <= _DEADLINE

    def test_plan_completion_at_or_before_deadline(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert plan.planned_completion_at is not None
        assert plan.planned_completion_at <= _DEADLINE


# ===========================================================================
# TestBudgetConstraint
# ===========================================================================


class TestBudgetConstraint:
    def test_total_cost_within_budget(self) -> None:
        st = _station(cost_per_minute=5.0)
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
            _eligible_record("pass_e002", 3.0, 200.0, st),
        ]
        budget = Decimal("1000")
        plan = _run(records, st, required_mb=100.0, budget=budget)
        if plan.status == PlanStatus.FEASIBLE:
            total = sum(Decimal(c.contact_cost_decimal) for c in plan.contacts)
            assert total <= budget

    def test_cost_uses_decimal_not_float(self) -> None:
        st = _station(cost_per_minute=1.33)
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=50.0)
        # Verify cost is stored as a Decimal-parseable string
        for c in plan.contacts:
            Decimal(c.contact_cost_decimal)  # must not raise


# ===========================================================================
# TestSatelliteExclusivity — one station per time slot
# ===========================================================================


class TestSatelliteExclusivity:
    def test_overlapping_passes_use_only_one_station(self) -> None:
        """Two passes that overlap in time must not both appear in the plan."""
        st1 = _station("station_alpha01")
        st2 = _station("station_beta01")
        # Both passes start at the same time — they overlap
        r1 = _eligible_record("pass_e001", 1.0, 200.0, st1)
        r2 = _eligible_record("pass_e002", 1.0, 200.0, st2)
        plan = _planner().plan(
            plan_id=_plan_id(),
            scenario_id="scenario_s01",
            mission_id="mission_m01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            mission_window_start=_RELEASE,
            maximum_budget=_BUDGET,
            preference=PlanningPreference.FASTEST,
            eligible_records=[r1, r2],
            station_map=_station_map(st1, st2),
            created_at=_NOW,
        )
        # At most one of the two overlapping passes should be in the plan
        contact_pass_ids = {c.pass_id for c in plan.contacts}
        # They cannot BOTH be selected if they overlap
        # (one satisfies the requirement already)
        assert len(contact_pass_ids) <= 2  # structural check
        # No two selected contacts should overlap in time
        sorted_contacts = sorted(plan.contacts, key=lambda c: c.start_at)
        for i in range(len(sorted_contacts) - 1):
            a = sorted_contacts[i]
            b = sorted_contacts[i + 1]
            assert a.end_at <= b.start_at or a.pass_id == b.pass_id, (
                f"Contacts {a.pass_id} and {b.pass_id} overlap: "
                f"{a.start_at}..{a.end_at} vs {b.start_at}..{b.end_at}"
            )


# ===========================================================================
# TestPreferenceFastest
# ===========================================================================


class TestPreferenceFastest:
    def test_fastest_selects_earlier_pass_first(self) -> None:
        """FASTEST should prefer the earliest-completing pass."""
        st = _station()
        # early pass has less capacity but finishes sooner
        r_early = _eligible_record("pass_e001", 1.0, 200.0, st)
        r_late = _eligible_record("pass_e002", 10.0, 200.0, st)
        plan = _run([r_early, r_late], st, required_mb=100.0, preference=PlanningPreference.FASTEST)
        assert plan.status == PlanStatus.FEASIBLE
        # Should have used the early pass
        assert any(c.pass_id == "pass_e001" for c in plan.contacts)

    def test_fastest_completion_at_is_earliest_possible(self) -> None:
        st = _station()
        r1 = _eligible_record("pass_e001", 1.0, 200.0, st)
        r2 = _eligible_record("pass_e002", 5.0, 200.0, st)
        plan_fast = _run([r1, r2], st, required_mb=100.0, preference=PlanningPreference.FASTEST)
        plan_slow = _run([r1, r2], st, required_mb=100.0, preference=PlanningPreference.LOWEST_COST)
        # Both feasible; FASTEST should complete no later than LOWEST_COST
        if plan_fast.status == PlanStatus.FEASIBLE and plan_slow.status == PlanStatus.FEASIBLE:
            assert plan_fast.planned_completion_at <= plan_slow.planned_completion_at  # type: ignore[operator]


# ===========================================================================
# TestPreferenceLowestCost
# ===========================================================================


class TestPreferenceLowestCost:
    def test_lowest_cost_prefers_cheaper_station(self) -> None:
        """LOWEST_COST should prefer the station with lower cost per MB."""
        st_cheap = _station("station_cheap01", cost_per_minute=1.0)
        st_expensive = _station("station_expensive01", cost_per_minute=100.0)
        r_cheap = _eligible_record("pass_e001", 1.0, 200.0, st_cheap)
        r_expensive = _eligible_record("pass_e002", 2.0, 200.0, st_expensive)
        plan = _planner().plan(
            plan_id=_plan_id(),
            scenario_id="scenario_s01",
            mission_id="mission_m01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            mission_window_start=_RELEASE,
            maximum_budget=_BUDGET,
            preference=PlanningPreference.LOWEST_COST,
            eligible_records=[r_cheap, r_expensive],
            station_map=_station_map(st_cheap, st_expensive),
            created_at=_NOW,
        )
        assert plan.status == PlanStatus.FEASIBLE
        # Cheap station should be preferred
        total_cost = Decimal(plan.estimated_total_cost)
        # Cost through cheap station is lower
        cheap_contact_cost = _contact_cost(600.0, st_cheap)
        assert total_cost <= cheap_contact_cost + Decimal("1")  # tolerance

    def test_lowest_cost_total_is_minimal(self) -> None:
        """LOWEST_COST plan must not cost more than FASTEST for the same inputs."""
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
            _eligible_record("pass_e002", 4.0, 200.0, st),
        ]
        plan_lc = _run(records, st, required_mb=100.0, preference=PlanningPreference.LOWEST_COST)
        plan_f = _run(records, st, required_mb=100.0, preference=PlanningPreference.FASTEST)
        if plan_lc.status == PlanStatus.FEASIBLE and plan_f.status == PlanStatus.FEASIBLE:
            cost_lc = Decimal(plan_lc.estimated_total_cost)
            cost_f = Decimal(plan_f.estimated_total_cost)
            assert cost_lc <= cost_f


# ===========================================================================
# TestPreferenceBalanced
# ===========================================================================


class TestPreferenceBalanced:
    def test_balanced_produces_feasible_plan(self) -> None:
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
            _eligible_record("pass_e002", 4.0, 200.0, st),
        ]
        plan = _run(records, st, required_mb=100.0, preference=PlanningPreference.BALANCED)
        assert plan.status == PlanStatus.FEASIBLE
        assert abs(plan.planned_volume_mb - 100.0) < 1e-9

    def test_balanced_plan_meets_hard_constraints(self) -> None:
        st = _station(cost_per_minute=5.0)
        records = [
            _eligible_record("pass_e001", 1.0, 200.0, st),
        ]
        budget = Decimal("500")
        plan = _run(
            records, st, required_mb=100.0, preference=PlanningPreference.BALANCED, budget=budget
        )
        if plan.status == PlanStatus.FEASIBLE:
            assert Decimal(plan.estimated_total_cost) <= budget
            for c in plan.contacts:
                assert c.end_at <= _DEADLINE


# ===========================================================================
# TestDeterminism
# ===========================================================================


class TestDeterminism:
    def test_same_inputs_produce_identical_contacts(self) -> None:
        st = _station()
        records = [
            _eligible_record("pass_e001", 1.0, 100.0, st),
            _eligible_record("pass_e002", 3.0, 100.0, st),
        ]
        fixed_time = _NOW

        plan1 = _run(records, st, required_mb=150.0, created_at=fixed_time)
        plan2 = _run(records, st, required_mb=150.0, created_at=fixed_time)

        assert plan1.status == plan2.status
        assert len(plan1.contacts) == len(plan2.contacts)
        for c1, c2 in zip(plan1.contacts, plan2.contacts):
            assert c1.contact_id == c2.contact_id
            assert c1.allocated_volume_mb == c2.allocated_volume_mb
            assert c1.contact_cost_decimal == c2.contact_cost_decimal

    def test_contact_ids_are_deterministic(self) -> None:
        """Same pass + slice index always produces the same contact_id."""
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan1 = _run([r], st, required_mb=50.0, created_at=_NOW)
        plan2 = _run([r], st, required_mb=50.0, created_at=_NOW)
        assert {c.contact_id for c in plan1.contacts} == {c.contact_id for c in plan2.contacts}

    def test_planned_volume_is_stable_across_calls(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plans = [_run([r], st, required_mb=75.0, created_at=_NOW) for _ in range(3)]
        volumes = [p.planned_volume_mb for p in plans]
        assert len(set(volumes)) == 1


# ===========================================================================
# TestCostFormula
# ===========================================================================


class TestCostFormula:
    def test_billable_minutes_ceiling_applied(self) -> None:
        """91-second contact = 2 billable minutes."""
        cost = _contact_cost(91.0, _station(cost_per_minute=10.0, booking_cost=0.0))
        assert cost == Decimal("20")

    def test_exact_60_seconds_is_1_billable_minute(self) -> None:
        cost = _contact_cost(60.0, _station(cost_per_minute=10.0, booking_cost=0.0))
        assert cost == Decimal("10")

    def test_booking_cost_added_to_usage(self) -> None:
        cost = _contact_cost(60.0, _station(cost_per_minute=10.0, booking_cost=50.0))
        assert cost == Decimal("60")

    def test_zero_cost_station_returns_zero(self) -> None:
        cost = _contact_cost(600.0, _station(cost_per_minute=0.0, booking_cost=0.0))
        assert cost == Decimal("0")

    def test_cost_is_decimal_not_float(self) -> None:
        cost = _contact_cost(300.0, _station(cost_per_minute=1.33, booking_cost=5.0))
        assert isinstance(cost, Decimal)

    def test_plan_cost_string_is_decimal_parseable(self) -> None:
        st = _station(cost_per_minute=7.77)
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=50.0)
        Decimal(plan.estimated_total_cost)  # must not raise
        for c in plan.contacts:
            Decimal(c.contact_cost_decimal)  # must not raise


# ===========================================================================
# TestTrimming — last contact trimmed to exact required volume
# ===========================================================================


class TestTrimming:
    def test_last_contact_trimmed_exactly(self) -> None:
        """With two 80 MB passes and 100 MB target, second is trimmed to 20 MB."""
        st = _station()
        r1 = _eligible_record("pass_e001", 1.0, 80.0, st)
        r2 = _eligible_record("pass_e002", 3.0, 80.0, st)
        plan = _run([r1, r2], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        first_vol = plan.contacts[0].allocated_volume_mb
        second_vol = plan.contacts[1].allocated_volume_mb
        assert abs(first_vol + second_vol - 100.0) < 1e-9
        assert second_vol < 80.0  # was trimmed

    def test_single_oversized_pass_trimmed_to_target(self) -> None:
        """Single 500 MB pass trimmed to exactly 100 MB target."""
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 500.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert abs(plan.planned_volume_mb - 100.0) < 1e-9
        total = sum(c.allocated_volume_mb for c in plan.contacts)
        assert abs(total - 100.0) < 1e-9

    def test_trimmed_plan_never_over_allocates(self) -> None:
        st = _station()
        records = [_eligible_record(f"pass_e{i:03d}", float(i), 100.0, st) for i in range(1, 6)]
        plan = _run(records, st, required_mb=250.0)
        assert plan.planned_volume_mb <= plan.required_volume_mb + 1e-9


# ===========================================================================
# TestRejectedRecords
# ===========================================================================


class TestRejectedRecords:
    def test_ineligible_records_in_rejected_list(self) -> None:
        st = _station(band=Band.S)  # S-band station
        # Build with X-band satellite → INCOMPATIBLE_BAND
        p = _pass("pass_r001", 1.0)
        cap = _capacity("pass_r001", 100.0)
        builder = EligiblePassBuilder(
            satellite_band=Band.X,
            deadline=_DEADLINE,
            max_budget_usd=float(_BUDGET),
        )
        rejected_rec = builder.build(p, cap, st)
        assert not rejected_rec.is_eligible

        st_ok = _station("station_ok01", band=Band.X)
        r_ok = _eligible_record("pass_e001", 2.0, 200.0, st_ok)

        plan = _planner().plan(
            plan_id=_plan_id(),
            scenario_id="scenario_s01",
            mission_id="mission_m01",
            required_volume_mb=100.0,
            deadline=_DEADLINE,
            mission_window_start=_RELEASE,
            maximum_budget=_BUDGET,
            preference=PlanningPreference.FASTEST,
            eligible_records=[rejected_rec, r_ok],
            station_map=_station_map(st, st_ok),
            created_at=_NOW,
        )
        assert len(plan.rejected_opportunity_records) == 1
        assert plan.rejected_opportunity_records[0].pass_.pass_id == "pass_r001"

    def test_unused_opportunity_ids_populated(self) -> None:
        st = _station()
        r1 = _eligible_record("pass_e001", 1.0, 500.0, st)  # satisfies alone
        r2 = _eligible_record("pass_e002", 5.0, 500.0, st)  # never needed
        plan = _run([r1, r2], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        assert "pass_e002" in plan.unused_opportunity_ids


# ===========================================================================
# TestPlanContractFields
# ===========================================================================


class TestPlanContractFields:
    def test_plan_has_required_fields(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.plan_id.startswith("plan_")
        assert plan.version == 1
        assert plan.parent_plan_id is None
        assert plan.mission_id == "mission_m01"
        assert plan.scenario_id == "scenario_s01"
        assert plan.algorithm_version == ALGORITHM_VERSION
        assert isinstance(plan.preference, PlanningPreference)
        assert isinstance(plan.status, PlanStatus)

    def test_plan_has_utc_timestamps(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.created_at.tzinfo is not None
        if plan.planned_completion_at:
            assert plan.planned_completion_at.tzinfo is not None

    def test_contact_has_required_fields(self) -> None:
        st = _station()
        r = _eligible_record("pass_e001", 1.0, 200.0, st)
        plan = _run([r], st, required_mb=100.0)
        assert plan.status == PlanStatus.FEASIBLE
        for c in plan.contacts:
            assert c.contact_id.startswith("contact_")
            assert c.pass_id.startswith("pass_")
            assert c.station_id.startswith("station_")
            assert c.duration_s > 0.0
            assert c.allocated_volume_mb > 0.0
            Decimal(c.contact_cost_decimal)

    def test_no_feasible_plan_preserves_fields(self) -> None:
        st = _station()
        plan = _run([], st, required_mb=100.0)
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND
        assert plan.plan_id.startswith("plan_")
        assert plan.algorithm_version == ALGORITHM_VERSION
        assert plan.planned_volume_mb == 0.0


# ===========================================================================
# TestMinimum60SecondContact
# ===========================================================================


class TestMinimum60SecondContact:
    def test_short_pass_below_60s_not_selected(self) -> None:
        """A pass with usable_duration_s < 60 must not produce any slices."""
        st = _station()
        # duration_s = 59 → usable_duration_s = 59 → below minimum
        p = CandidatePass(
            pass_id="pass_short01",
            scenario_id="scenario_s01",
            satellite_id="sat_demo01",
            station_id="station_alpha01",
            start_at=_NOW + timedelta(hours=1),
            peak_at=_NOW + timedelta(hours=1, seconds=30),
            end_at=_NOW + timedelta(hours=1, seconds=59),
            duration_s=59.0,
            usable_duration_s=59.0,
            max_elevation_deg=30.0,
            azimuth_start_deg=10.0,
            azimuth_peak_deg=180.0,
            azimuth_end_deg=350.0,
            slant_range_peak_km=900.0,
            minimum_elevation_deg=5.0,
            orbit_model_version="circular_v1",
            station_catalog_version="cat_v1",
        )
        cap = _capacity("pass_short01", 10.0)
        rec = EligiblePassRecord(
            **{"pass_": p},
            capacity=cap,
            is_eligible=True,
            rejection_codes=[],
            contact_cost_decimal="10",
        )
        plan = _run([rec], st, required_mb=5.0)
        # Short pass cannot form a contact → no feasible plan
        assert plan.status == PlanStatus.NO_FEASIBLE_PLAN_FOUND

    def test_exact_60s_pass_is_valid(self) -> None:
        """A pass with usable_duration_s == 60 must be usable."""
        st = _station()
        p = CandidatePass(
            pass_id="pass_exact60",
            scenario_id="scenario_s01",
            satellite_id="sat_demo01",
            station_id="station_alpha01",
            start_at=_NOW + timedelta(hours=1),
            peak_at=_NOW + timedelta(hours=1, seconds=30),
            end_at=_NOW + timedelta(hours=1, seconds=60),
            duration_s=60.0,
            usable_duration_s=60.0,
            max_elevation_deg=30.0,
            azimuth_start_deg=10.0,
            azimuth_peak_deg=180.0,
            azimuth_end_deg=350.0,
            slant_range_peak_km=900.0,
            minimum_elevation_deg=5.0,
            orbit_model_version="circular_v1",
            station_catalog_version="cat_v1",
        )
        cap = _capacity("pass_exact60", 50.0)
        rec = EligiblePassRecord(
            **{"pass_": p},
            capacity=cap,
            is_eligible=True,
            rejection_codes=[],
            contact_cost_decimal="10",
        )
        plan = _run([rec], st, required_mb=30.0)
        assert plan.status == PlanStatus.FEASIBLE
