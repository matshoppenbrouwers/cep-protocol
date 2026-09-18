"""Adapter registry: manages installed adapters and active selection.

The registry is the single point of contact for the sidecar/shell to
discover, switch, and communicate with harness adapters.
"""

from __future__ import annotations

import logging

from cep.adapter import HarnessAdapter

logger = logging.getLogger(__name__)


class AdapterRegistry:
    """Registry of installed harness adapters.

    Usage:
        registry = AdapterRegistry()
        registry.register(hermes_adapter)
        registry.register(openclaw_adapter)
        registry.set_active("hermes")
        await registry.active.send(event)
    """

    def __init__(self) -> None:
        self._adapters: dict[str, HarnessAdapter] = {}
        self._active_id: str | None = None

    def register(self, adapter: HarnessAdapter, adapter_id: str | None = None) -> None:
        """Register an adapter under ``adapter_id`` (defaults to ``adapter.id``).

        The explicit id lets several runtimes of the same harness type coexist:
        the adapter class has a fixed ``id`` (its type), but the registry keys
        on the runtime id so ``openclaw`` and a second ``openclaw-2`` don't
        collide.
        """
        key = adapter_id or adapter.id
        self._adapters[key] = adapter
        logger.info("Registered adapter: %s (%s)", key, adapter.name)

    def unregister(self, adapter_id: str) -> None:
        """Remove an adapter from the registry."""
        if self._active_id == adapter_id:
            self._active_id = None
        self._adapters.pop(adapter_id, None)

    def set_active(self, adapter_id: str) -> None:
        """Switch the active adapter."""
        if adapter_id not in self._adapters:
            raise KeyError(f"Unknown adapter: {adapter_id}")
        self._active_id = adapter_id
        logger.info("Active adapter: %s", adapter_id)

    @property
    def active(self) -> HarnessAdapter | None:
        """Currently active adapter, or None."""
        if self._active_id is None:
            return None
        return self._adapters.get(self._active_id)

    @property
    def active_id(self) -> str | None:
        return self._active_id

    def list_adapters(self) -> dict[str, str]:
        """Return {adapter_id: adapter_name} for all registered adapters."""
        return {aid: a.name for aid, a in self._adapters.items()}

    def get(self, adapter_id: str) -> HarnessAdapter | None:
        """Get adapter by ID."""
        return self._adapters.get(adapter_id)
