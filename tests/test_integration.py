"""Integration test: events flow adapter -> runtime -> subscriber."""

import pytest
from cep.adapter import HarnessAdapter, HarnessConfig, HealthStatus
from cep.registry import AdapterRegistry
from cep.runtime import ProtocolRuntime
from cep.types import (
    EventType,
    MessageChunkPayload,
    MessageCompletePayload,
    ShellEvent,
    UserMessagePayload,
)


class EchoAdapter(HarnessAdapter):
    """Minimal harness: answers a USER_MESSAGE with a streamed turn."""

    @property
    def id(self) -> str:
        return "echo"

    @property
    def name(self) -> str:
        return "Echo"

    async def connect(self, config: HarnessConfig) -> None:
        self._emit_status("connected")

    async def disconnect(self) -> None:
        self._emit_status("disconnected")

    async def send(self, event: ShellEvent) -> None:
        conversation_id = event.conversation_id
        self._emit_turn_start(conversation_id)
        for payload in (
            MessageChunkPayload(text=event.payload.text, done=False),
            MessageChunkPayload(text="", done=True),
            MessageCompletePayload(text=event.payload.text),
        ):
            self._handler(
                ShellEvent(
                    type=EventType.MESSAGE_CHUNK
                    if isinstance(payload, MessageChunkPayload)
                    else EventType.MESSAGE_COMPLETE,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=payload,
                )
            )
        self._emit_turn_end(conversation_id)

    def on_event(self, handler) -> None:
        self._handler = handler

    async def health_check(self) -> HealthStatus:
        return HealthStatus(connected=True, harness_id=self.id)


@pytest.mark.asyncio
async def test_user_message_through_adapter_send():
    """A turn reaches subscribers in order: TURN_START ... MESSAGE_COMPLETE, TURN_END."""
    runtime = ProtocolRuntime()
    recorded: list[ShellEvent] = []
    runtime.subscribe(recorded.append)

    adapter = EchoAdapter()
    adapter.on_event(runtime.dispatch)

    await adapter.send(
        ShellEvent(
            type=EventType.USER_MESSAGE,
            harness_id="echo",
            conversation_id="conv-send",
            payload=UserMessagePayload(text="find my notes"),
        )
    )

    types = [e.type for e in recorded]
    assert types[0] == EventType.TURN_START
    assert types[-2] == EventType.MESSAGE_COMPLETE
    assert types[-1] == EventType.TURN_END
    assert EventType.MESSAGE_CHUNK in types
    assert all(e.conversation_id == "conv-send" for e in recorded)


@pytest.mark.asyncio
async def test_registry_routes_to_active_adapter():
    """Registry selects the adapter whose events reach the runtime's subscribers."""
    registry = AdapterRegistry()
    runtime = ProtocolRuntime()
    completions: list[str] = []
    runtime.subscribe(
        lambda event: completions.append(event.payload.text),
        event_types={EventType.MESSAGE_COMPLETE},
    )

    adapter = EchoAdapter()
    adapter.on_event(runtime.dispatch)
    registry.register(adapter)
    registry.set_active("echo")

    await registry.active.send(
        ShellEvent(
            type=EventType.USER_MESSAGE,
            harness_id=registry.active.id,
            conversation_id="conv-registry",
            payload=UserMessagePayload(text="Test response"),
        )
    )

    assert completions == ["Test response"]


def test_subscriber_filter_ignores_other_event_types():
    """A type-filtered subscription sees only its own event types."""
    runtime = ProtocolRuntime()
    seen: list[ShellEvent] = []
    runtime.subscribe(seen.append, event_types={EventType.MESSAGE_COMPLETE})

    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="echo",
            conversation_id="c1",
            payload=MessageChunkPayload(text="partial", done=False),
        )
    )
    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_COMPLETE,
            harness_id="echo",
            conversation_id="c1",
            payload=MessageCompletePayload(text="done"),
        )
    )

    assert [e.type for e in seen] == [EventType.MESSAGE_COMPLETE]
