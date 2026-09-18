"""Common Event Protocol type definitions.

This module defines the shared contract between the shell and harness adapters.
Neither shell nor agent owns these types; they are the common language.
"""

from __future__ import annotations

import itertools
import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

logger = logging.getLogger(__name__)


class EventType(Enum):
    """Event types flowing through the Common Event Protocol."""

    # Harness -> Shell
    MESSAGE_CHUNK = "message_chunk"
    MESSAGE_COMPLETE = "message_complete"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    THINKING_BLOCK = "thinking_block"
    APPROVAL_REQUEST = "approval_request"
    ERROR = "error"
    STATUS = "status"
    TURN_START = "turn_start"
    TURN_END = "turn_end"

    # Shell -> Harness
    APPROVAL_RESPONSE = "approval_response"
    USER_MESSAGE = "user_message"
    CONTEXT_UPDATE = "context_update"
    CANCEL = "cancel"


# --- Payload dataclasses ---


@dataclass(frozen=True, slots=True)
class MessageChunkPayload:
    text: str
    done: bool = False


@dataclass(frozen=True, slots=True)
class MessageCompletePayload:
    text: str
    role: str = "assistant"


@dataclass(frozen=True, slots=True)
class ToolCallStartPayload:
    tool_name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ToolCallEndPayload:
    tool_name: str
    result: str = ""
    success: bool = True


@dataclass(frozen=True, slots=True)
class ThinkingBlockPayload:
    text: str


@dataclass(frozen=True, slots=True)
class ApprovalRequestPayload:
    action: str
    description: str
    risk: str = "medium"  # low, medium, high, advisory (informational, not a gate)
    request_id: str = ""  # correlation id echoed back in ApprovalResponsePayload


@dataclass(frozen=True, slots=True)
class ApprovalResponsePayload:
    approved: bool
    request_id: str = ""


@dataclass(frozen=True, slots=True)
class ErrorPayload:
    code: str
    message: str
    fatal: bool = False  # True when the turn cannot continue


@dataclass(frozen=True, slots=True)
class StatusPayload:
    state: str  # "connecting", "connected", "disconnected", "busy"


@dataclass(frozen=True, slots=True)
class TurnStartPayload:
    """Harness accepted a user message and began a turn."""


@dataclass(frozen=True, slots=True)
class TurnEndPayload:
    """Turn finished; the harness is idle for this conversation."""

    reason: str = "complete"  # complete, cancelled, error


@dataclass(frozen=True, slots=True)
class CancelPayload:
    """Shell requests the harness stop the running turn."""


@dataclass(frozen=True, slots=True)
class UserMessagePayload:
    text: str
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ContextUpdatePayload:
    app: str
    window: str
    project: str | None = None


# Union of all payload types for type hints
Payload = (
    MessageChunkPayload
    | MessageCompletePayload
    | ToolCallStartPayload
    | ToolCallEndPayload
    | ThinkingBlockPayload
    | ApprovalRequestPayload
    | ApprovalResponsePayload
    | ErrorPayload
    | StatusPayload
    | TurnStartPayload
    | TurnEndPayload
    | CancelPayload
    | UserMessagePayload
    | ContextUpdatePayload
    | dict[str, Any]  # fallback for extensibility
)


# Maps each event type to its typed payload dataclass for deserialization.
_PAYLOAD_TYPES: dict[EventType, type] = {
    EventType.MESSAGE_CHUNK: MessageChunkPayload,
    EventType.MESSAGE_COMPLETE: MessageCompletePayload,
    EventType.TOOL_CALL_START: ToolCallStartPayload,
    EventType.TOOL_CALL_END: ToolCallEndPayload,
    EventType.THINKING_BLOCK: ThinkingBlockPayload,
    EventType.APPROVAL_REQUEST: ApprovalRequestPayload,
    EventType.APPROVAL_RESPONSE: ApprovalResponsePayload,
    EventType.ERROR: ErrorPayload,
    EventType.STATUS: StatusPayload,
    EventType.TURN_START: TurnStartPayload,
    EventType.TURN_END: TurnEndPayload,
    EventType.USER_MESSAGE: UserMessagePayload,
    EventType.CONTEXT_UPDATE: ContextUpdatePayload,
    EventType.CANCEL: CancelPayload,
}


def _generate_event_id() -> str:
    return f"evt-{uuid.uuid4().hex[:12]}"


def _now_ms() -> int:
    return int(time.time() * 1000)


# Process-wide monotonic sequence so events created in the same millisecond
# still have a deterministic order. It resets each process, so shadow history
# replays sort by (timestamp, seq); seq only tie-breaks within a millisecond.
_seq_counter = itertools.count(1)


def _next_seq() -> int:
    return next(_seq_counter)


def _build_payload(event_type: EventType, raw: Any) -> Payload:
    """Reconstruct a typed payload from a raw dict, or pass it through."""
    payload_cls = _PAYLOAD_TYPES.get(event_type)
    if payload_cls is None or not isinstance(raw, dict):
        return cast(Payload, raw)
    try:
        return cast(Payload, payload_cls(**raw))
    except TypeError:
        logger.warning(
            "Payload for %s did not match %s fields; keeping raw dict",
            event_type.value,
            payload_cls.__name__,
        )
        return cast(Payload, raw)


@dataclass(slots=True)
class ShellEvent:
    """A single event in the Common Event Protocol.

    Every interaction between shell and harness flows as ShellEvents.
    """

    type: EventType
    harness_id: str
    conversation_id: str
    payload: Payload
    id: str = field(default_factory=_generate_event_id)
    timestamp: int = field(default_factory=_now_ms)
    seq: int = field(default_factory=_next_seq)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        if hasattr(self.payload, "__dataclass_fields__"):
            from dataclasses import asdict

            payload_dict = asdict(cast("DataclassInstance", self.payload))
        elif isinstance(self.payload, dict):
            payload_dict = self.payload
        else:
            payload_dict = {"value": str(self.payload)}

        return {
            "id": self.id,
            "type": self.type.value,
            "harness_id": self.harness_id,
            "conversation_id": self.conversation_id,
            "timestamp": self.timestamp,
            "seq": self.seq,
            "payload": payload_dict,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ShellEvent:
        """Deserialize from dict.

        Reconstructs the typed payload dataclass for known event types so
        attribute access (e.g. ``event.payload.text``) survives a round-trip.
        Falls back to the raw dict when a *known* event's payload shape does
        not match the dataclass fields.

        An unrecognised ``type`` raises ``ValueError``: CEP has no forward
        compatibility, so a shell cannot read event types added after it.
        """
        event_type = EventType(data["type"])
        raw_payload = data.get("payload", {})
        return cls(
            id=data["id"],
            type=event_type,
            harness_id=data["harness_id"],
            conversation_id=data["conversation_id"],
            timestamp=data["timestamp"],
            seq=data.get("seq", 0),
            payload=_build_payload(event_type, raw_payload),
        )
