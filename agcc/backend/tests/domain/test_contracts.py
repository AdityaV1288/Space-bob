"""Tests for ID prefix enforcement, UTC validation, units, and serialization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from agcc.domain.common import ModelRef, Provenance
from agcc.domain.enums import (
    RejectionCode,
    SourceType,
)
from agcc.domain.environment import WeatherSnapshot
from agcc.domain.errors import (
    external_data_unavailable,
    infeasible_mission,
    missing_configuration,
    validation_error,
)
from agcc.domain.mission import DownlinkMission, Scenario, ScenarioConstraints
from agcc.domain.orbit import CustomCircularOrbit, CustomSatellite, SatelliteCommunications
from agcc.domain.planning import (
    CandidatePass,
    CapacityEstimate,
    ContactPlan,
    FeasibilityResult,
)
from agcc.domain.simulation import (
    DataFragment,
    ReplanProposal,
    SimulationEvent,
)
from agcc.domain.stations import GroundStation

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
_NAIVE = datetime(2024, 6, 1, 12, 0, 0)  # no tzinfo

_PROVENANCE = Provenance(
    source_type=SourceType.MANUAL,
    source_name="test",
    fetched_at=_NOW,
)


def _orbit() -> CustomCircularOrbit:
    return CustomCircularOrbit(
        altitude_km=550.0,
        inclination_deg=53.0,
        raan_deg=0.0,
        phase_deg=0.0,
        epoch=_NOW,
    )


def _comms() -> SatelliteCommunications:
    from agcc.domain.enums import Band, LinkPolarization

    return SatelliteCommunications(
        band=Band.X,
        carrier_frequency_ghz=9.6,
        max_downlink_rate_mbps=100.0,
        protocol_efficiency=1.0,
        polarization=LinkPolarization.CIRCULAR,
        min_elevation_deg=5.0,
    )


def _satellite(sat_id: str = "sat_test01") -> CustomSatellite:
    return CustomSatellite(
        satellite_id=sat_id,
        name="TestSat",
        orbit=_orbit(),
        comms=_comms(),
        provenance=_PROVENANCE,
    )


_FULL_STATION_ASSUMPTIONS = [
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


def _station(station_id: str = "station_test01") -> GroundStation:
    from agcc.domain.enums import Band
    from agcc.domain.stations import FieldProvenance

    return GroundStation(
        station_id=station_id,
        name="TestStation",
        provider_id="provider_test",
        latitude_deg=48.0,
        longitude_deg=11.0,
        altitude_m=500.0,
        supported_bands=frozenset({Band.X}),
        minimum_elevation_deg=5.0,
        max_downlink_rate_mbps=100.0,
        setup_s=60,
        teardown_s=30,
        booking_cost=0.0,
        cost_per_minute=10.0,
        field_provenance=FieldProvenance(assumptions=_FULL_STATION_ASSUMPTIONS),
    )


def _candidate_pass(pass_id: str = "pass_001") -> CandidatePass:
    return CandidatePass(
        pass_id=pass_id,
        scenario_id="scenario_test01",
        satellite_id="sat_test01",
        station_id="station_test01",
        start_at=_NOW,
        peak_at=_NOW + timedelta(minutes=5),
        end_at=_NOW + timedelta(minutes=10),
        duration_s=600.0,
        usable_duration_s=480.0,
        max_elevation_deg=45.0,
        azimuth_start_deg=10.0,
        azimuth_peak_deg=180.0,
        azimuth_end_deg=350.0,
        slant_range_peak_km=800.0,
        minimum_elevation_deg=5.0,
        orbit_model_version="circular_kepler_v1",
        station_catalog_version="2026.08.1",
    )


# ---------------------------------------------------------------------------
# ID prefix tests
# ---------------------------------------------------------------------------


class TestIdPrefixes:
    def test_satellite_valid_id(self) -> None:
        sat = _satellite("sat_abc")
        assert sat.satellite_id == "sat_abc"

    def test_satellite_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError, match="sat_"):
            _satellite("WRONG_abc")

    def test_satellite_prefix_only_fails(self) -> None:
        with pytest.raises(ValidationError):
            _satellite("sat_")

    def test_station_valid_id(self) -> None:
        s = _station("station_x1")
        assert s.station_id == "station_x1"

    def test_station_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError, match="station_"):
            _station("badprefix_x1")

    def test_pass_valid_id(self) -> None:
        p = _candidate_pass("pass_0001")
        assert p.pass_id == "pass_0001"

    def test_pass_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            _candidate_pass("contact_0001")

    def test_mission_valid_id(self) -> None:
        m = DownlinkMission(
            mission_id="mission_m1",
            name="M1",
            required_volume_mb=100.0,
            release_at=_NOW,
            deadline_at=_NOW + timedelta(hours=24),
        )
        assert m.mission_id == "mission_m1"

    def test_mission_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            DownlinkMission(
                mission_id="plan_m1",
                name="M1",
                required_volume_mb=100.0,
                release_at=_NOW,
                deadline_at=_NOW + timedelta(hours=24),
            )

    def test_plan_valid_id(self) -> None:
        plan = ContactPlan(
            plan_id="plan_v1",
            scenario_id="scenario_s1",
            version=0,
            feasibility=FeasibilityResult(
                is_feasible=True,
                planned_volume_mb=100.0,
                planned_cost_usd=50.0,
            ),
        )
        assert plan.plan_id == "plan_v1"

    def test_plan_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            ContactPlan(
                plan_id="mission_v1",
                scenario_id="scenario_s1",
                version=0,
                feasibility=FeasibilityResult(
                    is_feasible=True,
                    planned_volume_mb=100.0,
                    planned_cost_usd=50.0,
                ),
            )

    def test_fragment_valid_id(self) -> None:
        f = DataFragment(
            fragment_id="fragment_f1",
            mission_id="mission_m1",
            volume_mb=10.0,
        )
        assert f.fragment_id == "fragment_f1"

    def test_fragment_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            DataFragment(
                fragment_id="event_f1",
                mission_id="mission_m1",
                volume_mb=10.0,
            )

    def test_event_valid_id(self) -> None:
        ev = SimulationEvent(
            event_id="event_e1",
            event_type="contact_started",
            occurred_at=_NOW,
        )
        assert ev.event_id == "event_e1"

    def test_event_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            SimulationEvent(
                event_id="fragment_e1",
                event_type="contact_started",
                occurred_at=_NOW,
            )

    def test_capacity_valid_id(self) -> None:
        c = CapacityEstimate(
            capacity_id="capacity_c1",
            pass_id="pass_p1",
            base_rate_mbps=50.0,
            usable_capacity_mb=375.0,
            average_effective_rate_mbps=50.0,
            peak_effective_rate_mbps=50.0,
            weather_data_quality="verified",
            model_version="capacity_v1",
            sample_count=6,
        )
        assert c.capacity_id == "capacity_c1"

    def test_capacity_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            CapacityEstimate(
                capacity_id="pass_c1",
                pass_id="pass_p1",
                base_rate_mbps=50.0,
                usable_capacity_mb=375.0,
                average_effective_rate_mbps=50.0,
                peak_effective_rate_mbps=50.0,
                weather_data_quality="verified",
                model_version="capacity_v1",
                sample_count=6,
            )

    def test_scenario_valid_id(self) -> None:
        from decimal import Decimal

        sc = Scenario(
            scenario_id="scenario_s1",
            name="S1",
            satellite_id="sat_x",
            station_ids=["station_a"],
            mission_id="mission_m",
            constraints=ScenarioConstraints(
                maximum_budget=Decimal("1000.00"),
                currency="USD",
            ),
        )
        assert sc.scenario_id == "scenario_s1"

    def test_scenario_wrong_prefix_fails(self) -> None:
        from decimal import Decimal

        with pytest.raises(ValidationError):
            Scenario(
                scenario_id="plan_s1",
                name="S1",
                satellite_id="sat_x",
                station_ids=["station_a"],
                mission_id="mission_m",
                constraints=ScenarioConstraints(
                    maximum_budget=Decimal("1000.00"),
                    currency="USD",
                ),
            )

    def test_proposal_valid_id(self) -> None:
        p = ReplanProposal(
            proposal_id="proposal_p1",
            plan_id="plan_v1",
            created_at=_NOW,
        )
        assert p.proposal_id == "proposal_p1"

    def test_proposal_wrong_prefix_fails(self) -> None:
        with pytest.raises(ValidationError):
            ReplanProposal(
                proposal_id="fragment_p1",
                plan_id="plan_v1",
                created_at=_NOW,
            )


# ---------------------------------------------------------------------------
# Naive datetime rejection
# ---------------------------------------------------------------------------


class TestNaiveDatetimes:
    def test_orbit_epoch_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            CustomCircularOrbit(
                altitude_km=550.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                phase_deg=0.0,
                epoch=_NAIVE,
            )

    def test_provenance_fetched_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            Provenance(
                source_type=SourceType.MANUAL,
                source_name="test",
                fetched_at=_NAIVE,
            )

    def test_pass_start_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            CandidatePass(
                pass_id="pass_001",
                scenario_id="scenario_test",
                satellite_id="sat_x",
                station_id="station_y",
                start_at=_NAIVE,
                peak_at=_NOW + timedelta(minutes=5),
                end_at=_NOW + timedelta(minutes=10),
                duration_s=600.0,
                usable_duration_s=480.0,
                max_elevation_deg=30.0,
                azimuth_start_deg=0.0,
                azimuth_peak_deg=0.0,
                azimuth_end_deg=0.0,
                slant_range_peak_km=900.0,
                minimum_elevation_deg=5.0,
                orbit_model_version="v1",
                station_catalog_version="v1",
            )

    def test_pass_end_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            CandidatePass(
                pass_id="pass_001",
                scenario_id="scenario_test",
                satellite_id="sat_x",
                station_id="station_y",
                start_at=_NOW,
                peak_at=_NOW + timedelta(minutes=5),
                end_at=_NAIVE,
                duration_s=600.0,
                usable_duration_s=480.0,
                max_elevation_deg=30.0,
                azimuth_start_deg=0.0,
                azimuth_peak_deg=0.0,
                azimuth_end_deg=0.0,
                slant_range_peak_km=900.0,
                minimum_elevation_deg=5.0,
                orbit_model_version="v1",
                station_catalog_version="v1",
            )

    def test_mission_deadline_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            DownlinkMission(
                mission_id="mission_m1",
                name="M",
                required_volume_mb=100.0,
                release_at=_NOW,
                deadline_at=_NAIVE,
            )

    def test_event_occurred_at_naive_rejected(self) -> None:
        with pytest.raises(ValidationError, match="naive"):
            SimulationEvent(
                event_id="event_e1",
                event_type="contact_started",
                occurred_at=_NAIVE,
            )

    def test_weather_naive_rejected(self) -> None:
        import hashlib
        import json

        payload = dict(
            snapshot_id="event_wx_test",
            station_id="station_x",
            valid_from=_NOW.isoformat().replace("+00:00", "Z"),
            valid_until=(_NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
            observed_at=_NAIVE.isoformat(),
            fetched_at=_NOW.isoformat().replace("+00:00", "Z"),
            precipitation_mm_per_hr=0.0,
            temperature_c=15.0,
            relative_humidity_pct=50.0,
            cloud_cover_pct=0.0,
            wind_speed_mps=0.0,
            source_kind="fixture",
            source_quality="verified",
            provenance=dict(
                source_type="manual",
                source_name="test",
                fetched_at=_NOW.isoformat().replace("+00:00", "Z"),
            ),
        )
        h = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(
                "utf-8"
            )
        ).hexdigest()
        with pytest.raises(ValidationError, match="naive"):
            WeatherSnapshot(
                snapshot_id="event_wx_test",
                station_id="station_x",
                valid_from=_NOW,
                valid_until=_NOW + timedelta(hours=1),
                observed_at=_NAIVE,
                fetched_at=_NOW,
                precipitation_mm_per_hr=0.0,
                temperature_c=15.0,
                relative_humidity_pct=50.0,
                cloud_cover_pct=0.0,
                wind_speed_mps=0.0,
                source_kind="fixture",
                source_quality="verified",
                raw_payload_hash=h,
                provenance=_PROVENANCE,
            )

    def test_non_utc_aware_normalizes_to_utc(self) -> None:
        """An aware non-UTC datetime must be accepted and normalized to UTC."""
        eastern = datetime(2024, 6, 1, 8, 0, 0, tzinfo=timezone(timedelta(hours=-4)))
        orbit = CustomCircularOrbit(
            altitude_km=550.0,
            inclination_deg=53.0,
            raan_deg=0.0,
            phase_deg=0.0,
            epoch=eastern,
        )
        assert orbit.epoch.tzinfo == timezone.utc
        assert orbit.epoch == datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Negative value rejection
# ---------------------------------------------------------------------------


class TestUnitConstraints:
    def test_altitude_out_of_range_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CustomCircularOrbit(
                altitude_km=100.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                phase_deg=0.0,
                epoch=_NOW,
            )

    def test_altitude_above_max_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CustomCircularOrbit(
                altitude_km=2001.0,
                inclination_deg=53.0,
                raan_deg=0.0,
                phase_deg=0.0,
                epoch=_NOW,
            )

    def test_downlink_rate_zero_rejected(self) -> None:
        from agcc.domain.enums import Band, LinkPolarization

        with pytest.raises(ValidationError):
            SatelliteCommunications(
                band=Band.X,
                carrier_frequency_ghz=9.6,
                max_downlink_rate_mbps=0.0,
                protocol_efficiency=1.0,
                polarization=LinkPolarization.CIRCULAR,
                min_elevation_deg=5.0,
            )

    def test_downlink_rate_negative_rejected(self) -> None:
        from agcc.domain.enums import Band, LinkPolarization

        with pytest.raises(ValidationError):
            SatelliteCommunications(
                band=Band.X,
                carrier_frequency_ghz=9.6,
                max_downlink_rate_mbps=-1.0,
                protocol_efficiency=1.0,
                polarization=LinkPolarization.CIRCULAR,
                min_elevation_deg=5.0,
            )

    def test_required_volume_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DownlinkMission(
                mission_id="mission_m1",
                name="M",
                required_volume_mb=0.0,
                release_at=_NOW,
                deadline_at=_NOW + timedelta(hours=24),
            )

    def test_required_volume_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DownlinkMission(
                mission_id="mission_m1",
                name="M",
                required_volume_mb=-10.0,
                release_at=_NOW,
                deadline_at=_NOW + timedelta(hours=24),
            )

    def test_fragment_volume_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DataFragment(
                fragment_id="fragment_f1",
                mission_id="mission_m1",
                volume_mb=0.0,
            )

    def test_station_max_rate_zero_rejected(self) -> None:
        from agcc.domain.enums import Band
        from agcc.domain.stations import FieldProvenance

        with pytest.raises(ValidationError):
            GroundStation(
                station_id="station_x",
                name="X",
                provider_id="prov_x",
                latitude_deg=0.0,
                longitude_deg=0.0,
                altitude_m=0.0,
                supported_bands=frozenset({Band.X}),
                minimum_elevation_deg=5.0,
                max_downlink_rate_mbps=0.0,
                setup_s=0,
                teardown_s=0,
                booking_cost=0.0,
                cost_per_minute=0.0,
                field_provenance=FieldProvenance(assumptions=_FULL_STATION_ASSUMPTIONS),
            )

    def test_capacity_mb_negative_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CapacityEstimate(
                capacity_id="capacity_c1",
                pass_id="pass_p1",
                base_rate_mbps=50.0,
                usable_capacity_mb=-1.0,
                average_effective_rate_mbps=50.0,
                peak_effective_rate_mbps=50.0,
                weather_data_quality="verified",
                model_version="capacity_v1",
                sample_count=6,
            )


# ---------------------------------------------------------------------------
# UTC Z serialization
# ---------------------------------------------------------------------------


class TestUtcSerialization:
    def test_orbit_epoch_serializes_with_z(self) -> None:
        orbit = _orbit()
        data = orbit.model_dump(mode="json")
        assert data["epoch"].endswith("Z")

    def test_pass_start_at_serializes_with_z(self) -> None:
        p = _candidate_pass()
        data = p.model_dump(mode="json")
        assert data["start_at"].endswith("Z")

    def test_event_serializes_with_z(self) -> None:
        ev = SimulationEvent(
            event_id="event_e1",
            event_type="contact_started",
            occurred_at=_NOW,
        )
        data = ev.model_dump(mode="json")
        assert data["occurred_at"].endswith("Z")

    def test_mission_deadline_serializes_with_z(self) -> None:
        m = DownlinkMission(
            mission_id="mission_m1",
            name="M",
            required_volume_mb=100.0,
            release_at=_NOW,
            deadline_at=_NOW + timedelta(hours=24),
        )
        data = m.model_dump(mode="json")
        assert data["deadline_at"].endswith("Z")


# ---------------------------------------------------------------------------
# Error constructors
# ---------------------------------------------------------------------------


class TestDomainErrors:
    def test_validation_error_code(self) -> None:
        err = validation_error("bad field")
        assert err.code == "VALIDATION_ERROR"
        assert "bad field" in err.message

    def test_missing_configuration_code(self) -> None:
        err = missing_configuration("weather_adapter")
        assert err.code == "MISSING_CONFIGURATION"
        assert "weather_adapter" in err.details.get("field", "")

    def test_infeasible_mission_code(self) -> None:
        err = infeasible_mission(RejectionCode.BUDGET_EXCEEDED)
        assert err.code == "INFEASIBLE_MISSION"
        assert err.details["rejection_code"] == "budget_exceeded"

    def test_external_data_unavailable_code(self) -> None:
        err = external_data_unavailable("weather_api")
        assert err.code == "EXTERNAL_DATA_UNAVAILABLE"
        assert err.details["source"] == "weather_api"

    def test_error_serializes_deterministically(self) -> None:
        err = validation_error("test", entity_refs={"z": "1", "a": "2"})
        d1 = err.model_dump_sorted()
        d2 = err.model_dump_sorted()
        assert list(d1.keys()) == sorted(d1.keys())
        assert d1 == d2

    def test_domain_error_immutable(self) -> None:
        err = validation_error("test")
        with pytest.raises(Exception):
            err.code = "OTHER"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ModelRef
# ---------------------------------------------------------------------------


class TestModelRef:
    def test_basic(self) -> None:
        ref = ModelRef(id="plan_abc", version=1)
        assert ref.id == "plan_abc"
        assert ref.version == 1

    def test_negative_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ModelRef(id="plan_abc", version=-1)


# ---------------------------------------------------------------------------
# CandidatePass invariants (Part D)
# ---------------------------------------------------------------------------


class TestCandidatePassInvariants:
    """Tests for the CandidatePass model_validator invariants."""

    def _base_kwargs(self, **overrides: object) -> dict:
        kw: dict = dict(
            pass_id="pass_001",
            scenario_id="scenario_test01",
            satellite_id="sat_test01",
            station_id="station_test01",
            start_at=_NOW,
            peak_at=_NOW + timedelta(minutes=5),
            end_at=_NOW + timedelta(minutes=10),
            duration_s=600.0,
            usable_duration_s=480.0,
            max_elevation_deg=45.0,
            azimuth_start_deg=10.0,
            azimuth_peak_deg=180.0,
            azimuth_end_deg=350.0,
            slant_range_peak_km=800.0,
            minimum_elevation_deg=5.0,
            orbit_model_version="circular_kepler_v1",
            station_catalog_version="2026.08.1",
        )
        kw.update(overrides)
        return kw

    def test_valid_pass_is_accepted(self) -> None:
        p = CandidatePass(**self._base_kwargs())
        assert p.pass_id == "pass_001"

    def test_peak_before_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(
                **self._base_kwargs(
                    peak_at=_NOW - timedelta(seconds=1),
                )
            )

    def test_peak_equal_to_start_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(
                **self._base_kwargs(
                    peak_at=_NOW,
                )
            )

    def test_end_before_peak_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(
                **self._base_kwargs(
                    end_at=_NOW + timedelta(minutes=3),
                    duration_s=300.0,
                    peak_at=_NOW + timedelta(minutes=4),
                )
            )

    def test_inconsistent_duration_rejected(self) -> None:
        """duration_s that differs from end_at - start_at by > 1s must be rejected."""
        with pytest.raises(ValidationError):
            CandidatePass(
                **self._base_kwargs(
                    duration_s=700.0,  # 600s actual, 700s stated → 100s diff > 1s
                )
            )

    def test_zero_usable_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(**self._base_kwargs(usable_duration_s=0.0))

    def test_usable_greater_than_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(**self._base_kwargs(usable_duration_s=700.0))

    def test_max_el_less_than_min_el_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(
                **self._base_kwargs(
                    max_elevation_deg=3.0,
                    minimum_elevation_deg=5.0,
                )
            )

    def test_blank_scenario_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(**self._base_kwargs(scenario_id=""))

    def test_blank_orbit_model_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(**self._base_kwargs(orbit_model_version=""))

    def test_blank_station_catalog_version_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CandidatePass(**self._base_kwargs(station_catalog_version=""))
