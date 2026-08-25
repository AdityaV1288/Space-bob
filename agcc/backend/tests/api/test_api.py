"""Acceptance coverage for Task 13 backend API integration."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from agcc.api.app import create_app
from agcc.api.contracts import EventSubscriptionMessage
from agcc.api.service import AgccApplicationService

NOW = datetime(2026, 8, 21, 0, 0, tzinfo=timezone.utc)
SCENARIO_ID = "scenario_api01"


def scenario_payload() -> dict[str, object]:
    station_ids = [
        "station_demo_australia",
        "station_demo_centraleurope",
        "station_demo_eastafrica",
        "station_demo_eastasia",
        "station_demo_eastcoastna",
        "station_demo_iceland",
        "station_demo_northarctic",
        "station_demo_southafrica",
        "station_demo_southamerica",
        "station_demo_westcoastna",
    ]
    constraints = {
        "maximum_budget": "100000",
        "currency": "USD",
        "station_selection": {"allow_all_eligible": True},
        "planning_preference": "fastest",
        "allow_additional_contact_proposals": True,
    }
    return {
        "scenario": {
            "scenario_id": SCENARIO_ID,
            "name": "API fixture scenario",
            "satellite_id": "sat_api01",
            "station_ids": station_ids,
            "mission_id": "mission_api01",
            "constraints": constraints,
        },
        "satellite": {
            "satellite_id": "sat_api01",
            "name": "API test satellite",
            "orbit": {
                "altitude_km": 550.0,
                "inclination_deg": 53.0,
                "raan_deg": 20.0,
                "phase_deg": 10.0,
                "epoch": NOW.isoformat(),
            },
            "comms": {
                "band": "X",
                "carrier_frequency_ghz": 9.6,
                "max_downlink_rate_mbps": 100.0,
                "protocol_efficiency": 0.9,
                "polarization": "circular",
                "min_elevation_deg": 5.0,
            },
            "provenance": {
                "source_type": "manual",
                "source_name": "api-test",
                "fetched_at": NOW.isoformat(),
                "assumption_fields": ["orbit", "comms"],
            },
        },
        "mission": {
            "mission_id": "mission_api01",
            "name": "Downlink one megabyte",
            "required_volume_mb": 1.0,
            "release_at": NOW.isoformat(),
            "deadline_at": (NOW + timedelta(days=1)).isoformat(),
        },
    }


@pytest.fixture(scope="module")
def api() -> Iterator[tuple[TestClient, AgccApplicationService]]:
    service = AgccApplicationService(fixture_mode=True)
    with TestClient(create_app(service)) as client:
        yield client, service


@pytest.fixture(scope="module")
def created(api: tuple[TestClient, AgccApplicationService]) -> TestClient:
    client, _ = api
    response = client.post("/api/scenarios", json=scenario_payload())
    assert response.status_code == 200, response.text
    return client


def assert_envelope(payload: dict[str, object]) -> None:
    assert payload["schema_version"] == "api.v1"
    assert isinstance(payload["request_id"], str)
    assert "scenario_id" in payload
    assert "error" in payload


def ensure_plan(client: TestClient) -> dict[str, object]:
    passes = client.get(f"/api/scenarios/{SCENARIO_ID}/passes")
    assert passes.status_code == 200, passes.text
    capacity = client.post(f"/api/scenarios/{SCENARIO_ID}/capacity", json={})
    assert capacity.status_code == 200, capacity.text
    plan = client.post(f"/api/scenarios/{SCENARIO_ID}/plans", json={"plan_id": "plan_api00000001"})
    assert plan.status_code == 200, plan.text
    assert plan.json()["data"]["status"] == "feasible"
    return plan.json()


def test_create_and_load_valid_scenario(created: TestClient) -> None:
    response = created.get(f"/api/scenarios/{SCENARIO_ID}")
    assert response.status_code == 200
    payload = response.json()
    assert_envelope(payload)
    assert payload["data"]["scenario"]["scenario_id"] == SCENARIO_ID


def test_invalid_scenario_returns_structured_4xx(
    api: tuple[TestClient, AgccApplicationService],
) -> None:
    client, _ = api
    invalid = scenario_payload()
    invalid["scenario"]["scenario_id"] = "bad"  # type: ignore[index]
    response = client.post("/api/scenarios", json=invalid)
    assert response.status_code == 422
    payload = response.json()
    assert_envelope(payload)
    assert payload["error"]["code"] == "REQUEST_VALIDATION_ERROR"
    assert payload["error"]["details"]["errors"]


def test_orbit_summary_and_ground_track(created: TestClient) -> None:
    summary = created.post(f"/api/scenarios/{SCENARIO_ID}/orbit/summary")
    assert summary.status_code == 200
    assert summary.json()["data"]["period_s"] > 0
    track = created.get(
        f"/api/scenarios/{SCENARIO_ID}/ground-track",
        params={
            "start_at": NOW.isoformat(),
            "end_at": (NOW + timedelta(minutes=5)).isoformat(),
            "step_s": 60,
        },
    )
    assert track.status_code == 200
    assert len(track.json()["data"]) == 6


def test_pass_capacity_feasibility_and_deterministic_plan(created: TestClient) -> None:
    passes = created.get(f"/api/scenarios/{SCENARIO_ID}/passes")
    assert passes.status_code == 200
    assert len(passes.json()["data"]) > 0
    capacity = created.post(f"/api/scenarios/{SCENARIO_ID}/capacity", json={})
    assert capacity.status_code == 200
    assert "NoWeatherAttenuationModel" in capacity.json()["assumptions"]
    feasibility = created.post(
        f"/api/scenarios/{SCENARIO_ID}/feasibility",
        json={"refresh_capacity": False},
    )
    assert feasibility.status_code == 200
    first = created.post(
        f"/api/scenarios/{SCENARIO_ID}/plans", json={"plan_id": "plan_api00000001"}
    )
    second = created.post(
        f"/api/scenarios/{SCENARIO_ID}/plans", json={"plan_id": "plan_api00000001"}
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["data"]["contacts"] == second.json()["data"]["contacts"]


def test_simulation_anomaly_replan_approval_events_and_export(
    created: TestClient,
    api: tuple[TestClient, AgccApplicationService],
) -> None:
    plan_payload = ensure_plan(created)
    plan = plan_payload["data"]
    start = created.post(
        f"/api/scenarios/{SCENARIO_ID}/simulation/start",
        json={"plan_id": plan["plan_id"]},
    )
    assert start.status_code == 200
    before = start.json()["data"]["sim_time"]
    step = created.post(f"/api/scenarios/{SCENARIO_ID}/simulation/step", json={"seconds": 60})
    assert step.status_code == 200
    assert step.json()["data"]["sim_time"] != before

    messages: list[EventSubscriptionMessage] = []
    _, service = api
    unsubscribe = service.subscriptions.subscribe(SCENARIO_ID, messages.append)
    anomaly = created.post(
        f"/api/scenarios/{SCENARIO_ID}/anomalies",
        json={
            "anomaly_type": "station_unavailable",
            "rate_multiplier": 0.5,
            "description": "Fixture station degradation",
        },
    )
    unsubscribe()
    assert anomaly.status_code == 200
    assert anomaly.json()["data"]["estimated_capacity_reduction_mb"] > 0
    assert messages and messages[-1].anomalies

    replan = created.post(
        f"/api/scenarios/{SCENARIO_ID}/replans", json={"reason": "Recover anomaly loss"}
    )
    assert replan.status_code == 200
    proposal = replan.json()["data"]
    assert proposal["status"] == "pending"
    approve = created.post(
        f"/api/scenarios/{SCENARIO_ID}/proposals/{proposal['proposal_id']}/approve",
        json={"reason": "Operator accepted exact proposal"},
    )
    assert approve.status_code == 200
    assert approve.json()["current_plan_id"] == proposal["proposed_plan_id"]

    events = created.get(f"/api/scenarios/{SCENARIO_ID}/events")
    assert events.status_code == 200
    assert events.json()["data"][0]["event_type"] == "simulation_started"
    exported = created.get(f"/api/scenarios/{SCENARIO_ID}/export/plan.json")
    fetched = created.get(f"/api/scenarios/{SCENARIO_ID}/plans/{proposal['proposed_plan_id']}")
    assert exported.json()["data"] == fetched.json()["data"]


def test_health_diagnostics_and_fixture_mode(created: TestClient) -> None:
    health = created.get("/health", headers={"X-Request-ID": "request_test01"})
    assert health.status_code == 200
    assert health.headers["X-Request-ID"] == "request_test01"
    diagnostics = created.get("/diagnostics")
    assert diagnostics.status_code == 200
    data = diagnostics.json()["data"]
    assert data["adapter_mode"] == "fixture"
    assert data["active_scenario_count"] == 1


def test_production_mode_never_silently_uses_fixture_weather() -> None:
    service = AgccApplicationService(fixture_mode=False)
    with TestClient(create_app(service)) as client:
        assert client.post("/api/scenarios", json=scenario_payload()).status_code == 200
        assert client.get(f"/api/scenarios/{SCENARIO_ID}/passes").status_code == 200
        response = client.post(f"/api/scenarios/{SCENARIO_ID}/capacity", json={})
        assert response.status_code == 503
        assert response.json()["error"]["code"] == "WEATHER_ATTENUATION_TABLE_MISSING"
