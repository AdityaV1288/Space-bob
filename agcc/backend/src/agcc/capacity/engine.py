"""Capacity estimation engine (Task 08).

Computes usable downlink capacity per pass using 10-second integration samples,
elevation-based rate derating, weather attenuation, and protocol efficiency.

Architecture ownership: Capacity owns estimated physical transfer amounts.
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timedelta, timezone
from typing import Callable

from agcc.capacity.attenuation import (
    ConfiguredWeatherAttenuationModel,
    NoWeatherAttenuationModel,
    WeatherAttenuationModel,
)
from agcc.domain.enums import SourceQuality
from agcc.domain.orbit import SatelliteCommunications
from agcc.domain.planning import CandidatePass, CapacityEstimate
from agcc.domain.stations import GroundStation

_SAMPLE_DURATION_S: float = 10.0
_CAPACITY_MODEL_VERSION = "capacity_v1"


# ---------------------------------------------------------------------------
# Eligibility check
# ---------------------------------------------------------------------------


def is_capacity_eligible(
    satellite_comms: SatelliteCommunications,
    station: GroundStation,
) -> bool:
    """Return True when station supports the satellite band and both rates are positive."""
    if station.supported_bands is None or station.max_downlink_rate_mbps is None:
        return False
    return (
        satellite_comms.band in station.supported_bands
        and satellite_comms.max_downlink_rate_mbps > 0.0
        and station.max_downlink_rate_mbps > 0.0
    )


# ---------------------------------------------------------------------------
# Capacity ID derivation
# ---------------------------------------------------------------------------


def _capacity_id(pass_id: str) -> str:
    digest = hashlib.sha256(f"capacity|{pass_id}".encode()).hexdigest()[:16]
    return f"capacity_{digest}"


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------


class CapacityEngine:
    """Estimates usable downlink capacity for a CandidatePass.

    Parameters
    ----------
    attenuation_model:
        Production use: NotConfiguredWeatherAttenuationModel (blocks until
        approved coefficients are loaded into ConfiguredWeatherAttenuationModel).
        Tests only: NoWeatherAttenuationModel.
    model_version:
        Capacity model version string embedded in every CapacityEstimate.
    """

    def __init__(
        self,
        attenuation_model: WeatherAttenuationModel,
        model_version: str = _CAPACITY_MODEL_VERSION,
        elevation_provider: Callable[[CandidatePass, datetime], float] | None = None,
    ) -> None:
        self._attenuation = attenuation_model
        self._model_version = model_version
        self._using_no_weather = isinstance(attenuation_model, NoWeatherAttenuationModel)
        self._elevation_provider = elevation_provider

    def estimate(
        self,
        pass_: CandidatePass,
        satellite_comms: SatelliteCommunications,
        station: GroundStation,
        precipitation_mm_per_hr: float = 0.0,
        weather_data_quality: SourceQuality = SourceQuality.UNAVAILABLE,
    ) -> CapacityEstimate:
        """Compute a CapacityEstimate for a single CandidatePass.

        Parameters
        ----------
        pass_:
            The CandidatePass to estimate capacity for.
        satellite_comms:
            Satellite communication parameters.
        station:
            Ground station parameters.
        precipitation_mm_per_hr:
            Precipitation rate at the station during the pass (mm/hr).
        weather_data_quality:
            Quality flag for the weather data used.

        Raises
        ------
        ValueError
            If the pass is not capacity-eligible.
        RuntimeError
            If production attenuation configuration is missing
            (WEATHER_ATTENUATION_TABLE_MISSING).
        """
        if not is_capacity_eligible(satellite_comms, station):
            raise ValueError(
                f"Pass {pass_.pass_id} is not capacity-eligible: "
                f"station band support={station.supported_bands}, "
                f"satellite band={satellite_comms.band}"
            )
        if (
            isinstance(self._attenuation, ConfiguredWeatherAttenuationModel)
            and weather_data_quality == SourceQuality.UNAVAILABLE
        ):
            raise ValueError("Weather data is unavailable; neutral weather is not permitted")

        assumptions: list[str] = []
        if self._using_no_weather:
            assumptions.append(NoWeatherAttenuationModel.ASSUMPTION_LABEL)
        if self._elevation_provider is None:
            assumptions.append("InterpolatedPassElevation")

        # is_capacity_eligible guards None; assert for type narrowing
        assert station.max_downlink_rate_mbps is not None
        base_rate_mbps = min(
            satellite_comms.max_downlink_rate_mbps,
            station.max_downlink_rate_mbps,
        )

        # The usable interval starts after setup and ends before teardown.
        usable_start: datetime = pass_.start_at + timedelta(seconds=station.setup_s)
        usable_end: datetime = pass_.end_at - timedelta(seconds=station.teardown_s)

        usable_s = (usable_end - usable_start).total_seconds()
        if usable_s <= 0.0:
            # No usable window — return zero capacity
            return CapacityEstimate(
                capacity_id=_capacity_id(pass_.pass_id),
                pass_id=pass_.pass_id,
                base_rate_mbps=base_rate_mbps,
                usable_capacity_mb=0.0,
                average_effective_rate_mbps=0.0,
                peak_effective_rate_mbps=0.0,
                weather_data_quality=weather_data_quality.value,
                model_version=self._model_version,
                assumptions=assumptions,
                sample_count=0,
            )

        # --- 10-second integration loop ---
        total_capacity_mb = 0.0
        peak_effective_rate_mbps = 0.0
        sample_rates: list[float] = []

        t = usable_start
        usable_end_ts = usable_end.timestamp()

        while t.timestamp() < usable_end_ts:
            # Clamp last sample to the usable window end
            sample_end_ts = min(t.timestamp() + _SAMPLE_DURATION_S, usable_end_ts)
            sample_duration = sample_end_ts - t.timestamp()
            sample_mid_ts = t.timestamp() + sample_duration / 2.0
            sample_mid = datetime.fromtimestamp(sample_mid_ts, tz=timezone.utc)

            elevation_at_sample = (
                self._elevation_provider(pass_, sample_mid)
                if self._elevation_provider is not None
                else _interpolate_elevation(pass_, sample_mid)
            )
            elevation_rad = math.radians(elevation_at_sample)
            elevation_factor = max(0.0, min(1.0, math.sin(elevation_rad)))

            weather_factor = self._attenuation.factor(
                satellite_comms.carrier_frequency_ghz,
                elevation_at_sample,
                precipitation_mm_per_hr,
                latitude_deg=station.latitude_deg,
                longitude_deg=station.longitude_deg,
                station_altitude_m=station.altitude_m,
                polarization=satellite_comms.polarization,
            )

            effective_rate = (
                base_rate_mbps
                * satellite_comms.protocol_efficiency
                * elevation_factor
                * weather_factor
            )

            sample_capacity_mb = effective_rate * sample_duration / 8.0

            total_capacity_mb += sample_capacity_mb
            sample_rates.append(effective_rate)
            if effective_rate > peak_effective_rate_mbps:
                peak_effective_rate_mbps = effective_rate

            t = datetime.fromtimestamp(
                min(t.timestamp() + _SAMPLE_DURATION_S, usable_end_ts), tz=timezone.utc
            )
            if t.timestamp() >= usable_end_ts:
                break

        sample_count = len(sample_rates)
        average_effective_rate_mbps = sum(sample_rates) / sample_count if sample_count > 0 else 0.0

        return CapacityEstimate(
            capacity_id=_capacity_id(pass_.pass_id),
            pass_id=pass_.pass_id,
            base_rate_mbps=base_rate_mbps,
            usable_capacity_mb=total_capacity_mb,
            average_effective_rate_mbps=average_effective_rate_mbps,
            peak_effective_rate_mbps=peak_effective_rate_mbps,
            weather_data_quality=weather_data_quality.value,
            model_version=self._model_version,
            assumptions=assumptions,
            sample_count=sample_count,
        )

    def effective_rate_at(
        self,
        pass_: CandidatePass,
        satellite_comms: SatelliteCommunications,
        station: GroundStation,
        at: datetime,
        *,
        precipitation_mm_per_hr: float,
    ) -> float:
        """Return the Task 08 modeled rate at one simulation instant."""
        if not is_capacity_eligible(satellite_comms, station):
            raise ValueError(f"Pass {pass_.pass_id} is not capacity-eligible")
        assert station.max_downlink_rate_mbps is not None
        elevation_deg = (
            self._elevation_provider(pass_, at)
            if self._elevation_provider is not None
            else _interpolate_elevation(pass_, at)
        )
        elevation_factor = max(0.0, min(1.0, math.sin(math.radians(elevation_deg))))
        weather_factor = self._attenuation.factor(
            satellite_comms.carrier_frequency_ghz,
            elevation_deg,
            precipitation_mm_per_hr,
            latitude_deg=station.latitude_deg,
            longitude_deg=station.longitude_deg,
            station_altitude_m=station.altitude_m,
            polarization=satellite_comms.polarization,
        )
        return (
            min(satellite_comms.max_downlink_rate_mbps, station.max_downlink_rate_mbps)
            * satellite_comms.protocol_efficiency
            * elevation_factor
            * weather_factor
        )


# ---------------------------------------------------------------------------
# Elevation interpolation within a pass
# ---------------------------------------------------------------------------


def _interpolate_elevation(pass_: CandidatePass, t: datetime) -> float:
    """Estimate elevation at time t by linear interpolation over the pass window.

    Uses start→peak and peak→end segments.  Returns minimum_elevation_deg
    at the boundaries.
    """
    start_ts = pass_.start_at.timestamp()
    peak_ts = pass_.peak_at.timestamp()
    end_ts = pass_.end_at.timestamp()
    t_ts = t.timestamp()

    min_el = pass_.minimum_elevation_deg
    peak_el = pass_.max_elevation_deg

    if peak_ts <= start_ts:
        return min_el

    if t_ts <= peak_ts:
        # Rising segment: start → peak
        frac = (t_ts - start_ts) / (peak_ts - start_ts) if peak_ts > start_ts else 0.0
        return min_el + frac * (peak_el - min_el)
    else:
        # Falling segment: peak → end
        frac = (t_ts - peak_ts) / (end_ts - peak_ts) if end_ts > peak_ts else 1.0
        return peak_el - frac * (peak_el - min_el)
