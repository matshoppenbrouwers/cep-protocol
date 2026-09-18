"""Protocol runtime — event dispatcher with pub/sub.

The runtime sits between adapters and the shell. Adapters emit events
into the runtime; shell components subscribe to events they care about.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable

from cep.types import EventType, ShellEvent

logger = logging.getLogger(__name__)

EventHandler = Callable[[ShellEvent], None]


class _Subscription:
    __slots__ = ("id", "handler", "event_types")

    def __init__(
        self,
        handler: EventHandler,
        event_types: set[EventType] | None,
    ) -> None:
        self.id = f"sub-{uuid.uuid4().hex[:8]}"
        self.handler = handler
        self.event_types = event_types  # None = all events


class ProtocolRuntime:
    """Central event dispatcher for the Common Event Protocol.

    Usage:
        runtime = ProtocolRuntime()
        sub_id = runtime.subscribe(handler, event_types={EventType.MESSAGE_CHUNK})
        runtime.dispatch(event)  # handler called if type matches
        runtime.unsubscribe(sub_id)
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, _Subscription] = {}

    def subscribe(
        self,
        handler: EventHandler,
        event_types: set[EventType] | None = None,
    ) -> str:
        """Register a handler. Returns subscription ID for unsubscribe."""
        sub = _Subscription(handler, event_types)
        self._subscriptions[sub.id] = sub
        return sub.id

    def unsubscribe(self, subscription_id: str) -> None:
        """Remove a subscription by ID."""
        self._subscriptions.pop(subscription_id, None)

    def dispatch(self, event: ShellEvent) -> None:
        """Send an event to all matching subscribers."""
        for sub in list(self._subscriptions.values()):
            if sub.event_types is None or event.type in sub.event_types:
                try:
                    sub.handler(event)
                except Exception:
                    logger.exception(
                        "Subscriber %s failed handling %s event",
                        sub.id,
                        event.type.value,
                    )
