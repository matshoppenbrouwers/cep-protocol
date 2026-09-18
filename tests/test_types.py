"""Tests for Common Event Protocol types."""

import json

from cep.types import (
    ApprovalRequestPayload,
    ApprovalResponsePayload,
    ContextUpdatePayload,
    EventType,
    MessageChunkPayload,
    ShellEvent,
    StatusPayload,
    ToolCallEndPayload,
    ToolCallStartPayload,
)


def test_event_type_values():
    """All event types have correct string values."""
    assert EventType.MESSAGE_CHUNK.value == "message_chunk"
    assert EventType.MESSAGE_COMPLETE.value == "message_complete"
    assert EventType.TOOL_CALL_START.value == "tool_call_start"
    assert EventType.TOOL_CALL_END.value == "tool_call_end"
    assert EventType.THINKING_BLOCK.value == "thinking_block"
    assert EventType.APPROVAL_REQUEST.value == "approval_request"
    assert EventType.APPROVAL_RESPONSE.value == "approval_response"
    assert EventType.ERROR.value == "error"
    assert EventType.STATUS.value == "status"
    assert EventType.USER_MESSAGE.value == "user_message"
    assert EventType.CONTEXT_UPDATE.value == "context_update"


def test_shell_event_creation():
    """ShellEvent creates with all required fields."""
    event = ShellEvent(
        type=EventType.MESSAGE_CHUNK,
        harness_id="hermes",
        conversation_id="conv-1",
        payload=MessageChunkPayload(text="Hello", done=False),
    )
    assert event.type == EventType.MESSAGE_CHUNK
    assert event.harness_id == "hermes"
    assert event.conversation_id == "conv-1"
    assert event.id  # auto-generated
    assert event.timestamp > 0


def test_shell_event_to_dict():
    """ShellEvent serializes to JSON-compatible dict."""
    event = ShellEvent(
        type=EventType.TOOL_CALL_START,
        harness_id="openclaw",
        conversation_id="conv-2",
        payload=ToolCallStartPayload(tool_name="web_search", args={"query": "test"}),
    )
    d = event.to_dict()
    assert d["type"] == "tool_call_start"
    assert d["harness_id"] == "openclaw"
    assert d["payload"]["tool_name"] == "web_search"
    assert json.dumps(d)  # must be JSON-serializable


def test_shell_event_from_dict():
    """ShellEvent deserializes from dict."""
    d = {
        "id": "evt-1",
        "type": "message_chunk",
        "harness_id": "hermes",
        "conversation_id": "conv-1",
        "timestamp": 1712345678000,
        "payload": {"text": "Hi", "done": False},
    }
    event = ShellEvent.from_dict(d)
    assert event.type == EventType.MESSAGE_CHUNK
    assert isinstance(event.payload, MessageChunkPayload)
    assert event.payload.text == "Hi"
    assert event.payload.done is False


def test_approval_request_payload():
    """ApprovalRequestPayload includes risk level."""
    payload = ApprovalRequestPayload(
        action="shell_execute",
        description="Run: rm -rf /tmp/test",
        risk="high",
    )
    assert payload.action == "shell_execute"
    assert payload.risk == "high"


def test_context_update_payload():
    """ContextUpdatePayload carries desktop context."""
    payload = ContextUpdatePayload(
        app="Visual Studio Code",
        window="main.py - my-project",
        project="my-project",
    )
    assert payload.app == "Visual Studio Code"
    assert payload.project == "my-project"


def test_status_payload_states():
    """StatusPayload accepts valid connection states."""
    for state in ("connected", "disconnected", "busy"):
        p = StatusPayload(state=state)
        assert p.state == state


def test_approval_request_response_correlation():
    """APPROVAL_RESPONSE.request_id correlates to the APPROVAL_REQUEST event id.

    Convention: ApprovalResponsePayload.request_id == ShellEvent.id of the
    APPROVAL_REQUEST it answers. The response is scoped to the same harness
    and conversation as the request.
    """
    request = ShellEvent(
        type=EventType.APPROVAL_REQUEST,
        harness_id="example",
        conversation_id="conv-1",
        payload=ApprovalRequestPayload(
            action="shell_execute",
            description="Run: rm -rf /tmp/test",
            risk="high",
        ),
    )

    response = ShellEvent(
        type=EventType.APPROVAL_RESPONSE,
        harness_id=request.harness_id,
        conversation_id=request.conversation_id,
        payload=ApprovalResponsePayload(approved=True, request_id=request.id),
    )

    assert response.payload.request_id == request.id
    assert response.harness_id == request.harness_id
    assert response.conversation_id == request.conversation_id


