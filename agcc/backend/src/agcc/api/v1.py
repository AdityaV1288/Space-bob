"""Session-scoped Task 15 REST and Server-Sent Events boundary."""

from __future__ import annotations

import json
import secrets
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Lock
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agcc.anomalies import AnomalyContext, AnomalyService, StructuredAnomalyInput
from agcc.api.contracts import (
    AnomalyRequest,
    HorizonRequest,
    PlanRequest,
    ProposalDecisionRequest,
    ReplanRequest,
    ScenarioCreateRequest,
    SimulationForkRequest,
    SimulationStartRequest,
)
from agcc.api.service import AgccApplicationService
from agcc.domain.mission import DownlinkMission
from agcc.domain.orbit import CustomCircularOrbit, SatelliteCommunications
from agcc.domain.stations import StationSelection
from agcc.granite import (
    GraniteAnomalyIntentParser,
    GraniteExplanationRequest,
    GraniteExplanationService,
    GraniteNotConfigured,
    granite_client_from_environment,
    granite_configuration,
)
from agcc.replanning import ForwardReplanner
from agcc.simulation import ClockSpeed

SESSION_TTL = timedelta(hours=24)


@dataclass
class SessionState:
    session_id: str
    service: AgccApplicationService = field(
        default_factory=lambda: AgccApplicationService(fixture_mode=True)
    )
    anomalies: AnomalyService = field(default_factory=AnomalyService)
    replanner: ForwardReplanner = field(default_factory=ForwardReplanner)
    scenario_id: str | None = None
    last_active_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    replan_lock: Lock = field(default_factory=Lock, repr=False)


class SessionRepository:
    def __init__(self) -> None:
        self._sessions: dict[str, SessionState] = {}

    def create(self) -> SessionState:
        self.evict_inactive()
        session_id = secrets.token_urlsafe(32)
        state = SessionState(session_id=session_id)
        self._sessions[session_id] = state
        return state

    def get(self, session_id: str) -> SessionState:
        self.evict_inactive()
        try:
            state = self._sessions[session_id]
        except KeyError as exc:
            raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"}) from exc
        state.last_active_at = datetime.now(UTC)
        return state

    def delete(self, session_id: str) -> None:
        if self._sessions.pop(session_id, None) is None:
            raise HTTPException(status_code=404, detail={"code": "SESSION_NOT_FOUND"})

    def evict_inactive(self, now: datetime | None = None) -> int:
        cutoff = (now or datetime.now(UTC)) - SESSION_TTL
        expired = [key for key, value in self._sessions.items() if value.last_active_at < cutoff]
        for key in expired:
            del self._sessions[key]
        return len(expired)


class AppContainer:
    def __init__(self, sessions: SessionRepository | None = None) -> None:
        self.sessions = sessions or SessionRepository()


class SessionResponse(BaseModel):
    session_id: str
    expires_after_inactive_s: int = 86400


class WeatherWindowRequest(BaseModel):
    start_at: datetime
    end_at: datetime


class SimulationSpeedRequest(BaseModel):
    speed: ClockSpeed


class AnomalyChatRequest(BaseModel):
    text: str


def _llm_failure(exc: Exception) -> dict[str, str]:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return {
                "code": "LLM_AUTHENTICATION_FAILED",
                "message": (
                    "Groq rejected the API key. Set GROQ_API_KEY in the PowerShell window "
                    "that starts the backend, then restart it."
                ),
            }
        if status == 403:
            return {
                "code": "LLM_ACCESS_DENIED",
                "message": "Groq authenticated the request but denied model access.",
            }
        if status == 404:
            return {
                "code": "LLM_MODEL_NOT_FOUND",
                "message": "The configured Groq model is unavailable; verify GROQ_MODEL_ID.",
            }
        if status == 429:
            return {
                "code": "LLM_RATE_LIMITED",
                "message": "Groq is rate-limited; wait briefly and retry.",
            }
        return {
            "code": "LLM_REQUEST_REJECTED",
            "message": f"Groq rejected the request with HTTP {status}.",
        }
    if isinstance(exc, httpx.RequestError):
        return {
            "code": "LLM_NETWORK_FAILED",
            "message": "The backend could not reach the configured Groq endpoint.",
        }
    return {
        "code": "LLM_RESPONSE_INVALID",
        "message": f"Groq response rejected: {str(exc)[:180]}",
    }


