"""Weather attenuation model: protocol and implementations.

The production configured model raises WEATHER_ATTENUATION_TABLE_MISSING until
the team supplies approved coefficients in the versioned JSON lookup table.

NoWeatherAttenuationModel is allowed in tests only and must be flagged as an
assumption in every CapacityEstimate that uses it.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from agcc.domain.enums import LinkPolarization

# ---------------------------------------------------------------------------
# Sentinel error code
# ---------------------------------------------------------------------------

WEATHER_ATTENUATION_TABLE_MISSING = "WEATHER_ATTENUATION_TABLE_MISSING"

# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------


class WeatherAttenuationModel(Protocol):
    """Returns a dimensionless attenuation factor in [0, 1]."""

    def factor(
        self,
        frequency_ghz: float,
        elevation_deg: float,
        precipitation_mm_per_hr: float,
        *,
        latitude_deg: float = 0.0,
        longitude_deg: float = 0.0,
        station_altitude_m: float = 0.0,
        polarization: LinkPolarization = LinkPolarization.CIRCULAR,
    ) -> float:
        """Return attenuation factor in [0, 1].

        A factor of 1.0 means no attenuation.
        A factor of 0.0 means total attenuation.
        Must raise ValueError if the returned value is outside [0, 1].
        """
        ...


# ---------------------------------------------------------------------------
# No-weather model — test use only
# ---------------------------------------------------------------------------


class NoWeatherAttenuationModel:
    """Always returns factor 1.0 (no attenuation).

    PERMITTED IN TESTS ONLY.  Any CapacityEstimate computed with this model
    must include "NoWeatherAttenuationModel" in its assumptions list.
    """

    ASSUMPTION_LABEL = "NoWeatherAttenuationModel"

    def factor(
        self,
        frequency_ghz: float,
        elevation_deg: float,
        precipitation_mm_per_hr: float,
        **_: object,
    ) -> float:
        return 1.0


# ---------------------------------------------------------------------------
# Configured model — production
# ---------------------------------------------------------------------------


class ConfiguredWeatherAttenuationModel:
    """Looks up attenuation factor from a versioned JSON table.

    Table format (JSON):
    {
      "version": "<string>",
      "entries": [
        {
          "freq_min_ghz": <float>,
          "freq_max_ghz": <float>,
          "rain_min_mm_per_hr": <float>,
          "rain_max_mm_per_hr": <float>,
          "elev_min_deg": <float>,
          "elev_max_deg": <float>,
          "factor": <float in [0,1]>
        },
        ...
      ]
    }

    Lookup: find the first entry whose ranges include the three input values.
    If no entry matches, raise ValueError.
    All factor values outside [0, 1] are rejected at load time.
    The model is immutable once loaded.
    """

    def __init__(self, table_path: Path) -> None:
        raw = json.loads(table_path.read_text(encoding="utf-8"))
        self._version: str = raw["version"]
        self._entries: list[dict[str, float]] = raw["entries"]
        # Validate all factors at load time
        for entry in self._entries:
            f = entry["factor"]
            if not (0.0 <= f <= 1.0):
                raise ValueError(
                    f"Weather attenuation table contains factor {f} outside [0, 1] "
                    f"in table version '{self._version}'"
                )

    @property
    def version(self) -> str:
        return self._version

    def factor(
        self,
        frequency_ghz: float,
        elevation_deg: float,
        precipitation_mm_per_hr: float,
        **_: object,
    ) -> float:
        for entry in self._entries:
            rain_ok = (
                entry["rain_min_mm_per_hr"]
                <= precipitation_mm_per_hr
                <= entry["rain_max_mm_per_hr"]
            )
            if (
                entry["freq_min_ghz"] <= frequency_ghz <= entry["freq_max_ghz"]
                and rain_ok
                and entry["elev_min_deg"] <= elevation_deg <= entry["elev_max_deg"]
            ):
                return float(entry["factor"])
        raise ValueError(
            f"No attenuation entry matches frequency={frequency_ghz} GHz, "
            f"elevation={elevation_deg} deg, rain={precipitation_mm_per_hr} mm/hr "
            f"in table version '{self._version}'"
        )


# ---------------------------------------------------------------------------
# Not-configured placeholder — production default until table is supplied
# ---------------------------------------------------------------------------


class NotConfiguredWeatherAttenuationModel:
    """Blocks calculation until approved attenuation coefficients are provided.

    Required configuration (not supplied here):
      - A weather_attenuation.json file with approved scientific coefficients.

    Raises RuntimeError with WEATHER_ATTENUATION_TABLE_MISSING on any call to
    factor(), preventing silent use of uninitialised physics.
    """

    def factor(
        self,
        frequency_ghz: float,
        elevation_deg: float,
        precipitation_mm_per_hr: float,
        **_: object,
    ) -> float:
        raise RuntimeError(
            f"{WEATHER_ATTENUATION_TABLE_MISSING}: Production weather attenuation "
            "coefficients have not been provided. Supply a versioned "
            "weather_attenuation.json table to use ConfiguredWeatherAttenuationModel."
        )


class ItuRainAttenuationModel:
    """Level-A instantaneous rain loss using ITU-R P.838-3 and P.839-4 data.

    P.838 supplies polarization-aware specific attenuation. P.839 supplies the
    geospatial rain height. The effective path reduction follows the P.618-13
    Earth-space geometry, evaluated with the live rain rate. The result is a
    deterministic model estimate, not measured RF telemetry.
    """

    version = "itu-r-p838-3+p839-4+p618-13-live-v1"

    def factor(
        self,
        frequency_ghz: float,
        elevation_deg: float,
        precipitation_mm_per_hr: float,
        *,
        latitude_deg: float = 0.0,
        longitude_deg: float = 0.0,
        station_altitude_m: float = 0.0,
        polarization: LinkPolarization = LinkPolarization.CIRCULAR,
    ) -> float:
        if precipitation_mm_per_hr <= 0.0:
            return 1.0
        if elevation_deg <= 0.0:
            return 0.0
        gamma_r = _specific_attenuation(
            precipitation_mm_per_hr, frequency_ghz, elevation_deg, polarization
        )
        rain_height_km = _rain_height_km(latitude_deg, longitude_deg)
        station_height_km = station_altitude_m / 1000.0
        height_delta = max(0.0, rain_height_km - station_height_km)
        if height_delta <= 0.0 or gamma_r <= 0.0:
            return 1.0
        theta = math.radians(elevation_deg)
        slant_path_km = height_delta / math.sin(theta)
        horizontal_km = slant_path_km * math.cos(theta)
        horizontal_reduction = 1.0 / (
            1.0
            + 0.78 * math.sqrt(max(0.0, horizontal_km * gamma_r / frequency_ghz))
            - 0.38 * (1.0 - math.exp(-2.0 * horizontal_km))
        )
        reduced_horizontal = horizontal_km * horizontal_reduction
        if reduced_horizontal <= 0.0:
            reduced_slant = slant_path_km
        else:
            zeta = math.atan2(height_delta, reduced_horizontal)
            reduced_slant = (
                reduced_horizontal / math.cos(theta)
                if zeta > theta
                else height_delta / math.sin(theta)
            )
        chi = max(0.0, 36.0 - abs(latitude_deg))
        vertical_term = (
            31.0
            * (1.0 - math.exp(-elevation_deg / (1.0 + chi)))
            * math.sqrt(max(0.0, reduced_slant * gamma_r))
            / (frequency_ghz**2)
            - 0.45
        )
        vertical_reduction = 1.0 / (
            1.0 + math.sqrt(max(0.0, math.sin(theta))) * max(0.0, vertical_term)
        )
        attenuation_db = gamma_r * reduced_slant * vertical_reduction
        factor = float(10.0 ** (-attenuation_db / 10.0))
        return max(0.0, min(1.0, factor))


def _specific_attenuation(
    rain_rate: float,
    frequency_ghz: float,
    elevation_deg: float,
    polarization: LinkPolarization,
) -> float:
    from itur.models import itu838  # type: ignore[import-untyped]

    tau = {
        LinkPolarization.HORIZONTAL: 0.0,
        LinkPolarization.VERTICAL: 90.0,
        LinkPolarization.CIRCULAR: 45.0,
    }[polarization]
    result = itu838.rain_specific_attenuation(
        rain_rate, frequency_ghz, elevation_deg, tau
    )
    return float(result.value)


@lru_cache(maxsize=512)
def _rain_height_km(latitude_deg: float, longitude_deg: float) -> float:
    from itur.models import itu839

    result = itu839.rain_height(latitude_deg, longitude_deg)
    return float(result.value)
