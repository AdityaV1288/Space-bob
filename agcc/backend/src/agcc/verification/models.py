"""Verification-only result contracts; no product behavior lives here."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class VerificationOutcome(BaseModel):
    name: str
    status: Literal["pass", "fail", "unsupported"]
    code: str
    details: dict[str, Any] = Field(default_factory=dict)


class BenchmarkMetrics(BaseModel):
    delivered_volume_mb: float
    expected_delivered_volume_mb: float
    cost: str
    deadline_met: bool
    contact_count: int
    station_utilization: dict[str, float]
    replan_count: int
    plan_churn: int
    runtime_ms: dict[str, float]
    objective_value: float


class BaselineComparison(BaseModel):
    greedy_contact_count: int
    earliest_feasible_contact_count: int
    exact_solver_status: Literal["not_installed", "not_run", "completed"]
    clear_capacity_mb: float
    degraded_capacity_mb: float
    nominal_expected_delivery_mb: float
    outage_expected_delivery_mb: float
    hysteresis_status: Literal["not_implemented"]


class VerificationReport(BaseModel):
    status: Literal["pass", "fail"]
    artifact_hash: str
    correctness: list[VerificationOutcome]
    failures: list[VerificationOutcome]
    metrics: BenchmarkMetrics
    baselines: BaselineComparison
    known_limitations: list[str]
