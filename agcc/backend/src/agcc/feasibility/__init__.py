"""Mission feasibility — eligible pass records, bounds, and relaxation suggestions.

Architecture
------------
EligiblePassRecord   — one pass evaluated against all hard filters; carries
                       capacity, cost, and rejection codes.

FeasibilityReport    — aggregate over all eligible passes for a scenario;
                       answers "can the mission be met before the deadline?"

FeasibilityChecker   — stateless, pure function that produces a FeasibilityReport
                       from a list of EligiblePassRecord objects and constraints.

Cost formula (always Decimal, never binary float)
-------------------------------------------------
    billable_minutes = ceil(usable_duration_s / 60)
    usage_cost       = billable_minutes * cost_per_minute
    contact_cost     = booking_cost + usage_cost

All money is stored as Decimal; inputs from float fields are converted via
str() to avoid binary-float rounding.
"""

from __future__ import annotations

import math
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Sequence

from pydantic import BaseModel, Field, field_validator

from agcc.domain.common import _require_utc
from agcc.domain.enums import RejectionCode
from agcc.domain.mission import PlanningPreference as PlanningPreference  # re-export
from agcc.domain.planning import CandidatePass, CapacityEstimate

# ---------------------------------------------------------------------------
# Feasibility status
# ---------------------------------------------------------------------------


class FeasibilityStatus(str, Enum):
    POTENTIALLY_FEASIBLE = "potentially_feasible"
    INFEASIBLE_CAPACITY = "infeasible_capacity"
    INFEASIBLE_BUDGET = "infeasible_budget"
    INFEASIBLE_DEADLINE = "infeasible_deadline"
    UNVERIFIED_ENVIRONMENT = "unverified_environment"


# ---------------------------------------------------------------------------
# EligiblePassRecord
# ---------------------------------------------------------------------------


class EligiblePassRecord(BaseModel):
    """A candidate pass evaluated for mission eligibility.

    Passes that fail any hard filter carry rejection codes and is_eligible=False;
    they are retained in the report for diagnostics and relaxation suggestions.
    """

    model_config = {"frozen": True}

    pass_: CandidatePass = Field(alias="pass_")
    capacity: CapacityEstimate
    is_eligible: bool
    rejection_codes: list[RejectionCode] = Field(default_factory=list)

    # Cost in the scenario currency (Decimal stored as str for Pydantic compat)
    contact_cost_decimal: str = Field(
        description="Full contact cost as Decimal string (booking + usage)"
    )

    model_config = {"frozen": True, "populate_by_name": True}


# ---------------------------------------------------------------------------
# Relaxation suggestions (pure data, no application)
# ---------------------------------------------------------------------------


class RelaxationSuggestions(BaseModel):
    """Calculated (not applied) suggestions for resolving infeasibility."""

    model_config = {"frozen": True}

    # Deadline: how many seconds past original deadline are needed
    deadline_extension_s: float | None = Field(
        default=None,
        description="Extra seconds beyond deadline to become capacity-feasible",
    )

    # Budget: how much more money is needed
    additional_budget_needed: str | None = Field(
        default=None,
        description="Additional budget needed as Decimal string",
    )

    # Stations: IDs in catalog but not authorized
    excluded_station_candidates: list[str] = Field(
        default_factory=list,
        description="Station IDs present in catalog but not in the authorized set",
    )

    # Target reduction: how many MB to drop off the required volume
    required_volume_reduction_mb: float | None = Field(
        default=None,
        description="Required volume reduction in MB to become feasible with available passes",
    )


# ---------------------------------------------------------------------------
# FeasibilityReport
# ---------------------------------------------------------------------------


class FeasibilityReport(BaseModel):
    """Feasibility analysis output for a scenario + mission pair."""

    model_config = {"frozen": True}

    scenario_id: str
    mission_id: str

    status: FeasibilityStatus

    # Capacity bounds
    total_eligible_capacity_mb: float = Field(ge=0.0)
    capacity_shortfall_mb: float = Field(ge=0.0)

    # Cost bound (Decimal string)
    minimum_possible_cost: str = Field(description="Lower-bound cost estimate as Decimal string")

    # Deadline bound
    earliest_possible_completion_at: datetime | None = Field(
        default=None,
        description="Earliest datetime when required volume can be accumulated (UTC)",
    )

    # Record detail
    eligible_count: int = Field(ge=0)
    total_count: int = Field(ge=0)
    records: list[EligiblePassRecord] = Field(default_factory=list)

    # Relaxation
    suggestions: RelaxationSuggestions | None = None

    @field_validator("earliest_possible_completion_at", mode="before")
    @classmethod
    def _check_utc(cls, v: object) -> object:
        if v is None:
            return v
        return _require_utc(v)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Cost calculation helpers
