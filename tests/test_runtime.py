"""Tests for protocol runtime event dispatcher."""

import pytest

from cep.runtime import ProtocolRuntime
from cep.types import (
    EventType,
    MessageChunkPayload,
    ShellEvent,
    StatusPayload,
)


@pytest.fixture
def runtime():
    return ProtocolRuntime()


def test_subscribe_and_receive(runtime):
    """Subscriber receives events matching its filter."""
    received = []

    runtime.subscribe(
        lambda event: received.append(event),
        event_types={EventType.MESSAGE_CHUNK},
    )

    event = ShellEvent(
        type=EventType.MESSAGE_CHUNK,
        harness_id="test",
        conversation_id="c1",
        payload=MessageChunkPayload(text="hello", done=False),
    )
    runtime.dispatch(event)

    assert len(received) == 1
    assert received[0].payload.text == "hello"


def test_subscribe_filters_by_type(runtime):
    """Subscriber only receives matching event types."""
    received = []

    runtime.subscribe(
        lambda event: received.append(event),
        event_types={EventType.MESSAGE_CHUNK},
    )

    runtime.dispatch(
        ShellEvent(
            type=EventType.STATUS,
            harness_id="test",
            conversation_id="c1",
            payload=StatusPayload(state="connected"),
        )
    )

    assert len(received) == 0


def test_subscribe_all_events(runtime):
    """Subscriber with no filter receives all events."""
    received = []
    runtime.subscribe(lambda event: received.append(event))

    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="test",
            conversation_id="c1",
            payload=MessageChunkPayload(text="a", done=False),
        )
    )
    runtime.dispatch(
        ShellEvent(
            type=EventType.STATUS,
            harness_id="test",
            conversation_id="c1",
            payload=StatusPayload(state="connected"),
        )
    )

    assert len(received) == 2


def test_unsubscribe(runtime):
    """Unsubscribed handler stops receiving events."""
    received = []
    sub_id = runtime.subscribe(lambda event: received.append(event))

    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="test",
            conversation_id="c1",
            payload=MessageChunkPayload(text="a", done=False),
        )
    )
    assert len(received) == 1

    runtime.unsubscribe(sub_id)

    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="test",
            conversation_id="c1",
            payload=MessageChunkPayload(text="b", done=False),
        )
    )
    assert len(received) == 1  # no new events


def test_multiple_subscribers(runtime):
    """Multiple subscribers each receive matching events."""
    received_a = []
    received_b = []

    runtime.subscribe(
        lambda e: received_a.append(e),
        event_types={EventType.MESSAGE_CHUNK},
    )
    runtime.subscribe(
        lambda e: received_b.append(e),
        event_types={EventType.MESSAGE_CHUNK, EventType.STATUS},
    )

    runtime.dispatch(
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="test",
            conversation_id="c1",
            payload=MessageChunkPayload(text="x", done=False),
        )
    )

    assert len(received_a) == 1
    assert len(received_b) == 1
