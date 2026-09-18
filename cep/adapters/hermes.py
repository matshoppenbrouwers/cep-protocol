"""Hermes adapter — SSE/HTTP integration via OpenAI-compatible API.

Connects to Hermes at http://localhost:8642. Uses /v1/chat/completions
with stream=true for real-time token streaming via Server-Sent Events.

Hermes does not have a native approval mechanism, so this adapter
synthesizes approval_request events for risky tool calls (shell_execute,
file_operation, etc.).
"""

from __future__ import annotations

import asyncio
import codecs
import json
import logging
import time
import uuid
from typing import Any

from cep.adapter import (
    ConfigurationError,
    EventHandler,
    HarnessAdapter,
    HarnessConfig,
    HealthStatus,
)
from cep.types import (
    ApprovalRequestPayload,
    EventType,
    MessageChunkPayload,
    MessageCompletePayload,
    ShellEvent,
    ToolCallStartPayload,
)

logger = logging.getLogger(__name__)


class RiskyToolsGateError(ConfigurationError):
    """Hermes was asked to connect without the risky-tools opt-in.

    A distinct type so the one-click connect flow can surface a deliberate
    consent step instead of treating it as a generic connection failure.
    """


# Tools that should trigger approval before execution
_RISKY_TOOLS = frozenset(
    {
        "shell_execute",
        "bash",
        "computer",
        "file_write",
        "file_delete",
    }
)


def _flush_tool_buffers(
    tool_buffers: dict[int, dict[str, str]],
    harness_id: str,
    conversation_id: str,
) -> list[ShellEvent]:
    """Emit events for fully-buffered tool calls.

    Risky tools (``_RISKY_TOOLS``) emit an advisory APPROVAL_REQUEST; it does not
    hold execution. Non-risky tools emit a TOOL_CALL_START with parsed args.
    Neither emits TOOL_CALL_END: Hermes returns no execution result, so the
    outcome is unknown and is not asserted.
    """
    events: list[ShellEvent] = []
    for buf in tool_buffers.values():
        name = buf["name"]
        args_str = buf["args"]
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            args = {}

        if name in _RISKY_TOOLS:
            # Hermes runs tools server-side, so this approval is advisory —
            # the tool already executed. "advisory" tells the UI to render a
            # warning, not a blocking gate.
            request_id = f"evt-{uuid.uuid4().hex[:12]}"
            events.append(
                ShellEvent(
                    type=EventType.APPROVAL_REQUEST,
                    harness_id=harness_id,
                    conversation_id=conversation_id,
                    id=request_id,
                    payload=ApprovalRequestPayload(
                        action=name,
                        description=f"Execute {name} with arguments: {args_str or '{}'}",
                        risk="advisory",
                        request_id=request_id,
                    ),
                )
            )
            continue

        events.append(
            ShellEvent(
                type=EventType.TOOL_CALL_START,
                harness_id=harness_id,
                conversation_id=conversation_id,
                payload=ToolCallStartPayload(tool_name=name, args=args),
            )
        )
        # No TOOL_CALL_END here. Reaching this point only means Hermes finished
        # streaming the call's *arguments*; it executes tools server-side and
        # sends back no execution result, so emitting an end event would assert
        # success="True", result="" for an outcome this adapter never observed.
    return events


def _parse_sse_chunk(
    chunk: dict[str, Any],
    harness_id: str,
    conversation_id: str,
    tool_buffers: dict[int, dict[str, str]] | None = None,
) -> list[ShellEvent]:
    """Parse a single SSE JSON chunk from Hermes into ShellEvents.

    Hermes uses the OpenAI streaming format. Tool calls stream their name and
    arguments across multiple chunks, so ``tool_buffers`` (keyed by tool index)
    accumulates them; the caller passes the same dict for the whole stream.

    - content deltas -> MESSAGE_CHUNK
    - tool_calls (name + incremental arguments) -> buffered, flushed on finish
    - finish_reason -> flush buffered tool calls; "stop" also emits done=True
    """
    if tool_buffers is None:
        tool_buffers = {}
    events: list[ShellEvent] = []

    choices = chunk.get("choices", [])
    if not choices:
        return events

    choice = choices[0]
    delta = choice.get("delta", {})
    finish_reason = choice.get("finish_reason")

    # Content streaming
    content = delta.get("content")
    if content:
        events.append(
            ShellEvent(
                type=EventType.MESSAGE_CHUNK,
                harness_id=harness_id,
                conversation_id=conversation_id,
                payload=MessageChunkPayload(text=content, done=False),
            )
        )

    # Tool calls — accumulate name and streamed arguments, emit nothing yet.
    for tc in delta.get("tool_calls", []):
        index = tc.get("index", 0)
        func = tc.get("function", {})
        buf = tool_buffers.setdefault(index, {"name": "", "args": ""})
        if func.get("name"):
            buf["name"] = func["name"]
        buf["args"] += func.get("arguments") or ""

    # Stream / tool-call end — flush buffered tool calls, then signal done.
    if finish_reason:
        events.extend(_flush_tool_buffers(tool_buffers, harness_id, conversation_id))
        tool_buffers.clear()
        if finish_reason == "stop":
            events.append(
                ShellEvent(
                    type=EventType.MESSAGE_CHUNK,
                    harness_id=harness_id,
                    conversation_id=conversation_id,
                    payload=MessageChunkPayload(text="", done=True),
                )
            )

    return events


