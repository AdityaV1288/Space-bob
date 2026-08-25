"""Export JSON Schema for every top-level domain model into schemas/."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel

from agcc.domain.environment import SpaceWeatherSnapshot, WeatherSnapshot
from agcc.domain.errors import DomainError
from agcc.domain.mission import DownlinkMission, Scenario, ScenarioConstraints
from agcc.domain.orbit import CustomCircularOrbit, CustomSatellite, SatelliteCommunications
from agcc.domain.planning import (
    CandidatePass,
    CapacityEstimate,
    ContactPlan,
    FeasibilityResult,
    PlanDiff,
    PlannedAllocation,
)
from agcc.domain.simulation import (
    AnomalyProposal,
    DataFragment,
    ReplanProposal,
    SimulationEvent,
)
from agcc.domain.stations import GroundStation, StationCatalog

_MODELS: list[type[BaseModel]] = [
    CustomCircularOrbit,
    SatelliteCommunications,
    CustomSatellite,
    GroundStation,
    StationCatalog,
    WeatherSnapshot,
    SpaceWeatherSnapshot,
    CandidatePass,
    CapacityEstimate,
    DownlinkMission,
    ScenarioConstraints,
    Scenario,
    FeasibilityResult,
    PlannedAllocation,
    ContactPlan,
    DataFragment,
    SimulationEvent,
    AnomalyProposal,
    ReplanProposal,
    PlanDiff,
    DomainError,
]


def export(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for model in _MODELS:
        schema = model.model_json_schema()
        path = output_dir / f"{model.__name__}.json"
        path.write_text(
            json.dumps(schema, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"  exported {path}")


if __name__ == "__main__":
    # __file__ = agcc/backend/scripts/export_schemas.py
    # .parent       -> agcc/backend/scripts/
    # .parent.parent -> agcc/backend/
    # .parent.parent.parent -> agcc/   (repo root)
    repo_root = Path(__file__).resolve().parent.parent.parent
    schemas_dir = repo_root / "schemas"
    print(f"Exporting schemas to {schemas_dir}")
    export(schemas_dir)
    print("Done.")
