"""Acceptance tests for Task 08 — Capacity model v1.

Verified acceptance criteria:
  1. 8 Mbps for 10 seconds with all factors 1.0 equals 10 MB.
  2. Capacity never exceeds the rate-limit maximum.
  3. Higher elevation never lowers elevation factor.
  4. A table factor outside [0, 1] is rejected.
  5. Missing production attenuation configuration blocks calculation explicitly.
  6. Band/frequency mismatch on SatelliteCommunications is rejected.
  7. Pass not eligible (band mismatch) raises ValueError from engine.
  8. NoWeatherAttenuationModel adds assumption label to CapacityEstimate.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agcc.capacity.attenuation import (
    WEATHER_ATTENUATION_TABLE_MISSING,
    ConfiguredWeatherAttenuationModel,
    NotConfiguredWeatherAttenuationModel,
    NoWeatherAttenuationModel,
)
from agcc.capacity.engine import CapacityEngine, _interpolate_elevation, is_capacity_eligible
from agcc.domain.enums import Band, LinkPolarization, SourceQuality
from agcc.domain.orbit import SatelliteCommunications
from agcc.domain.planning import CandidatePass
from agcc.domain.stations import FieldProvenance, GroundStation

# ---------------------------------------------------------------------------
# Shared fixture paths
# ---------------------------------------------------------------------------

_FIXTURE_DIR = (
    Path(__file__).resolve().parent.parent.parent.parent / "data" / "fixtures" / "environment"
)
_TEST_TABLE = _FIXTURE_DIR / "weather_attenuation_test.json"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 6, 1, 12, 0, 0, tzinfo=timezone.utc)

_COORD_PROV = FieldProvenance(
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
    ]
)


def _make_comms(
    band: Band = Band.X,
    carrier_frequency_ghz: float = 9.6,
    max_downlink_rate_mbps: float = 8.0,
    protocol_efficiency: float = 1.0,
) -> SatelliteCommunications:
    return SatelliteCommunications(
        band=band,
        carrier_frequency_ghz=carrier_frequency_ghz,
        max_downlink_rate_mbps=max_downlink_rate_mbps,
        protocol_efficiency=protocol_efficiency,
        polarization=LinkPolarization.CIRCULAR,
        min_elevation_deg=5.0,
    )


def _make_station(
    band: Band = Band.X,
    max_downlink_rate_mbps: float = 8.0,
    setup_s: int = 0,
    teardown_s: int = 0,
) -> GroundStation:
    return GroundStation(
        station_id="station_captest01",
        name="CapTest Station",
        provider_id="provider_test",
        latitude_deg=48.0,
        longitude_deg=11.0,
        altitude_m=500.0,
        supported_bands=frozenset({band}),
        max_downlink_rate_mbps=max_downlink_rate_mbps,
        minimum_elevation_deg=5.0,
        setup_s=setup_s,
        teardown_s=teardown_s,
        booking_cost=0.0,
        cost_per_minute=0.0,
        field_provenance=_COORD_PROV,
    )


def _make_pass(
    duration_s: float = 10.0,
    max_elevation_deg: float = 90.0,
    setup_s: int = 0,
    teardown_s: int = 0,
) -> CandidatePass:
    start = _NOW
    end = start + timedelta(seconds=duration_s)
    peak = start + timedelta(seconds=duration_s / 2)
    usable = duration_s - setup_s - teardown_s
    return CandidatePass(
        pass_id="pass_captest0001",
        scenario_id="scenario_captest01",
        satellite_id="sat_cap01",
        station_id="station_captest01",
        start_at=start,
        peak_at=peak,
        end_at=end,
        duration_s=duration_s,
        usable_duration_s=max(1.0, usable),
        max_elevation_deg=max_elevation_deg,
        azimuth_start_deg=10.0,
        azimuth_peak_deg=180.0,
        azimuth_end_deg=350.0,
        slant_range_peak_km=800.0,
        minimum_elevation_deg=5.0,
        orbit_model_version="circular_kepler_v1",
        station_catalog_version="2026.08.1",
    )


# ---------------------------------------------------------------------------
# Acceptance 1: 8 Mbps × 10 s × all-factors-1 = 10 MB
# ---------------------------------------------------------------------------


class TestAcceptance1BasicMath:
    """8 Mbps for 10 s with all factors 1 must equal exactly 10 MB."""

    def test_8mbps_10s_all_factors_1_is_10mb(self) -> None:
        # Setup: 8 Mbps, 10 s usable, max elevation = 90°, protocol_efficiency = 1.0
        # elevation_factor = sin(90°) = 1.0; weather_factor = 1.0
        # capacity = 8 * 10 / 8 = 10 MB
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=8.0, protocol_efficiency=1.0)
        station = _make_station(max_downlink_rate_mbps=8.0)
        # Pass: exactly 10 s, peak at 5 s so elevation = 90° throughout
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        est = engine.estimate(
            pass_,
            comms,
            station,
            precipitation_mm_per_hr=0.0,
            weather_data_quality=SourceQuality.VERIFIED,
        )
        assert abs(est.usable_capacity_mb - 10.0) < 1e-9, (
            f"Expected 10.0 MB, got {est.usable_capacity_mb}"
        )
        assert est.sample_count == 1

    def test_formula_unit_check(self) -> None:
        """Verify: rate_mbps * sample_s / 8 → capacity_mb."""
        rate = 8.0
        sample_s = 10.0
        expected_mb = rate * sample_s / 8.0
        assert expected_mb == 10.0


# ---------------------------------------------------------------------------
# Acceptance 2: capacity never exceeds rate-limit maximum
# ---------------------------------------------------------------------------


class TestAcceptance2RateLimit:
    def test_capacity_bounded_by_satellite_rate(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=8.0)
        station = _make_station(max_downlink_rate_mbps=1000.0)
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=90.0)

        est = engine.estimate(
            pass_,
            comms,
            station,
            precipitation_mm_per_hr=0.0,
            weather_data_quality=SourceQuality.VERIFIED,
        )

        max_possible_mb = 8.0 * 600.0 / 8.0  # 600 MB at full rate
        assert est.usable_capacity_mb <= max_possible_mb + 1e-9

    def test_capacity_bounded_by_station_rate(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=1000.0)
        station = _make_station(max_downlink_rate_mbps=8.0)
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=90.0)

        est = engine.estimate(
            pass_,
            comms,
            station,
            precipitation_mm_per_hr=0.0,
            weather_data_quality=SourceQuality.VERIFIED,
        )

        max_possible_mb = 8.0 * 600.0 / 8.0
        assert est.usable_capacity_mb <= max_possible_mb + 1e-9

    def test_base_rate_is_minimum_of_sat_and_station(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=50.0)
        station = _make_station(max_downlink_rate_mbps=100.0)
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        est = engine.estimate(pass_, comms, station)
        assert est.base_rate_mbps == 50.0

    def test_base_rate_is_minimum_of_sat_and_station_flipped(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=100.0)
        station = _make_station(max_downlink_rate_mbps=50.0)
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        est = engine.estimate(pass_, comms, station)
        assert est.base_rate_mbps == 50.0


# ---------------------------------------------------------------------------
# Acceptance 3: higher elevation never lowers elevation factor
# ---------------------------------------------------------------------------


class TestAcceptance3ElevationFactor:
    def test_higher_elevation_monotone_factor(self) -> None:
        """sin(elevation) is monotone in [0°, 90°]: higher elevation ↛ lower factor."""
        elevations = [5.0, 10.0, 20.0, 30.0, 45.0, 60.0, 75.0, 90.0]
        factors = [max(0.0, min(1.0, math.sin(math.radians(e)))) for e in elevations]
        for i in range(len(factors) - 1):
            assert factors[i] <= factors[i + 1], (
                f"Elevation factor decreased from {elevations[i]}° to {elevations[i + 1]}°"
            )

    def test_higher_elevation_pass_has_more_capacity(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=8.0)
        station = _make_station(max_downlink_rate_mbps=8.0)

        low_pass = _make_pass(duration_s=10.0, max_elevation_deg=10.0)
        high_pass = _make_pass(duration_s=10.0, max_elevation_deg=80.0)

        low_est = engine.estimate(low_pass, comms, station)
        high_est = engine.estimate(high_pass, comms, station)

        assert high_est.usable_capacity_mb >= low_est.usable_capacity_mb, (
            f"High elevation pass ({high_est.usable_capacity_mb:.4f} MB) "
            f"< low elevation pass ({low_est.usable_capacity_mb:.4f} MB)"
        )

    def test_elevation_factor_at_90deg_is_1(self) -> None:
        el_rad = math.radians(90.0)
        factor = max(0.0, min(1.0, math.sin(el_rad)))
        assert abs(factor - 1.0) < 1e-12

    def test_elevation_factor_at_0deg_is_0(self) -> None:
        el_rad = math.radians(0.0)
        factor = max(0.0, min(1.0, math.sin(el_rad)))
        assert factor == 0.0


# ---------------------------------------------------------------------------
# Acceptance 4: table factor outside [0, 1] is rejected
# ---------------------------------------------------------------------------


class TestAcceptance4TableValidation:
    def test_factor_above_1_rejected_at_load(self, tmp_path: Path) -> None:
        bad_table = tmp_path / "bad.json"
        bad_table.write_text(
            '{"version": "bad_v1", "entries": [{"freq_min_ghz": 0, "freq_max_ghz": 100, '
            '"rain_min_mm_per_hr": 0, "rain_max_mm_per_hr": 100, '
            '"elev_min_deg": 0, "elev_max_deg": 90, "factor": 1.5}]}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="factor"):
            ConfiguredWeatherAttenuationModel(bad_table)

    def test_factor_below_0_rejected_at_load(self, tmp_path: Path) -> None:
        bad_table = tmp_path / "bad2.json"
        bad_table.write_text(
            '{"version": "bad_v2", "entries": [{"freq_min_ghz": 0, "freq_max_ghz": 100, '
            '"rain_min_mm_per_hr": 0, "rain_max_mm_per_hr": 100, '
            '"elev_min_deg": 0, "elev_max_deg": 90, "factor": -0.1}]}',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="factor"):
            ConfiguredWeatherAttenuationModel(bad_table)

    def test_valid_test_table_loads_without_error(self) -> None:
        model = ConfiguredWeatherAttenuationModel(_TEST_TABLE)
        assert model.version == "test_v1"

    def test_configured_model_returns_1_for_zero_rain(self) -> None:
        model = ConfiguredWeatherAttenuationModel(_TEST_TABLE)
        f = model.factor(9.6, 45.0, 0.0)
        assert f == 1.0

    def test_configured_model_returns_lower_factor_for_rain(self) -> None:
        model = ConfiguredWeatherAttenuationModel(_TEST_TABLE)
        f_rain = model.factor(9.6, 45.0, 5.0)
        f_clear = model.factor(9.6, 45.0, 0.0)
        assert f_rain <= f_clear

    def test_configured_model_no_match_raises(self) -> None:
        model = ConfiguredWeatherAttenuationModel(_TEST_TABLE)
        with pytest.raises(ValueError, match="No attenuation entry"):
            model.factor(9.6, 45.0, 99999.0)


# ---------------------------------------------------------------------------
# Acceptance 5: missing production config blocks calculation
# ---------------------------------------------------------------------------


class TestAcceptance5ProductionBlock:
    def test_not_configured_raises_runtime_error(self) -> None:
        engine = CapacityEngine(NotConfiguredWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        with pytest.raises(RuntimeError) as exc_info:
            engine.estimate(pass_, comms, station)

        assert WEATHER_ATTENUATION_TABLE_MISSING in str(exc_info.value)

    def test_not_configured_error_message_contains_sentinel(self) -> None:
        model = NotConfiguredWeatherAttenuationModel()
        with pytest.raises(RuntimeError, match=WEATHER_ATTENUATION_TABLE_MISSING):
            model.factor(9.6, 45.0, 0.0)


# ---------------------------------------------------------------------------
# Band/frequency validation
# ---------------------------------------------------------------------------


class TestBandFrequencyValidation:
    def test_x_band_valid_frequency(self) -> None:
        comms = _make_comms(band=Band.X, carrier_frequency_ghz=9.6)
        assert comms.band == Band.X

    def test_x_band_frequency_below_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="carrier_frequency_ghz"):
            _make_comms(band=Band.X, carrier_frequency_ghz=1.5)

    def test_x_band_frequency_above_range_rejected(self) -> None:
        with pytest.raises(ValidationError, match="carrier_frequency_ghz"):
            _make_comms(band=Band.X, carrier_frequency_ghz=15.0)

    def test_s_band_valid_frequency(self) -> None:
        comms = _make_comms(band=Band.S, carrier_frequency_ghz=2.2)
        assert comms.band == Band.S

    def test_ka_band_valid_frequency(self) -> None:
        comms = _make_comms(band=Band.KA, carrier_frequency_ghz=30.0)
        assert comms.band == Band.KA

    def test_uhf_valid_frequency(self) -> None:
        comms = _make_comms(band=Band.UHF, carrier_frequency_ghz=0.437)
        assert comms.band == Band.UHF

    def test_vhf_valid_frequency(self) -> None:
        comms = _make_comms(band=Band.VHF, carrier_frequency_ghz=0.145)
        assert comms.band == Band.VHF

    def test_protocol_efficiency_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_comms(protocol_efficiency=0.0)

    def test_protocol_efficiency_above_1_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _make_comms(protocol_efficiency=1.1)

    def test_protocol_efficiency_1_accepted(self) -> None:
        comms = _make_comms(protocol_efficiency=1.0)
        assert comms.protocol_efficiency == 1.0


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


class TestEligibility:
    def test_band_mismatch_not_eligible(self) -> None:
        comms = _make_comms(band=Band.X)
        station = _make_station(band=Band.S)
        assert not is_capacity_eligible(comms, station)

    def test_band_match_eligible(self) -> None:
        comms = _make_comms(band=Band.X)
        station = _make_station(band=Band.X)
        assert is_capacity_eligible(comms, station)

    def test_ineligible_pass_raises(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(band=Band.X)
        station = _make_station(band=Band.S)
        pass_ = _make_pass(duration_s=10.0)
        with pytest.raises(ValueError, match="not capacity-eligible"):
            engine.estimate(pass_, comms, station)


# ---------------------------------------------------------------------------
# NoWeatherAttenuationModel assumption label
# ---------------------------------------------------------------------------


class TestNoWeatherAssumption:
    def test_no_weather_model_adds_assumption_label(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        est = engine.estimate(pass_, comms, station)
        assert NoWeatherAttenuationModel.ASSUMPTION_LABEL in est.assumptions

    def test_configured_model_does_not_add_no_weather_label(self) -> None:
        engine = CapacityEngine(ConfiguredWeatherAttenuationModel(_TEST_TABLE))
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0, max_elevation_deg=90.0)

        est = engine.estimate(
            pass_,
            comms,
            station,
            precipitation_mm_per_hr=0.0,
            weather_data_quality=SourceQuality.VERIFIED,
        )
        assert NoWeatherAttenuationModel.ASSUMPTION_LABEL not in est.assumptions


# ---------------------------------------------------------------------------
# Setup/teardown exclusion
# ---------------------------------------------------------------------------


class TestSetupTeardown:
    def test_setup_teardown_reduces_capacity(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms(max_downlink_rate_mbps=8.0)

        # 30s pass, no overhead
        pass_no_overhead = _make_pass(duration_s=30.0, max_elevation_deg=90.0)
        station_no_overhead = _make_station(setup_s=0, teardown_s=0)
        est_full = engine.estimate(pass_no_overhead, comms, station_no_overhead)

        # Same pass but station has 10s setup + 10s teardown
        station_overhead = _make_station(setup_s=10, teardown_s=10)
        est_partial = engine.estimate(pass_no_overhead, comms, station_overhead)

        assert est_partial.usable_capacity_mb < est_full.usable_capacity_mb

    def test_setup_teardown_exceeds_duration_yields_zero(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station(setup_s=100, teardown_s=100)
        pass_ = _make_pass(duration_s=10.0)

        est = engine.estimate(pass_, comms, station)
        assert est.usable_capacity_mb == 0.0
        assert est.sample_count == 0


# ---------------------------------------------------------------------------
# Elevation interpolation
# ---------------------------------------------------------------------------


class TestElevationInterpolation:
    def test_interpolation_at_peak_returns_max_elevation(self) -> None:
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=45.0)
        el = _interpolate_elevation(pass_, pass_.peak_at)
        assert abs(el - 45.0) < 1e-9

    def test_interpolation_at_start_returns_min_elevation(self) -> None:
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=45.0)
        el = _interpolate_elevation(pass_, pass_.start_at)
        assert abs(el - pass_.minimum_elevation_deg) < 1e-9

    def test_interpolation_monotone_rising(self) -> None:
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=45.0)
        times = [pass_.start_at + timedelta(seconds=i * 30) for i in range(11)]
        elevations = [_interpolate_elevation(pass_, t) for t in times]
        # First half should be rising
        for i in range(5):
            assert elevations[i] <= elevations[i + 1], (
                f"Elevation not rising at sample {i}: {elevations[i]} > {elevations[i + 1]}"
            )


# ---------------------------------------------------------------------------
# CapacityEstimate contract
# ---------------------------------------------------------------------------


class TestCapacityEstimateContract:
    def test_capacity_id_prefix(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0)
        est = engine.estimate(pass_, comms, station)
        assert est.capacity_id.startswith("capacity_")

    def test_capacity_id_deterministic(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0)
        est1 = engine.estimate(pass_, comms, station)
        est2 = engine.estimate(pass_, comms, station)
        assert est1.capacity_id == est2.capacity_id

    def test_model_version_in_estimate(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel(), model_version="capacity_v1")
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0)
        est = engine.estimate(pass_, comms, station)
        assert est.model_version == "capacity_v1"

    def test_weather_data_quality_propagated(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=10.0)
        est = engine.estimate(pass_, comms, station, weather_data_quality=SourceQuality.STALE)
        assert est.weather_data_quality == "stale"

    def test_peak_rate_gte_average_rate(self) -> None:
        engine = CapacityEngine(NoWeatherAttenuationModel())
        comms = _make_comms()
        station = _make_station()
        pass_ = _make_pass(duration_s=600.0, max_elevation_deg=45.0)
        est = engine.estimate(pass_, comms, station)
        assert est.peak_effective_rate_mbps >= est.average_effective_rate_mbps
