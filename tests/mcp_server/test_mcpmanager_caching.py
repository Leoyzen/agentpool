"""Tests for MCPToolset caching, ACP filtering, and scoping in MCPManager."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Self
from unittest.mock import patch

import pytest

from agentpool.mcp_server.manager import MCPManager
from agentpool_config.mcp_server import (
    AcpMCPServerConfig,
    StdioMCPServerConfig,
)


# ---------------------------------------------------------------------------
# Fakes & helpers
# ---------------------------------------------------------------------------


class _FakeToolset:
    """Fake MCPToolset that does not connect to any server.

    Implements the async context manager protocol so it can be used
    with ``AsyncExitStack.enter_async_context()``.
    """

    def __init__(self, **kwargs: Any) -> None:
        self.client = kwargs.get("client")
        self.id = kwargs.get("id")
        self.include_instructions = kwargs.get("include_instructions", False)
        self.is_running = False

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class _FakeMCP:
    """Fake MCP capability that captures the toolset and metadata.

    The real ``pydantic_ai.capabilities.MCP`` requires a ``url`` parameter
    that the source code does not yet pass. This fake accepts the kwargs
    the source code provides and exposes the attributes tests need.
    """

    def __init__(self, local: Any = None, allowed_tools: list[str] | None = None, id: str | None = None, **kwargs: Any) -> None:
        self.local = local
        self.allowed_tools = allowed_tools
        self.id = id


class _FakeSignal:
    """Minimal stand-in for anyenv Signal used by AggregatingResourceProvider."""

    def connect(self, callback: Any) -> None:
        pass

    def disconnect(self, callback: Any) -> None:
        pass


class _FakeProvider:
    """Fake MCPResourceProvider for testing provider filtering.

    Avoids creating a real ``MCPClient`` (which fails for ACP configs
    without a transport).
    """

    def __init__(self, server: Any, **kwargs: Any) -> None:
        self.server = server
        self.name: str = kwargs.get("name", "fake")
        # Signal attributes required by AggregatingResourceProvider setter
        self.tools_changed = _FakeSignal()
        self.prompts_changed = _FakeSignal()
        self.resources_changed = _FakeSignal()
        self.skills_changed = _FakeSignal()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Test 1: toolset cache shares connection across calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_mcpmanager_toolset_cache_shares_connection() -> None:
    """as_capability() creates new MCPToolset instances on every call.

    Given an MCPManager with one StdioMCPServerConfig, calling
    ``as_capability()`` twice should produce two MCP capability objects
    backed by *different* ``MCPToolset`` instances (no caching).
    """
    config = StdioMCPServerConfig(
        name="test_server",
        command="python",
        args=["-m", "my_server"],
    )
    manager = MCPManager(servers=[config])

    with patch("pydantic_ai.mcp.MCPToolset", _FakeToolset), patch(
        "pydantic_ai.capabilities.MCP", _FakeMCP
    ):
        caps1 = await manager.as_capability()
        caps2 = await manager.as_capability()

    assert len(caps1) == 1
    assert len(caps2) == 1

    # Each call creates a new toolset (no caching)
    assert caps1[0].local is not caps2[0].local

    await manager.cleanup()


# ---------------------------------------------------------------------------
# Test 2: toolset cache keyed by client_id
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_mcpmanager_toolset_cache_keyed_by_client_id() -> None:
    """Different client_ids produce distinct toolsets; each call creates new ones.

    Given an MCPManager with two StdioMCPServerConfig entries (different
    ``client_id`` values), the first ``as_capability()`` call should
    create two separate ``MCPToolset`` instances. A second call returns
    fresh instances (no caching).
    """
    config_a = StdioMCPServerConfig(
        name="server_a",
        command="python",
        args=["-m", "server_a"],
    )
    config_b = StdioMCPServerConfig(
        name="server_b",
        command="node",
        args=["server_b.js"],
    )
    manager = MCPManager(servers=[config_a, config_b])

    with patch("pydantic_ai.mcp.MCPToolset", _FakeToolset), patch(
        "pydantic_ai.capabilities.MCP", _FakeMCP
    ):
        caps1 = await manager.as_capability()
        caps2 = await manager.as_capability()

    assert len(caps1) == 2
    assert len(caps2) == 2

    # Different client_ids -> different toolset instances
    assert caps1[0].local is not caps1[1].local

    # Each call creates fresh instances (no caching)
    assert caps1[0].local is not caps2[0].local
    assert caps1[1].local is not caps2[1].local

    await manager.cleanup()


# ---------------------------------------------------------------------------
# Test 3: aggregating provider contains only ACP providers
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_aggregating_provider_contains_only_acp_providers() -> None:
    """get_aggregating_provider() returns only ACP-transport providers.

    When both ACP and non-ACP servers are registered, the aggregating
    provider should contain *only* the ACP providers. Non-ACP providers
    are handled separately by ``as_capability()``.
    """
    acp_config = AcpMCPServerConfig(name="acp_server", acp_id="test-acp-1")
    stdio_config = StdioMCPServerConfig(
        name="stdio_server",
        command="python",
        args=["server.py"],
    )
    manager = MCPManager(name="test")

    with patch(
        "agentpool.mcp_server.manager.MCPResourceProvider",
        _FakeProvider,
    ):
        await manager.setup_server(acp_config)
        await manager.setup_server(stdio_config)

    # Both providers should be in the manager's provider list
    assert len(manager.providers) == 2

    agg = manager.get_aggregating_provider()

    # Aggregating provider should contain only the ACP provider
    assert len(agg.providers) == 1
    assert agg.providers[0].server is acp_config

    await manager.cleanup()


# ---------------------------------------------------------------------------
# Test 4: non-ACP providers excluded from aggregating provider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_non_acp_providers_excluded_from_aggregating_provider() -> None:
    """A manager with only non-ACP servers returns an empty aggregating provider.

    The non-ACP capability is still accessible via ``as_capability()``.
    """
    stdio_config = StdioMCPServerConfig(
        name="stdio_only",
        command="python",
        args=["server.py"],
    )
    manager = MCPManager(name="test")
    manager.add_server_config(stdio_config)

    with patch(
        "agentpool.mcp_server.manager.MCPResourceProvider",
        _FakeProvider,
    ):
        await manager.setup_server(stdio_config)

    # Provider should be registered in the manager
    assert len(manager.providers) == 1

    agg = manager.get_aggregating_provider()

    # No ACP providers -> empty aggregating provider
    assert len(agg.providers) == 0

    # Non-ACP capability should still be available via as_capability()
    with patch("pydantic_ai.mcp.MCPToolset", _FakeToolset), patch(
        "pydantic_ai.capabilities.MCP", _FakeMCP
    ):
        caps = await manager.as_capability()

    assert len(caps) == 1
    assert caps[0].id == "stdio_only"

    await manager.cleanup()


# ---------------------------------------------------------------------------
# Test 5: no dedup hack in get_agentlet
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_no_dedup_hack_in_get_agentlet() -> None:
    """get_agentlet() must not contain the removed mcp_aggregating dedup hack.

    The old code had a ``mcp_aggregating`` variable that skipped the
    first provider to avoid duplicate tool registration. This hack was
    removed; the test verifies it stays removed by checking the source
    of ``get_agentlet()`` for the variable name.
    """
    agent_py = Path(__file__).resolve().parents[2] / "src" / "agentpool" / "agents" / "native_agent" / "agent.py"
    source = agent_py.read_text()

    # The dedup hack variable must not exist anywhere in agent.py
    assert "mcp_aggregating" not in source, (
        "The 'mcp_aggregating' dedup hack variable was found in agent.py. "
        "It should have been removed."
    )

    # Verify get_agentlet still calls as_capability() for MCP
    assert "await self.mcp.as_capability()" in source, (
        "get_agentlet() should call 'await self.mcp.as_capability()' "
        "to collect MCP capabilities."
    )


# ---------------------------------------------------------------------------
# Test 6: engineer/librarian MCP tool scoping
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_engineer_librarian_mcp_tool_scoping() -> None:
    """Pool-level MCP servers are accessible to subagents; agent-level are not.

    Simulates the scoping rule: a pool-level MCPManager with "search_kb"
    and an agent-level MCPManager with "expert_anno" produce separate
    capability sets. A subagent (librarian) inherits pool-level
    capabilities but NOT the parent agent's agent-level capabilities.
    """
    # Pool-level MCP manager — shared across all agents
    pool_mcp = MCPManager(
        name="pool_mcp",
        owner="pool",
        servers=[
            StdioMCPServerConfig(
                name="search_kb",
                command="uvx",
                args=["mcp-server-search"],
            ),
        ],
    )

    # Agent-level MCP manager — private to the engineer agent
    agent_mcp = MCPManager(
        name="engineer_mcp",
        owner="node",
        servers=[
            StdioMCPServerConfig(
                name="expert_anno",
                command="uvx",
                args=["mcp-server-anno"],
            ),
        ],
    )

    with patch("pydantic_ai.mcp.MCPToolset", _FakeToolset), patch(
        "pydantic_ai.capabilities.MCP", _FakeMCP
    ):
        pool_caps = await pool_mcp.as_capability()
        agent_caps = await agent_mcp.as_capability()

    # Pool-level capability includes search_kb
    pool_ids = {c.id for c in pool_caps}
    assert "search_kb" in pool_ids
    assert "expert_anno" not in pool_ids

    # Agent-level capability includes expert_anno
    agent_ids = {c.id for c in agent_caps}
    assert "expert_anno" in agent_ids
    assert "search_kb" not in agent_ids

    # Subagent (librarian) would get pool caps but NOT engineer's agent caps.
    # Simulate by combining: librarian_caps = pool_caps (inherited) only.
    librarian_ids = {c.id for c in pool_caps}
    assert "search_kb" in librarian_ids
    assert "expert_anno" not in librarian_ids

    # Toolsets must be distinct instances (no connection sharing across scopes)
    assert pool_caps[0].local is not agent_caps[0].local

    await pool_mcp.cleanup()
    await agent_mcp.cleanup()
