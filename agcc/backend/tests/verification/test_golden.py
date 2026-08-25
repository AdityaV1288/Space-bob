"""Golden scenario, determinism, correctness, and failure-injection assertions."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agcc.verification.runner import GoldenVerificationRunner


@pytest.fixture(scope="module")
def golden() -> tuple[dict[str, object], object]:
    return GoldenVerificationRunner().run()


def test_all_twelve_stages_are_persisted(
    golden: tuple[dict[str, object], object],
) -> None:
    artifacts, _ = golden
    assert list(artifacts) == [
        "01_validated_scenario",
        "02_satellite_summary",
        "03_ground_track",
        "04_pass_windows",
        "05_capacity_estimates",
        "06_feasibility_candidates",
        "07_baseline_plan",
        "08_simulation_events",
        "09_anomaly_impact_report",
        "10_replan_proposal",
        "11_plan_outcome",
        "12_final_metrics",
    ]
    golden_dir = Path(__file__).resolve().parents[3] / "data" / "golden"
    for name, value in artifacts.items():
        expected = json.loads((golden_dir / f"{name}.json").read_text(encoding="utf-8"))
        assert expected == value


def test_golden_scenario_has_multiple_stations_passes_and_contacts(
    golden: tuple[dict[str, object], object],
) -> None:
    artifacts, _ = golden
    scenario = artifacts["01_validated_scenario"]["request"]["scenario"]
    assert len(scenario["station_ids"]) >= 4
    assert len(artifacts["04_pass_windows"]) > 4
    assert len(artifacts["07_baseline_plan"]["contacts"]) > 2


def test_weather_degradation_is_scoped_and_reduces_capacity(
    golden: tuple[dict[str, object], object],
) -> None:
    artifacts, report = golden
    capacity = artifacts["05_capacity_estimates"]
    clear = {item["pass_id"]: item for item in capacity["clear"]}
    degraded = {item["pass_id"]: item for item in capacity["degraded"]}
    changed = [
        pass_id
        for pass_id in clear
        if degraded[pass_id]["usable_capacity_mb"] < clear[pass_id]["usable_capacity_mb"]
    ]
    assert changed
    assert report.baselines.degraded_capacity_mb < report.baselines.clear_capacity_mb


def test_correctness_and_failure_injections_have_structured_outcomes(
    golden: tuple[dict[str, object], object],
) -> None:
    _, report = golden
    assert report.status == "pass"
    assert all(item.status == "pass" for item in report.correctness)
    failure_map = {item.name: item for item in report.failures}
    assert failure_map["missing_station_rate"].status == "pass"
    assert failure_map["missing_weather_interval"].code == ("WEATHER_ATTENUATION_TABLE_MISSING")
    assert failure_map["invalid_orbit"].status == "pass"
    assert failure_map["all_stations_incompatible"].status == "pass"
    assert failure_map["target_exceeds_total_capacity"].status == "pass"
    assert failure_map["station_outage_all_remaining_contacts"].status == "pass"
    assert failure_map["deadline_already_passed"].status == "pass"
    assert failure_map["provider_booking_lead_time"].status == "unsupported"
    assert failure_map["ibm_credentials_missing"].details["live_call_attempted"] is False


def test_event_sequence_and_golden_hash_are_reproducible(
    golden: tuple[dict[str, object], object],
) -> None:
    artifacts, first_report = golden
    events = artifacts["08_simulation_events"]
    assert [event["sequence_number"] for event in events] == list(range(len(events)))
    second_artifacts, second_report = GoldenVerificationRunner().run()
    assert second_artifacts == artifacts
    assert second_report.artifact_hash == first_report.artifact_hash


def test_performance_stages_are_measured_separately(
    golden: tuple[dict[str, object], object],
) -> None:
    _, report = golden
    assert {
        "propagation",
        "pass_generation",
        "capacity",
        "feasibility",
        "planning",
        "simulation",
        "replanning",
    }.issubset(report.metrics.runtime_ms)
    assert all(value >= 0.0 for value in report.metrics.runtime_ms.values())