def test_approval_response_correlation_survives_round_trip():
    """The request_id linkage is preserved through serialization."""
    request = ShellEvent(
        type=EventType.APPROVAL_REQUEST,
        harness_id="hermes",
        conversation_id="conv-2",
        payload=ApprovalRequestPayload(action="write_file", description="Save notes"),
    )
    response = ShellEvent(
        type=EventType.APPROVAL_RESPONSE,
        harness_id="hermes",
        conversation_id="conv-2",
        payload=ApprovalResponsePayload(approved=False, request_id=request.id),
    )

    restored = ShellEvent.from_dict(json.loads(json.dumps(response.to_dict())))
    assert isinstance(restored.payload, ApprovalResponsePayload)
    assert restored.payload.request_id == request.id


def test_from_dict_reconstructs_typed_payload_round_trip():
    """from_dict(to_dict(e)) yields a typed payload with attribute access."""
    event = ShellEvent(
        type=EventType.TOOL_CALL_END,
        harness_id="example",
        conversation_id="conv-3",
        payload=ToolCallEndPayload(tool_name="web_search", result="ok", success=True),
    )
    restored = ShellEvent.from_dict(event.to_dict())
    assert isinstance(restored.payload, ToolCallEndPayload)
    assert restored.payload.tool_name == "web_search"
    assert restored.payload.result == "ok"
    assert restored.payload.success is True
    assert restored == event


def test_from_dict_keeps_dict_fallback_for_unknown_payload_shape():
    """Payloads that don't fit the typed dataclass stay as a raw dict."""
    d = {
        "id": "evt-x",
        "type": "message_chunk",
        "harness_id": "hermes",
        "conversation_id": "conv-1",
        "timestamp": 1712345678000,
        "payload": {"unexpected_field": "value"},
    }
    event = ShellEvent.from_dict(d)
    assert event.payload == {"unexpected_field": "value"}


def test_turn_and_cancel_event_types():
    """v2 lifecycle event types exist with correct wire values."""
    assert EventType.TURN_START.value == "turn_start"
    assert EventType.TURN_END.value == "turn_end"
    assert EventType.CANCEL.value == "cancel"


def test_turn_end_round_trip():
    """TurnEndPayload survives to_dict/from_dict with typed payload."""
    from cep.types import TurnEndPayload

    event = ShellEvent(
        type=EventType.TURN_END,
        harness_id="example",
        conversation_id="conv-9",
        payload=TurnEndPayload(reason="cancelled"),
    )
    restored = ShellEvent.from_dict(json.loads(json.dumps(event.to_dict())))
    assert isinstance(restored.payload, TurnEndPayload)
    assert restored.payload.reason == "cancelled"


def test_cancel_round_trip():
    """CancelPayload survives serialization."""
    from cep.types import CancelPayload

    event = ShellEvent(
        type=EventType.CANCEL,
        harness_id="hermes",
        conversation_id="conv-9",
        payload=CancelPayload(),
    )
    restored = ShellEvent.from_dict(event.to_dict())
    assert isinstance(restored.payload, CancelPayload)


def test_approval_request_carries_request_id():
    """ApprovalRequestPayload has an explicit correlation id."""
    payload = ApprovalRequestPayload(
        action="shell", description="rm -rf", risk="high", request_id="req-1"
    )
    assert payload.request_id == "req-1"


def test_error_payload_fatal_flag():
    """ErrorPayload defaults to non-fatal and round-trips fatal=True."""
    from cep.types import ErrorPayload

    assert ErrorPayload(code="x", message="y").fatal is False
    event = ShellEvent(
        type=EventType.ERROR,
        harness_id="h",
        conversation_id="c",
        payload=ErrorPayload(code="boom", message="dead", fatal=True),
    )
    restored = ShellEvent.from_dict(event.to_dict())
    assert restored.payload.fatal is True


def test_seq_is_monotonic():
    """Events created in sequence get strictly increasing seq numbers."""
    events = [
        ShellEvent(
            type=EventType.MESSAGE_CHUNK,
            harness_id="h",
            conversation_id="c",
            payload=MessageChunkPayload(text=str(i)),
        )
        for i in range(5)
    ]
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == 5


def test_seq_round_trip_and_legacy_default():
    """seq survives serialization; missing seq deserializes to 0."""
    event = ShellEvent(
        type=EventType.STATUS,
        harness_id="h",
        conversation_id="c",
        payload=StatusPayload(state="connected"),
    )
    assert ShellEvent.from_dict(event.to_dict()).seq == event.seq

    legacy = event.to_dict()
    del legacy["seq"]
    assert ShellEvent.from_dict(legacy).seq == 0
