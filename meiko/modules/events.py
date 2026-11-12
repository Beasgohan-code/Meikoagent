"""In-process event hub: the bridge between bots/jobs and the webapp.

Every module publishes small JSON events (``music.player``, ``encoder.job``,
``leech.transfer`` …).  Webapp clients subscribe through the API's WebSocket
endpoint; each subscriber gets a bounded queue and slow clients are dropped
rather than blocking the producer.  The hub is process-local on purpose: the
control plane is single-process in this deployment, and a Redis fan-out can be
added behind the same interface later without touching callers.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator


@dataclass
class Event:
    topic: str
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)
    scope: str = "global"

    def to_dict(self) -> dict[str, Any]:
        return {"topic": self.topic, "scope": self.scope, "ts": self.ts, "payload": self.payload}

    @property
    def module(self) -> str:
        return self.topic.split(".", 1)[0]


class Subscription:
    """One webapp connection's view of the hub."""

    def __init__(self, hub: "EventHub", topics: set[str], scope: str | None) -> None:
        self.hub = hub
        self.topics = topics
        self.scope = scope
        self.queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=256)
        self.dropped = 0

    def matches(self, event: Event) -> bool:
        if self.scope and event.scope != self.scope:
            return False
        return not self.topics or event.topic in self.topics or event.module in self.topics

    def offer(self, event: Event) -> None:
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
            # drop the oldest event so the newest state always wins
            try:
                self.queue.get_nowait()
                self.queue.put_nowait(event)
            except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover
                pass

    async def __aiter__(self) -> AsyncIterator[Event]:  # pragma: no cover - streaming
        while True:
            yield await self.queue.get()


class EventHub:
    """Topic-based fan-out with a replay buffer per topic."""

    def __init__(self, *, replay: int = 20) -> None:
        self._subscribers: set[Subscription] = set()
        self._recent: dict[str, deque[Event]] = defaultdict(lambda: deque(maxlen=replay))
        self.counts: dict[str, int] = defaultdict(int)

    def subscribe(self, topics: set[str] | None = None, scope: str | None = None) -> Subscription:
        subscription = Subscription(self, set(topics or ()), scope)
        self._subscribers.add(subscription)
        return subscription

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def publish(self, topic: str, payload: dict[str, Any] | None = None, *, scope: str = "global") -> Event:
        event = Event(topic=topic, payload=payload or {}, scope=scope)
        self._recent[topic].append(event)
        self.counts[topic] += 1
        for subscription in list(self._subscribers):
            if subscription.matches(event):
                subscription.offer(event)
        return event

    def recent(self, topic: str | None = None, *, limit: int = 20) -> list[Event]:
        if topic:
            return list(self._recent.get(topic, ()))[-limit:]
        merged = [event for events in self._recent.values() for event in events]
        merged.sort(key=lambda event: event.ts)
        return merged[-limit:]

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": len(self._subscribers),
            "topics": dict(sorted(self.counts.items())),
            "buffered": sum(len(events) for events in self._recent.values()),
        }


_HUB: EventHub | None = None


def get_event_hub() -> EventHub:
    global _HUB
    if _HUB is None:
        _HUB = EventHub()
    return _HUB


def reset_event_hub() -> None:
    global _HUB
    _HUB = None


__all__ = ["Event", "EventHub", "Subscription", "get_event_hub", "reset_event_hub"]