class HermesAdapter(HarnessAdapter):
    """Adapter for Hermes Agent (NousResearch).

    Transport: HTTP POST to /v1/chat/completions with SSE streaming.
    Health: GET /health.
    Sessions: X-Hermes-Session-Id header for persistent conversations.

    Agent-profile mapping: the profile's ``model`` becomes the
    per-request model and its ``system_prompt`` is prepended as a system
    message; the session header is scoped to the profile so switching agents
    starts a fresh session. Hermes' API server has no per-request working dir
    and runs tools server-side (no per-turn permission map), so those two
    fields are declared unsupported.
    """

    unsupported_profile_fields = frozenset({"working_dir", "permissions"})

    def __init__(self) -> None:
        super().__init__()
        self._config: HarnessConfig | None = None
        self._connected = False
        self._session: Any = None  # aiohttp.ClientSession, lazy import
        # Per-conversation turn state so concurrent turns on the shared protocol
        # loop can't cancel or clobber each other:
        # conversation_id -> {"cancelled": bool, "response": in-flight SSE response}
        self._turns: dict[str, dict[str, Any]] = {}

    @property
    def id(self) -> str:
        return "hermes"

    @property
    def name(self) -> str:
        return "Hermes Agent"

    async def connect(self, config: HarnessConfig) -> None:
        """Open an aiohttp session to Hermes.

        Hermes executes its own tools server-side, so this adapter can only
        observe risky tool calls after the fact — its ``APPROVAL_REQUEST`` is
        advisory and does NOT gate or hold execution (unlike OpenClaw's round-trip).
        Connecting therefore requires an explicit ``allow_risky_tools`` opt-in
        so risky tools are effectively disabled through Hermes unless an operator
        knowingly accepts that they run without a real pre-execution gate.
        """
        if config.extra.get("allow_risky_tools") is not True:
            raise RiskyToolsGateError(
                "Hermes executes tools server-side and cannot gate risky tools "
                f"({', '.join(sorted(_RISKY_TOOLS))}) before they run; its approval "
                "signal is advisory only. Set extra['allow_risky_tools']=True (the boolean, "
                "not a string) to "
                "connect anyway and accept that risk."
            )

        try:
            import aiohttp
        except ImportError:
            logger.error("aiohttp required for Hermes adapter: pip install aiohttp")
            raise

        self._config = config
        self._profile = config.profile
        self._session = aiohttp.ClientSession(
            base_url=config.base_url,
            headers=self._build_headers(),
        )
        self._connected = True
        self._emit_status("connected")

    async def disconnect(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None
        self._connected = False
        self._emit_status("disconnected")

    def _cancel_turn(self, conversation_id: str) -> None:
        """Mark a running turn cancelled and abort its in-flight response."""
        turn = self._turns.get(conversation_id)
        if turn is None:
            self.last_cancel_result = {"cancelled": False, "mode": "local"}
            return
        turn["cancelled"] = True
        resp = turn["response"]
        if resp is not None:
            # Abort the in-flight SSE response so the read loop terminates
            # now instead of at the next chunk boundary.
            resp.close()
            self.last_cancel_result = {"cancelled": True, "mode": "propagated"}
        else:
            # Initial POST still in flight; the stream loop stops at its
            # first chunk.
            self.last_cancel_result = {"cancelled": True, "mode": "local"}

    async def _stream_turn(
        self, event: ShellEvent, body: dict[str, Any], active_turn: dict[str, Any]
    ) -> str:
        """POST the turn and consume its stream; returns the turn_end reason."""
        assert self._session is not None
        async with self._session.post(
            "/v1/chat/completions",
            json=body,
            headers={"X-Hermes-Session-Id": self._session_id(event.conversation_id)},
        ) as resp:
            if resp.status != 200:
                self._emit_error(
                    f"Hermes returned {resp.status}",
                    conversation_id=event.conversation_id,
                    fatal=True,
                )
                return "error"
            active_turn["response"] = resp
            try:
                stream = await self._consume_sse_stream(resp, event.conversation_id, active_turn)
            finally:
                active_turn["response"] = None
            return self._turn_reason_for(stream, event.conversation_id)

    async def send(self, event: ShellEvent) -> None:
        """Send user message to Hermes via /v1/chat/completions."""
        if event.type == EventType.CANCEL:
            self._cancel_turn(event.conversation_id)
            return
        if event.type != EventType.USER_MESSAGE:
            return

        if not self._session or not self._config:
            self._emit_error("Not connected to Hermes", conversation_id=event.conversation_id)
            return

        payload = event.payload
        text = payload.text if hasattr(payload, "text") else str(payload)

        body = self._build_body(text)

        if event.conversation_id in self._turns:
            self._emit_error(
                f"A turn is already running for conversation {event.conversation_id}; "
                "cancel it before sending another.",
                conversation_id=event.conversation_id,
            )
            return
        active_turn: dict[str, Any] = {"cancelled": False, "response": None}
        self._turns[event.conversation_id] = active_turn
        turn_reason = "complete"
        self._emit_turn_start(event.conversation_id)
        try:
            turn_reason = await self._stream_turn(event, body, active_turn)
        except asyncio.CancelledError:
            # Cancelling the task must still close the turn for subscribers,
            # then propagate so the caller's cancellation semantics hold.
            self._turns.pop(event.conversation_id, None)
            self._emit_turn_end(event.conversation_id, reason="cancelled")
            raise
        except Exception as exc:
            if active_turn["cancelled"]:
                # A cancel can abort the request mid-flight; that's the intended
                # outcome, not an error.
                turn_reason = "cancelled"
            else:
                self._emit_error(
                    f"Hermes request failed: {exc}",
                    conversation_id=event.conversation_id,
                    fatal=True,
                )
                turn_reason = "error"
        finally:
            if self._turns.get(event.conversation_id) is active_turn:
                self._turns.pop(event.conversation_id, None)
        self._emit_turn_end(event.conversation_id, reason=turn_reason)

    def _turn_reason_for(self, stream: dict[str, Any], conversation_id: str) -> str:
        """Map a finished stream onto a turn_end reason, emitting any error.

        A cancel is an intended stop, not a failure. An error envelope and a
        stream that stopped without a terminal marker are both failures, and
        the second one is reported rather than passed off as a complete reply.
        """
        if stream["cancelled"]:
            return "cancelled"
        if stream["error"]:
            self._emit_error(
                f"Hermes stream error: {stream['error']}",
                conversation_id=conversation_id,
            )
            return "error"
        if not stream["terminal"]:
            self._emit_error(
                "Hermes stream ended without a terminal marker; the reply is incomplete.",
                conversation_id=conversation_id,
            )
            return "error"
        return "complete"

    async def _consume_sse_stream(
        self, resp: Any, conversation_id: str, turn: dict[str, Any]
    ) -> dict[str, Any]:
        """Read SSE lines from response and dispatch ShellEvents.

        Returns ``cancelled`` (stopped by a cancel request), ``terminal`` (the
        server sent a finish marker) and ``error`` (an error envelope arrived).
        A stream that ends without a terminal marker is reported as such rather
        than promoted to a completed message.
        """
        full_text = ""
        buffer = ""
        tool_buffers: dict[int, dict[str, str]] = {}
        cancelled = False
        terminal = False
        stream_error: str | None = None
        # A multi-byte character can straddle two network chunks, so decode
        # incrementally rather than per-chunk.
        decoder = codecs.getincrementaldecoder("utf-8")()
        try:
            async for raw_bytes in resp.content.iter_any():
                if turn["cancelled"]:
                    cancelled = True
                    break
                buffer += decoder.decode(raw_bytes)
                while "\n" in buffer:
                    line_str, buffer = buffer.split("\n", 1)
                    line = self._dispatch_sse_line(line_str, conversation_id, tool_buffers)
                    full_text += line["text"]
                    if line["terminal"]:
                        terminal = True
                    if line["error"]:
                        stream_error = line["error"]
                        break
                if stream_error:
                    break
        except Exception:
            # A cancel closes the response mid-read; that abort is the intended
            # outcome, not an error. Anything else propagates.
            if not turn["cancelled"]:
                raise
            cancelled = True

        # Only a stream the server actually finished yields MESSAGE_COMPLETE.
        # Promoting a truncated or failed stream would report partial text as
        # the final answer.
        if self._handler and full_text and terminal and not stream_error and not cancelled:
            self._handler(
                ShellEvent(
                    type=EventType.MESSAGE_COMPLETE,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=MessageCompletePayload(text=full_text),
                )
            )
        return {"cancelled": cancelled, "terminal": terminal, "error": stream_error}

    def _dispatch_sse_line(
        self,
        line_str: str,
        conversation_id: str,
        tool_buffers: dict[int, dict[str, str]],
    ) -> dict[str, Any]:
        """Handle one SSE line.

        Returns the text delta it contributed, whether the line was a terminal
        marker, and any error envelope the server sent in place of a chunk.
        """
        result: dict[str, Any] = {"text": "", "terminal": False, "error": None}
        line_str = line_str.strip()
        # SSE spec allows the field value with or without a leading space
        # ("data:{...}" or "data: {...}"); OpenAI-compatible servers emit the
        # spaced form, but tolerate both so a strict server doesn't drop tokens.
        if not line_str.startswith("data:"):
            return result
        data_str = line_str[5:].lstrip()
        if data_str == "[DONE]":
            result["terminal"] = True
            return result

        chunk = json.loads(data_str)
        # An OpenAI-compatible server can send {"error": ...} instead of a chunk.
        # Ignoring it would let a failed stream finish as a clean turn.
        if isinstance(chunk, dict) and chunk.get("error"):
            err = chunk["error"]
            result["error"] = err.get("message") if isinstance(err, dict) else str(err)
            return result
        if any(c.get("finish_reason") for c in chunk.get("choices", [])):
            result["terminal"] = True
        for shell_event in _parse_sse_chunk(chunk, self.id, conversation_id, tool_buffers):
            if self._handler:
                self._handler(shell_event)
            if isinstance(shell_event.payload, MessageChunkPayload):
                result["text"] += shell_event.payload.text
        return result

    def on_event(self, handler: EventHandler) -> None:
        self._handler = handler

    async def health_check(self) -> HealthStatus:
        if not self._session:
            return HealthStatus(connected=False, harness_id=self.id, error="No session")
        start = time.perf_counter()
        try:
            async with self._session.get("/health") as resp:
                return HealthStatus(
                    connected=resp.status == 200,
                    harness_id=self.id,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                )
        except Exception as exc:
            return HealthStatus(connected=False, harness_id=self.id, error=str(exc))

    def _build_body(self, text: str) -> dict[str, Any]:
        """Build the chat-completions body, applying the active profile.

        The profile's ``model`` overrides the connect-time default and its
        ``system_prompt`` is prepended as a system message.
        """
        profile = self._profile
        model = (
            (profile.model if profile and profile.model else None)
            or (self._config.extra.get("model") if self._config else None)
            # Hermes advertises the default profile's model as "hermes-agent"
            # (docs: user-guide/features/api-server; the advertised name defaults
            # to the profile name, or "hermes-agent" for the default profile).
            # Confirmed via docs 2026-07-17; a profile/recipe may still override.
            or "hermes-agent"
        )
        messages: list[dict[str, str]] = []
        if profile and profile.system_prompt:
            messages.append({"role": "system", "content": profile.system_prompt})
        messages.append({"role": "user", "content": text})
        return {"model": model, "messages": messages, "stream": True}

    def _session_id(self, conversation_id: str) -> str:
        """Scope the Hermes session to the active profile.

        Two profiles on the same conversation map to distinct sessions so
        switching agents starts fresh context; without a profile the raw
        conversation id is used (unchanged default).
        """
        if self._profile is not None:
            return f"{self._profile.profile_id}:{conversation_id}"
        return conversation_id

    def _build_headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._config and self._config.auth_token:
            headers["Authorization"] = f"Bearer {self._config.auth_token}"
        return headers
