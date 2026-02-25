"""Typed event system for Nemo runtime orchestration."""

from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Callable, Protocol


class EventType(str, Enum):
    """All first-class runtime events emitted by the engine."""

    # Run lifecycle
    RUN_STARTED = "run:started"
    RUN_COMPLETED = "run:completed"
    RUN_INTERRUPTED = "run:interrupted"
    RUN_ERROR = "run:error"

    # Frontier lifecycle
    FRONTIER_REFRESHED = "frontier:refreshed"
    FRONTIER_SATURATED = "frontier:saturated"

    # Step lifecycle
    STEP_STARTED = "step:started"
    STEP_PHASE = "step:phase"
    STEP_COMPLETED = "step:completed"
    STEP_ERROR = "step:error"
    STEP_SKIPPED = "step:skipped"

    # Artifact events
    INSIGHT_CREATED = "insight:created"
    EDGE_CREATED = "edge:created"
    CONTRADICTION_DETECTED = "contradiction:detected"
    THREAD_UPDATED = "thread:updated"

    # Internal state
    MEMORY_LOADED = "memory:loaded"
    LEARNING_RECORDED = "learning:recorded"


@dataclass
class NemoEvent:
    """A single immutable event instance emitted by the engine."""

    type: EventType
    run_id: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    step_num: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dictionary."""
        return {
            "type": self.type.value,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            "step_num": self.step_num,
            **self.payload,
        }


class EventSubscriber(Protocol):
    """Subscriber protocol for async event consumers."""

    async def on_event(self, event: NemoEvent) -> Any: ...


@dataclass
class _Subscription:
    handler: EventSubscriber | Callable[[NemoEvent], Any]
    type_filter: set[EventType] | None = None


class EventBus:
    """Central async event dispatcher with optional type filtering."""

    def __init__(self) -> None:
        self._subscriptions: list[_Subscription] = []

    def subscribe(
        self,
        handler: EventSubscriber | Callable[[NemoEvent], Any],
        types: list[EventType] | None = None,
    ) -> Callable[[], None]:
        """Register a subscriber and return an unsubscribe callback."""
        subscription = _Subscription(handler=handler, type_filter=set(types) if types else None)
        self._subscriptions.append(subscription)

        def _unsubscribe() -> None:
            if subscription in self._subscriptions:
                self._subscriptions.remove(subscription)

        return _unsubscribe

    async def emit(self, event: NemoEvent) -> list[Any]:
        """
        Dispatch one event to all matching subscribers.

        Returns subscriber results in execution order so engine code can inspect
        special control responses (for example, a hook requesting a step skip).
        """
        results: list[Any] = []
        for subscription in list(self._subscriptions):
            if subscription.type_filter and event.type not in subscription.type_filter:
                continue
            handler = subscription.handler
            if hasattr(handler, "on_event"):
                value = handler.on_event(event)  # type: ignore[attr-defined]
            else:
                value = handler(event)  # type: ignore[misc]
            if inspect.isawaitable(value):
                value = await value
            results.append(value)
        return results
