"""Acceptance checks for the converted hybrid demonstration catalogue."""

from pathlib import Path

from agcc.stations.catalog import load_catalog_from_file


def test_hybrid_catalog_is_valid_and_explicitly_assumed() -> None:
    path = Path(__file__).resolve().parents[3] / "data" / "catalogs" / "stations.hybrid.json"
    catalog = load_catalog_from_file(path)
    assert len(catalog.stations) == 105
    assert all(station.simultaneous_contacts == 1 for station in catalog.stations)
    assert all(station.planner_eligible for station in catalog.stations)
    assert all(not station.field_provenance.sources for station in catalog.stations)
    assert all(
        "latitude_deg" in station.field_provenance.assumptions
        for station in catalog.stations
    )
    assert catalog.provenance.assumption_fields == ["stations.*"]
