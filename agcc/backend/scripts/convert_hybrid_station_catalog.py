"""Convert the user-supplied hybrid catalogue into AGCC's validated schema."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ASSUMED_FIELDS = [
    "name",
    "provider_id",
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
    "simultaneous_contacts",
]


def convert(source: dict[str, Any], generated_at: datetime) -> dict[str, Any]:
    stations: list[dict[str, Any]] = []
    for raw in source.get("stations", []):
        location = raw["location"]
        stations.append(
            {
                "station_id": raw["id"],
                "name": raw["name"],
                "provider_id": raw["provider_id"],
                "latitude_deg": location["latitude_deg"],
                "longitude_deg": location["longitude_deg"],
                "altitude_m": location["elevation_m"],
                "supported_bands": raw.get("supported_bands"),
                "max_downlink_rate_mbps": raw.get("max_data_rate_mbps"),
                "minimum_elevation_deg": raw["minimum_elevation_deg"],
                "setup_s": raw["setup_s"],
                "teardown_s": raw["teardown_s"],
                "cost_model": "per_minute",
                "booking_cost": 0.0,
                "cost_per_minute": raw["cost_per_minute_usd"],
                "currency": "USD",
                "simultaneous_contacts": 1,
                "field_provenance": {"sources": {}, "assumptions": ASSUMED_FIELDS},
                "enabled": True,
            }
        )
    timestamp = generated_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "_comment": (
            "Hybrid demo catalogue converted from a user-supplied unverified dataset. "
            "Real provider/place labels are retained, but all operational and location "
            "fields remain explicit simulation assumptions until authoritative citations "
            "are supplied. Reliability scores were dropped because AGCC has no validated "
            "reliability contract. Simultaneous contacts were clamped to the MVP limit of 1."
        ),
        "catalog_id": "catalog_hybrid_ground_stations_v1",
        "schema_version": "1.0.0",
        "catalog_version": "2026.08.1-hybrid",
        "generated_at": timestamp,
        "provenance": {
            "source_type": "fixture",
            "source_name": "User-supplied hybrid station dataset",
            "source_uri": None,
            "source_version": "v1-unverified",
            "observed_at": None,
            "fetched_at": timestamp,
            "assumption_fields": ["stations.*"],
        },
        "stations": stations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8", errors="replace"))
    result = convert(source, datetime.now(UTC))
    args.destination.write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
