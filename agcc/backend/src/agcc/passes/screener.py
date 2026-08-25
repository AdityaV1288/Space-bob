"""StationScreener protocol and default MVP implementation."""

from __future__ import annotations

from typing import Protocol

from agcc.domain.orbit import CustomCircularOrbit
from agcc.domain.stations import GroundStation


class StationScreener(Protocol):
    """Abstract interface for pre-screening stations before pass computation."""

    def screen(
        self,
        orbit: CustomCircularOrbit,
        stations: list[GroundStation],
    ) -> list[GroundStation]:
        """Return the subset of stations worth computing passes for."""
        ...


class DefaultStationScreener:
    """MVP implementation: returns every authorized station unchanged.

    No geometric pre-screening is applied.  All stations proceed to the
    full pass-window computation.  A more optimised screener may be
    substituted later without changing the engine interface.
    """

    def screen(
        self,
        orbit: CustomCircularOrbit,
        stations: list[GroundStation],
    ) -> list[GroundStation]:
        return list(stations)
