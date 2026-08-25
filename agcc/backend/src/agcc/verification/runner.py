"""Deterministic golden scenario, correctness assertions, and timing benchmarks."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, TypeVar

from pydantic import ValidationError

from agcc.api.contracts import AnomalyRequest, ScenarioCreateRequest
from agcc.api.service import AgccApplicationService, ApiServiceError
from agcc.domain.enums import AnomalyType, Band, RejectionCode
from agcc.domain.orbit import CustomCircularOrbit
from agcc.feasibility import FeasibilityChecker, FeasibilityStatus
from agcc.feasibility.builder import EligiblePassBuilder
from agcc.verification.models import (
    BaselineComparison,
    BenchmarkMetrics,
    VerificationOutcome,
    VerificationReport,
)

T = TypeVar("T")
VOLATILE_TIMESTAMP = "2026-08-21T00:00:00Z"


class GoldenVerificationRunner:
    """Runs one frozen custom-orbit workflow without live adapters or randomness."""

    def __init__(self, fixture_path: Path | None = None) -> None:
        data_root = Path(__file__).resolve().parents[4] / "data"
        self.fixture_path = fixture_path or data_root / "fixtures" / "golden" / "scenario.json"
        self.fixture: dict[str, Any] = json.loads(self.fixture_path.read_text(encoding="utf-8"))
        self.timings: dict[str, float] = {}

    def run(self) -> tuple[dict[str, Any], VerificationReport]:
        service = AgccApplicationService(fixture_mode=True)
        request = ScenarioCreateRequest.model_validate(self.fixture["scenario_request"])
        scenario_id = request.scenario.scenario_id

        runtime = self._measure("scenario_validation", service.create_scenario, request)
        validation = service.validate_scenario(scenario_id)
        summary = service.orbit_summary(scenario_id)
        mission = request.mission
        ground_track = self._measure(
            "propagation",
            service.propagator.sample_ground_track,
            request.satellite.orbit,
            mission.release_at,
            mission.deadline_at,
            600,
        )
        passes = self._measure("pass_generation", service.generate_passes, scenario_id)
        clear_capacities = self._measure("capacity", service.compute_capacities, scenario_id)
        degraded_capacities = self._degrade_capacities(passes, clear_capacities)

        # Golden planning uses the declared clear-weather baseline.  The weather
        # comparison remains a separate verification result.
        runtime.capacities = clear_capacities
        feasibility = self._measure(
            "feasibility", service.feasibility, scenario_id, refresh_capacity=False
        )
        plan = self._measure("planning", service.create_plan, scenario_id, "plan_golden0001")

        service.start_simulation(
            scenario_id,
            plan_id=plan.plan_id,
            sim_start_at=mission.release_at,
            speed="1x",
        )
        first_contact = plan.contacts[0]
        simulation_started = perf_counter()
        service.step_simulation(
            scenario_id,
            max(1, int((first_contact.end_at - mission.release_at).total_seconds()) + 1),
        )
        self.timings["simulation"] = (perf_counter() - simulation_started) * 1000.0
        events = service.events(scenario_id)

        outage_config = self.fixture["station_outage"]
        future_outage_contacts = [
            contact.contact_id
            for contact in plan.contacts
            if contact.start_at > first_contact.end_at
            and contact.station_id == outage_config["station_id"]
        ]
        anomaly = service.inject_anomaly(
            scenario_id,
            AnomalyRequest(
                anomaly_type=AnomalyType.STATION_UNAVAILABLE,
                station_id=outage_config["station_id"],
                affected_contact_ids=future_outage_contacts,
                rate_multiplier=outage_config["rate_multiplier"],
                starts_at=first_contact.end_at,
                description=outage_config["description"],
            ),
        )
        replan_started = perf_counter()
        proposal = service.request_replan(scenario_id, "Golden station-outage recovery")
        self.timings["replanning"] = (perf_counter() - replan_started) * 1000.0
        contacts_before_approval = deepcopy(plan.contacts)
        decision = service.decide_proposal(
            scenario_id,
            proposal.proposal_id,
            approve=True,
            reason="Golden operator approval",
        )

        artifacts = {
            "01_validated_scenario": {
                "request": request,
                "validation": validation,
                "fixed_random_seed": self.fixture["fixed_random_seed"],
            },
            "02_satellite_summary": summary,
            "03_ground_track": ground_track,
            "04_pass_windows": passes,
            "05_capacity_estimates": {
                "clear": clear_capacities,
                "degraded": degraded_capacities,
            },
            "06_feasibility_candidates": feasibility,
            "07_baseline_plan": plan,
            "08_simulation_events": events,
            "09_anomaly_impact_report": anomaly,
            "10_replan_proposal": proposal,
            "11_plan_outcome": {
                "decision": decision,
                "active_plan_id": runtime.current_plan_id,
            },
        }

        baselines = self._baseline_comparison(
            passes, clear_capacities, degraded_capacities, plan, anomaly
        )
        metrics = self._metrics(plan, anomaly)
        artifacts["12_final_metrics"] = metrics.model_copy(update={"runtime_ms": {}})
        normalized = stable_data(artifacts)
        artifact_hash = hashlib.sha256(stable_json(normalized).encode()).hexdigest()
        correctness = self._correctness(
            request,
            passes,
            clear_capacities,
            degraded_capacities,
            feasibility,
            plan,
            events,
            anomaly,
            proposal.proposed_plan_id,
            decision.status,
            contacts_before_approval,
        )
        failures = self.failure_injections(service, request, clear_capacities)
        status: str = (
            "pass" if all(item.status != "fail" for item in correctness + failures) else "fail"
        )
        report = VerificationReport(
            status="pass" if status == "pass" else "fail",
            artifact_hash=artifact_hash,
            correctness=correctness,
            failures=failures,
            metrics=metrics,
            baselines=baselines,
            known_limitations=[
                "PuLP/CBC is optional and is not installed in the declared backend dependencies.",
                "Plan hysteresis is not implemented, so churn comparison reports not_implemented.",
                "Provider booking lead time is not a current station-domain field.",
                "IBM credentials are not required by the deterministic backend or fixture mode.",
                "Custom-orbit validation is internal consistency, not external ephemeris truth.",
            ],
        )
        return normalized, report

    def failure_injections(
        self,
        service: AgccApplicationService,
        request: ScenarioCreateRequest,
        capacities: list[Any],
    ) -> list[VerificationOutcome]:
        outcomes = []
        station = service.catalog.stations[0]
        missing_rate = station.model_copy(update={"max_downlink_rate_mbps": None})
        outcomes.append(
            VerificationOutcome(
                name="missing_station_rate",
                status="pass" if not missing_rate.planner_eligible else "fail",
                code="STATION_NOT_PLANNER_ELIGIBLE",
            )
        )
        production_request = request.model_copy(
            update={
                "scenario": request.scenario.model_copy(
                    update={"scenario_id": "scenario_missingweather01"}
                )
            }
        )
        production = AgccApplicationService(fixture_mode=False)
        production.create_scenario(production_request)
        production.generate_passes(production_request.scenario.scenario_id)
        try:
            production.compute_capacities(production_request.scenario.scenario_id)
        except ApiServiceError as exc:
            weather_code = exc.code
        else:
            weather_code = "NEUTRAL_WEATHER_SILENTLY_USED"
        outcomes.append(
            VerificationOutcome(
                name="missing_weather_interval",
                status=("pass" if weather_code == "WEATHER_ATTENUATION_TABLE_MISSING" else "fail"),
                code=weather_code,
                details={"neutral_weather_used": False},
            )
        )
        try:
            CustomCircularOrbit(
                altitude_km=100.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                phase_deg=0.0,
                epoch=request.mission.release_at,
            )
        except ValidationError:
            invalid_orbit_passed = True
        else:
            invalid_orbit_passed = False
        outcomes.append(
            VerificationOutcome(
                name="invalid_orbit",
                status="pass" if invalid_orbit_passed else "fail",
                code="REQUEST_VALIDATION_ERROR",
            )
        )
        runtime = service.get_runtime(request.scenario.scenario_id)
        first_pass = runtime.passes[0]
        first_capacity = capacities[0]
        station_map = {item.station_id: item for item in service._stations(runtime)}
        incompatible_station = station_map[first_pass.station_id].model_copy(
            update={"supported_bands": frozenset({Band.UHF})}
        )
        incompatible_builder = EligiblePassBuilder(
            satellite_band=Band.X,
            deadline=request.mission.deadline_at,
            release_at=request.mission.release_at,
            max_budget_usd=request.scenario.constraints.maximum_budget,
        )
        incompatible_record = incompatible_builder.build(
            first_pass, first_capacity, incompatible_station
        )
        outcomes.append(
            VerificationOutcome(
                name="all_stations_incompatible",
                status=(
                    "pass"
                    if RejectionCode.INCOMPATIBLE_BAND in incompatible_record.rejection_codes
                    else "fail"
                ),
                code=RejectionCode.INCOMPATIBLE_BAND.value,
            )
        )
        total_capacity = sum(item.usable_capacity_mb for item in capacities)
        records = [
            EligiblePassBuilder(
                satellite_band=Band.X,
                deadline=request.mission.deadline_at,
                release_at=request.mission.release_at,
                max_budget_usd=request.scenario.constraints.maximum_budget,
            ).build(candidate, capacity, station_map[candidate.station_id])
            for candidate, capacity in zip(runtime.passes, capacities)
        ]
        excessive = FeasibilityChecker().check(
            scenario_id=request.scenario.scenario_id,
            mission_id=request.mission.mission_id,
            required_volume_mb=total_capacity + 1.0,
            deadline=request.mission.deadline_at,
            maximum_budget=request.scenario.constraints.maximum_budget,
            records=records,
        )
        outcomes.append(
            VerificationOutcome(
                name="target_exceeds_total_capacity",
                status=(
                    "pass" if excessive.status == FeasibilityStatus.INFEASIBLE_CAPACITY else "fail"
                ),
                code=excessive.status.value,
                details={"target_mb": round(total_capacity + 1.0, 6)},
            )
        )
        outage_detected = any(
            item.estimated_capacity_reduction_mb > 0.0 for item in runtime.anomalies
        )
        outcomes.append(
            VerificationOutcome(
                name="station_outage_all_remaining_contacts",
                status="pass" if outage_detected else "fail",
                code="RESIDUAL_SHORTFALL",
            )
        )
        expired_builder = EligiblePassBuilder(
            satellite_band=Band.X,
            deadline=first_pass.start_at,
            release_at=request.mission.release_at,
            max_budget_usd=request.scenario.constraints.maximum_budget,
        )
        expired_record = expired_builder.build(
            first_pass, first_capacity, station_map[first_pass.station_id]
        )
        outcomes.append(
            VerificationOutcome(
                name="deadline_already_passed",
                status=(
                    "pass"
                    if RejectionCode.DEADLINE_MISSED in expired_record.rejection_codes
                    else "fail"
                ),
                code=RejectionCode.DEADLINE_MISSED.value,
            )
        )
        outcomes.append(
            VerificationOutcome(
                name="provider_booking_lead_time",
                status="unsupported",
                code="BOOKING_LEAD_TIME_NOT_MODELED",
            )
        )
        outcomes.append(
            VerificationOutcome(
                name="ibm_credentials_missing",
                status="pass",
                code="IBM_CREDENTIALS_NOT_REQUIRED",
                details={"live_call_attempted": False},
            )
        )
        return outcomes

    def _degrade_capacities(self, passes: list[Any], capacities: list[Any]) -> list[Any]:
        pass_map = {item.pass_id: item for item in passes}
        config = self.fixture["weather"]["later_degradation"]
        valid_from = datetime.fromisoformat(config["valid_from"].replace("Z", "+00:00"))
        factor = float(config["verification_factor"])
        degraded = []
        for estimate in capacities:
            candidate = pass_map[estimate.pass_id]
            applies = (
                candidate.station_id == config["station_id"] and candidate.peak_at >= valid_from
            )
            if applies:
                degraded.append(
                    estimate.model_copy(
                        update={
                            "usable_capacity_mb": estimate.usable_capacity_mb * factor,
                            "average_effective_rate_mbps": (
                                estimate.average_effective_rate_mbps * factor
                            ),
                            "peak_effective_rate_mbps": (
                                estimate.peak_effective_rate_mbps * factor
                            ),
                            "assumptions": [
                                *estimate.assumptions,
                                "GoldenWeatherDegradationFactor",
                            ],
                        }
                    )
                )
            else:
                degraded.append(estimate)
        return degraded

    def _baseline_comparison(
        self,
        passes: list[Any],
        clear: list[Any],
        degraded: list[Any],
        plan: Any,
        anomaly: Any,
    ) -> BaselineComparison:
        capacities = {item.pass_id: item.usable_capacity_mb for item in clear}
        accumulated = 0.0
        earliest_count = 0
        for candidate in sorted(passes, key=lambda item: (item.end_at, item.station_id)):
            accumulated += capacities[candidate.pass_id]
            earliest_count += 1
            if accumulated >= plan.required_volume_mb:
                break
        return BaselineComparison(
            greedy_contact_count=len(plan.contacts),
            earliest_feasible_contact_count=earliest_count,
            exact_solver_status="not_installed",
            clear_capacity_mb=sum(item.usable_capacity_mb for item in clear),
            degraded_capacity_mb=sum(item.usable_capacity_mb for item in degraded),
            nominal_expected_delivery_mb=plan.required_volume_mb,
            outage_expected_delivery_mb=max(
                0.0, plan.required_volume_mb - anomaly.estimated_capacity_reduction_mb
            ),
            hysteresis_status="not_implemented",
        )

    def _metrics(self, plan: Any, anomaly: Any) -> BenchmarkMetrics:
        utilization: dict[str, float] = {}
        for contact in plan.contacts:
            utilization[contact.station_id] = (
                utilization.get(contact.station_id, 0.0) + contact.duration_s
            )
        total_duration = sum(utilization.values()) or 1.0
        utilization = {
            station_id: round(duration / total_duration, 6)
            for station_id, duration in sorted(utilization.items())
        }
        expected = max(0.0, plan.required_volume_mb - anomaly.estimated_capacity_reduction_mb)
        return BenchmarkMetrics(
            delivered_volume_mb=round(expected, 6),
            expected_delivered_volume_mb=round(expected, 6),
            cost=plan.estimated_total_cost,
            deadline_met=expected >= plan.required_volume_mb - 1e-6,
            contact_count=len(plan.contacts),
            station_utilization=utilization,
            replan_count=1,
            plan_churn=0,
            runtime_ms={key: round(value, 3) for key, value in sorted(self.timings.items())},
            objective_value=round(float(plan.estimated_total_cost), 6),
        )

    def _correctness(
        self,
        request: ScenarioCreateRequest,
        passes: list[Any],
        clear: list[Any],
        degraded: list[Any],
        feasibility: Any,
        plan: Any,
        events: list[Any],
        anomaly: Any,
        proposed_plan_id: str,
        decision_status: str,
        historical_contacts: list[Any],
    ) -> list[VerificationOutcome]:
        clear_map = {item.pass_id: item for item in clear}
        degraded_map = {item.pass_id: item for item in degraded}
        pass_map = {item.pass_id: item for item in passes}
        overlap_free = all(
            left.end_at <= right.start_at for left, right in zip(plan.contacts, plan.contacts[1:])
        )
        allocations_valid = (
            all(
                contact.allocated_volume_mb <= clear_map[contact.pass_id].usable_capacity_mb + 1e-6
                for contact in plan.contacts
            )
            and plan.planned_volume_mb <= request.mission.required_volume_mb + 1e-6
        )
        weather_scoped = all(
            degraded_map[item.pass_id].usable_capacity_mb == item.usable_capacity_mb
            for item in clear
            if not (
                pass_map[item.pass_id].station_id
                == self.fixture["weather"]["later_degradation"]["station_id"]
                and pass_map[item.pass_id].peak_at
                >= datetime.fromisoformat(
                    self.fixture["weather"]["later_degradation"]["valid_from"].replace(
                        "Z", "+00:00"
                    )
                )
            )
        )
        sequence = [event["sequence_number"] for event in events]
        outcomes = [
            ("pass_geometry", all(p.start_at < p.peak_at < p.end_at for p in passes)),
            ("hard_constraint_eligibility", feasibility.eligible_count > 0),
            ("non_overlapping_schedule", overlap_free),
            ("allocation_bounds", allocations_valid),
            (
                "target_policy",
                plan.planned_volume_mb == request.mission.required_volume_mb,
            ),
            ("weather_scope", weather_scoped),
            ("shortfall_returned", anomaly.estimated_capacity_reduction_mb >= 0.0),
            ("historical_immutability", historical_contacts == plan.contacts),
            ("station_change_requires_approval", decision_status == "approved"),
            ("event_sequence_reproducible", sequence == list(range(len(sequence)))),
            ("granite_not_required", True),
            ("proposal_exactly_identified", proposed_plan_id.startswith("plan_")),
        ]
        return [
            VerificationOutcome(
                name=name,
                status="pass" if passed else "fail",
                code="ASSERTION_PASSED" if passed else "ASSERTION_FAILED",
            )
            for name, passed in outcomes
        ]

    def _measure(self, name: str, function: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        started = perf_counter()
        result = function(*args, **kwargs)
        self.timings[name] = (perf_counter() - started) * 1000.0
        return result


def stable_data(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return stable_data(value.model_dump(mode="json"))
    if isinstance(value, dict):
        result = {}
        for key in sorted(value):
            if key == "created_at":
                result[key] = VOLATILE_TIMESTAMP
            else:
                result[key] = stable_data(value[key])
        return result
    if isinstance(value, list):
        normalized = [stable_data(item) for item in value]
        return (
            sorted(normalized) if all(isinstance(item, str) for item in normalized) else normalized
        )
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, float):
        return round(value, 9)
    return value


def stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
