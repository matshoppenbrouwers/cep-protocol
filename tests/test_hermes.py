"""Tests for Hermes SSE/HTTP adapter."""

import asyncio

import pytest

from cep.adapter import HarnessConfig
from cep.adapters.hermes import HermesAdapter, _parse_sse_chunk
from cep.adapter import ConfigurationError
from cep.types import CancelPayload, EventType, ShellEvent, UserMessagePayload


def test_parse_sse_content_chunk():
    """Parse a streaming content delta from Hermes SSE."""
    chunk = {"choices": [{"delta": {"content": "Hello world"}, "finish_reason": None}]}
    events = _parse_sse_chunk(chunk, harness_id="hermes", conversation_id="c1")
    assert len(events) == 1
    assert events[0].type == EventType.MESSAGE_CHUNK
    assert events[0].payload.text == "Hello world"
    assert events[0].payload.done is False


def test_parse_sse_done_chunk():
    """Parse a finish_reason=stop chunk."""
    chunk = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
    events = _parse_sse_chunk(chunk, harness_id="hermes", conversation_id="c1")
    assert len(events) == 1
    assert events[0].type == EventType.MESSAGE_CHUNK
    assert events[0].payload.done is True


def test_parse_sse_content_and_stop_chunk():
    """A chunk carrying both content and finish_reason=stop still emits done=True."""
    chunk = {"choices": [{"delta": {"content": "final"}, "finish_reason": "stop"}]}
    events = _parse_sse_chunk(chunk, harness_id="hermes", conversation_id="c1")
    assert len(events) == 2
    assert events[0].type == EventType.MESSAGE_CHUNK
    assert events[0].payload.text == "final"
    assert events[0].payload.done is False
    assert events[1].type == EventType.MESSAGE_CHUNK
    assert events[1].payload.done is True


def test_parse_sse_tool_call_buffers_args():
    """A non-risky tool call buffers streamed args and emits start+end on flush."""
    buffers: dict = {}
    name_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_123",
                            "function": {"name": "web_search", "arguments": ""},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    args_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '{"query": "test"}'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    flush_chunk = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}

    # Name + args chunks buffer, emit nothing yet.
    assert _parse_sse_chunk(name_chunk, "hermes", "c1", buffers) == []
    assert _parse_sse_chunk(args_chunk, "hermes", "c1", buffers) == []

    # Flush emits TOOL_CALL_START (with populated args) then TOOL_CALL_END.
    events = _parse_sse_chunk(flush_chunk, "hermes", "c1", buffers)
    assert [e.type for e in events] == [
        EventType.TOOL_CALL_START,
        EventType.TOOL_CALL_END,
    ]
    assert events[0].payload.tool_name == "web_search"
    assert events[0].payload.args == {"query": "test"}
    assert events[1].payload.tool_name == "web_search"


def test_parse_sse_risky_tool_yields_approval_request():
    """A risky tool name yields APPROVAL_REQUEST instead of a silent tool start."""
    buffers: dict = {}
    name_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call_9",
                            "function": {"name": "shell_execute", "arguments": ""},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    args_chunk = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "function": {"arguments": '{"cmd": "rm -rf /"}'},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ]
    }
    flush_chunk = {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}

    assert _parse_sse_chunk(name_chunk, "hermes", "c1", buffers) == []
    assert _parse_sse_chunk(args_chunk, "hermes", "c1", buffers) == []

    events = _parse_sse_chunk(flush_chunk, "hermes", "c1", buffers)
    assert len(events) == 1
    assert events[0].type == EventType.APPROVAL_REQUEST
    assert events[0].payload.action == "shell_execute"
    assert events[0].payload.risk == "advisory"
    assert events[0].payload.request_id == events[0].id
    assert "rm -rf /" in events[0].payload.description


def test_parse_sse_empty_delta():
    """Empty delta produces no events."""
    chunk = {"choices": [{"delta": {}, "finish_reason": None}]}
    events = _parse_sse_chunk(chunk, harness_id="hermes", conversation_id="c1")
    assert len(events) == 0


def test_adapter_properties():
    adapter = HermesAdapter()
    assert adapter.id == "hermes"
    assert adapter.name == "Hermes Agent"


class _FakeSSEResponse:
    """SSE response whose read blocks until close() aborts it."""

    status = 200

    def __init__(self) -> None:
        self.closed = False
        self._abort = asyncio.Event()
        self.content = self

    async def iter_any(self):
        yield b'data: {"choices": [{"delta": {"content": "hi"}, "finish_reason": null}]}\n'
        await self._abort.wait()
        raise ConnectionResetError("connection aborted")

    def close(self) -> None:
        self.closed = True
        self._abort.set()

    async def __aenter__(self) -> "_FakeSSEResponse":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False


class _FakeSSESession:
    def __init__(self, resp: _FakeSSEResponse) -> None:
        self._resp = resp

    def post(self, url, json=None, headers=None):
        return self._resp


def _user_message(conversation_id: str = "conv-1") -> ShellEvent:
    return ShellEvent(
        type=EventType.USER_MESSAGE,
        harness_id="hermes",
        conversation_id=conversation_id,
        payload=UserMessagePayload(text="go"),
    )


def _cancel(conversation_id: str = "conv-1") -> ShellEvent:
    return ShellEvent(
        type=EventType.CANCEL,
        harness_id="hermes",
        conversation_id=conversation_id,
        payload=CancelPayload(),
    )


