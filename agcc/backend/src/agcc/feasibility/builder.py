"""EligiblePassBuilder — applies hard filters to produce EligiblePassRecord list.

Hard filters applied in order
------------------------------
1. Pass deadline: pass.start_at < deadline (pass must start before deadline)
2. Band compatibility: satellite band ∈ station.supported_bands
3. Station availability: station.enabled and station.planner_eligible
4. Usable duration > 0: usable_duration_s > 0
5. Budget: contact_cost <= max_budget_usd (per-contact soft check for record)

Each rejected record is retained with one or more RejectionCode values.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from agcc.domain.enums import Band, RejectionCode
from agcc.domain.planning import CandidatePass, CapacityEstimate
from agcc.domain.stations import GroundStation
from agcc.feasibility import EligiblePassRecord, compute_contact_cost


class EligiblePassBuilder:
    """Converts (pass, capacity, station) triples into EligiblePassRecord list.

    Parameters
    ----------
    satellite_band:
        The RF band of the satellite, from SatelliteCommunications.band.
    deadline:
        Hard mission deadline.  Passes starting at or after this time are rejected.
    max_budget_usd:
        Per-contact budget cap.
    """

    def __init__(
        self,
        *,
        satellite_band: Band,
        deadline: datetime,
        max_budget_usd: Decimal | float,
        release_at: datetime | None = None,
    ) -> None:
        self._band = satellite_band
        self._deadline = deadline
        self._budget = Decimal(str(max_budget_usd))
        self._release_at = release_at or datetime.min.replace(tzinfo=timezone.utc)

    def build(
        self,
        pass_: CandidatePass,
        capacity: CapacityEstimate,
        station: GroundStation,
    ) -> EligiblePassRecord:
        """Evaluate a single pass and return an EligiblePassRecord."""
        rejection_codes: list[RejectionCode] = []

        # 1. Deadline: pass must start before the deadline
        if pass_.start_at >= self._deadline or pass_.end_at <= self._release_at:
            rejection_codes.append(RejectionCode.DEADLINE_MISSED)

        # 2. Band compatibility
        if station.supported_bands is None or self._band not in station.supported_bands:
            rejection_codes.append(RejectionCode.INCOMPATIBLE_BAND)

        # 3. Station availability
        if not station.enabled or not station.planner_eligible:
            rejection_codes.append(RejectionCode.STATION_UNAVAILABLE)

        # 4. Usable duration
        if pass_.usable_duration_s <= 0.0:
            rejection_codes.append(RejectionCode.BELOW_ELEVATION)

        # 5. Per-contact budget check
        cost = compute_contact_cost(
            pass_.usable_duration_s,
            station.booking_cost,
            station.cost_per_minute,
        )
        if cost > self._budget:
            rejection_codes.append(RejectionCode.BUDGET_EXCEEDED)

        return EligiblePassRecord(
            **{"pass_": pass_},
            capacity=capacity,
            is_eligible=len(rejection_codes) == 0,
            rejection_codes=rejection_codes,
            contact_cost_decimal=str(cost),
        )

    def build_all(
        self,
        triples: Sequence[tuple[CandidatePass, CapacityEstimate, GroundStation]],
    ) -> list[EligiblePassRecord]:
        """Evaluate a sequence of (pass, capacity, station) triples."""
        return [self.build(p, c, s) for p, c, s in triples]
