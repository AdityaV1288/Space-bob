"""Tests for station catalog loading, validation, filtering, and placeholder."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agcc.domain.common import Provenance
from agcc.domain.enums import Band, CostModel, SourceType
from agcc.domain.stations import (
    FieldProvenance,
    GroundStation,
    StationCatalog,
    StationSelection,
)
from agcc.stations.catalog import (
    ExternalDataUnavailable,
    NotConfiguredStationCatalogProvider,
    load_catalog_from_file,
)
from agcc.stations.filtering import filter_stations

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FULL_ASSUMPTIONS = [
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
]

_FULL_PROVENANCE = FieldProvenance(assumptions=_FULL_ASSUMPTIONS)

_CATALOG_PROVENANCE = Provenance(
    source_type=SourceType.FIXTURE,
    source_name="test",
    fetched_at=datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
)

_CATALOG_META = {
    "catalog_id": "catalog_test_v1",
    "schema_version": "1.0.0",
    "catalog_version": "2026.08.1",
    "generated_at": datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
    "provenance": _CATALOG_PROVENANCE,
}


def _make_station(
    station_id: str = "station_test01",
    provider_id: str = "provider_test",
    enabled: bool = True,
    supported_bands: frozenset[Band] | None = None,
    max_downlink_rate_mbps: float | None = 100.0,
    field_provenance: FieldProvenance | None = None,
    cost_model: CostModel = CostModel.PER_MINUTE,
) -> GroundStation:
    return GroundStation(
        station_id=station_id,
        name="Test Station",
        provider_id=provider_id,
        latitude_deg=48.0,
        longitude_deg=11.0,
        altitude_m=500.0,
        supported_bands=supported_bands if supported_bands is not None else frozenset({Band.X}),
        max_downlink_rate_mbps=max_downlink_rate_mbps,
        minimum_elevation_deg=5.0,
        setup_s=60,
        teardown_s=30,
        cost_model=cost_model,
        booking_cost=0.0,
        cost_per_minute=10.0,
        currency="USD",
        field_provenance=field_provenance or _FULL_PROVENANCE,
        enabled=enabled,
    )


def _make_catalog(stations: list[GroundStation]) -> StationCatalog:
    return StationCatalog(
        **_CATALOG_META,
        stations=stations,
    )


# ---------------------------------------------------------------------------
# Coordinate provenance validation
# ---------------------------------------------------------------------------


class TestCoordinateProvenance:
    def test_valid_with_all_coords_in_assumptions(self) -> None:
        s = _make_station()
        assert s.station_id == "station_test01"

    def test_valid_with_coords_in_sources(self) -> None:
        fp = FieldProvenance(
            sources={
                "latitude_deg": "synthetic",
                "longitude_deg": "synthetic",
                "altitude_m": "synthetic",
                "supported_bands": "synthetic",
                "max_downlink_rate_mbps": "synthetic",
                "minimum_elevation_deg": "synthetic",
                "setup_s": "synthetic",
                "teardown_s": "synthetic",
                "cost_model": "synthetic",
                "booking_cost": "synthetic",
                "cost_per_minute": "synthetic",
                "currency": "synthetic",
            }
        )
        s = _make_station(field_provenance=fp)
        assert s.latitude_deg == 48.0

    def test_missing_latitude_provenance_fails(self) -> None:
        fp = FieldProvenance(
            assumptions=[
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
            ]
        )
        with pytest.raises(ValidationError, match="latitude_deg"):
            _make_station(field_provenance=fp)

    def test_missing_longitude_provenance_fails(self) -> None:
        fp = FieldProvenance(
            assumptions=[
                "latitude_deg",
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
            ]
        )
        with pytest.raises(ValidationError, match="longitude_deg"):
            _make_station(field_provenance=fp)

    def test_missing_altitude_provenance_fails(self) -> None:
        fp = FieldProvenance(
            assumptions=[
                "latitude_deg",
                "longitude_deg",
                "supported_bands",
                "max_downlink_rate_mbps",
                "minimum_elevation_deg",
                "setup_s",
                "teardown_s",
                "cost_model",
                "booking_cost",
                "cost_per_minute",
                "currency",
            ]
        )
        with pytest.raises(ValidationError, match="altitude_m"):
            _make_station(field_provenance=fp)

    def test_empty_provenance_fails(self) -> None:
        with pytest.raises(ValidationError):
            _make_station(field_provenance=FieldProvenance())

    def test_mixed_source_and_assumption_valid(self) -> None:
        fp = FieldProvenance(
            sources={"latitude_deg": "catalog"},
            assumptions=[
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
            ],
        )
        s = _make_station(field_provenance=fp)
        assert s.latitude_deg == 48.0

    def test_field_in_both_sources_and_assumptions_rejected(self) -> None:
        """A field appearing in both sources and assumptions must be rejected."""
        fp = FieldProvenance(
            sources={"latitude_deg": "survey"},
            assumptions=[
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
            ],
        )
        with pytest.raises(ValidationError):
            _make_station(field_provenance=fp)


# ---------------------------------------------------------------------------
# Invalid coordinates
# ---------------------------------------------------------------------------


class TestInvalidCoordinates:
    def test_latitude_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="p",
                latitude_deg=91.0,
                longitude_deg=0.0,
                altitude_m=0.0,
                supported_bands=frozenset({Band.X}),
                max_downlink_rate_mbps=100.0,
                minimum_elevation_deg=5.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                field_provenance=_FULL_PROVENANCE,
            )

    def test_longitude_out_of_range(self) -> None:
        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="p",
                latitude_deg=0.0,
                longitude_deg=181.0,
                altitude_m=0.0,
                supported_bands=frozenset({Band.X}),
                max_downlink_rate_mbps=100.0,
                minimum_elevation_deg=5.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                field_provenance=_FULL_PROVENANCE,
            )

    def test_longitude_180_rejected(self) -> None:
        """Longitude 180.0 is excluded (lt=180.0)."""
        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="p",
                latitude_deg=0.0,
                longitude_deg=180.0,
                altitude_m=0.0,
                supported_bands=frozenset({Band.X}),
                max_downlink_rate_mbps=100.0,
                minimum_elevation_deg=5.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                field_provenance=_FULL_PROVENANCE,
            )

    def test_longitude_minus_180_accepted(self) -> None:
        """Longitude -180.0 is accepted (ge=-180.0)."""
        s = GroundStation(
            station_id="station_x",
            name="X",
            provider_id="p",
            latitude_deg=0.0,
            longitude_deg=-180.0,
            altitude_m=0.0,
            supported_bands=frozenset({Band.X}),
            max_downlink_rate_mbps=100.0,
            minimum_elevation_deg=5.0,
            setup_s=0,
            teardown_s=0,
            booking_cost=0.0,
            cost_per_minute=0.0,
            field_provenance=_FULL_PROVENANCE,
        )
        assert s.longitude_deg == -180.0

    def test_negative_altitude_fails(self) -> None:
        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="p",
                latitude_deg=0.0,
                longitude_deg=0.0,
                altitude_m=-1.0,
                supported_bands=frozenset({Band.X}),
                max_downlink_rate_mbps=100.0,
                minimum_elevation_deg=5.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                field_provenance=_FULL_PROVENANCE,
            )


# ---------------------------------------------------------------------------
# None fields and planner eligibility
# ---------------------------------------------------------------------------


class TestPlannerEligibility:
    def test_eligible_when_bands_and_rate_set(self) -> None:
        s = _make_station()
        assert s.planner_eligible is True

    def test_not_eligible_when_no_bands(self) -> None:
        s = _make_station(supported_bands=frozenset())
        assert s.planner_eligible is False

    def test_not_eligible_when_bands_none(self) -> None:
        """Station with supported_bands=None is not eligible."""
        fp = FieldProvenance(
            assumptions=[
                "latitude_deg",
                "longitude_deg",
                "altitude_m",
                "minimum_elevation_deg",
                "setup_s",
                "teardown_s",
                "cost_model",
                "booking_cost",
                "cost_per_minute",
                "currency",
            ],
        )
        s = GroundStation(
            station_id="station_incomplete",
            name="Incomplete Station",
            provider_id="p",
            latitude_deg=0.0,
            longitude_deg=0.0,
            altitude_m=0.0,
            supported_bands=None,
            max_downlink_rate_mbps=None,
            minimum_elevation_deg=5.0,
            setup_s=0,
            teardown_s=0,
            booking_cost=0.0,
            cost_per_minute=0.0,
            field_provenance=fp,
        )
        assert s.planner_eligible is False

    def test_not_eligible_when_rate_none(self) -> None:
        """Station with max_downlink_rate_mbps=None is not eligible."""
        fp = FieldProvenance(
            assumptions=[
                "latitude_deg",
                "longitude_deg",
                "altitude_m",
                "supported_bands",
                "minimum_elevation_deg",
                "setup_s",
                "teardown_s",
                "cost_model",
                "booking_cost",
                "cost_per_minute",
                "currency",
            ],
        )
        s = GroundStation(
            station_id="station_norate",
            name="No Rate Station",
            provider_id="p",
            latitude_deg=0.0,
            longitude_deg=0.0,
            altitude_m=0.0,
            supported_bands=frozenset({Band.X}),
            max_downlink_rate_mbps=None,
            minimum_elevation_deg=5.0,
            setup_s=0,
            teardown_s=0,
            booking_cost=0.0,
            cost_per_minute=0.0,
            field_provenance=fp,
        )
        assert s.planner_eligible is False

    def test_not_eligible_when_disabled(self) -> None:
        s = _make_station(enabled=False)
        assert s.planner_eligible is False

    def test_eligible_flag_preserved_in_catalog(self) -> None:
        eligible = _make_station(station_id="station_e1")
        no_bands = _make_station(station_id="station_nb1", supported_bands=frozenset())
        catalog = _make_catalog([eligible, no_bands])
        by_id = {s.station_id: s for s in catalog.stations}
        assert by_id["station_e1"].planner_eligible is True
        assert by_id["station_nb1"].planner_eligible is False


# ---------------------------------------------------------------------------
# Provenance preservation
# ---------------------------------------------------------------------------


class TestProvenancePreservation:
    def test_sources_preserved(self) -> None:
        fp = FieldProvenance(
            sources={
                "latitude_deg": "survey2023",
                "longitude_deg": "survey2023",
                "altitude_m": "dem",
                "supported_bands": "survey2023",
                "max_downlink_rate_mbps": "survey2023",
                "minimum_elevation_deg": "survey2023",
                "setup_s": "survey2023",
                "teardown_s": "survey2023",
                "cost_model": "survey2023",
                "booking_cost": "survey2023",
                "cost_per_minute": "survey2023",
                "currency": "survey2023",
            }
        )
        s = _make_station(field_provenance=fp)
        assert s.field_provenance.sources["latitude_deg"] == "survey2023"
        assert s.field_provenance.sources["altitude_m"] == "dem"

    def test_assumptions_preserved(self) -> None:
        s = _make_station(field_provenance=_FULL_PROVENANCE)
        assert "latitude_deg" in s.field_provenance.assumptions

    def test_field_provenance_frozen(self) -> None:
        """The GroundStation model is frozen — normal attribute assignment must raise."""
        s = _make_station()
        with pytest.raises(Exception):
            s.field_provenance = FieldProvenance()  # type: ignore[misc]


# ---------------------------------------------------------------------------
# StationCatalog metadata
# ---------------------------------------------------------------------------


class TestCatalogMetadata:
    def test_catalog_metadata_survives_roundtrip(self) -> None:
        catalog = _make_catalog([])
        assert catalog.catalog_id == "catalog_test_v1"
        assert catalog.schema_version == "1.0.0"
        assert catalog.catalog_version == "2026.08.1"

    def test_catalog_id_must_start_with_catalog_prefix(self) -> None:
        with pytest.raises(ValidationError):
            StationCatalog(
                catalog_id="badprefix_v1",
                schema_version="1.0.0",
                catalog_version="1.0",
                generated_at=datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
                provenance=_CATALOG_PROVENANCE,
            )

    def test_catalog_id_prefix_only_rejected(self) -> None:
        with pytest.raises(ValidationError):
            StationCatalog(
                catalog_id="catalog_",
                schema_version="1.0.0",
                catalog_version="1.0",
                generated_at=datetime(2026, 8, 20, 0, 0, 0, tzinfo=timezone.utc),
                provenance=_CATALOG_PROVENANCE,
            )

    def test_duplicate_station_ids_rejected(self) -> None:
        s1 = _make_station(station_id="station_dup1")
        s2 = _make_station(station_id="station_dup1")
        with pytest.raises(ValidationError):
            _make_catalog([s1, s2])


# ---------------------------------------------------------------------------
# Filter order
# ---------------------------------------------------------------------------


class TestFilterOrder:
    def test_disabled_station_excluded(self) -> None:
        s = _make_station(station_id="station_a1", enabled=False)
        sel = StationSelection(allow_all_eligible=True)
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []

    def test_non_planner_eligible_excluded(self) -> None:
        s = _make_station(station_id="station_a1", supported_bands=frozenset())
        sel = StationSelection(allow_all_eligible=True)
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []

    def test_incomplete_station_excluded_by_filter(self) -> None:
        """A station with supported_bands=None is excluded by filter but present in catalog."""
        fp = FieldProvenance(
            assumptions=[
                "latitude_deg",
                "longitude_deg",
                "altitude_m",
                "minimum_elevation_deg",
                "setup_s",
                "teardown_s",
                "cost_model",
                "booking_cost",
                "cost_per_minute",
                "currency",
            ],
        )
        s = GroundStation(
            station_id="station_incomplete",
            name="Incomplete",
            provider_id="p",
            latitude_deg=0.0,
            longitude_deg=0.0,
            altitude_m=0.0,
            supported_bands=None,
            max_downlink_rate_mbps=None,
            minimum_elevation_deg=5.0,
            setup_s=0,
            teardown_s=0,
            booking_cost=0.0,
            cost_per_minute=0.0,
            field_provenance=fp,
        )
        catalog = _make_catalog([s])
        # Catalog retains the station
        assert len(catalog.stations) == 1
        # But filter excludes it
        sel = StationSelection(allow_all_eligible=True)
        result = filter_stations(catalog, sel)
        assert result == []

    def test_explicit_exclusion_removes_station(self) -> None:
        s = _make_station(station_id="station_a1")
        sel = StationSelection(
            allow_all_eligible=True,
            excluded_station_ids=frozenset({"station_a1"}),
        )
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []

    def test_authorized_station_id_passes(self) -> None:
        s = _make_station(station_id="station_a1")
        sel = StationSelection(authorized_station_ids=frozenset({"station_a1"}))
        result = filter_stations(_make_catalog([s]), sel)
        assert len(result) == 1

    def test_authorized_provider_id_passes(self) -> None:
        s = _make_station(station_id="station_a1", provider_id="provider_alpha")
        sel = StationSelection(authorized_provider_ids=frozenset({"provider_alpha"}))
        result = filter_stations(_make_catalog([s]), sel)
        assert len(result) == 1

    def test_allow_all_passes_all_eligible_enabled(self) -> None:
        stations = [
            _make_station(station_id="station_a1"),
            _make_station(station_id="station_a2"),
        ]
        sel = StationSelection(allow_all_eligible=True)
        result = filter_stations(_make_catalog(stations), sel)
        assert len(result) == 2

    def test_exclusion_applied_before_allow_all(self) -> None:
        """Exclusion must still remove even when allow_all_eligible=True."""
        stations = [
            _make_station(station_id="station_a1"),
            _make_station(station_id="station_a2"),
        ]
        sel = StationSelection(
            allow_all_eligible=True,
            excluded_station_ids=frozenset({"station_a1"}),
        )
        result = filter_stations(_make_catalog(stations), sel)
        assert len(result) == 1
        assert result[0].station_id == "station_a2"

    def test_no_authorization_passes_nothing(self) -> None:
        s = _make_station(station_id="station_a1")
        sel = StationSelection()  # allow_all=False, no authorized ids
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []

    def test_filter_order_disabled_before_eligibility(self) -> None:
        """A disabled station with no bands must be dropped at the enabled step."""
        s = _make_station(station_id="station_a1", enabled=False, supported_bands=frozenset())
        sel = StationSelection(allow_all_eligible=True)
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []

    def test_exclusion_wins_over_authorized_station(self) -> None:
        """A station in both authorized and excluded must be excluded."""
        s = _make_station(station_id="station_a1")
        sel = StationSelection(
            authorized_station_ids=frozenset({"station_a1"}),
            excluded_station_ids=frozenset({"station_a1"}),
        )
        result = filter_stations(_make_catalog([s]), sel)
        assert result == []


# ---------------------------------------------------------------------------
# Deterministic catalog sorting
# ---------------------------------------------------------------------------


class TestDeterministicSorting:
    def test_catalog_sorted_by_station_id(self) -> None:
        """load_catalog_from_file must sort stations by station_id."""
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        ids = [s.station_id for s in catalog.stations]
        assert ids == sorted(ids)

    def test_catalog_loaded_twice_is_identical(self) -> None:
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        c1 = load_catalog_from_file(demo_path)
        c2 = load_catalog_from_file(demo_path)
        assert c1 == c2

    def test_demo_has_12_stations(self) -> None:
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        assert len(catalog.stations) == 12

    def test_demo_stations_have_full_assumptions(self) -> None:
        """Every station in demo catalog must have all 12 fields in assumptions."""
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        required = set(_FULL_ASSUMPTIONS)
        for s in catalog.stations:
            covered = set(s.field_provenance.assumptions) | set(s.field_provenance.sources.keys())
            missing = required - covered
            assert not missing, f"Station {s.station_id} missing provenance for: {missing}"

    def test_demo_catalog_metadata(self) -> None:
        demo_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.demo.json"
        )
        catalog = load_catalog_from_file(demo_path)
        assert catalog.catalog_id == "catalog_demo_v1"
        assert catalog.schema_version == "1.0.0"
        assert catalog.catalog_version == "2026.08.1"

    def test_missing_assumed_prices_receive_nonzero_simulated_pricing(self) -> None:
        hybrid_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.hybrid.json"
        )
        catalog = load_catalog_from_file(hybrid_path)
        kourou = next(item for item in catalog.stations if item.name == "ESA Kourou")
        assert kourou.booking_cost > 0.0
        assert kourou.cost_per_minute > 0.0
        assert "booking_cost" in kourou.field_provenance.assumptions
        assert "cost_per_minute" in kourou.field_provenance.assumptions

    def test_template_catalog_loads_and_has_incomplete_station(self) -> None:
        """Template catalog loads, has 1 station, and it is not planner eligible."""
        template_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "catalogs"
            / "stations.template.json"
        )
        catalog = load_catalog_from_file(template_path)
        assert len(catalog.stations) == 1
        assert catalog.stations[0].planner_eligible is False
        assert catalog.stations[0].supported_bands is None
        assert catalog.stations[0].max_downlink_rate_mbps is None


# ---------------------------------------------------------------------------
# Placeholder failure
# ---------------------------------------------------------------------------


class TestNotConfiguredPlaceholder:
    def test_raises_external_data_unavailable(self) -> None:
        provider = NotConfiguredStationCatalogProvider()
        with pytest.raises(ExternalDataUnavailable):
            provider.load()

    def test_error_code_is_catalog_not_configured(self) -> None:
        provider = NotConfiguredStationCatalogProvider()
        try:
            provider.load()
        except ExternalDataUnavailable as exc:
            assert exc.error.code == "EXTERNAL_DATA_UNAVAILABLE"
            assert "STATION_CATALOG_NOT_CONFIGURED" in exc.error.details.get("source", "")
        else:
            pytest.fail("ExternalDataUnavailable not raised")


# ---------------------------------------------------------------------------
# Cost model
# ---------------------------------------------------------------------------


class TestCostModel:
    def test_per_minute_model(self) -> None:
        s = _make_station(cost_model=CostModel.PER_MINUTE)
        assert s.cost_model == CostModel.PER_MINUTE

    def test_per_contact_plus_minute_model(self) -> None:
        s = _make_station(cost_model=CostModel.PER_CONTACT_PLUS_MINUTE)
        assert s.cost_model == CostModel.PER_CONTACT_PLUS_MINUTE

    def test_none_cost_model(self) -> None:
        s = _make_station(cost_model=CostModel.NONE)
        assert s.cost_model == CostModel.NONE

    def test_simultaneous_contacts_fixed_at_1(self) -> None:
        s = _make_station()
        assert s.simultaneous_contacts == 1

    def test_simultaneous_contacts_above_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="p",
                latitude_deg=0.0,
                longitude_deg=0.0,
                altitude_m=0.0,
                supported_bands=frozenset({Band.X}),
                max_downlink_rate_mbps=100.0,
                minimum_elevation_deg=5.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                simultaneous_contacts=2,
                field_provenance=_FULL_PROVENANCE,
            )
