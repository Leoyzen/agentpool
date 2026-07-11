"""Unit tests for McpServerCap — MCP server capability with Resource Protocols.

Tests cover:
- Delegation: list_tools, call_tool, list_resources, read_resource, resource_exists
- Lazy init: no connection at construct, connection on first list_tools
- Change notification mapping: tools/list_changed → ChangeEvent
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.mcp_server_cap import McpServerCap
from agentpool.capabilities.resource_protocols import (
    ChangeObservable,
    McpResource,
    ToolEntry,
)


if TYPE_CHECKING:
    from typing import Self


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeMCPClient:
    """Async mock of MCPClient for testing McpServerCap."""

    _tools: list[Any] = field(default_factory=list)
    _resources: list[Any] = field(default_factory=list)
    _read_results: dict[str, list[Any]] = field(default_factory=dict)
    _connected: bool = False
    _tool_change_callback: Any = None
    config: Any = None

    async def __aenter__(self) -> Self:
        self._connected = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self._connected = False

    async def list_tools(self) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._tools)

    async def list_resources(self) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._resources)

    async def read_resource(self, uri: str) -> list[Any]:
        if not self._connected:
            raise RuntimeError("Not connected")
        if uri not in self._read_results:
            raise RuntimeError(f"Resource not found: {uri}")
        return self._read_results[uri]

    async def call_tool(self, name: str, *args: Any, **kwargs: Any) -> str:
        return f"called:{name}"

    def convert_tool(self, tool: Any) -> Any:
        return tool

    async def trigger_tool_change(self) -> None:
        """Simulate MCP server sending notifications/tools/list_changed."""
        if self._tool_change_callback is not None:
            await self._tool_change_callback()


class FakeSessionPool:
    """Fake SessionConnectionPool that returns a FakeMCPClient."""

    def __init__(self, client: FakeMCPClient) -> None:
        self._client = client
        self.get_client_call_count = 0

    async def get_client(self, config: Any, skill_name: str | None = None) -> FakeMCPClient:
        self.get_client_call_count += 1
        self._client._connected = True
        return self._client


def _make_tool(name: str = "test_tool", description: str = "A test tool") -> MagicMock:
    """Create a fake MCP tool."""
    tool = MagicMock()
    tool.name = name
    tool.description = description
    tool.inputSchema = {"type": "object", "properties": {}}
    return tool


def _make_resource(
    uri: str, name: str = "", description: str = "", mime_type: str = ""
) -> MagicMock:
    """Create a fake MCP resource."""
    res = MagicMock()
    res.uri = uri
    res.name = name
    res.description = description
    res.mimeType = mime_type
    return res


def _make_text_content(text: str) -> MagicMock:
    """Create a fake TextResourceContents."""
    content = MagicMock()
    content.text = text
    return content


def _make_config(client_id: str = "test_server") -> MagicMock:
    """Create a fake BaseMCPServerConfig."""
    config = MagicMock()
    config.client_id = client_id
    return config


# ---------------------------------------------------------------------------
# isinstance tests
# ---------------------------------------------------------------------------


def test_mcp_server_cap_is_mcp_resource() -> None:
    """McpServerCap is an instance of McpResource (structural typing)."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, McpResource)


