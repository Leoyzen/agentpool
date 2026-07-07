"""Reproduction tests for stale MCP connection bug on session resume.

When an agent shares the pool's MCPManager (``_mcp_shared = True``), the
``_toolset_cache`` on that shared manager is never invalidated between
sessions.  On session resume, a new ``SessionConnectionPool`` is created
with fresh transports, but ``as_capability()`` returns the OLD cached
``MCPToolset`` (which holds the dead transport from the previous
WebSocket session).  The agentlet then tries to initialize MCP via the
dead transport, causing a 300-second timeout and silent run failure.

These tests reproduce the issue at the MCPManager level without requiring
real ACP connections or WebSocket infrastructure.
"""

from __future__ import annotations

from typing import Any, Self, cast
from unittest.mock import patch

import pytest

from agentpool.mcp_server.config_snapshot import McpConfigEntry, McpConfigSnapshot
from agentpool.mcp_server.manager import MCPManager
from agentpool.mcp_server.session_pool import SessionConnectionPool
from agentpool_config.mcp_server import AcpMCPServerConfig


# ---------------------------------------------------------------------------
# Fakes (matching test_mcpmanager_caching.py patterns)
# ---------------------------------------------------------------------------


class _FakeToolset:
    """Fake MCPToolset that captures the transport for inspection."""

    def __init__(self, **kwargs: Any) -> None:
        self.client: Any = kwargs.get("client")
        self.id = kwargs.get("id")
        self.is_running = False

    async def __aenter__(self) -> Self:
        self.is_running = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self.is_running = False


class _FakeMCP:
    """Fake MCP capability that exposes the underlying toolset."""

    def __init__(
        self,
        local: Any = None,
        allowed_tools: list[str] | None = None,
        id: str | None = None,  # noqa: A002
        **kwargs: Any,
    ) -> None:
        self.local = local
        self.allowed_tools = allowed_tools
        self.id = id


class _FakeTransport:
    """Fake transport with a label to distinguish session 1 vs session 2."""

    def __init__(self, label: str) -> None:
        self.label = label


# ---------------------------------------------------------------------------
# Test: stale toolset returned after session resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_session_resume_returns_stale_toolset_from_cache() -> None:
    """as_capability() returns stale toolset after session resume.

    This test reproduces the root cause: when a shared MCPManager caches
    a toolset for an ACP MCP server during session 1, and session 2
    (resume) provides a fresh transport via a new SessionConnectionPool,
    ``as_capability()`` still returns the OLD cached toolset with the
    OLD (dead) transport.

    Steps:
    1. Create a shared MCPManager (simulating pool-level manager).
    2. Session 1: create SessionConnectionPool, add transport A, build
       snapshot, call ``as_capability()`` -> toolset cached with transport A.
    3. Session 2 (resume): create NEW SessionConnectionPool, add transport B,
       build NEW snapshot, call ``as_capability()``.
    4. Assert: the returned toolset still holds transport A (stale),
       NOT transport B (fresh).
    """
    # Shared pool-level MCPManager (agent has _mcp_shared = True)
    manager = MCPManager(name="pool_mcp")

    # ACP MCP server config — deterministic client_id across sessions
    acp_config = AcpMCPServerConfig(name="scratchpad", acp_id="acp-server-1")
    client_id = acp_config.client_id  # "acp_acp-server-1"

    # --- Session 1 ---
    session1_pool = SessionConnectionPool(session_id="session-1")
    transport_a = _FakeTransport("session-1-transport")
    await session1_pool.add_transport(client_id, cast(Any, transport_a))

    snapshot1 = McpConfigSnapshot(
        session_configs=(
            McpConfigEntry(server_config=acp_config, source="session"),
        ),
    )

    with (
        patch("pydantic_ai.mcp.MCPToolset", _FakeToolset),
        patch("pydantic_ai.capabilities.MCP", _FakeMCP),
    ):
        caps1 = await manager.as_capability(
            snapshot=snapshot1, session_pool=session1_pool
        )

    assert len(caps1) == 1
    toolset1 = cast(_FakeToolset, caps1[0].local)
    assert toolset1.client is transport_a  # toolset holds session 1 transport
    assert client_id in manager._toolset_cache  # cached

    # --- Session 2 (resume) ---
    session2_pool = SessionConnectionPool(session_id="session-2")
    transport_b = _FakeTransport("session-2-transport")
    await session2_pool.add_transport(client_id, cast(Any, transport_b))

    snapshot2 = McpConfigSnapshot(
        session_configs=(
            McpConfigEntry(server_config=acp_config, source="session"),
        ),
    )

    with (
        patch("pydantic_ai.mcp.MCPToolset", _FakeToolset),
        patch("pydantic_ai.capabilities.MCP", _FakeMCP),
    ):
        caps2 = await manager.as_capability(
            snapshot=snapshot2, session_pool=session2_pool
        )

    assert len(caps2) == 1
    toolset2 = cast(_FakeToolset, caps2[0].local)

    # BUG: toolset2 is the SAME cached object as toolset1
    assert toolset2 is toolset1

    # BUG: toolset2 still holds transport_a (dead), not transport_b (fresh)
    assert toolset2.client is transport_a
    assert toolset2.client is not transport_b

    # The session2_pool has the correct new transport, but it's unused
    fresh_transport = await session2_pool.get_transport(acp_config)
    assert fresh_transport is cast(Any, transport_b)

    await manager.cleanup()
    await session1_pool.cleanup()
    await session2_pool.cleanup()


