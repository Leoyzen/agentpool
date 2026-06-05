"""Tests for ACPSessionManager child-session path (RFC-0028 T13)."""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool.orchestrator.core import SessionPool
from agentpool.sessions import SessionData
from agentpool.sessions.store import MemorySessionStore
from agentpool_server.acp_server.session_manager import ACPSessionManager


def _make_pool_with_sessions() -> tuple[AgentPool, Agent, SessionPool, MemorySessionStore]:
    """Create a pool with a real SessionPool backed by MemorySessionStore."""
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)

    store = MemorySessionStore()
    session_pool = SessionPool(pool=pool, store=store)
    pool._session_pool = session_pool

    # Also wire up storage.generate_session_id for top-level path
    pool.storage.generate_session_id = MagicMock(return_value="session_top_001")  # type: ignore[assignment]

    return pool, agent, session_pool, store


def _make_acp_session_manager(pool: AgentPool) -> ACPSessionManager:
    """Create an ACPSessionManager with minimal mock ACP agent."""
    manager = ACPSessionManager(pool=pool)

    # Mock out ACPSession creation and initialization to avoid needing
    # a real ACP client and all the initialization machinery.
    with patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession:
        mock_session = MagicMock()
        mock_session.session_id = "session_top_001"
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

    return manager


async def test_top_level_session_has_no_parent():
    """Top-level ACP session (no parent_session_id) should have parent_id=None
    and a computed project_id."""
    pool, agent, sessions, store = _make_pool_with_sessions()

    manager = ACPSessionManager(pool=pool)
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    with (
        patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession,
        patch("agentpool_server.acp_server.session_manager.ClientCapabilities"),
    ):
        mock_session = MagicMock()
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

        session_id = await manager.create_session(
            agent=agent,
            cwd=tempfile.gettempdir(),
            client=mock_client,
            acp_agent=mock_acp_agent,
        )

    # Verify the session was persisted via store
    data = await store.load(session_id)
    assert data is not None
    assert data.parent_id is None
    assert data.project_id is not None
    assert data.agent_name == "test_agent"


async def test_child_session_inherits_parent_project_id():
    """Child ACP session (with parent_session_id) should inherit
    project_id and cwd from the parent session."""
    pool, agent, sessions, store = _make_pool_with_sessions()

    # Create a parent session in the store first
    from agentpool_storage.opencode_provider.helpers import compute_project_id

    parent_cwd = tempfile.gettempdir()
    parent_project_id = compute_project_id(parent_cwd)
    parent_data = SessionData(
        session_id="parent_session_001",
        agent_name="test_agent",
        cwd=parent_cwd,
        project_id=parent_project_id,
    )
    await store.save(parent_data)

    manager = ACPSessionManager(pool=pool)
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    with (
        patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession,
        patch("agentpool_server.acp_server.session_manager.ClientCapabilities"),
    ):
        mock_session = MagicMock()
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

        session_id = await manager.create_session(
            agent=agent,
            cwd="/some/other/cwd",  # Different cwd — should be overridden by parent's
            client=mock_client,
            acp_agent=mock_acp_agent,
            parent_session_id="parent_session_001",
        )

    # Load child session data from store
    child_data = await store.load(session_id)
    assert child_data is not None
    # Child should inherit parent's project_id
    assert child_data.project_id == parent_project_id
    # Child should inherit parent's cwd
    assert child_data.cwd == parent_cwd
    # Child should reference parent
    assert child_data.parent_id == "parent_session_001"
    # Agent type should be "acp"
    assert child_data.agent_type == "acp"


async def test_child_session_uses_effective_cwd_for_acp_session():
    """When creating a child ACP session, the ACPSession object should
    receive the inherited cwd, not the caller-provided cwd."""
    pool, agent, sessions, store = _make_pool_with_sessions()

    parent_cwd = tempfile.gettempdir()
    from agentpool_storage.opencode_provider.helpers import compute_project_id

    parent_project_id = compute_project_id(parent_cwd)
    parent_data = SessionData(
        session_id="parent_session_002",
        agent_name="test_agent",
        cwd=parent_cwd,
        project_id=parent_project_id,
    )
    await store.save(parent_data)

    manager = ACPSessionManager(pool=pool)
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    with (
        patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession,
        patch("agentpool_server.acp_server.session_manager.ClientCapabilities"),
    ):
        mock_session = MagicMock()
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

        await manager.create_session(
            agent=agent,
            cwd="/different/cwd",
            client=mock_client,
            acp_agent=mock_acp_agent,
            parent_session_id="parent_session_002",
        )

        # Verify ACPSession was constructed with the inherited cwd
        call_kwargs = MockSession.call_args
        assert call_kwargs.kwargs.get("cwd") == parent_cwd or call_kwargs[1].get("cwd") == parent_cwd


async def test_no_parent_session_id_preserves_existing_behavior():
    """When parent_session_id is None, the existing top-level behavior
    (compute project_id from cwd, direct SessionData save) is preserved."""
    pool, agent, sessions, store = _make_pool_with_sessions()

    manager = ACPSessionManager(pool=pool)
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    with (
        patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession,
        patch("agentpool_server.acp_server.session_manager.ClientCapabilities"),
    ):
        mock_session = MagicMock()
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

        cwd = tempfile.gettempdir()
        session_id = await manager.create_session(
            agent=agent,
            cwd=cwd,
            client=mock_client,
            acp_agent=mock_acp_agent,
            parent_session_id=None,
        )

    data = await store.load(session_id)
    assert data is not None
    assert data.parent_id is None
    assert data.project_id is not None
    # cwd should match what was provided
    assert data.cwd == cwd


async def test_child_session_without_pool_sessions_falls_back_to_top_level():
    """When pool.sessions is None but parent_session_id is provided,
    should fall back to top-level behavior."""
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    pool._session_pool = None

    pool.storage.generate_session_id = MagicMock(return_value="session_fallback_001")  # type: ignore[assignment]

    manager = ACPSessionManager(pool=pool)
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    with (
        patch("agentpool_server.acp_server.session_manager.ACPSession") as MockSession,
        patch("agentpool_server.acp_server.session_manager.ClientCapabilities"),
    ):
        mock_session = MagicMock()
        mock_session.register_update_callback = MagicMock()
        mock_session.initialize = AsyncMock()
        mock_session.initialize_mcp_servers = AsyncMock()
        MockSession.return_value = mock_session

        cwd = tempfile.gettempdir()
        session_id = await manager.create_session(
            agent=agent,
            cwd=cwd,
            client=mock_client,
            acp_agent=mock_acp_agent,
            parent_session_id="some_parent_id",  # Will be ignored since pool.sessions is None
        )

    # Should have used the top-level path (computed project_id, no parent_id)
    # We can't check the store since there's no store configured, but
    # the session_id should be from generate_session_id (top-level path)
    assert session_id == "session_fallback_001"
