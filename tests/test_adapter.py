"""Tests for harness adapter base class."""

import pytest

from cep.adapter import HarnessAdapter, HarnessConfig, HealthStatus


def test_harness_config_creation():
    """HarnessConfig holds connection details."""
    config = HarnessConfig(
        harness_id="hermes",
        name="Hermes Agent",
        host="127.0.0.1",
        port=8642,
        auth_token="secret-123",
    )
    assert config.harness_id == "hermes"
    assert config.port == 8642
    assert config.base_url == "http://127.0.0.1:8642"


def test_harness_config_base_url_custom():
    """HarnessConfig allows custom base_url override."""
    config = HarnessConfig(
        harness_id="openclaw",
        name="OpenClaw",
        base_url="ws://localhost:18789",
    )
    assert config.base_url == "ws://localhost:18789"


def test_harness_config_ws_url_transport_aware():
    """ws_url is ws:// for the socket while base_url stays http:// for REST."""
    config = HarnessConfig(harness_id="openclaw", name="OpenClaw", port=18789)
    assert config.ws_url == "ws://127.0.0.1:18789"
    assert config.base_url == "http://127.0.0.1:18789"


def test_harness_config_ws_url_without_port():
    """ws_url omits the port when none is configured."""
    config = HarnessConfig(harness_id="openclaw", name="OpenClaw", host="localhost")
    assert config.ws_url == "ws://localhost"


def test_adapter_base_initializes_handler():
    """Base __init__ sets _handler so subclasses can't forget it."""

    class _StubAdapter(HarnessAdapter):
        @property
        def id(self) -> str:
            return "stub"

        @property
        def name(self) -> str:
            return "Stub"

        async def connect(self, config):  # noqa: ANN001
            pass

        async def disconnect(self):
            pass

        async def send(self, event):  # noqa: ANN001
            pass

        def on_event(self, handler):  # noqa: ANN001
            self._handler = handler

        async def health_check(self):
            return HealthStatus(connected=True, harness_id=self.id)

    adapter = _StubAdapter()
    assert adapter._handler is None
    adapter._emit_status("connected")  # must not raise AttributeError


def test_health_status_fields():
    """HealthStatus reports adapter state."""
    status = HealthStatus(
        connected=True,
        harness_id="hermes",
        latency_ms=12,
    )
    assert status.connected
    assert status.latency_ms == 12


def test_adapter_is_abstract():
    """Cannot instantiate HarnessAdapter directly."""
    with pytest.raises(TypeError):
        HarnessAdapter()
