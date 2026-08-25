"""Pass-midpoint snapshot selection and staleness logic.

Alignment rule (spec):
  For a pass, choose the snapshot covering the pass midpoint.
  If none covers it, use the closest snapshot only when its age is within
  max_staleness_s; otherwise mark environment unavailable.
  Never silently use neutral weather.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from agcc.domain.enums import SourceQuality
from agcc.domain.environment import WeatherSnapshot
from agcc.domain.planning import CandidatePass


class AlignmentResult:
    """Result of aligning a weather snapshot to a pass."""

    __slots__ = ("snapshot", "quality", "reason")

    def __init__(
        self,
        snapshot: WeatherSnapshot | None,
        quality: SourceQuality,
        reason: str,
    ) -> None:
        self.snapshot = snapshot
        self.quality = quality
        self.reason = reason

    @property
    def available(self) -> bool:
        return self.quality != SourceQuality.UNAVAILABLE


def align_to_pass(
    pass_: CandidatePass,
    snapshots: list[WeatherSnapshot],
    max_staleness_s: float = 3600.0,
) -> AlignmentResult:
    """Select the best WeatherSnapshot for a pass.

    Algorithm:
      1. Compute pass midpoint = start_at + duration_s / 2.
      2. Search for a snapshot whose valid_from <= midpoint < valid_until.
         If found → return it with its own source_quality.
      3. If not found, find the closest snapshot by |midpoint − snapshot midpoint|.
         If closest snapshot age (|midpoint − observed_at|) <= max_staleness_s
         → return it marked STALE.
      4. Otherwise → return UNAVAILABLE (no snapshot).

    "Snapshot midpoint" used for proximity is midpoint of [valid_from, valid_until).
    "Age" is the absolute distance from the pass midpoint to observed_at.
    """
    if not snapshots:
        return AlignmentResult(None, SourceQuality.UNAVAILABLE, "no snapshots available")

    midpoint = pass_.start_at + timedelta(seconds=pass_.duration_s / 2.0)

    # Step 2 — exact coverage
    for snap in snapshots:
        if snap.covers(midpoint):
            return AlignmentResult(snap, snap.source_quality, "exact coverage")

    # Step 3 — closest by snapshot midpoint distance
    def _snap_mid(s: WeatherSnapshot) -> datetime:
        delta = (s.valid_until - s.valid_from) / 2
        return s.valid_from + delta

    closest = min(snapshots, key=lambda s: abs((_snap_mid(s) - midpoint).total_seconds()))
    age_s = abs((closest.observed_at - midpoint).total_seconds())

    if age_s <= max_staleness_s:
        return AlignmentResult(closest, SourceQuality.STALE, f"closest snapshot, age {age_s:.0f}s")

    return AlignmentResult(
        None,
        SourceQuality.UNAVAILABLE,
        f"closest snapshot age {age_s:.0f}s exceeds max_staleness_s {max_staleness_s:.0f}s",
    )