def create_v1_router(container: AppContainer) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def state(x_agcc_session: str | None = Header(default=None)) -> SessionState:
        if not x_agcc_session:
            raise HTTPException(status_code=401, detail={"code": "SESSION_HEADER_REQUIRED"})
        return container.sessions.get(x_agcc_session)

    def scenario_id(session: SessionState) -> str:
        if session.scenario_id is None:
            raise HTTPException(status_code=409, detail={"code": "SCENARIO_REQUIRED"})
        return session.scenario_id

    def runtime(session: SessionState) -> Any:
        return session.service.get_runtime(scenario_id(session))

    @router.post("/sessions", operation_id="createSession")
    def create_session() -> SessionResponse:
        created = container.sessions.create()
        return SessionResponse(session_id=created.session_id)

    @router.delete("/sessions/{session_id}", operation_id="deleteSession")
    def delete_session(session_id: str) -> dict[str, bool]:
        container.sessions.delete(session_id)
        return {"deleted": True}

    @router.get("/catalog/stations", operation_id="listStations")
    def stations(session: SessionState = Depends(state)) -> Any:
        return session.service.catalog

    @router.post("/scenario", operation_id="createScenario")
    def create_scenario(
        body: ScenarioCreateRequest, session: SessionState = Depends(state)
    ) -> Any:
        runtime = session.service.create_scenario(body)
        session.scenario_id = body.scenario.scenario_id
        return runtime.definition

    @router.put("/scenario/orbit", operation_id="updateOrbit")
    def update_orbit(
        body: CustomCircularOrbit, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        satellite = current.definition.satellite.model_copy(update={"orbit": body})
        current.definition = current.definition.model_copy(update={"satellite": satellite})
        return body

    @router.put("/scenario/communications", operation_id="updateCommunications")
    def update_communications(
        body: SatelliteCommunications, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        satellite = current.definition.satellite.model_copy(update={"comms": body})
        current.definition = current.definition.model_copy(update={"satellite": satellite})
        return body

    @router.put("/scenario/stations", operation_id="updateStations")
    def update_stations(
        body: StationSelection, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        constraints = current.definition.scenario.constraints.model_copy(
            update={"station_selection": body}
        )
        scenario = current.definition.scenario.model_copy(update={"constraints": constraints})
        current.definition = current.definition.model_copy(update={"scenario": scenario})
        return body

    @router.put("/scenario/mission", operation_id="updateMission")
    def update_mission(
        body: DownlinkMission, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        if body.mission_id != current.definition.scenario.mission_id:
            raise HTTPException(status_code=422, detail={"code": "MISSION_ID_MISMATCH"})
        current.definition = current.definition.model_copy(update={"mission": body})
        return body

    @router.post("/passes/compute", operation_id="computePasses")
    def compute_passes(session: SessionState = Depends(state)) -> Any:
        return session.service.generate_passes(scenario_id(session))

    @router.post("/weather", operation_id="getLiveWeather")
    def live_weather(
        body: WeatherWindowRequest, session: SessionState = Depends(state)
    ) -> Any:
        return session.service.weather_snapshots(
            scenario_id(session), body.start_at, body.end_at
        )

    @router.get("/space-weather", operation_id="getSpaceWeather")
    def space_weather(session: SessionState = Depends(state)) -> Any:
        return session.service.space_weather(scenario_id(session))

    @router.get("/watsonx/status", operation_id="getWatsonxStatus")
    def watsonx_status(
        probe: bool = False, session: SessionState = Depends(state)
    ) -> Any:
        del session
        configuration = granite_configuration()
        if not configuration["configured"]:
            return {**configuration, "status": "not_configured", "reachable": False}
        if not probe:
            return {**configuration, "status": "configured", "reachable": None}
        try:
            granite_client_from_environment().generate_json(
                'Return exactly {"status":"ok"} as a JSON object and nothing else.'
            )
            return {**configuration, "status": "ready", "reachable": True}
        except Exception as exc:
            failure = _llm_failure(exc)
            return {
                **configuration,
                "status": failure["code"].lower(),
                "reachable": False,
                "message": failure["message"],
            }

    @router.get("/orbit/ground-track", operation_id="getGroundTrack")
    def ground_track(session: SessionState = Depends(state)) -> Any:
        current = runtime(session)
        return session.service.ground_track(
            current.definition.scenario.scenario_id,
            HorizonRequest(
                start_at=current.definition.mission.release_at,
                end_at=current.definition.mission.deadline_at,
                step_s=300,
            ),
        )

    @router.post("/plan", operation_id="createPlan")
    def create_plan(body: PlanRequest, session: SessionState = Depends(state)) -> Any:
        ident = scenario_id(session)
        session.service.compute_capacities(ident)
        session.service.feasibility(ident, refresh_capacity=False)
        return session.service.create_plan(
            ident,
            body.plan_id,
            mission_window_start=body.mission_window_start,
        )

    @router.get("/plan/current", operation_id="getCurrentPlan")
    def current_plan(session: SessionState = Depends(state)) -> Any:
        runtime = session.service.get_runtime(scenario_id(session))
        if runtime.current_plan_id is None:
            raise HTTPException(status_code=409, detail={"code": "PLAN_REQUIRED"})
        return session.service.get_plan(
            runtime.definition.scenario.scenario_id, runtime.current_plan_id
        )

    @router.post("/simulation/start", operation_id="startSimulation")
    def start_simulation(
        body: SimulationStartRequest, session: SessionState = Depends(state)
    ) -> Any:
        ident = scenario_id(session)
        session.service.start_simulation(
            ident,
            plan_id=body.plan_id,
            sim_start_at=body.sim_start_at,
            speed=body.speed.value,
            capacity_policy=body.capacity_policy,
        )
        return session.service.simulation_state(ident)

    @router.post("/simulation/pause", operation_id="pauseSimulation")
    def pause_simulation(session: SessionState = Depends(state)) -> Any:
        ident = scenario_id(session)
        session.service.pause_simulation(ident)
        return session.service.simulation_state(ident)

    @router.post("/simulation/resume", operation_id="resumeSimulation")
    def resume_simulation(session: SessionState = Depends(state)) -> Any:
        runtime = session.service.get_runtime(scenario_id(session))
        session.service.resume_simulation(runtime.definition.scenario.scenario_id)
        return session.service.simulation_state(runtime.definition.scenario.scenario_id)

    @router.post("/simulation/speed", operation_id="setSimulationSpeed")
    def set_simulation_speed(
        body: SimulationSpeedRequest, session: SessionState = Depends(state)
    ) -> Any:
        ident = scenario_id(session)
        session.service.set_simulation_speed(ident, body.speed.value)
        return session.service.simulation_state(ident)

    @router.post("/simulation/fork", operation_id="forkSimulation")
    def fork_simulation(
        body: SimulationForkRequest, session: SessionState = Depends(state)
    ) -> Any:
        """Reset this isolated session to a supplied prediction snapshot."""
        ident = scenario_id(session)
        current = session.service.get_runtime(ident)
        if not (
            current.definition.mission.release_at
            <= body.sim_time
            <= current.definition.mission.deadline_at
        ):
            raise HTTPException(
                status_code=422,
                detail={"code": "FORK_TIME_OUTSIDE_MISSION"},
            )
        session.service.start_simulation(
            ident,
            plan_id=current.current_plan_id,
            sim_start_at=body.sim_time,
            speed="paused",
            initial_delivered_mb=min(
                body.delivered_mb, current.definition.mission.required_volume_mb
            ),
        )
        return session.service.simulation_state(ident)

    @router.get("/simulation/state", operation_id="getSimulationState")
    def simulation_state(session: SessionState = Depends(state)) -> Any:
        return session.service.simulation_state(scenario_id(session))

    @router.get("/simulation/events", operation_id="getSimulationEvents")
    def simulation_events(session: SessionState = Depends(state)) -> Any:
        return session.service.events(scenario_id(session))

    @router.get("/mission/resolution", operation_id="getMissionResolution")
    def mission_resolution(session: SessionState = Depends(state)) -> Any:
        current = runtime(session)
        state_payload = session.service.simulation_state(scenario_id(session))
        facts = {
            "required_mb": state_payload["required_mb"],
            "delivered_mb": state_payload["delivered_mb"],
            "shortfall_mb": state_payload["remaining_mb"],
            "deadline_at": state_payload["deadline_at"],
            "budget": str(current.definition.scenario.constraints.maximum_budget),
            "approved_contacts": len(session.service.get_plan(
                current.definition.scenario.scenario_id, current.current_plan_id or ""
            ).contacts),
        }
        explanation = GraniteExplanationService().explain(
            GraniteExplanationRequest(
                task="explain_predicted_shortfall",
                verified_facts=facts,
                fact_ids=list(facts),
            )
        )
        return {
            "reason": explanation,
            "approval_prompt": (
                "Approve a forward replan that may add an eligible station, increase budget, "
                "or request a later deadline. No constraint is changed without approval."
            ),
            "facts": facts,
        }

    @router.post("/anomalies/parse", operation_id="parseAnomaly")
    def parse_anomaly(
        body: StructuredAnomalyInput, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        plan = session.service.get_plan(
            current.definition.scenario.scenario_id, current.current_plan_id or ""
        )
        context = AnomalyContext(
            scenario_id=current.definition.scenario.scenario_id,
            station_ids=current.definition.scenario.station_ids,
            station_names={
                station.station_id: station.name
                for station in session.service.catalog.stations
                if station.station_id in current.definition.scenario.station_ids
            },
            contact_ids=[item.contact_id for item in plan.contacts],
            simulation_time=(
                current.simulation.sim_time
                if current.simulation and current.simulation.sim_time
                else current.definition.mission.release_at
            ),
        )
        return session.anomalies.propose_structured(
            current.definition.scenario.scenario_id,
            body,
            context,
            datetime.now(UTC),
        )

    @router.post("/anomalies/chat", operation_id="chatAnomaly")
    async def chat_anomaly(
        body: AnomalyChatRequest, session: SessionState = Depends(state)
    ) -> Any:
        current = runtime(session)
        plan = session.service.get_plan(
            current.definition.scenario.scenario_id, current.current_plan_id or ""
        )
        context = AnomalyContext(
            scenario_id=current.definition.scenario.scenario_id,
            station_ids=current.definition.scenario.station_ids,
            station_names={
                station.station_id: station.name
                for station in session.service.catalog.stations
                if station.station_id in current.definition.scenario.station_ids
            },
            contact_ids=[item.contact_id for item in plan.contacts],
            simulation_time=(
                current.simulation.sim_time
                if current.simulation and current.simulation.sim_time
                else current.definition.mission.release_at
            ),
        )
        parser = GraniteAnomalyIntentParser(granite_client_from_environment())
        try:
            return await session.anomalies.propose_text(
                current.definition.scenario.scenario_id,
                body.text,
                context,
                parser,
                datetime.now(UTC),
            )
        except GraniteNotConfigured as exc:
            raise HTTPException(
                status_code=503,
                detail={
                    "code": "LLM_NOT_CONFIGURED",
                    "message": (
                        "Set GROQ_API_KEY in the PowerShell window that starts the "
                        "backend, then restart it before using anomaly chat."
                    ),
                },
            ) from exc
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            failure = _llm_failure(exc)
            raise HTTPException(
                status_code=502,
                detail=failure,
            ) from exc

    @router.post("/anomalies/confirm", operation_id="confirmAnomaly")
    def confirm_anomaly(
        proposal_id: str, session: SessionState = Depends(state)
    ) -> Any:
        runtime = session.service.get_runtime(scenario_id(session))
        proposal = session.anomalies.proposals[proposal_id]
        affected_contacts = (
            [proposal.intent.contact_id] if proposal.intent.contact_id else []
        )
        if proposal.intent.station_id:
            plan = session.service.get_plan(
                runtime.definition.scenario.scenario_id, runtime.current_plan_id or ""
            )
            affected_contacts.extend(
                item.contact_id
                for item in plan.contacts
                if item.station_id == proposal.intent.station_id
            )
            if not affected_contacts:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "ANOMALY_HAS_NO_PLANNED_CONTACT",
                        "message": "Selected station has no contact in the active plan",
                    },
                )
        active = session.anomalies.confirm(proposal_id, datetime.now(UTC))
        session.service.inject_anomaly(
            runtime.definition.scenario.scenario_id,
            AnomalyRequest(
                anomaly_type=active.anomaly_type,
                station_id=active.station_id,
                affected_contact_ids=sorted(set(affected_contacts)),
                rate_multiplier=active.rate_multiplier,
                starts_at=active.starts_at,
                ends_at=active.ends_at,
                confidence=active.confidence,
                cause=active.cause,
                assumptions=active.assumptions,
                description=proposal.source_text,
            ),
        )
        return active

    @router.post("/replans", operation_id="requestReplan")
    def request_replan(
        body: ReplanRequest, session: SessionState = Depends(state)
    ) -> Any:
        if not session.replan_lock.acquire(blocking=False):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "REPLAN_ALREADY_RUNNING",
                    "message": "A replan calculation is already running for this timeline.",
                },
            )
        try:
            return _calculate_replan(body, session)
        finally:
            session.replan_lock.release()

    def _calculate_replan(body: ReplanRequest, session: SessionState) -> Any:
        current = runtime(session)
        if current.current_plan_id is None:
            raise HTTPException(status_code=409, detail={"code": "PLAN_REQUIRED"})
        old = session.service.get_plan(
            current.definition.scenario.scenario_id, current.current_plan_id
        )
        replan_at = (
            current.simulation.sim_time
            if current.simulation and current.simulation.sim_time
            else current.definition.mission.release_at
        )
        affected_contact_ids = {
            contact_id
            for anomaly in current.anomalies
            for contact_id in anomaly.affected_contact_ids
        }
        excluded_pass_ids = {
            item.pass_id
            for item in old.contacts
            if item.contact_id in affected_contact_ids and item.start_at > replan_at
        }
        proposed_id = f"plan_replan{len(session.replanner.proposals) + 1:08d}"
        required_remaining = max(
            0.0,
            current.definition.mission.required_volume_mb
            - (current.simulation.delivered_mb if current.simulation else 0.0),
        )
        future = session.service.create_plan(
            current.definition.scenario.scenario_id,
            proposed_id,
            excluded_pass_ids=excluded_pass_ids,
            activate=False,
            required_volume_mb=required_remaining,
            mission_window_start=replan_at,
            allow_budget_override=True,
        )
        candidate = None
        if future.status.value == "feasible":
            preserved = [item for item in old.contacts if item.start_at <= replan_at]
            combined_contacts = sorted(
                [*preserved, *future.contacts], key=lambda item: item.start_at
            )
            combined_cost = sum(
                Decimal(item.contact_cost_decimal) for item in combined_contacts
            )
            candidate = future.model_copy(update={
                "required_volume_mb": current.definition.mission.required_volume_mb,
                "planned_volume_mb": sum(
                    item.allocated_volume_mb for item in combined_contacts
                ),
                "contacts": combined_contacts,
                "estimated_total_cost": str(combined_cost),
            })
            current.plans[candidate.plan_id] = candidate
        current.current_plan_id = old.plan_id
        shortfall = current.simulation.predicted_shortfall_mb if current.simulation else 0.0
        suggestions = current.feasibility.suggestions if current.feasibility else None
        return session.replanner.propose(
            current_plan=old,
            candidate_plan=candidate,
            now=replan_at,
            predicted_shortfall_mb=shortfall,
            authorized_station_ids=set(current.definition.scenario.station_ids),
            suggestions=suggestions,
            trigger="user_requested",
        )

    @router.get("/replans/pending", operation_id="getPendingReplans")
    def pending_replans(session: SessionState = Depends(state)) -> Any:
        return [
            item
            for item in session.replanner.proposals.values()
            if item.status == "pending"
        ]

    @router.post("/replans/{proposal_id}/approve", operation_id="approveReplan")
    def approve_replan(
        proposal_id: str,
        body: ProposalDecisionRequest,
        session: SessionState = Depends(state),
    ) -> Any:
        current = runtime(session)
        proposal = session.replanner.proposals[proposal_id]
        candidate = proposal.proposed_plan
        if candidate is None:
            raise HTTPException(
                status_code=409,
                detail={"code": "REPLAN_HAS_NO_EXECUTABLE_PLAN"},
            )

        # Build one validated immutable definition before changing proposal state.
        # Domain aggregates are frozen by design and must never be assigned into.
        station_ids = list(dict.fromkeys([
            *current.definition.scenario.station_ids,
            *(contact.station_id for contact in candidate.contacts),
        ]))
        old_constraints = current.definition.scenario.constraints
        old_selection = old_constraints.station_selection
        selection = old_selection.model_copy(update={
            "authorized_station_ids": list(dict.fromkeys([
                *old_selection.authorized_station_ids,
                *(contact.station_id for contact in candidate.contacts),
            ])),
        })
        plan_cost = sum(
            Decimal(contact.contact_cost_decimal) for contact in candidate.contacts
        )
        # Pressing Approve is the explicit authority to accept this candidate's
        # calculated cost. Only the minimum required ceiling is raised.
        constraints = old_constraints.model_copy(update={
            "station_selection": selection,
            "maximum_budget": max(old_constraints.maximum_budget, plan_cost),
        })
        scenario = current.definition.scenario.model_copy(update={
            "station_ids": station_ids,
            "constraints": constraints,
        })
        latest_end = max(
            (contact.end_at for contact in candidate.contacts),
            default=current.definition.mission.deadline_at,
        )
        if latest_end > current.definition.mission.deadline_at:
            raise HTTPException(
                status_code=409,
                detail={"code": "REPLAN_EXCEEDS_APPROVED_DEADLINE"},
            )
        mission = current.definition.mission
        updated_definition = current.definition.model_copy(update={
            "scenario": scenario,
            "mission": mission,
        })

        # Approval is atomic from the caller's perspective. Network-backed weather
        # refresh or activation validation may fail; neither may consume the pending
        # proposal or leave a half-updated runtime behind.
        runtime_snapshot = {
            "definition": current.definition,
            "passes": current.passes,
            "capacities": current.capacities,
            "weather_snapshots": current.weather_snapshots,
            "feasibility": current.feasibility,
            "plans": dict(current.plans),
            "current_plan_id": current.current_plan_id,
            "dispatch": current.dispatch,
            "simulation": current.simulation,
        }
        proposal_snapshot = proposal.model_copy(deep=True)
        history_snapshot = deepcopy(session.replanner.plan_history)
        try:
            current.definition = updated_definition
            decision = session.replanner.approve(proposal_id)
            current.plans[decision.active_plan.plan_id] = decision.active_plan
            session.service.activate_replan(
                current.definition.scenario.scenario_id, decision.active_plan.plan_id
            )
            return decision
        except Exception:
            for key, value in runtime_snapshot.items():
                setattr(current, key, value)
            session.replanner.proposals[proposal_id] = proposal_snapshot
            session.replanner.plan_history = history_snapshot
            raise

    @router.post("/replans/{proposal_id}/reject", operation_id="rejectReplan")
    def reject_replan(
        proposal_id: str,
        body: ProposalDecisionRequest,
        session: SessionState = Depends(state),
    ) -> Any:
        del body
        return session.replanner.reject(proposal_id)

    @router.get("/events/stream", operation_id="streamEvents")
    def event_stream(session: SessionState = Depends(state)) -> StreamingResponse:
        events = session.service.events(scenario_id(session))

        def generate() -> Any:
            for event in events:
                sequence = event["sequence_number"]
                data = json.dumps(jsonable_encoder(event))
                yield f"id: {sequence}\nevent: simulation\ndata: {data}\n\n"
            yield ": stream-ready\n\n"

        return StreamingResponse(generate(), media_type="text/event-stream")

    return router


__all__ = ["AppContainer", "SessionRepository", "SessionState", "create_v1_router"]
