"""Station filtering: apply StationSelection to a StationCatalog.

Filtering order (fixed by spec):
  1. enabled — discard disabled stations.
  2. complete planner data — keep only planner_eligible stations.
  3. explicit exclusions — remove stations in excluded_station_ids.
  4. allow-all or authorized set:
       - if allow_all_eligible: keep all remaining.
       - else: keep stations whose station_id is in authorized_station_ids
               OR whose provider_id is in authorized_provider_ids.

Band-compatibility filtering happens later (at pass-engine time) and is NOT
performed here, as specified.
"""

from __future__ import annotations

from agcc.domain.stations import GroundStation, StationCatalog, StationSelection


def filter_stations(
    catalog: StationCatalog,
    selection: StationSelection,
) -> list[GroundStation]:
    """Return stations from catalog that pass the selection filter.

    The returned list is in deterministic station_id order (catalog is
    already sorted; this preserves that order).
    """
    result: list[GroundStation] = []

    for station in catalog.stations:
        # Step 1 — enabled
        if not station.enabled:
            continue

        # Step 2 — complete planner data
        if not station.planner_eligible:
            continue

        # Step 3 — explicit exclusions
        if station.station_id in selection.excluded_station_ids:
            continue

        # Step 4 — allow-all or authorized set
        if selection.allow_all_eligible:
            result.append(station)
        else:
            if (
                station.station_id in selection.authorized_station_ids
                or station.provider_id in selection.authorized_provider_ids
            ):
                result.append(station)

    return result
