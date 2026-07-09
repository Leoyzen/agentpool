"""Tests for MCPCapability — wraps a single MCP server as AbstractCapability + ResourceSource."""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from mcp.types import (
    BlobResourceContents,
    Resource as MCPResource,
    TextResourceContents,
)
from pydantic_ai.capabilities import AbstractCapability
import pytest

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.mcp_capability import MCPCapability
from agentpool.capabilities.resource_source import (
    ResourceNotFoundError,
    ResourceSource,
)


if TYPE_CHECKING:
    from typing import Self

    from pydantic_ai.tools import AgentDepsT


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


@dataclass
class FakeMCPClient:
    """Async mock of MCPClient with resource + lifecycle methods."""

    server_name: str = "test_server"
    _resources: list[MCPResource] = field(default_factory=list)
    _read_results: dict[str, list[TextResourceContents | BlobResourceContents]] = field(
        default_factory=dict
    )
    _connected: bool = False
    tool_change_callback: Any = None

    @property
    def config(self) -> Any:
        mock_config = MagicMock()
        mock_config.name = self.server_name
        return mock_config

    @property
    def _tool_change_callback(self) -> Any:
        return self.tool_change_callback

    @_tool_change_callback.setter
    def _tool_change_callback(self, value: Any) -> None:
        self.tool_change_callback = value

    async def __aenter__(self) -> Self:
        self._connected = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self._connected = False

    async def list_resources(self) -> list[MCPResource]:
        if not self._connected:
            raise RuntimeError("Not connected")
        return list(self._resources)

    async def read_resource(
        self,
        uri: str,
    ) -> list[TextResourceContents | BlobResourceContents]:
        if not self._connected:
            raise RuntimeError("Not connected")
        if uri not in self._read_results:
            raise RuntimeError(f"Resource not found: {uri}")
        return self._read_results[uri]

    async def trigger_tool_change(self) -> None:
        """Simulate MCP server sending notifications/tools/list_changed."""
        if self.tool_change_callback is not None:
            await self.tool_change_callback()


def _make_client(
    *,
    server_name: str = "test_server",
    resources: list[MCPResource] | None = None,
    read_results: dict[str, list[TextResourceContents | BlobResourceContents]] | None = None,
) -> FakeMCPClient:
    """Build a FakeMCPClient with pre-populated resource data."""
    return FakeMCPClient(
        server_name=server_name,
        _resources=resources or [],
        _read_results=read_results or {},
    )


def _make_capability(
    *,
    server_name: str = "test_server",
    resources: list[MCPResource] | None = None,
    read_results: dict[str, list[TextResourceContents | BlobResourceContents]] | None = None,
    name: str | None = None,
) -> tuple[MCPCapability[AgentDepsT], FakeMCPClient]:
    """Build an MCPCapability with its underlying fake client."""
    client = _make_client(
        server_name=server_name,
        resources=resources,
        read_results=read_results,
    )
    cap = MCPCapability(client, name=name)  # type: ignore[arg-type]
    return cap, client


# ---------------------------------------------------------------------------
# isinstance tests
# ---------------------------------------------------------------------------


def test_is_abstract_capability() -> None:
    """MCPCapability is an instance of AbstractCapability."""
    cap, _ = _make_capability()
    assert isinstance(cap, AbstractCapability)


def test_is_resource_source() -> None:
    """MCPCapability is an instance of ResourceSource (structural typing)."""
    cap, _ = _make_capability()
    assert isinstance(cap, ResourceSource)


# ---------------------------------------------------------------------------
# name property
# ---------------------------------------------------------------------------


def test_name_defaults_to_server_name() -> None:
    """Capability name defaults to the MCP server config name."""
    cap, _ = _make_capability(server_name="my_server")
    assert cap.name == "my_server"


def test_name_override() -> None:
    """Capability name can be overridden."""
    cap, _ = _make_capability(server_name="my_server", name="custom_cap")
    assert cap.name == "custom_cap"


# ---------------------------------------------------------------------------
# ResourceSource.list() — URI scheme
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_returns_mcp_uri_scheme() -> None:
    """list() returns resources with mcp://{server_name}/{path} URIs."""
    resources = [
        MCPResource(uri="file:///docs/readme.md", name="readme"),
        MCPResource(uri="config://app/settings", name="settings"),
    ]
    cap, _ = _make_capability(resources=resources)
    async with cap:
        result = await cap.list()

    assert len(result) == 2
    assert all(r.uri.startswith("mcp://test_server/") for r in result)
    assert result[0].uri == "mcp://test_server/file:///docs/readme.md"
    assert result[1].uri == "mcp://test_server/config://app/settings"


@pytest.mark.asyncio
async def test_list_preserves_name_and_mime_type() -> None:
    """list() preserves name and mime_type from MCP resources."""
    resources = [
        MCPResource(
            uri="file:///data.json",
            name="data.json",
            description="JSON data",
            mimeType="application/json",
        ),
    ]
    cap, _ = _make_capability(resources=resources)
    async with cap:
        result = await cap.list()

    assert len(result) == 1
    res = result[0]
    assert res.name == "data.json"
    assert res.mime_type == "application/json"
    assert res.description == "JSON data"


