"""Level-A ITU-R instantaneous rain attenuation checks."""

from __future__ import annotations

import pytest

from agcc.capacity.attenuation import ItuRainAttenuationModel
from agcc.domain.enums import LinkPolarization


def factor(
    rain: float,
    frequency: float = 9.6,
    polarization: LinkPolarization = LinkPolarization.CIRCULAR,
) -> float:
    return ItuRainAttenuationModel().factor(
        frequency,
        30.0,
        rain,
        latitude_deg=12.97,
        longitude_deg=77.59,
        station_altitude_m=920.0,
        polarization=polarization,
    )


@pytest.fixture(autouse=True)
def fixed_official_rain_height(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agcc.capacity.attenuation._rain_height_km", lambda _lat, _lon: 4.8)


def test_dry_weather_is_neutral() -> None:
    assert factor(0.0) == 1.0


def test_more_rain_reduces_the_factor() -> None:
    assert 0.0 < factor(25.0) < factor(5.0) < 1.0


def test_ka_band_is_more_affected_than_x_band() -> None:
    assert factor(20.0, 30.0) < factor(20.0, 9.6)


def test_required_polarization_changes_p838_result() -> None:
    horizontal = factor(20.0, polarization=LinkPolarization.HORIZONTAL)
    vertical = factor(20.0, polarization=LinkPolarization.VERTICAL)
    circular = factor(20.0, polarization=LinkPolarization.CIRCULAR)
    assert len({round(horizontal, 8), round(vertical, 8), round(circular, 8)}) == 3
