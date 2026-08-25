"""Subscription abstraction for a later SSE/WebSocket transport."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from agcc.api.contracts import EventSubscriptionMessage

EventSubscriber = Callable[[EventSubscriptionMessage], None]


class EventSubscriptionHub:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[EventSubscriber]] = defaultdict(list)

    def subscribe(self, scenario_id: str, subscriber: EventSubscriber) -> Callable[[], None]:
        self._subscribers[scenario_id].append(subscriber)

        def unsubscribe() -> None:
            listeners = self._subscribers.get(scenario_id, [])
            if subscriber in listeners:
                listeners.remove(subscriber)

        return unsubscribe

    def publish(self, message: EventSubscriptionMessage) -> None:
        for subscriber in tuple(self._subscribers.get(message.scenario_id, [])):
            subscriber(message)