@pytest.mark.asyncio
async def test_list_empty_when_no_resources() -> None:
    """list() returns empty list when server has no resources."""
    cap, _ = _make_capability(resources=[])
    async with cap:
        result = await cap.list()

    assert result == []


@pytest.mark.asyncio
async def test_list_default_mime_type() -> None:
    """list() uses application/octet-stream when mimeType is None."""
    resources = [MCPResource(uri="file:///raw", name="raw")]
    cap, _ = _make_capability(resources=resources)
    async with cap:
        result = await cap.list()

    assert result[0].mime_type == "application/octet-stream"


# ---------------------------------------------------------------------------
# ResourceSource.read(uri) — strips prefix, calls MCP read
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_text_resource() -> None:
    """read() strips mcp:// prefix and returns text content."""
    read_results = {
        "file:///docs/readme.md": [
            TextResourceContents(
                uri="file:///docs/readme.md",
                text="Hello world",
            ),
        ],
    }
    cap, _ = _make_capability(read_results=read_results)
    async with cap:
        content = await cap.read("mcp://test_server/file:///docs/readme.md")

    assert content.uri == "mcp://test_server/file:///docs/readme.md"
    assert content.content == "Hello world"
    assert content.mime_type == "application/octet-stream"


@pytest.mark.asyncio
async def test_read_blob_resource() -> None:
    """read() returns binary content for blob resources (base64-decoded)."""
    blob_data = b"\x89PNG\r\n\x1a\n"
    blob_b64 = base64.b64encode(blob_data).decode("ascii")
    read_results = {
        "file:///image.png": [
            BlobResourceContents(
                uri="file:///image.png",
                blob=blob_b64,
                mimeType="image/png",
            ),
        ],
    }
    cap, _ = _make_capability(read_results=read_results)
    async with cap:
        content = await cap.read("mcp://test_server/file:///image.png")

    assert content.content == blob_data
    assert content.mime_type == "image/png"


@pytest.mark.asyncio
async def test_read_with_mime_type() -> None:
    """read() preserves mime_type from the MCP response."""
    read_results = {
        "file:///data.json": [
            TextResourceContents(
                uri="file:///data.json",
                text='{"key": 1}',
                mimeType="application/json",
            ),
        ],
    }
    cap, _ = _make_capability(read_results=read_results)
    async with cap:
        content = await cap.read("mcp://test_server/file:///data.json")

    assert content.content == '{"key": 1}'
    assert content.mime_type == "application/json"


@pytest.mark.asyncio
async def test_read_unknown_uri_raises_not_found() -> None:
    """read() raises ResourceNotFoundError for unknown URIs."""
    cap, _ = _make_capability()
    async with cap:
        with pytest.raises(ResourceNotFoundError):
            await cap.read("mcp://test_server/file:///nonexistent")


@pytest.mark.asyncio
async def test_read_uri_without_prefix_raises_not_found() -> None:
    """read() raises ResourceNotFoundError for URIs without mcp:// prefix."""
    cap, _ = _make_capability()
    async with cap:
        with pytest.raises(ResourceNotFoundError):
            await cap.read("file:///some/path")


@pytest.mark.asyncio
async def test_read_uri_wrong_server_raises_not_found() -> None:
    """read() raises ResourceNotFoundError when server name doesn't match."""
    cap, _ = _make_capability(server_name="server_a")
    async with cap:
        with pytest.raises(ResourceNotFoundError):
            await cap.read("mcp://server_b/file:///path")


# ---------------------------------------------------------------------------
# ResourceSource.exists(uri)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_exists_true_for_known_resource() -> None:
    """exists() returns True for a resource that the server has."""
    resources = [MCPResource(uri="file:///config.toml", name="config")]
    cap, _ = _make_capability(resources=resources)
    async with cap:
        result = await cap.exists("mcp://test_server/file:///config.toml")

    assert result is True


@pytest.mark.asyncio
async def test_exists_false_for_unknown_resource() -> None:
    """exists() returns False for a resource the server doesn't have."""
    cap, _ = _make_capability(resources=[])
    async with cap:
        result = await cap.exists("mcp://test_server/file:///missing")

    assert result is False


@pytest.mark.asyncio
async def test_exists_false_for_wrong_prefix() -> None:
    """exists() returns False for URIs without the correct mcp:// prefix."""
    resources = [MCPResource(uri="file:///x", name="x")]
    cap, _ = _make_capability(resources=resources)
    async with cap:
        result = await cap.exists("file:///x")

    assert result is False


@pytest.mark.asyncio
async def test_exists_false_for_wrong_server() -> None:
    """exists() returns False when server name doesn't match."""
    resources = [MCPResource(uri="file:///x", name="x")]
    cap, _ = _make_capability(resources=resources, server_name="server_a")
    async with cap:
        result = await cap.exists("mcp://server_b/file:///x")

    assert result is False


# ---------------------------------------------------------------------------
# on_change() — method exists, satisfies ResourceSource protocol
# ---------------------------------------------------------------------------