def test_mcp_server_cap_is_change_observable() -> None:
    """McpServerCap is an instance of ChangeObservable."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert isinstance(cap, ChangeObservable)


# ---------------------------------------------------------------------------
# Lazy init tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_lazy_init_no_connection_at_construct() -> None:
    """McpServerCap does not connect at construction time."""
    client = FakeMCPClient()
    pool = FakeSessionPool(client)
    cap = McpServerCap(config=_make_config(), session_pool=pool)

    assert cap._client is None
    assert pool.get_client_call_count == 0


@pytest.mark.anyio
async def test_lazy_init_connection_on_first_list_tools() -> None:
    """First list_tools() triggers client creation via _ensure_client()."""
    client = FakeMCPClient(_tools=[_make_tool()])
    pool = FakeSessionPool(client)
    cap = McpServerCap(config=_make_config(), session_pool=pool)

    assert pool.get_client_call_count == 0
    tools = await cap.list_tools()
    assert pool.get_client_call_count == 1
    assert len(tools) == 1
    assert isinstance(tools[0], ToolEntry)
    assert tools[0].name == "test_tool"

    # Second call reuses cached client
    await cap.list_tools()
    assert pool.get_client_call_count == 1


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_list_tools_delegation() -> None:
    """list_tools() delegates to MCPClient.list_tools() and maps to ToolEntry."""
    tools = [_make_tool("tool_a", "Tool A"), _make_tool("tool_b", "Tool B")]
    client = FakeMCPClient(_tools=tools)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_tools()
    assert len(result) == 2
    assert result[0].name == "tool_a"
    assert result[0].description == "Tool A"
    assert result[1].name == "tool_b"


@pytest.mark.anyio
async def test_list_resources_delegation() -> None:
    """list_resources() delegates to MCPClient.list_resources()."""
    resources = [
        _make_resource("file:///path1", "res1", "Resource 1", "text/plain"),
        _make_resource("file:///path2", "res2"),
    ]
    client = FakeMCPClient(_resources=resources)
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.list_resources()
    assert len(result) == 2
    assert result[0].uri == "file:///path1"
    assert result[0].name == "res1"
    assert result[0].description == "Resource 1"
    assert result[0].mime_type == "text/plain"


@pytest.mark.anyio
async def test_read_resource_existing() -> None:
    """read_resource() returns content for existing resource."""
    text_content = _make_text_content("hello world")
    client = FakeMCPClient(
        _resources=[_make_resource("file:///path1")],
        _read_results={"file:///path1": [text_content]},
    )
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    content = await cap.read_resource("file:///path1")
    assert content == "hello world"


@pytest.mark.anyio
async def test_read_resource_nonexistent() -> None:
    """read_resource() returns None for nonexistent resource."""
    client = FakeMCPClient(_resources=[])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    content = await cap.read_resource("file:///nonexistent")
    assert content is None


@pytest.mark.anyio
async def test_resource_exists_true() -> None:
    """resource_exists() returns True for existing resource."""
    client = FakeMCPClient(_resources=[_make_resource("file:///path1")])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    assert await cap.resource_exists("file:///path1") is True


@pytest.mark.anyio
async def test_resource_exists_false() -> None:
    """resource_exists() returns False for nonexistent resource."""
    client = FakeMCPClient(_resources=[])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    assert await cap.resource_exists("file:///nonexistent") is False


# ---------------------------------------------------------------------------
# Change notification tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_change_notification_tools_changed() -> None:
    """tools/list_changed notification yields ChangeEvent."""
    client = FakeMCPClient(_tools=[_make_tool()])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    # Initialize the client (sets up change callback)
    await cap._ensure_client()

    # Get the change stream
    stream = cap.on_change()
    assert stream is not None

    # Trigger a tool change
    await client.trigger_tool_change()

    # Consume the event with a timeout
    async with asyncio.timeout(1.0):
        event = await stream.__anext__()

    assert isinstance(event, ChangeEvent)
    assert event.kind == "tools_changed"
    assert event.capability_name == cap.name
    assert event.source_uri == f"mcp://{cap.name}"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_aenter_returns_self() -> None:
    """__aenter__ returns self without connecting (lazy)."""
    client = FakeMCPClient()
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    result = await cap.__aenter__()
    assert result is cap
    assert cap._client is None  # Lazy: no connection


@pytest.mark.anyio
async def test_aexit_clears_client() -> None:
    """__aexit__ clears the cached client reference."""
    client = FakeMCPClient(_tools=[_make_tool()])
    cap = McpServerCap(config=_make_config(), session_pool=FakeSessionPool(client))

    # Force client creation
    await cap.list_tools()
    assert cap._client is not None

    await cap.__aexit__(None, None, None)
    assert cap._client is None
    assert len(cap._change_queues) == 0


# ---------------------------------------------------------------------------
# get_instructions test
# ---------------------------------------------------------------------------


def test_get_instructions_returns_none() -> None:
    """get_instructions() returns None — MCP servers don't provide prompt instructions."""
    cap = McpServerCap(
        config=_make_config(),
        session_pool=FakeSessionPool(FakeMCPClient()),
    )
    assert cap.get_instructions() is None