@pytest.mark.asyncio
async def test_cancel_aborts_inflight_sse_stream():
    """CANCEL closes the in-flight SSE response so the read loop stops promptly."""
    adapter = HermesAdapter()
    adapter._config = HarnessConfig(harness_id="hermes", name="Hermes Agent", port=8642)
    resp = _FakeSSEResponse()
    adapter._session = _FakeSSESession(resp)

    events: list[ShellEvent] = []
    adapter.on_event(events.append)

    send_task = asyncio.create_task(adapter.send(_user_message()))
    for _ in range(20):  # let the stream deliver its first chunk
        if any(e.type == EventType.MESSAGE_CHUNK for e in events):
            break
        await asyncio.sleep(0)

    await adapter.send(_cancel())
    await send_task

    assert resp.closed is True
    assert adapter.last_cancel_result == {"cancelled": True, "mode": "propagated"}
    turn_end = next(e for e in events if e.type == EventType.TURN_END)
    assert turn_end.payload.reason == "cancelled"
    assert not any(e.type == EventType.ERROR for e in events)


def test_hermes_declares_unsupported_profile_fields():
    """Hermes' API server can't express a per-request working dir or permissions."""
    assert HermesAdapter().unsupported_profile_fields == frozenset({"working_dir", "permissions"})


def test_profile_shapes_request_body_and_session():
    """Two profiles shape the outgoing request differently: model, system prompt,
    and a profile-scoped session id."""
    from cep.adapter import AgentProfile

    adapter = HermesAdapter()
    adapter._config = HarnessConfig(harness_id="hermes", name="Hermes Agent", port=8642)

    docs = AgentProfile.new(
        harness_id="hermes", name="Docs", model="hermes-3", system_prompt="You write docs."
    )
    code = AgentProfile.new(harness_id="hermes", name="Code", model="hermes-code")

    adapter.apply_profile(docs)
    body_docs = adapter._build_body("hi")
    session_docs = adapter._session_id("conv-1")

    adapter.apply_profile(code)
    body_code = adapter._build_body("hi")
    session_code = adapter._session_id("conv-1")

    assert body_docs["model"] == "hermes-3"
    assert body_docs["messages"][0] == {"role": "system", "content": "You write docs."}
    assert body_docs["messages"][-1] == {"role": "user", "content": "hi"}

    assert body_code["model"] == "hermes-code"
    assert body_code["messages"] == [{"role": "user", "content": "hi"}]  # no system prompt

    # Same conversation, different profile -> distinct sessions.
    assert session_docs != session_code
    assert session_docs.endswith(":conv-1")


def test_build_body_without_profile_falls_back_to_default_model():
    """With no active profile the connect-time/default model is used."""
    adapter = HermesAdapter()
    adapter._config = HarnessConfig(harness_id="hermes", name="Hermes Agent", port=8642)
    body = adapter._build_body("hi")
    assert body["model"] == "hermes-agent"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert adapter._session_id("conv-1") == "conv-1"


@pytest.mark.asyncio
async def test_cancel_without_active_stream_reports_nothing_cancelled():
    """CANCEL with no turn running reports cancelled=False."""
    adapter = HermesAdapter()
    await adapter.send(_cancel())
    assert adapter.last_cancel_result == {"cancelled": False, "mode": "local"}


@pytest.mark.asyncio
async def test_connect_refused_without_risky_tool_optin():
    """Connecting must be refused unless risky tools are explicitly allowed (H4)."""
    adapter = HermesAdapter()
    config = HarnessConfig(harness_id="hermes", name="Hermes Agent", port=8642)

    with pytest.raises(ConfigurationError, match="allow_risky_tools"):
        await adapter.connect(config)

    assert adapter._session is None
    assert adapter._connected is False


@pytest.mark.asyncio
async def test_cancel_targets_only_its_conversation():
    """Concurrent turns on the shared adapter: cancelling one conversation
    must not abort the other's in-flight stream (per-conversation turn state)."""
    adapter = HermesAdapter()
    adapter._config = HarnessConfig(harness_id="hermes", name="Hermes Agent", port=8642)
    resp1, resp2 = _FakeSSEResponse(), _FakeSSEResponse()

    class _TwoTurnSession:
        def __init__(self) -> None:
            self._responses = [resp1, resp2]

        def post(self, url, json=None, headers=None):
            return self._responses.pop(0)

    adapter._session = _TwoTurnSession()
    events: list[ShellEvent] = []
    adapter.on_event(events.append)

    async def _wait_for_chunk(conversation_id: str) -> None:
        for _ in range(50):
            if any(
                e.type == EventType.MESSAGE_CHUNK and e.conversation_id == conversation_id
                for e in events
            ):
                return
            await asyncio.sleep(0)
        raise AssertionError(f"no chunk arrived for {conversation_id}")

    task1 = asyncio.get_running_loop().create_task(adapter.send(_user_message("conv-1")))
    await _wait_for_chunk("conv-1")
    task2 = asyncio.get_running_loop().create_task(adapter.send(_user_message("conv-2")))
    await _wait_for_chunk("conv-2")

    await adapter.send(_cancel("conv-1"))
    await task1

    assert resp1.closed is True
    assert resp2.closed is False  # the other turn keeps streaming
    ends = [e for e in events if e.type == EventType.TURN_END]
    assert [e.conversation_id for e in ends] == ["conv-1"]
    assert ends[0].payload.reason == "cancelled"

    await adapter.send(_cancel("conv-2"))
    await task2
    assert resp2.closed is True