# ---------------------------------------------------------------------------
# Test: cache key is deterministic across sessions (explains why bug occurs)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_acp_client_id_is_deterministic_across_sessions() -> None:
    """AcpMCPServerConfig.client_id is deterministic.

    The ``client_id`` for ACP configs is ``f"acp_{acp_id}"``, which means
    the same ACP server always maps to the same ``_toolset_cache`` key.
    This is why the cache hit occurs on session resume — the key doesn't
    change even though the transport does.
    """
    config1 = AcpMCPServerConfig(name="server", acp_id="my-acp-1")
    config2 = AcpMCPServerConfig(name="server", acp_id="my-acp-1")

    assert config1.client_id == config2.client_id
    assert config1.client_id == "acp_my-acp-1"


# ---------------------------------------------------------------------------
# Test: SessionConnectionPool correctly provides fresh transport
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_session_pool_provides_fresh_transport_on_resume() -> None:
    """SessionConnectionPool correctly returns the new transport.

    This test confirms that SessionConnectionPool is NOT the source of
    the bug — it properly stores and returns the fresh transport. The
    bug is in MCPManager._toolset_cache bypassing the pool.
    """
    acp_config = AcpMCPServerConfig(name="scratchpad", acp_id="acp-server-1")
    client_id = acp_config.client_id

    # Session 1
    pool1 = SessionConnectionPool(session_id="s1")
    t1 = _FakeTransport("old")
    await pool1.add_transport(client_id, cast(Any, t1))
    result1 = await pool1.get_transport(acp_config)
    assert result1 is cast(Any, t1)

    # Session 2 (resume) — new pool, new transport
    pool2 = SessionConnectionPool(session_id="s2")
    t2 = _FakeTransport("new")
    await pool2.add_transport(client_id, cast(Any, t2))
    result2 = await pool2.get_transport(acp_config)
    assert result2 is cast(Any, t2)
    assert result2 is not cast(Any, t1)  # Fresh transport, not stale

    await pool1.cleanup()
    await pool2.cleanup()


