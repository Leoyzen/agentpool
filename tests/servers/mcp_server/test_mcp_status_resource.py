"""Tests for the agentpool://mcp-servers/status MCP resource.

Tests that the MCPServer registers a resource exposing MCP server
connection statuses as JSON.
"""

from __future__ import annotations

import json
from unittest.mock import Mock

import pytest

from agentpool.common_types import MCPServerStatus


pytestmark = pytest.mark.asyncio


def _make_mock_pool_with_providers(
    providers: list[MCPResourceProviderStub],
) -> Mock:
    """Create a mock AgentPool with the given MCP providers."""
    pool = Mock()
    mcp_manager = Mock()
    mcp_manager.get_mcp_providers = Mock(return_value=providers)
    pool.mcp = mcp_manager
    return pool


class MCPResourceProviderStub:
    """Minimal stub matching the interface used by the resource handler."""

    def __init__(self, status: MCPServerStatus) -> None:
        self._status = status

    def get_status(self) -> MCPServerStatus:
        return self._status


async def test_mcp_status_resource_returns_json() -> None:
    """The status resource returns JSON with server connection info."""
    from agentpool_config.pool_server import MCPPoolServerConfig
    from agentpool_server.mcp_server.server import MCPServer

    providers = [
        MCPResourceProviderStub(
            MCPServerStatus(
                name="srv-1",
                status="connected",
                server_type="stdio",
                display_name="Server One",
            ),
        ),
        MCPResourceProviderStub(
            MCPServerStatus(
                name="srv-2",
                status="error",
                server_type="sse",
                display_name="Server Two",
                error="Connection refused",
            ),
        ),
    ]
    pool = _make_mock_pool_with_providers(providers)
    config = MCPPoolServerConfig(enabled=True, transport="stdio")
    server = MCPServer(pool, config)

    server._register_mcp_status_resource()

    fastmcp = server.fastmcp
    resources = await fastmcp.list_resources()
    status_resource = next(
        (r for r in resources if str(r.uri) == "agentpool://mcp-servers/status"),
        None,
    )
    assert status_resource is not None
    assert status_resource.mime_type == "application/json"
    read_result = await fastmcp.read_resource(
        "agentpool://mcp-servers/status",
        run_middleware=False,
    )
    content = read_result.contents[0].content
    data = json.loads(content)
    assert "servers" in data
    servers = data["servers"]
    assert len(servers) == 2
    assert servers[0]["name"] == "srv-1"
    assert servers[0]["status"] == "connected"
    assert servers[0]["server_type"] == "stdio"
    assert servers[1]["name"] == "srv-2"
    assert servers[1]["status"] == "error"
    assert servers[1]["error"] == "Connection refused"


async def test_mcp_status_resource_empty_when_no_providers() -> None:
    """The status resource returns empty servers list when no providers exist."""
    from agentpool_config.pool_server import MCPPoolServerConfig
    from agentpool_server.mcp_server.server import MCPServer

    pool = _make_mock_pool_with_providers([])
    config = MCPPoolServerConfig(enabled=True, transport="stdio")
    server = MCPServer(pool, config)

    server._register_mcp_status_resource()

    read_result = await server.fastmcp.read_resource(
        "agentpool://mcp-servers/status",
        run_middleware=False,
    )
    data = json.loads(read_result.contents[0].content)
    assert data["servers"] == []
