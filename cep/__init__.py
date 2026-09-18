"""Common Event Protocol (CEP).

A 14-event schema for the traffic between a shell (the UI a person types into)
and a harness (the agent runtime that answers), plus the adapter contract that
translates a harness's native protocol into those events.

The core (:mod:`cep.types`, :mod:`cep.runtime`, :mod:`cep.adapter`,
:mod:`cep.registry`) is standard-library only. The Hermes reference adapter
lives in :mod:`cep.adapters.hermes` and needs the ``hermes`` extra.
"""

from cep.adapter import (
    AgentProfile,
    ConfigurationError,
    EventHandler,
    HarnessAdapter,
    HarnessConfig,
    HealthStatus,
)
from cep.registry import AdapterRegistry
from cep.runtime import ProtocolRuntime
from cep.types import EventType, ShellEvent

__all__ = [
    "AdapterRegistry",
    "AgentProfile",
    "ConfigurationError",
    "EventHandler",
    "EventType",
    "HarnessAdapter",
    "HarnessConfig",
    "HealthStatus",
    "ProtocolRuntime",
    "ShellEvent",
]
