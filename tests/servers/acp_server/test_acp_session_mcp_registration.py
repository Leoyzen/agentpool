"""TDD test: session MCP configs merged into agent MCPManager snapshot.

## Behavior

``ACPSession.initialize_mcp_servers()`` converts each MCP server to an
``McpConfigEntry`` and stores it in the agent's MCPManager session context
snapshot via ``update_session_snapshot()``.  For ACP-transport servers, the
transport is created and stored in the agent's ``_session_connection_pool``.

This replaces the old behavior of creating ``MCPResourceProvider`` instances
and registering them on ``agent.tools``.
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool.orchestrator.core import SessionPool
from agentpool.sessions.store import MemorySessionStore
from agentpool_server.acp_server.session import ACPSession


def _make_pool_with_session_pool() -> tuple[AgentPool, Agent, SessionPool]:
    """Create a real pool with SessionPool backed by MemorySessionStore."""
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})
    pool = AgentPool(manifest)

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    store = MemorySessionStore()
    session_pool = SessionPool(pool=pool, store=store)
    pool._session_pool = session_pool
    return pool, agent, session_pool


def _make_mock_http_mcp_server(name: str = "workspace-fs") -> Any:
    """Create a mock HttpMcpServer (non-ACP) for testing."""
    from acp.schema.mcp import HttpMcpServer

    return HttpMcpServer(name=name, url="http://localhost:9999/mcp")


@pytest.mark.unit
async def test_initialize_mcp_servers_registers_providers_on_agent() -> None:
    """After initialize_mcp_servers(), the MCPManager snapshot has session config entries.

    Given: An ACPSession with an HttpMcpServer.
    When: initialize_mcp_servers() is called.
    Then: The agent's MCPManager snapshot.session_configs contains an entry
          whose server_config.name matches the MCP server name.
    """
    _pool, _agent, session_pool = _make_pool_with_session_pool()
    await session_pool.start()

    session_id = "test-mcp-init-001"
    cwd = tempfile.gettempdir()

    await session_pool.create_session(session_id, agent_name="test_agent")
    session_agent = await session_pool.sessions.get_or_create_session_agent(session_id)

    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    acp_session = ACPSession(
        session_id=session_id,
        agent=session_agent,
        cwd=cwd,
        client=mock_client,
        mcp_servers=[_make_mock_http_mcp_server()],
        acp_agent=mock_acp_agent,
    )

    await acp_session.initialize_mcp_servers()

    # EXPECT: snapshot is set and contains the session config
    ctx = session_agent.mcp.get_session_context(session_id)
    assert ctx is not None and ctx.snapshot is not None, (
        "snapshot should be set after initialize_mcp_servers()"
    )
    session_configs = ctx.snapshot.session_configs
    assert len(session_configs) >= 1, "session_configs should contain at least one entry"
    config_names = [e.server_config.name for e in session_configs]
    assert "workspace-fs" in config_names, (
        "session_configs should contain the MCP server 'workspace-fs'"
    )

    await session_pool.shutdown()


@pytest.mark.unit
async def test_initialize_mcp_servers_with_no_servers_is_noop() -> None:
    """initialize_mcp_servers() with no servers does nothing.

    Given: An ACPSession with mcp_servers=None.
    When: initialize_mcp_servers() is called.
    Then: The MCPManager snapshot has no config entries (no changes).
    """
    _pool, _agent, session_pool = _make_pool_with_session_pool()
    await session_pool.start()

    session_id = "test-mcp-init-002"
    await session_pool.create_session(session_id, agent_name="test_agent")
    session_agent = await session_pool.sessions.get_or_create_session_agent(session_id)

    acp_session = ACPSession(
        session_id=session_id,
        agent=session_agent,
        cwd=tempfile.gettempdir(),
        client=MagicMock(),
        mcp_servers=None,
        acp_agent=MagicMock(),
    )

    await acp_session.initialize_mcp_servers()

    # snapshot should have no session configs (no servers to configure)
    ctx = session_agent.mcp.get_session_context(session_id)
    if ctx is not None and ctx.snapshot is not None:
        assert len(ctx.snapshot.session_configs) == 0, (
            "session_configs should be empty when no MCP servers are configured"
        )

    await session_pool.shutdown()


@pytest.mark.unit
async def test_initialize_mcp_servers_does_not_duplicate_providers() -> None:
    """Calling initialize_mcp_servers() twice does not duplicate config entries.

    Given: An ACPSession with an HttpMcpServer.
    When: initialize_mcp_servers() is called twice.
    Then: The MCPManager snapshot.session_configs contains exactly one entry
          for the server (deduplicated by client_id).
    """
    _pool, _agent, session_pool = _make_pool_with_session_pool()
    await session_pool.start()

    session_id = "test-mcp-init-003"
    await session_pool.create_session(session_id, agent_name="test_agent")
    session_agent = await session_pool.sessions.get_or_create_session_agent(session_id)

    acp_session = ACPSession(
        session_id=session_id,
        agent=session_agent,
        cwd=tempfile.gettempdir(),
        client=MagicMock(),
        mcp_servers=[_make_mock_http_mcp_server()],
        acp_agent=MagicMock(),
    )

    await acp_session.initialize_mcp_servers()
    # Second call — should deduplicate by client_id
    await acp_session.initialize_mcp_servers()

    ctx = session_agent.mcp.get_session_context(session_id)
    assert ctx is not None and ctx.snapshot is not None
    session_configs = ctx.snapshot.session_configs
    # EXPECT: only one entry for the server
    client_ids = [e.server_config.client_id for e in session_configs]
    assert len(client_ids) == len(set(client_ids)), (
        "session_configs should not contain duplicate client_ids"
    )

    await session_pool.shutdown()
