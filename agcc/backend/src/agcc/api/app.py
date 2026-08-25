"""FastAPI composition root and thin Task 13 route handlers."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.routing import APIRouter
from pydantic import ValidationError

from agcc.api.contracts import (
    SCHEMA_VERSION,
    AnomalyRequest,
    ApiEnvelope,
    CapacityRequest,
    FeasibilityRequest,
    HorizonRequest,
    PlanRequest,
    ProposalDecisionRequest,
    ReplanRequest,
    ScenarioCreateRequest,
    SimulationStartRequest,
    SimulationStepRequest,
)
from agcc.api.service import AgccApplicationService, ApiServiceError
from agcc.api.v1 import AppContainer, create_v1_router
from agcc.explanations import ExplanationRequest, ExplanationService
from agcc.granite import GraniteExplanationRequest, GraniteExplanationService


def create_app(service: AgccApplicationService | None = None) -> FastAPI:
    application_service = service or AgccApplicationService()
    app = FastAPI(title="AGCC Backend", version="0.1.0")
    app.state.agcc_service = application_service
    app.state.explanation_service = ExplanationService()
    app.state.granite_explanation_service = GraniteExplanationService()
    app.state.container = AppContainer()

    @app.middleware("http")
    async def correlation_id(request: Request, call_next: Any) -> Any:
        request.state.request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    @app.exception_handler(ApiServiceError)
    async def service_error(request: Request, exc: ApiServiceError) -> JSONResponse:
        return _error_response(
            request,
            exc.status_code,
            exc.code,
            exc.message,
            entity_refs=exc.entity_refs,
            details=exc.details,
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        detail: dict[str, Any] = exc.detail if isinstance(exc.detail, dict) else {}
        return _error_response(
            request,
            exc.status_code,
            str(detail.get("code", "HTTP_ERROR")),
            str(detail.get("message", exc.detail)),
            entity_refs=detail.get("entity_refs", {}),
            details=detail.get("details", {}),
        )

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            request,
            422,
            "VALIDATION_ERROR"
            if request.url.path.startswith("/api/v1")
            else "REQUEST_VALIDATION_ERROR",
            "Request validation failed",
            details={"errors": exc.errors()},
        )

    @app.exception_handler(ValidationError)
    async def model_validation_error(request: Request, exc: ValidationError) -> JSONResponse:
        return _error_response(
            request,
            422,
            "DOMAIN_VALIDATION_ERROR",
            "Domain validation failed",
            details={"errors": exc.errors()},
        )

    @app.get("/health")
    def health(request: Request) -> ApiEnvelope:
        return _ok(request, None, {"status": "ok"})

    @app.get("/diagnostics")
    def diagnostics(request: Request) -> ApiEnvelope:
        return _ok(request, None, application_service.diagnostics())

    @app.post("/api/explanations")
    def create_explanation(request: Request, body: ExplanationRequest) -> ApiEnvelope:
        """Generate grounded text without granting the model access to domain mutations."""
        response = app.state.explanation_service.explain(body)
        return _ok(request, None, response, current_plan_id=body.plan_id)

    @app.get("/api/explanations/{explanation_request_id}")
    def get_explanation(request: Request, explanation_request_id: str) -> ApiEnvelope:
        try:
            record = app.state.explanation_service.store.get(explanation_request_id)
        except KeyError as exc:
            raise ApiServiceError(
                404,
                "EXPLANATION_NOT_FOUND",
                f"Explanation not found: {explanation_request_id}",
                entity_refs={"request_id": explanation_request_id},
            ) from exc
        return _ok(request, None, record, current_plan_id=record.plan_id)

    @app.post("/api/v1/explanations", operation_id="createGroundedExplanation")
    def create_grounded_explanation(body: GraniteExplanationRequest) -> Any:
        return app.state.granite_explanation_service.explain(body)

    router = APIRouter(prefix="/api/scenarios")

    @router.post("")
    def create_scenario(request: Request, body: ScenarioCreateRequest) -> ApiEnvelope:
        runtime = application_service.create_scenario(body)
        return _ok(
            request,
            body.scenario.scenario_id,
            runtime.definition,
            provenance={"station_catalog": application_service.catalog.catalog_version},
        )

    @router.get("/{scenario_id}")
    def get_scenario(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, runtime.definition)

    @router.post("/{scenario_id}/validate")
    def validate_scenario(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.validate_scenario(scenario_id))

    @router.post("/{scenario_id}/orbit/summary")
    def orbit_summary(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.orbit_summary(scenario_id))

    @router.get("/{scenario_id}/ground-track")
    def ground_track(
        request: Request,
        scenario_id: str,
        start_at: datetime = Query(),
        end_at: datetime = Query(),
        step_s: int = Query(default=60, ge=1, le=3600),
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        horizon = HorizonRequest(start_at=start_at, end_at=end_at, step_s=step_s)
        return _runtime_ok(request, runtime, application_service.ground_track(scenario_id, horizon))

    @router.get("/{scenario_id}/passes")
    def passes(
        request: Request,
        scenario_id: str,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        horizon = None
        if start_at is not None or end_at is not None:
            if start_at is None or end_at is None:
                raise ApiServiceError(
                    422, "HORIZON_INCOMPLETE", "start_at and end_at must be supplied together"
                )
            horizon = HorizonRequest(start_at=start_at, end_at=end_at)
        return _runtime_ok(
            request, runtime, application_service.generate_passes(scenario_id, horizon)
        )

    @router.post("/{scenario_id}/capacity")
    def capacity(request: Request, scenario_id: str, body: CapacityRequest) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        data = application_service.compute_capacities(scenario_id, body.pass_ids)
        assumptions = sorted({label for item in data for label in item.assumptions})
        return _runtime_ok(request, runtime, data, assumptions=assumptions)

    @router.post("/{scenario_id}/feasibility")
    def feasibility(request: Request, scenario_id: str, body: FeasibilityRequest) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(
            request,
            runtime,
            application_service.feasibility(scenario_id, refresh_capacity=body.refresh_capacity),
        )

    @router.post("/{scenario_id}/plans")
    def create_plan(request: Request, scenario_id: str, body: PlanRequest) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        plan = application_service.create_plan(scenario_id, body.plan_id)
        return _runtime_ok(request, runtime, plan)

    @router.get("/{scenario_id}/plans/{plan_id}")
    def get_plan(request: Request, scenario_id: str, plan_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.get_plan(scenario_id, plan_id))

    @router.post("/{scenario_id}/simulation/start")
    def simulation_start(
        request: Request, scenario_id: str, body: SimulationStartRequest
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        application_service.start_simulation(
            scenario_id,
            plan_id=body.plan_id,
            sim_start_at=body.sim_start_at,
            speed=body.speed.value,
        )
        return _runtime_ok(request, runtime, application_service.simulation_state(scenario_id))

    @router.post("/{scenario_id}/simulation/pause")
    def simulation_pause(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        application_service.pause_simulation(scenario_id)
        return _runtime_ok(request, runtime, application_service.simulation_state(scenario_id))

    @router.post("/{scenario_id}/simulation/step")
    def simulation_step(
        request: Request, scenario_id: str, body: SimulationStepRequest
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        application_service.step_simulation(scenario_id, body.seconds)
        return _runtime_ok(request, runtime, application_service.simulation_state(scenario_id))

    @router.post("/{scenario_id}/anomalies")
    def anomalies(request: Request, scenario_id: str, body: AnomalyRequest) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.inject_anomaly(scenario_id, body))

    @router.post("/{scenario_id}/replans")
    def replans(request: Request, scenario_id: str, body: ReplanRequest) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(
            request, runtime, application_service.request_replan(scenario_id, body.reason)
        )

    @router.post("/{scenario_id}/proposals/{proposal_id}/approve")
    def approve(
        request: Request,
        scenario_id: str,
        proposal_id: str,
        body: ProposalDecisionRequest,
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        data = application_service.decide_proposal(
            scenario_id, proposal_id, approve=True, reason=body.reason
        )
        return _runtime_ok(request, runtime, data)

    @router.post("/{scenario_id}/proposals/{proposal_id}/reject")
    def reject(
        request: Request,
        scenario_id: str,
        proposal_id: str,
        body: ProposalDecisionRequest,
    ) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        data = application_service.decide_proposal(
            scenario_id, proposal_id, approve=False, reason=body.reason
        )
        return _runtime_ok(request, runtime, data)

    @router.get("/{scenario_id}/events")
    def events(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.events(scenario_id))

    @router.get("/{scenario_id}/export/plan.json")
    def export_plan(request: Request, scenario_id: str) -> ApiEnvelope:
        runtime = application_service.get_runtime(scenario_id)
        return _runtime_ok(request, runtime, application_service.export_plan(scenario_id))

    app.include_router(router)
    app.include_router(create_v1_router(app.state.container))
    return app


def _ok(
    request: Request,
    scenario_id: str | None,
    data: Any,
    *,
    current_plan_id: str | None = None,
    provenance: dict[str, Any] | None = None,
    assumptions: list[str] | None = None,
) -> ApiEnvelope:
    return ApiEnvelope(
        request_id=request.state.request_id,
        scenario_id=scenario_id,
        current_plan_id=current_plan_id,
        data=data,
        provenance=provenance,
        assumptions=assumptions or [],
    )


def _runtime_ok(
    request: Request,
    runtime: Any,
    data: Any,
    *,
    assumptions: list[str] | None = None,
) -> ApiEnvelope:
    return _ok(
        request,
        runtime.definition.scenario.scenario_id,
        data,
        current_plan_id=runtime.current_plan_id,
        assumptions=assumptions,
    )


def _error_response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    entity_refs: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    scenario_id = request.path_params.get("scenario_id")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
        "scenario_id": scenario_id,
        "current_plan_id": None,
        "data": None,
        "provenance": None,
        "assumptions": [],
        "error": {
            "code": code,
            "message": message,
            "entity_refs": entity_refs or {},
            "details": details or {},
        },
    }
    return JSONResponse(status_code=status_code, content=jsonable_encoder(payload))


app = create_app()