# ---------------------------------------------------------------------------

_ZERO = Decimal("0")
_ONE = Decimal("1")
_SIXTY = Decimal("60")


def _to_decimal(value: float) -> Decimal:
    """Convert a float to Decimal via string to avoid binary-float artifacts."""
    return Decimal(str(value))


def compute_contact_cost(
    usable_duration_s: float,
    booking_cost: float,
    cost_per_minute: float,
) -> Decimal:
    """Return the full contact cost using billable-minute rounding.

    billable_minutes = ceil(usable_duration_s / 60)
    usage_cost       = billable_minutes * cost_per_minute
    contact_cost     = booking_cost + usage_cost
    """
    billable_minutes = Decimal(math.ceil(usable_duration_s / 60))
    usage = billable_minutes * _to_decimal(cost_per_minute)
    return _to_decimal(booking_cost) + usage


# ---------------------------------------------------------------------------
# FeasibilityChecker
# ---------------------------------------------------------------------------


class FeasibilityChecker:
    """Stateless feasibility evaluator.

    Call ``check()`` with the pre-built list of eligible records, the scenario
    constraints, and the required mission parameters.  Returns a FeasibilityReport.
    """

    def check(
        self,
        *,
        scenario_id: str,
        mission_id: str,
        required_volume_mb: float,
        deadline: datetime,
        maximum_budget: Decimal,
        records: Sequence[EligiblePassRecord],
        catalog_station_ids: Sequence[str] | None = None,
        authorized_station_ids: Sequence[str] | None = None,
    ) -> FeasibilityReport:
        """Compute feasibility bounds and return a FeasibilityReport.

        Parameters
        ----------
        scenario_id, mission_id:
            Identifiers propagated to the report.
        required_volume_mb:
            Volume the mission requires (>0).
        deadline:
            Hard deadline UTC datetime.
        maximum_budget:
            Hard budget cap as Decimal (never binary float).
        records:
            Pre-evaluated EligiblePassRecord list (any order).
        catalog_station_ids:
            All station IDs available in the catalog (for relaxation suggestions).
        authorized_station_ids:
            Station IDs authorized in this scenario (for relaxation suggestions).
        """
        maximum_budget = Decimal(str(maximum_budget))
        eligible = [r for r in records if r.is_eligible]
        budget_relaxed = [
            r for r in records if set(r.rejection_codes).issubset({RejectionCode.BUDGET_EXCEEDED})
        ]
        total_count = len(records)
        eligible_count = len(eligible)

        # Sort eligible records chronologically for greedy accumulation
        eligible_sorted = sorted(eligible, key=lambda r: r.pass_.start_at)

        # --- Capacity bound ---
        total_eligible_capacity_mb = sum(r.capacity.usable_capacity_mb for r in eligible_sorted)
        capacity_shortfall_mb = max(0.0, required_volume_mb - total_eligible_capacity_mb)

        # --- Minimum cost lower bound (greedy cheapest-first) ---
        # Sort by cost ascending to find the cheapest way to reach required volume
        by_cost = sorted(
            eligible_sorted,
            key=lambda r: Decimal(r.contact_cost_decimal),
        )
        min_cost = _ZERO
        accumulated_for_cost = 0.0
        for r in by_cost:
            if accumulated_for_cost >= required_volume_mb:
                break
            min_cost += Decimal(r.contact_cost_decimal)
            accumulated_for_cost += r.capacity.usable_capacity_mb

        # --- Earliest completion (greedy chronological) ---
        earliest_completion: datetime | None = None
        accumulated = 0.0
        for r in eligible_sorted:
            accumulated += r.capacity.usable_capacity_mb
            if accumulated >= required_volume_mb:
                earliest_completion = r.pass_.end_at
                break

        # --- Status determination ---
        has_unverified = any(
            r.capacity.weather_data_quality in ("unavailable", "stale") for r in eligible_sorted
        )

        budget_relaxed_capacity = sum(r.capacity.usable_capacity_mb for r in budget_relaxed)
        if (
            capacity_shortfall_mb > 0.0
            and budget_relaxed_capacity >= required_volume_mb
            and any(RejectionCode.BUDGET_EXCEEDED in r.rejection_codes for r in records)
        ):
            status = FeasibilityStatus.INFEASIBLE_BUDGET
        elif capacity_shortfall_mb > 0.0:
            status = FeasibilityStatus.INFEASIBLE_CAPACITY
        elif earliest_completion is not None and earliest_completion > deadline:
            status = FeasibilityStatus.INFEASIBLE_DEADLINE
        elif Decimal(str(min_cost)) > maximum_budget:
            status = FeasibilityStatus.INFEASIBLE_BUDGET
        elif has_unverified and total_eligible_capacity_mb >= required_volume_mb:
            status = FeasibilityStatus.UNVERIFIED_ENVIRONMENT
        else:
            status = FeasibilityStatus.POTENTIALLY_FEASIBLE

        # --- Relaxation suggestions (only when infeasible) ---
        suggestions: RelaxationSuggestions | None = None
        if status != FeasibilityStatus.POTENTIALLY_FEASIBLE:
            suggestions = self._compute_suggestions(
                records=records,
                required_volume_mb=required_volume_mb,
                deadline=deadline,
                maximum_budget=maximum_budget,
                total_eligible_capacity_mb=total_eligible_capacity_mb,
                catalog_station_ids=catalog_station_ids,
                authorized_station_ids=authorized_station_ids,
            )

        return FeasibilityReport(
            scenario_id=scenario_id,
            mission_id=mission_id,
            status=status,
            total_eligible_capacity_mb=total_eligible_capacity_mb,
            capacity_shortfall_mb=capacity_shortfall_mb,
            minimum_possible_cost=str(min_cost),
            earliest_possible_completion_at=earliest_completion,
            eligible_count=eligible_count,
            total_count=total_count,
            records=list(records),
            suggestions=suggestions,
        )

    # ------------------------------------------------------------------

    def _compute_suggestions(
        self,
        *,
        records: Sequence[EligiblePassRecord],
        required_volume_mb: float,
        deadline: datetime,
        maximum_budget: Decimal,
        total_eligible_capacity_mb: float,
        catalog_station_ids: Sequence[str] | None,
        authorized_station_ids: Sequence[str] | None,
    ) -> RelaxationSuggestions:
        """Compute relaxation suggestions — pure calculations, never applied."""

        # 1. Deadline extension: how much later do we need the deadline to be?
        deadline_extension_s: float | None = None
        deadline_candidates = [
            r for r in records if set(r.rejection_codes).issubset({RejectionCode.DEADLINE_MISSED})
        ]
        all_records_sorted = sorted(deadline_candidates, key=lambda r: r.pass_.start_at)
        acc = 0.0
        for r in all_records_sorted:
            if r.capacity.usable_capacity_mb > 0:
                acc += r.capacity.usable_capacity_mb
                if acc >= required_volume_mb:
                    if r.pass_.end_at > deadline:
                        extension = (r.pass_.end_at - deadline).total_seconds()
                        deadline_extension_s = extension
                    break

        # 2. Additional budget: what does the cheapest set exceeding volume cost?
        eligible_sorted_by_cost = sorted(
            [
                r
                for r in records
                if set(r.rejection_codes).issubset({RejectionCode.BUDGET_EXCEEDED})
            ],
            key=lambda r: Decimal(r.contact_cost_decimal),
        )
        budget_dec = maximum_budget
        total_cost = _ZERO
        acc2 = 0.0
        for r in eligible_sorted_by_cost:
            if acc2 >= required_volume_mb:
                break
            total_cost += Decimal(r.contact_cost_decimal)
            acc2 += r.capacity.usable_capacity_mb

        additional_budget: str | None = None
        if total_cost > budget_dec and acc2 >= required_volume_mb:
            extra = total_cost - budget_dec
            additional_budget = str(extra.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

        # 3. Excluded station candidates
        excluded_candidates: list[str] = []
        if catalog_station_ids is not None and authorized_station_ids is not None:
            authorized_set = set(authorized_station_ids)
            excluded_candidates = [sid for sid in catalog_station_ids if sid not in authorized_set]

        # 4. Required volume reduction
        required_reduction: float | None = None
        if total_eligible_capacity_mb < required_volume_mb:
            required_reduction = required_volume_mb - total_eligible_capacity_mb

        return RelaxationSuggestions(
            deadline_extension_s=deadline_extension_s,
            additional_budget_needed=additional_budget,
            excluded_station_candidates=excluded_candidates,
            required_volume_reduction_mb=required_reduction,
        )