# ---------------------------------------------------------------------------
# Test: multiple ACP servers all go stale
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_multiple_acp_servers_all_go_stale_on_resume() -> None:
    """When multiple ACP servers are configured, all toolsets go stale.

    The bug affects every ACP MCP server, not just one. Each server's
    toolset is cached by its deterministic client_id, so all of them
    return stale transports on session resume.
    """
    manager = MCPManager(name="pool_mcp")

    config_a = AcpMCPServerConfig(name="server_a", acp_id="acp-a")
    config_b = AcpMCPServerConfig(name="server_b", acp_id="acp-b")

    # Session 1
    pool1 = SessionConnectionPool(session_id="s1")
    t_a1 = _FakeTransport("a-s1")
    t_b1 = _FakeTransport("b-s1")
    await pool1.add_transport(config_a.client_id, cast(Any, t_a1))
    await pool1.add_transport(config_b.client_id, cast(Any, t_b1))

    snapshot1 = McpConfigSnapshot(
        session_configs=(
            McpConfigEntry(server_config=config_a, source="session"),
            McpConfigEntry(server_config=config_b, source="session"),
        ),
    )

    with (
        patch("pydantic_ai.mcp.MCPToolset", _FakeToolset),
        patch("pydantic_ai.capabilities.MCP", _FakeMCP),
    ):
        caps1 = await manager.as_capability(
            snapshot=snapshot1, session_pool=pool1
        )

    assert len(caps1) == 2

    # Session 2 (resume)
    pool2 = SessionConnectionPool(session_id="s2")
    t_a2 = _FakeTransport("a-s2")
    t_b2 = _FakeTransport("b-s2")
    await pool2.add_transport(config_a.client_id, cast(Any, t_a2))
    await pool2.add_transport(config_b.client_id, cast(Any, t_b2))

    snapshot2 = McpConfigSnapshot(
        session_configs=(
            McpConfigEntry(server_config=config_a, source="session"),
            McpConfigEntry(server_config=config_b, source="session"),
        ),
    )

    with (
        patch("pydantic_ai.mcp.MCPToolset", _FakeToolset),
        patch("pydantic_ai.capabilities.MCP", _FakeMCP),
    ):
        caps2 = await manager.as_capability(
            snapshot=snapshot2, session_pool=pool2
        )

    assert len(caps2) == 2

    stale_transports = {cast(Any, t_a1), cast(Any, t_b1)}
    fresh_transports = {cast(Any, t_a2), cast(Any, t_b2)}

    # Both toolsets are stale (hold session 1 transports)
    for cap in caps2:
        toolset = cast(_FakeToolset, cap.local)
        toolset_client = cast(Any, toolset.client)
        assert toolset_client in stale_transports, (
            f"Toolset holds stale transport {toolset_client.label}, "
            f"expected one of session-1 transports"
        )
        assert toolset_client not in fresh_transports, (
            f"Toolset should NOT hold session-2 transport "
            f"{toolset_client.label}"
        )

    await manager.cleanup()
    await pool1.cleanup()
    await pool2.cleanup()


# ---------------------------------------------------------------------------
# Test: disconnect_all clears cache (but is not called on session resume)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_disconnect_all_clears_cache_but_not_called_on_resume() -> None:
    """disconnect_all() clears _toolset_cache, but session resume doesn't call it.

    This test documents that ``disconnect_all()`` would fix the issue if
    called between sessions, but the session resume path does NOT call it.
    The shared MCPManager persists its cache across session boundaries.
    """
    manager = MCPManager(name="pool_mcp")

    acp_config = AcpMCPServerConfig(name="scratchpad", acp_id="acp-1")
    client_id = acp_config.client_id

    # Session 1 — populate cache
    pool1 = SessionConnectionPool(session_id="s1")
    t1 = _FakeTransport("s1")
    await pool1.add_transport(client_id, cast(Any, t1))

    snapshot = McpConfigSnapshot(
        session_configs=(
            McpConfigEntry(server_config=acp_config, source="session"),
        ),
    )

    with (
        patch("pydantic_ai.mcp.MCPToolset", _FakeToolset),
        patch("pydantic_ai.capabilities.MCP", _FakeMCP),
    ):
        await manager.as_capability(snapshot=snapshot, session_pool=pool1)

    assert len(manager._toolset_cache) == 1

    # disconnect_all would clear it...
    await manager.disconnect_all()
    assert len(manager._toolset_cache) == 0

    # ...but session resume path calls neither disconnect_all() nor
    # any cache invalidation. In production, close_session() only calls
    # agent.__aexit__() which calls agent.cleanup(), not pool.mcp.disconnect_all().
    # The shared pool-level MCPManager._toolset_cache is never touched.

    await pool1.cleanup()
