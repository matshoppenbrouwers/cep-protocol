"""Adapter contract: the base class every harness adapter implements.

Each adapter translates between a harness's native protocol
(WebSocket, SSE, direct calls) and the Common Event Protocol.

Adapters implement five methods and two properties, the entire integration surface.
"""

from __future__ import annotations

import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from cep.types import (
    ErrorPayload,
    EventType,
    ShellEvent,
    StatusPayload,
    TurnEndPayload,
    TurnStartPayload,
)

EventHandler = Callable[[ShellEvent], None]


class ConfigurationError(Exception):
    """A harness adapter was configured in a way it cannot honor."""


# Fields kept on the persisted profile record; anything else in a stored dict
# is dropped on load so unknown keys can't leak into the runtime object.
_FIELDS = frozenset(
    {
        "profile_id",
        "harness_id",
        "name",
        "working_dir",
        "model",
        "system_prompt",
        "permissions",
        "created_at",
    }
)


@dataclass(slots=True)
class AgentProfile:
    """A named agent configuration bound to one harness runtime."""

    profile_id: str
    harness_id: str
    name: str
    working_dir: str | None = None
    model: str | None = None
    system_prompt: str | None = None
    permissions: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)

    @classmethod
    def new(
        cls,
        harness_id: str,
        name: str,
        *,
        profile_id: str | None = None,
        working_dir: str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        permissions: dict[str, Any] | None = None,
    ) -> AgentProfile:
        """Create a profile, generating a unique ``profile_id`` when absent."""
        return cls(
            profile_id=profile_id or uuid.uuid4().hex,
            harness_id=harness_id,
            name=name,
            working_dir=working_dir,
            model=model,
            system_prompt=system_prompt,
            permissions=permissions or {},
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict for persistence."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AgentProfile:
        """Rebuild from a persisted dict, ignoring unknown keys."""
        known = {k: v for k, v in data.items() if k in _FIELDS}
        return cls(**known)


@dataclass(slots=True)
class HarnessConfig:
    """Connection configuration for a harness."""

    harness_id: str
    name: str
    host: str = "127.0.0.1"
    port: int | None = None
    auth_token: str | None = None
    base_url: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    # Installed-runtime metadata (populated once the harness is app-managed).
    binary_path: str | None = None
    version: str | None = None
    # Active agent profile shaping the turn (working dir, model, prompt, perms).
    profile: AgentProfile | None = None

    def __post_init__(self) -> None:
        if self.base_url is None and self.port is not None:
            self.base_url = f"http://{self.host}:{self.port}"
        elif self.base_url is None:
            self.base_url = f"http://{self.host}"

    @property
    def ws_url(self) -> str:
        """WebSocket URL for ws-transport adapters; REST/health stay on base_url."""
        if self.port is not None:
            return f"ws://{self.host}:{self.port}"
        return f"ws://{self.host}"


@dataclass(frozen=True, slots=True)
class HealthStatus:
    """Health check result from an adapter."""

    connected: bool
    harness_id: str
    latency_ms: int | None = None
    error: str | None = None


class HarnessAdapter(ABC):
    """Abstract base for harness adapters.

    Each adapter translates a harness's native events to/from ShellEvents.
    The base __init__ initializes self._handler so subclasses can't forget it.
    """

    # Agent-profile fields this harness cannot express on a turn. Surfaced to
    # the UI (via list_adapters / list_agent_profiles) so an unsupported field
    # is flagged rather than silently dropped. Subclasses override.
    unsupported_profile_fields: frozenset[str] = frozenset()

    def __init__(self) -> None:
        self._handler: EventHandler | None = None
        # Active agent profile shaping the current turn (model, prompt, working
        # dir, permissions). Applied per-send by the shell; None means defaults.
        self._profile: AgentProfile | None = None
        # Outcome of the most recent CANCEL send. "propagated" = the running
        # turn was actually stopped; "local" = only shell-side state changed.
        self.last_cancel_result: dict[str, Any] = {"cancelled": True, "mode": "local"}

    def apply_profile(self, profile: AgentProfile | None) -> None:
        """Set the active agent profile that shapes subsequent turns.

        Adapters read ``self._profile`` when building an outgoing request; those
        that can't honor a field list it in ``unsupported_profile_fields``.
        """
        self._profile = profile

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this adapter."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for display."""

    @abstractmethod
    async def connect(self, config: HarnessConfig) -> None:
        """Establish connection to the harness."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close connection to the harness."""

    @abstractmethod
    async def send(self, event: ShellEvent) -> None:
        """Send an event to the harness (user message, approval, context)."""

    @abstractmethod
    def on_event(self, handler: EventHandler) -> None:
        """Register handler for events from the harness."""

    @abstractmethod
    async def health_check(self) -> HealthStatus:
        """Check harness connectivity. Non-blocking."""

    def has_active_turn(self, conversation_id: str) -> bool:
        """Whether a turn is still running for this conversation after ``send()``.

        Synchronous adapters (send blocks for the whole turn) keep the default
        False. Async-transport adapters whose events arrive on a listener task
        after ``send()`` returns must override this so the shell knows to keep
        streaming until the turn ends.
        """
        return False

    def _emit_status(self, state: str, conversation_id: str = "") -> None:
        """Emit a STATUS event to the registered handler."""
        if self._handler:
            self._handler(
                ShellEvent(
                    type=EventType.STATUS,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=StatusPayload(state=state),
                )
            )

    def _emit_error(
        self,
        message: str,
        code: str | None = None,
        conversation_id: str = "",
        fatal: bool = False,
    ) -> None:
        """Emit an ERROR event to the registered handler."""
        if self._handler:
            self._handler(
                ShellEvent(
                    type=EventType.ERROR,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=ErrorPayload(
                        code=code or f"{self.id}_error",
                        message=message,
                        fatal=fatal,
                    ),
                )
            )

    def _emit_turn_start(self, conversation_id: str) -> None:
        """Signal that the harness accepted a user message and began a turn."""
        if self._handler:
            self._handler(
                ShellEvent(
                    type=EventType.TURN_START,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=TurnStartPayload(),
                )
            )

    def _emit_turn_end(self, conversation_id: str, reason: str = "complete") -> None:
        """Signal that the turn finished and the harness is idle again."""
        if self._handler:
            self._handler(
                ShellEvent(
                    type=EventType.TURN_END,
                    harness_id=self.id,
                    conversation_id=conversation_id,
                    payload=TurnEndPayload(reason=reason),
                )
            )
