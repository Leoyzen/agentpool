"""Tests for ACP initialize response MCP server status metadata.

Verifies that AgentPoolACPAgent.initialize() includes MCP server connection
statuses in the response's field_meta.mcp_servers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from acp import InitializeRequest
from agentpool.common_types import MCPServerStatus


if TYPE_CHECKING:
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


pytestmark = pytest.mark.asyncio


def _make_mock_provider(status: MCPServerStatus) -> Mock:
    """Create a mock MCPResourceProvider with the given status."""
    provider = Mock()
    provider.get_status = Mock(return_value=status)
    return provider


async def test_initialize_includes_mcp_servers_in_field_meta(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """initialize() response includes mcp_servers in field_meta."""
    pool = mock_acp_agent.agent_pool
    assert pool is not None

    providers = [
        _make_mock_provider(
            MCPServerStatus(
                name="srv-1",
                status="connected",
                server_type="stdio",
                display_name="Server One",
            ),
        ),
        _make_mock_provider(
            MCPServerStatus(
                name="srv-2",
                status="error",
                server_type="sse",
                display_name="Server Two",
                error="Connection refused",
            ),
        ),
    ]
    pool.mcp.get_mcp_providers = Mock(return_value=providers)

    response = await mock_acp_agent.initialize(
        InitializeRequest(protocol_version=1),
    )

    assert response.field_meta is not None
    assert "mcp_servers" in response.field_meta
    servers = response.field_meta["mcp_servers"]
    assert len(servers) == 2
    assert servers[0]["name"] == "srv-1"
    assert servers[0]["status"] == "connected"
    assert servers[0]["server_type"] == "stdio"
    assert servers[1]["name"] == "srv-2"
    assert servers[1]["status"] == "error"
    assert servers[1]["error"] == "Connection refused"


async def test_initialize_field_meta_none_when_no_providers(
    mock_acp_agent: AgentPoolACPAgent,
) -> None:
    """initialize() response has field_meta None when no MCP servers configured."""
    pool = mock_acp_agent.agent_pool
    assert pool is not None
    pool.mcp.get_mcp_providers = Mock(return_value=[])

    response = await mock_acp_agent.initialize(
        InitializeRequest(protocol_version=1),
    )

    assert response.field_meta is None
