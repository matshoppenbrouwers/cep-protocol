"""Tests for adapter registry and switcher."""

import pytest
from cep.adapter import HarnessAdapter, HarnessConfig, HealthStatus
from cep.registry import AdapterRegistry
from cep.types import ShellEvent


class FakeAdapter(HarnessAdapter):
    """Test adapter implementation."""

    def __init__(self, adapter_id: str = "fake", adapter_name: str = "Fake"):
        self._id = adapter_id
        self._name = adapter_name
        self._connected = False
        self._handler = None

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._name

    async def connect(self, config: HarnessConfig) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def send(self, event: ShellEvent) -> None:
        pass

    def on_event(self, handler):
        self._handler = handler

    async def health_check(self) -> HealthStatus:
        return HealthStatus(connected=self._connected, harness_id=self._id)


@pytest.fixture
def registry():
    return AdapterRegistry()


def test_register_adapter(registry):
    registry.register(FakeAdapter("hermes", "Hermes"))
    assert "hermes" in registry.list_adapters()


def test_set_active(registry):
    adapter = FakeAdapter("hermes", "Hermes")
    registry.register(adapter)
    registry.set_active("hermes")
    assert registry.active is adapter


def test_set_active_unknown_raises(registry):
    with pytest.raises(KeyError):
        registry.set_active("nonexistent")


def test_list_adapters(registry):
    registry.register(FakeAdapter("hermes", "Hermes"))
    registry.register(FakeAdapter("openclaw", "OpenClaw"))
    adapters = registry.list_adapters()
    assert set(adapters.keys()) == {"hermes", "openclaw"}


def test_active_defaults_to_none(registry):
    assert registry.active is None
