"""Simulation clock, execution engine, and event store (Task 12).

Architecture
------------
ClockSpeed          — simulation speed multiplier enum.
SimulationClock     — tracks simulation time; advances by wall-clock delta × speed.
SimulationEvent     — append-only event record (extends domain SimulationEvent).
SessionEventStore   — in-memory, append-only event log.
SimulationEngine    — advances simulation time tick by tick, executes contacts,
                      produces events, maintains predicted completion.

Clock model
-----------
SimulationClock is pure-functional: it records a (sim_time, wall_time) anchor
and a speed.  Calling advance(wall_now) returns a new clock with updated anchor.
No real sleep or asyncio is used; callers control advancement.

Tick model
----------
Transfer tick = 1 simulated second.
At each tick during a contact:
    elevation = interpolated from pass geometry
    elevation_factor = sin(elevation_rad)
    realized_rate_mbps = base_rate_mbps * protocol_efficiency * elevation_factor
                         * anomaly_multiplier
    delivered_mb_tick = min(remaining_fragment_mb, realized_rate_mbps * 1s / 8)

Shortfall forecast
------------------
After every tick:
    predicted_final = delivered + remaining capacity in future approved contacts
    predicted_shortfall = max(0, required - predicted_final)
SHORTFALL_PREDICTED fired only on: zero → positive transition, or increase ≥ 1 MB.

Event ordering
--------------
All events carry a monotonically increasing sequence_number (0-based within a run).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from enum import Enum
from typing import Callable

from agcc.dispatch import (
    DispatchPlan,
    DispatchRedistributor,
    Fragment,
    FragmentState,
    ResidualShortfall,
)
from agcc.domain.enums import EventType
from agcc.planner import ContactPlan, PlannedContact

# ---------------------------------------------------------------------------
# Clock speed
# ---------------------------------------------------------------------------

TICK_S: float = 1.0  # 1 simulated second per tick


class ClockSpeed(str, Enum):
    PAUSED = "paused"
    X1 = "1x"
    X5 = "5x"
    X10 = "10x"
    X20 = "20x"
    X60 = "60x"
    X100 = "100x"
    X1000 = "1000x"


_SPEED_MAP: dict[ClockSpeed, float] = {
    ClockSpeed.PAUSED: 0.0,
    ClockSpeed.X1: 1.0,
    ClockSpeed.X5: 5.0,
    ClockSpeed.X10: 10.0,
    ClockSpeed.X20: 20.0,
    ClockSpeed.X60: 60.0,
    ClockSpeed.X100: 100.0,
    ClockSpeed.X1000: 1000.0,
}


# ---------------------------------------------------------------------------
# SimulationClock
# ---------------------------------------------------------------------------


class SimulationClock:
    """Tracks simulation time relative to wall-clock time.

    Pure functional: all mutating operations return a new SimulationClock.
    Wall-clock time advances sim time; simulation time is authoritative.
    """

    __slots__ = ("_sim_time", "_anchor_wall", "_speed")

    def __init__(
        self,
        sim_time: datetime,
        anchor_wall: datetime,
        speed: ClockSpeed = ClockSpeed.PAUSED,
    ) -> None:
        self._sim_time = sim_time
        self._anchor_wall = anchor_wall
        self._speed = speed

    @property
    def sim_time(self) -> datetime:
        return self._sim_time

    @property
    def speed(self) -> ClockSpeed:
        return self._speed

    @property
    def is_paused(self) -> bool:
        return self._speed == ClockSpeed.PAUSED

    def set_speed(self, speed: ClockSpeed, wall_now: datetime) -> "SimulationClock":
        """Return a new clock with the given speed, anchored at wall_now."""
        # First advance to wall_now with current speed, then re-anchor.
        advanced = self.advance(wall_now)
        return SimulationClock(advanced._sim_time, wall_now, speed)

    def advance(self, wall_now: datetime) -> "SimulationClock":
        """Return a new clock advanced to wall_now."""
        if self._speed == ClockSpeed.PAUSED:
            return SimulationClock(self._sim_time, wall_now, self._speed)
        multiplier = _SPEED_MAP[self._speed]
        wall_delta_s = (wall_now - self._anchor_wall).total_seconds()
        sim_delta_s = wall_delta_s * multiplier
        new_sim = self._sim_time + timedelta(seconds=sim_delta_s)
        return SimulationClock(new_sim, wall_now, self._speed)

    def ticks_elapsed(self, from_sim: datetime) -> int:
        """Number of whole 1-second ticks between from_sim and self.sim_time."""
        delta = (self._sim_time - from_sim).total_seconds()
        return max(0, int(delta))

    def __repr__(self) -> str:
        return f"SimulationClock(sim_time={self._sim_time.isoformat()}, speed={self._speed.value})"


# ---------------------------------------------------------------------------
# SimulationEvent
# ---------------------------------------------------------------------------


def _event_id(seq: int) -> str:
    digest = hashlib.sha256(f"event|{seq}".encode()).hexdigest()[:16]
    return f"event_{digest}"


class SimulationEvent:
    """An append-only event record emitted by the simulation engine.

    Not a Pydantic model: the engine emits many events and keeps them in memory;
    a lightweight dataclass-like object is sufficient.  Serialisation for the
    API layer is handled at the boundary.
    """

    __slots__ = (
        "event_id",
        "sequence_number",
        "event_type",
        "sim_time",
        "contact_id",
        "fragment_id",
        "delivered_volume_mb",
        "rate_mbps",
        "predicted_shortfall_mb",
        "description",
    )

    def __init__(
        self,
        *,
        sequence_number: int,
        event_type: EventType,
        sim_time: datetime,
        contact_id: str | None = None,
        fragment_id: str | None = None,
        delivered_volume_mb: float | None = None,
        rate_mbps: float | None = None,
        predicted_shortfall_mb: float | None = None,
        description: str = "",
    ) -> None:
        self.event_id = _event_id(sequence_number)
        self.sequence_number = sequence_number
        self.event_type = event_type
        self.sim_time = sim_time
        self.contact_id = contact_id
        self.fragment_id = fragment_id
        self.delivered_volume_mb = delivered_volume_mb
        self.rate_mbps = rate_mbps
        self.predicted_shortfall_mb = predicted_shortfall_mb
        self.description = description

    def __repr__(self) -> str:
        return (
            f"SimulationEvent(seq={self.sequence_number}, "
            f"type={self.event_type.value}, sim_time={self.sim_time.isoformat()})"
        )


# ---------------------------------------------------------------------------
# SessionEventStore
# ---------------------------------------------------------------------------


class SessionEventStore:
    """In-memory append-only event log.  No filesystem or database writes."""

    def __init__(self) -> None:
        self._events: list[SimulationEvent] = []

    def append(self, event: SimulationEvent) -> None:
        """Append an event.  Raises ValueError if sequence_number is not monotone."""
        if self._events:
            last_seq = self._events[-1].sequence_number
            if event.sequence_number <= last_seq:
                raise ValueError(f"Non-monotone sequence: {event.sequence_number} <= {last_seq}")
        self._events.append(event)

    def all_events(self) -> list[SimulationEvent]:
        return list(self._events)

    def events_of_type(self, event_type: EventType) -> list[SimulationEvent]:
        return [e for e in self._events if e.event_type == event_type]

    def __len__(self) -> int:
        return len(self._events)


# ---------------------------------------------------------------------------
# SimulationEngine
# ---------------------------------------------------------------------------


class SimulationEngine:
    """Executes a ContactPlan tick by tick in simulation time.

    Parameters
    ----------
    plan:
        A feasible ContactPlan.
    dispatch:
        The DispatchPlan produced from the same plan.
    required_volume_mb:
        Mission data target.
    deadline:
        Mission hard deadline.
    base_rate_mbps:
        Satellite+station minimum rate (from capacity estimation).
    protocol_efficiency:
        Satellite link protocol efficiency factor.
    store:
        SessionEventStore to append events to.

    Usage
    -----
    engine = SimulationEngine(...)
    engine.start(sim_start_time)
    while not engine.is_finished:
        engine.tick(sim_now)        # advance by 1 simulated second
    """

    def __init__(
        self,
        *,
        plan: ContactPlan,
        dispatch: DispatchPlan,
        required_volume_mb: float,
        deadline: datetime,
        base_rate_mbps: float,
        protocol_efficiency: float,
        store: SessionEventStore,
        anomaly_multiplier: float = 1.0,
        rate_provider: Callable[[PlannedContact, datetime], float] | None = None,
        frozen_capacity_policy: bool = False,
        initial_delivered_mb: float = 0.0,
    ) -> None:
        self._plan = plan
        self._dispatch = dispatch
        self._required_volume_mb = required_volume_mb
        self._deadline = deadline
        self._base_rate_mbps = base_rate_mbps
        self._protocol_efficiency = protocol_efficiency
        self._store = store
        self._anomaly_multiplier = anomaly_multiplier
        self._rate_provider = rate_provider
        self._frozen_capacity_policy = frozen_capacity_policy

        # Mutable state
        existing_events = store.all_events()
        self._seq = (
            existing_events[-1].sequence_number + 1
            if existing_events
            else 0
        )
        self._delivered_mb = initial_delivered_mb
        self._sim_time: datetime | None = None
        self._started = False
        self._finished = False

        # Contact execution state
        self._contacts_sorted: list[PlannedContact] = sorted(
            plan.contacts, key=lambda c: c.start_at
        )
        self._contact_index = 0  # index into _contacts_sorted
        self._active_contact: PlannedContact | None = None
        self._active_contact_delivered_mb: float = 0.0

        # Fragment queue state: per-contact ordered fragment list
        self._contact_fragment_queues: dict[str, list[Fragment]] = {}
        self._fragment_remaining_mb: dict[str, float] = {}
        self._build_fragment_queues()

        # Predicted shortfall tracking (for transition detection)
        self._last_predicted_shortfall_mb: float = 0.0
        self._last_residual_shortfall: ResidualShortfall | None = None
        self._mission_ended = False

    @property
    def is_finished(self) -> bool:
        return self._finished

    @property
    def delivered_mb(self) -> float:
        return self._delivered_mb

    @property
    def sim_time(self) -> datetime | None:
        return self._sim_time

    @property
    def dispatch(self) -> DispatchPlan:
        return self._dispatch

    @property
    def last_residual_shortfall(self) -> ResidualShortfall | None:
        return self._last_residual_shortfall

    @property
    def active_contact(self) -> PlannedContact | None:
        """Contact currently executing, exposed read-only for API presentation."""
        return self._active_contact

    @property
    def predicted_shortfall_mb(self) -> float:
        return self._last_predicted_shortfall_mb

    @property
    def anomaly_multiplier(self) -> float:
        return self._anomaly_multiplier

    def current_rate_mbps(self) -> float:
        if self._active_contact is None or self._sim_time is None:
            return 0.0
        return self._rate_at(self._active_contact, self._sim_time) * self._anomaly_multiplier

    @property
    def active_contact_delivered_mb(self) -> float:
        """Volume actually delivered during the currently active contact."""
        return self._active_contact_delivered_mb

    def set_anomaly_multiplier(self, multiplier: float) -> None:
        """Apply a validated execution-rate multiplier after modeled capacity."""
        if not 0.0 <= multiplier <= 1.0:
            raise ValueError("anomaly multiplier must be in [0, 1]")
        self._anomaly_multiplier = multiplier

    def record_external_event(
        self, event_type: EventType, description: str, *, sim_time: datetime | None = None
    ) -> None:
        """Append a domain event at the authoritative internal simulation time."""
        self._emit(
            event_type,
            sim_time or self._sim_time or self._deadline,
            description=description,
        )

    def start(self, sim_start: datetime) -> None:
        """Start the simulation at sim_start.  Must be called before tick()."""
        if self._started:
            raise RuntimeError("SimulationEngine already started")
        self._started = True
        self._sim_time = sim_start
        self._emit(EventType.SIMULATION_STARTED, sim_start)

    def tick(self, sim_now: datetime) -> None:
        """Advance simulation to sim_now (must be >= previous sim_time)."""
        if not self._started:
            raise RuntimeError("Call start() before tick()")
        if self._finished:
            return
        if self._sim_time is not None and sim_now < self._sim_time:
            raise ValueError("sim_now must be >= current sim_time")

        # The deadline is a hard clock boundary.  A large realtime step must
        # never leave either the engine or the globe visually beyond it.
        sim_now = min(sim_now, self._deadline)
        self._sim_time = sim_now

        # Advance contact state machine
        self._advance_contacts(sim_now)

        # Forecast shortfall
        self._update_shortfall_forecast(sim_now)

        if sim_now == self._deadline and not self._mission_ended:
            if self._delivered_mb < self._required_volume_mb:
                self._emit(
                    EventType.MISSION_DEADLINE_MISSED,
                    self._deadline,
                    description=(
                        f"delivered={self._delivered_mb:.4f} < "
                        f"required={self._required_volume_mb:.4f}; resolution approval required"
                    ),
                )
                self._mission_ended = True
                self._finished = True

    def pause(self, sim_now: datetime) -> None:
        """Emit SIMULATION_PAUSED event."""
        self._emit(EventType.SIMULATION_PAUSED, sim_now)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_fragment_queues(self) -> None:
        """Build per-contact ordered fragment queues from the dispatch plan."""
        self._contact_fragment_queues.clear()
        self._fragment_remaining_mb.clear()
        frag_map = {f.fragment_id: f for f in self._dispatch.fragments}
        for alloc in self._dispatch.allocations:
            queue = [
                frag_map[fid]
                for fid in alloc.fragment_ids
                if fid in frag_map and frag_map[fid].state != FragmentState.DELIVERED
            ]
            self._contact_fragment_queues[alloc.contact_id] = queue
            for fragment in queue:
                self._fragment_remaining_mb[fragment.fragment_id] = fragment.volume_mb

    def _emit(
        self,
        event_type: EventType,
        sim_time: datetime,
        *,
        contact_id: str | None = None,
        fragment_id: str | None = None,
        delivered_volume_mb: float | None = None,
        rate_mbps: float | None = None,
        predicted_shortfall_mb: float | None = None,
        description: str = "",
    ) -> SimulationEvent:
        ev = SimulationEvent(
            sequence_number=self._seq,
            event_type=event_type,
            sim_time=sim_time,
            contact_id=contact_id,
            fragment_id=fragment_id,
            delivered_volume_mb=delivered_volume_mb,
            rate_mbps=rate_mbps,
            predicted_shortfall_mb=predicted_shortfall_mb,
            description=description,
        )
        self._store.append(ev)
        self._seq += 1
        return ev

    def _advance_contacts(self, sim_now: datetime) -> None:
        """Start/end contacts and execute 1-second ticks within active contacts."""
        # Close active contact if past its end
        if self._active_contact is not None:
            if sim_now >= self._active_contact.end_at:
                self._close_active_contact(sim_now)

        # Open next contact if time has come
        while self._active_contact is None and self._contact_index < len(self._contacts_sorted):
            nxt = self._contacts_sorted[self._contact_index]
            if sim_now >= nxt.start_at:
                self._open_contact(nxt, sim_now)
                self._contact_index += 1
            else:
                break

        # Execute tick within active contact
        if self._active_contact is not None:
            self._execute_tick(self._active_contact, sim_now)

    def _open_contact(self, contact: PlannedContact, sim_now: datetime) -> None:
        self._active_contact = contact
        self._active_contact_delivered_mb = 0.0
        self._emit(
            EventType.CONTACT_STARTED,
            sim_now,
            contact_id=contact.contact_id,
            description=f"station={contact.station_id}",
        )
        # Emit FRAGMENT_STARTED for first fragment in this contact's queue
        queue = self._contact_fragment_queues.get(contact.contact_id, [])
        if queue:
            first = queue[0]
            self._emit(
                EventType.FRAGMENT_STARTED,
                sim_now,
                contact_id=contact.contact_id,
                fragment_id=first.fragment_id,
            )

    def _close_active_contact(self, sim_now: datetime) -> None:
        contact = self._active_contact
        assert contact is not None
        # A Prediction run executes the already-integrated frozen ledger.  Close
        # the contact on the same authoritative pass boundary used by planning,
        # rather than allowing browser polling cadence to discard the fractional
        # tail of its allocation.  Anomaly branches deliberately bypass this
        # reconciliation so their modeled degradation remains observable.
        if self._frozen_capacity_policy and self._anomaly_multiplier == 1.0:
            self._settle_frozen_contact(contact, sim_now)
        self._emit(
            EventType.CONTACT_ENDED,
            sim_now,
            contact_id=contact.contact_id,
            delivered_volume_mb=self._active_contact_delivered_mb,
        )
        allocations_before = {
            item.contact_id: item.assigned_volume_mb
            for item in self._dispatch.allocations
        }
        self._dispatch, self._last_residual_shortfall = DispatchRedistributor().record_delivery(
            self._dispatch,
            contact.contact_id,
            self._active_contact_delivered_mb,
            sim_now,
            self._contacts_sorted,
        )
        for allocation in self._dispatch.allocations:
            if allocation.contact_id == contact.contact_id:
                continue
            delta = allocation.assigned_volume_mb - allocations_before.get(
                allocation.contact_id, 0.0
            )
            if delta > 1e-9:
                self._emit(
                    EventType.DATA_REROUTED,
                    sim_now,
                    contact_id=allocation.contact_id,
                    delivered_volume_mb=delta,
                    description=(
                        f"source_contact={contact.contact_id}; "
                        f"destination_contact={allocation.contact_id}; "
                        f"rerouted_volume_mb={delta:.6f}"
                    ),
                )
        self._build_fragment_queues()
        self._active_contact = None
        self._active_contact_delivered_mb = 0.0

        # Check mission completion
        if not self._mission_ended and self._delivered_mb >= self._required_volume_mb - 1e-9:
            self._emit(
                EventType.MISSION_COMPLETED,
                sim_now,
                delivered_volume_mb=self._delivered_mb,
                description=f"required={self._required_volume_mb:.4f}",
            )
            self._mission_ended = True
            self._finished = True

    def _settle_frozen_contact(self, contact: PlannedContact, sim_now: datetime) -> None:
        """Consume the exact frozen allocation at its authoritative end boundary."""
        remaining = max(0.0, contact.allocated_volume_mb - self._active_contact_delivered_mb)
        queue = self._contact_fragment_queues.get(contact.contact_id, [])
        while remaining > 1e-9 and queue:
            fragment = queue.pop(0)
            fragment_remaining = self._fragment_remaining_mb.pop(
                fragment.fragment_id, fragment.volume_mb
            )
            delivered = min(fragment_remaining, remaining)
            self._delivered_mb += delivered
            self._active_contact_delivered_mb += delivered
            remaining -= delivered
            event_type = (
                EventType.FRAGMENT_DELIVERED
                if delivered >= fragment_remaining - 1e-9
                else EventType.FRAGMENT_PARTIAL
            )
            self._emit(
                event_type,
                sim_now,
                contact_id=contact.contact_id,
                fragment_id=fragment.fragment_id,
                delivered_volume_mb=delivered,
                description="frozen ledger reconciled at planned contact boundary",
            )
            if delivered < fragment_remaining - 1e-9:
                self._fragment_remaining_mb[fragment.fragment_id] = (
                    fragment_remaining - delivered
                )
                queue.insert(0, fragment)

    def _execute_tick(self, contact: PlannedContact, sim_now: datetime) -> None:
        """Execute one 1-second simulation tick within an active contact."""
        queue = self._contact_fragment_queues.get(contact.contact_id, [])
        if not queue:
            return

        # Compute rate at this tick via elevation interpolation
        # We use the contact's pass geometry via start/peak/end times.
        # Build a minimal pass proxy using the contact's timing.
        rate_mbps = self._rate_at(contact, sim_now)

        self._emit(
            EventType.RATE_UPDATED,
            sim_now,
            contact_id=contact.contact_id,
            rate_mbps=rate_mbps,
        )

        realized = rate_mbps * self._anomaly_multiplier
        capacity_tick = realized * TICK_S / 8.0

        # Apply capacity to fragment queue
        remaining_tick = capacity_tick
        while remaining_tick > 1e-9 and queue:
            frag = queue[0]
            frag_remaining = self._fragment_remaining_mb[frag.fragment_id]

            tick_delivered = min(frag_remaining, remaining_tick)
            remaining_tick -= tick_delivered
            self._delivered_mb += tick_delivered
            self._active_contact_delivered_mb += tick_delivered

            if tick_delivered >= frag_remaining - 1e-9:
                # Fragment fully delivered
                queue.pop(0)
                self._fragment_remaining_mb.pop(frag.fragment_id, None)
                self._emit(
                    EventType.FRAGMENT_DELIVERED,
                    sim_now,
                    contact_id=contact.contact_id,
                    fragment_id=frag.fragment_id,
                    delivered_volume_mb=frag_remaining,
                )
                # If there's another fragment, start it
                if queue:
                    self._emit(
                        EventType.FRAGMENT_STARTED,
                        sim_now,
                        contact_id=contact.contact_id,
                        fragment_id=queue[0].fragment_id,
                    )
            else:
                # Partial delivery — update fragment in place (mutable volume tracking)
                # We track remaining on a per-fragment basis via a mutable dict
                self._emit(
                    EventType.FRAGMENT_PARTIAL,
                    sim_now,
                    contact_id=contact.contact_id,
                    fragment_id=frag.fragment_id,
                    delivered_volume_mb=tick_delivered,
                )
                # Update the fragment's remaining volume for next tick
                # Since Fragment is immutable, use a local mutable volume tracker
                self._update_fragment_remaining(contact.contact_id, frag, tick_delivered)
                break

        # Check mission completion mid-contact
        if not self._mission_ended and self._delivered_mb >= self._required_volume_mb - 1e-9:
            self._close_active_contact(sim_now)

    def _rate_at(self, contact: PlannedContact, sim_now: datetime) -> float:
        """Estimate effective transfer rate (Mbit/s) at sim_now within a contact.

        Uses linear elevation interpolation over the contact window, then
        applies sin(elevation) as the elevation factor.
        """
        if self._rate_provider is not None:
            return max(0.0, self._rate_provider(contact, sim_now))
        return max(0.0, self._base_rate_mbps * self._protocol_efficiency)

        start_ts = contact.start_at.timestamp()
        end_ts = contact.end_at.timestamp()
        sim_ts = sim_now.timestamp()

        # Interpolate elevation linearly: assume peak at midpoint
        mid_ts = (start_ts + end_ts) / 2.0

        total = end_ts - start_ts
        if total <= 0.0:
            return 0.0

        # Simple triangular profile: min at edges, max at midpoint
        # We don't have pass-level geometry here; use contact's allocated_volume_mb
        # as a proxy for the average rate, distributing linearly around mid.
        # Per spec: "query the capacity model with current orbit geometry."
        # Since we don't have CandidatePass directly, we use the average rate
        # derived from the contact's allocated volume and duration.
        avg_rate = (contact.allocated_volume_mb * 8.0) / contact.duration_s
        # Scale by a triangular elevation factor (1.0 at peak, min at edges)
        if sim_ts <= mid_ts:
            frac = (sim_ts - start_ts) / (mid_ts - start_ts) if mid_ts > start_ts else 0.0
        else:
            frac = (end_ts - sim_ts) / (end_ts - mid_ts) if end_ts > mid_ts else 0.0
        frac = max(0.0, min(1.0, frac))
        # Scale: min rate = 0.5 × avg, max = 1.5 × avg
        rate = avg_rate * (0.5 + frac)
        return max(0.0, rate)

    def _update_fragment_remaining(
        self,
        contact_id: str,
        frag: Fragment,
        delivered_this_tick: float,
    ) -> None:
        """Replace the head fragment in the queue with a reduced-volume sentinel.

        Since Fragment is immutable, we replace it with a lightweight mutable
        wrapper by tracking remaining volume in a separate dict.
        """
        queue = self._contact_fragment_queues[contact_id]
        if queue and queue[0].fragment_id == frag.fragment_id:
            remaining = self._fragment_remaining_mb[frag.fragment_id] - delivered_this_tick
            self._fragment_remaining_mb[frag.fragment_id] = max(0.0, remaining)

    def _update_shortfall_forecast(self, sim_now: datetime) -> None:
        """Compute predicted shortfall and emit SHORTFALL_PREDICTED if triggered."""
        # Capacity remaining in future (not-yet-started + active) contacts
        future_capacity = self._remaining_planned_capacity(sim_now)
        predicted_final = self._delivered_mb + future_capacity
        shortfall = max(0.0, self._required_volume_mb - predicted_final)

        prev = self._last_predicted_shortfall_mb
        # Emit on: zero → positive, or increase ≥ 1 MB
        if shortfall > 1e-9 and (prev < 1e-9 or shortfall - prev >= 1.0):
            self._emit(
                EventType.SHORTFALL_PREDICTED,
                sim_now,
                predicted_shortfall_mb=shortfall,
                description=f"predicted_final={predicted_final:.4f}",
            )
        self._last_predicted_shortfall_mb = shortfall

    def _remaining_planned_capacity(self, sim_now: datetime) -> float:
        """Sum of allocated_volume_mb for all future+active contacts."""
        if self._frozen_capacity_policy:
            # Prediction consumes the exact frozen dispatch ledger. Assigned
            # fragments are mission volume, not a second physical-capacity estimate.
            viable_contacts = {
                item.contact_id
                for item in self._contacts_sorted
                if item.end_at > sim_now
            }
            return sum(
                fragment.volume_mb
                for fragment in self._dispatch.fragments
                if fragment.assigned_contact_id in viable_contacts
                and fragment.state in {FragmentState.ASSIGNED, FragmentState.TRANSMITTING}
            )
        total = 0.0
        allocations = {item.contact_id: item for item in self._dispatch.allocations}
        for c in self._contacts_sorted:
            if c.end_at > sim_now:
                allocation = allocations.get(c.contact_id)
                approved_capacity = (
                    allocation.planned_volume_mb
                    if allocation is not None
                    else c.allocated_volume_mb
                )
                # Partially or fully in the future
                if c.start_at <= sim_now:
                    # Active contact: count only the undelivered portion
                    # Use planned capacity minus what we've already delivered in this contact
                    total += max(0.0, approved_capacity - self._active_contact_delivered_mb)
                else:
                    # Future contact
                    total += approved_capacity
        return total