def test_on_change_method_exists() -> None:
    """on_change() is callable (satisfies ResourceSource protocol structurally)."""
    cap, _ = _make_capability()
    # on_change() is an async generator function — calling it returns
    # an async generator without starting execution. The method's existence
    # satisfies ResourceSource structurally (verified by isinstance above).
    stream = cap.on_change()
    assert stream is not None


# ---------------------------------------------------------------------------
# AbstractCapability.on_change() — ChangeEvent for tools_changed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_capability_on_change_yields_tools_changed() -> None:
    """on_change() yields ChangeEvent(kind='tools_changed')."""
    cap, client = _make_capability()
    async with cap:
        gen = cap.on_change()
        # Start consuming the generator in a background task
        task = asyncio.create_task(gen.__anext__())
        # Give the generator time to start
        await asyncio.sleep(0.05)
        # Trigger the tool change callback
        await client.trigger_tool_change()
        # Wait for the event
        event = await asyncio.wait_for(task, timeout=2.0)
        assert isinstance(event, ChangeEvent)
        assert event.kind == "tools_changed"
        assert event.capability_name == "test_server"
        # Close the generator
        await gen.aclose()


@pytest.mark.asyncio
async def test_capability_on_change_multiple_events() -> None:
    """on_change() yields multiple ChangeEvents for successive tool changes."""
    cap, client = _make_capability()
    async with cap:
        gen = cap.on_change()
        # First event
        task1 = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.05)
        await client.trigger_tool_change()
        event1 = await asyncio.wait_for(task1, timeout=2.0)

        # Second event
        task2 = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.05)
        await client.trigger_tool_change()
        event2 = await asyncio.wait_for(task2, timeout=2.0)

        assert event1.kind == "tools_changed"
        assert event2.kind == "tools_changed"
        await gen.aclose()


@pytest.mark.asyncio
async def test_capability_on_change_restores_callback() -> None:
    """on_change() restores the original callback when generator is closed."""
    cap, client = _make_capability()
    original = client.tool_change_callback
    async with cap:
        gen = cap.on_change()
        await gen.aclose()
    # After closing, the original callback should be restored
    assert client.tool_change_callback is original


# ---------------------------------------------------------------------------
# get_toolset()
# ---------------------------------------------------------------------------


def test_get_toolset_returns_callable_or_none() -> None:
    """get_toolset() returns a ToolsetFunc or None."""
    cap, _ = _make_capability()
    toolset = cap.get_toolset()
    # MCPCapability returns a ToolsetFunc (async callable) that builds
    # an MCPToolset lazily per-run, or None if no tools available.
    assert toolset is None or callable(toolset)


# ---------------------------------------------------------------------------
# get_instructions()
# ---------------------------------------------------------------------------


def test_get_instructions_returns_none() -> None:
    """get_instructions() returns None — MCP instructions handled separately."""
    cap, _ = _make_capability()
    assert cap.get_instructions() is None


# ---------------------------------------------------------------------------
# Lifecycle: __aenter__ / __aexit__
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_aenter_connects_client() -> None:
    """__aenter__ connects the underlying MCP client."""
    cap, client = _make_capability()
    assert client._connected is False
    result = await cap.__aenter__()
    assert result is cap
    assert client._connected is True
    await cap.__aexit__(None, None, None)


@pytest.mark.asyncio
async def test_aexit_disconnects_client() -> None:
    """__aexit__ disconnects the underlying MCP client."""
    cap, client = _make_capability()
    await cap.__aenter__()
    assert client._connected is True
    await cap.__aexit__(None, None, None)
    assert client._connected is False


@pytest.mark.asyncio
async def test_async_context_manager_protocol() -> None:
    """MCPCapability works as an async context manager."""
    cap, client = _make_capability()
    async with cap:
        assert client._connected is True
    assert client._connected is False


@pytest.mark.asyncio
async def test_aexit_propagates_exception() -> None:
    """__aexit__ cleans up even when an exception occurs in the body."""
    cap, client = _make_capability()
    with pytest.raises(ValueError, match="boom"):  # noqa: PT012
        async with cap:
            assert client._connected is True
            raise ValueError("boom")
    assert client._connected is False


# ---------------------------------------------------------------------------
# URI prefix logic
# ---------------------------------------------------------------------------


def test_uri_prefix_uses_server_name() -> None:
    """URI prefix is mcp://{server_name}/."""
    cap, _ = _make_capability(server_name="my_server")
    assert cap._uri_prefix_str == "mcp://my_server/"


def test_uri_prefix_uses_name_override() -> None:
    """URI prefix uses the name override, not the server name."""
    cap, _ = _make_capability(server_name="config_name", name="display_name")
    assert cap._uri_prefix_str == "mcp://display_name/"


# ---------------------------------------------------------------------------
# Client property
# ---------------------------------------------------------------------------


def test_client_property_returns_wrapped_client() -> None:
    """The client property returns the wrapped MCPClient."""
    cap, client = _make_capability()
    assert cap.client is client
