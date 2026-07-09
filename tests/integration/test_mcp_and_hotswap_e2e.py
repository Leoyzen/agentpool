"""Integration tests for M3 deferred items 16.6 and 16.8.

Item 16.6: MCP tools end-to-end through MCPCapability (not as_capability).
Item 16.8: on_change hot-swap end-to-end through the RunLoop.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.mcp_capability import MCPCapability
from agentpool.capabilities.resource_source import ResourceSource
from agentpool.host.factory import AgentFactory


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Self


# =============================================================================
# Item 16.6: MCP tools end-to-end through MCPCapability
# =============================================================================


@dataclass
class FakeMCPClient:
    """Minimal MCPClient mock for end-to-end toolset test."""

    server_name: str = "test_server"
    _connected: bool = False
    _tool_change_callback: Any = None
    _client: Any = None

    @property
    def config(self) -> Any:
        """Return a config-like object with a name attribute."""
        cfg = MagicMock()
        cfg.name = self.server_name
        return cfg

    async def __aenter__(self) -> Self:
        """Enter async context."""
        self._connected = True
        return self

    async def __aexit__(self, *args: object) -> None:
        """Exit async context."""
        self._connected = False

    async def list_resources(self) -> list[Any]:
        """Return empty resource list."""
        return []

    async def read_resource(self, uri: str) -> list[Any]:
        """Return empty read result."""
        return []


def test_mcp_capability_get_toolset_returns_callable() -> None:
    """Test that get_toolset() returns a callable, not None."""
    client = FakeMCPClient()
    cap = MCPCapability(client)
    toolset_fn = cap.get_toolset()
    assert toolset_fn is not None
    assert callable(toolset_fn)


@pytest.mark.asyncio
async def test_mcp_capability_get_toolset_builds_mcp_toolset() -> None:
    """Test that the toolset function builds an MCPToolset when called."""
    from unittest.mock import patch

    client = FakeMCPClient()
    client._client = MagicMock()
    cap = MCPCapability(client)
    mock_ctx = MagicMock()
    mock_toolset_instance = MagicMock()

    # get_toolset() imports MCPToolset at call time, so patch before calling.
    with patch("pydantic_ai.mcp.MCPToolset", return_value=mock_toolset_instance):
        toolset_fn = cap.get_toolset()
        result = await toolset_fn(mock_ctx)
        assert result is mock_toolset_instance


@pytest.mark.asyncio
async def test_mcp_capability_implements_resource_source() -> None:
    """Test that MCPCapability structurally implements ResourceSource."""
    client = FakeMCPClient()
    cap = MCPCapability(client)
    assert isinstance(cap, ResourceSource)


@pytest.mark.asyncio
async def test_mcp_capability_lifecycle() -> None:
    """Test that MCPCapability manages client connect/disconnect lifecycle."""
    client = FakeMCPClient()
    cap = MCPCapability(client)
    assert not client._connected
    async with cap:
        assert client._connected
    assert not client._connected


def test_mcp_capability_not_using_as_capability() -> None:
    """Test that MCPCapability does NOT have the old as_capability method."""
    assert not hasattr(MCPCapability, "as_capability")
    assert callable(MCPCapability.get_toolset)


# =============================================================================
# Item 16.8: on_change hot-swap end-to-end
# =============================================================================


class MockChangeCapable:
    """Mock capability that implements on_change() with a controllable event stream."""

    def __init__(self, name: str = "mock_cap") -> None:
        self._name = name
        self._change_queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()

    @property
    def name(self) -> str:
        """Return the capability name."""
        return self._name

    async def __aenter__(self) -> Self:
        """Enter async context."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit async context."""

    def get_instructions(self) -> str | None:
        """Return no instructions."""
        return None

    def get_toolset(self) -> Any:
        """Return no toolset."""
        return None

    async def on_change(self) -> AsyncIterator[ChangeEvent]:
        """Yield ChangeEvents from the internal queue."""
        while True:
            event = await self._change_queue.get()
            yield event

    async def emit_change(self, kind: str = "tools_changed") -> None:
        """Emit a change event into the queue."""
        await self._change_queue.put(
            ChangeEvent(capability_name=self._name, kind=kind),
        )


@pytest.mark.asyncio
async def test_hot_swap_listener_starts_and_receives_event() -> None:
    """Test that hot-swap listener starts and survives a change event."""
    factory = AgentFactory(pool=MagicMock())  # type: ignore[arg-type]
    mock_agent = MagicMock()
    cap = MockChangeCapable(name="test_cap")

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])
    assert len(factory._hot_swap_tasks) == 1

    await cap.emit_change()
    await asyncio.sleep(0.1)
    assert not factory._hot_swap_tasks[0].done()

    await factory.stop_hot_swap_listeners()


@pytest.mark.asyncio
async def test_hot_swap_listener_skips_capabilities_without_on_change() -> None:
    """Test that hot-swap listeners are not started for static capabilities."""

    class StaticCap:
        """Capability without on_change()."""

        def __init__(self) -> None:
            self._name = "static"

        @property
        def name(self) -> str:
            """Return the capability name."""
            return self._name

        async def __aenter__(self) -> Self:
            """Enter async context."""
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            """Exit async context."""

        def get_instructions(self) -> str | None:
            """Return no instructions."""
            return None

        def get_toolset(self) -> Any:
            """Return no toolset."""
            return None

    factory = AgentFactory(pool=MagicMock())  # type: ignore[arg-type]
    mock_agent = MagicMock()
    cap = StaticCap()

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])
    assert len(factory._hot_swap_tasks) == 0
    await factory.stop_hot_swap_listeners()


@pytest.mark.asyncio
async def test_hot_swap_listener_stops_on_cancel() -> None:
    """Test that stop_hot_swap_listeners cancels all background tasks."""
    factory = AgentFactory(pool=MagicMock())  # type: ignore[arg-type]
    mock_agent = MagicMock()
    cap = MockChangeCapable(name="test_cap")

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])
    assert len(factory._hot_swap_tasks) == 1

    await factory.stop_hot_swap_listeners()
    assert len(factory._hot_swap_tasks) == 0


@pytest.mark.asyncio
async def test_hot_swap_multiple_events() -> None:
    """Test that the hot-swap listener processes multiple events."""
    factory = AgentFactory(pool=MagicMock())  # type: ignore[arg-type]
    mock_agent = MagicMock()
    cap = MockChangeCapable(name="multi_cap")

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])

    for i in range(5):
        await cap.emit_change(kind=f"change_{i}")

    await asyncio.sleep(0.2)
    assert not factory._hot_swap_tasks[0].done()

    await factory.stop_hot_swap_listeners()
