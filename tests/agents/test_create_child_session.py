"""Tests for AgentContext.create_child_session() convenience API."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentContext
from agentpool.sessions import SessionData
from agentpool.sessions.store import MemorySessionStore


@pytest.fixture
def mock_node() -> MagicMock:
    """Create a mock MessageNode with session_id."""
    node = MagicMock()
    node.name = "parent_agent"
    node.session_id = "ses_parent_abc123"
    node.agent_pool = None  # Will be set per-test
    return node


def _make_mock_session_pool(store: MemorySessionStore) -> MagicMock:
    """Create a mock session_pool that persists via MemorySessionStore."""
    session_pool = MagicMock()

    async def mock_create_session(
        *,
        session_id: str,
        agent_name: str,
        parent_session_id: str | None = None,
        agent_type: str = "native",
        **kwargs: object,
    ) -> MagicMock:
        parent_data = None
        if parent_session_id:
            parent_data = await store.load(parent_session_id)
        session_data = SessionData(
            session_id=session_id,
            agent_name=agent_name,
            parent_id=parent_session_id,
            agent_type=agent_type,
            project_id=parent_data.project_id if parent_data else None,
            cwd=parent_data.cwd if parent_data else None,
        )
        await store.save(session_data)
        return MagicMock(session_id=session_id)

    session_pool.create_session = mock_create_session
    return session_pool


async def test_create_child_session_with_pool(mock_node: MagicMock) -> None:
    """When pool is available, create_child_session delegates to session_pool."""
    store = MemorySessionStore()
    mock_pool = MagicMock()
    mock_pool.manifest.name = "test_pool"
    mock_pool.session_pool = _make_mock_session_pool(store)

    mock_node.agent_pool = mock_pool

    # Persist the parent session so the manager can inherit project_id/cwd
    parent = SessionData(
        session_id="ses_parent_abc123",
        agent_name="coordinator",
        project_id="proj_42",
        cwd="/home/user/project",
    )

    ctx = AgentContext(node=mock_node)

    async with store:
        await store.save(parent)
        child_id = await ctx.create_child_session(
            agent_name="coder",
            agent_type="native",
        )

    # Verify child was persisted with correct fields
    child = await store.load(child_id)
    assert child is not None
    assert child.parent_id == "ses_parent_abc123"
    assert child.agent_name == "coder"
    assert child.agent_type == "native"
    assert child.project_id == "proj_42"
    assert child.cwd == "/home/user/project"


async def test_create_child_session_with_explicit_parent(mock_node: MagicMock) -> None:
    """When parent_session_id is provided explicitly, it overrides node.session_id."""
    store = MemorySessionStore()
    mock_pool = MagicMock()
    mock_pool.manifest.name = "test_pool"
    mock_pool.session_pool = _make_mock_session_pool(store)

    mock_node.agent_pool = mock_pool

    # Persist a different parent
    other_parent = SessionData(
        session_id="ses_other_parent",
        agent_name="router",
        project_id="proj_99",
        cwd="/tmp/workspace",
    )

    ctx = AgentContext(node=mock_node)

    async with store:
        await store.save(other_parent)
        child_id = await ctx.create_child_session(
            agent_name="analyst",
            agent_type="acp",
            parent_session_id="ses_other_parent",
        )

    child = await store.load(child_id)
    assert child is not None
    assert child.parent_id == "ses_other_parent"
    assert child.agent_name == "analyst"
    assert child.agent_type == "acp"
    assert child.project_id == "proj_99"
    assert child.cwd == "/tmp/workspace"


async def test_create_child_session_no_pool(mock_node: MagicMock) -> None:
    """When no pool is available, create_child_session falls back to generate_session_id."""
    mock_node.agent_pool = None

    ctx = AgentContext(node=mock_node)
    child_id = await ctx.create_child_session(
        agent_name="coder",
        agent_type="native",
    )

    # Should return a non-empty generated ID without persistence
    assert child_id is not None
    assert len(child_id) > 0
    assert child_id.startswith("ses_")


async def test_create_child_session_pool_without_sessions(mock_node: MagicMock) -> None:
    """When pool exists but session_pool is None, falls back to generate_session_id."""
    mock_pool = MagicMock()
    mock_pool.session_pool = None

    mock_node.agent_pool = mock_pool

    ctx = AgentContext(node=mock_node)
    child_id = await ctx.create_child_session(
        agent_name="coder",
        agent_type="native",
    )

    assert child_id is not None
    assert len(child_id) > 0
    assert child_id.startswith("ses_")


async def test_create_child_session_no_node_session_id(mock_node: MagicMock) -> None:
    """When node has no session_id and no explicit parent, fallback to generate_session_id."""
    mock_node.session_id = None
    store = MemorySessionStore()
    mock_pool = MagicMock()
    mock_pool.manifest.name = "test_pool"
    mock_pool.session_pool = _make_mock_session_pool(store)

    mock_node.agent_pool = mock_pool

    ctx = AgentContext(node=mock_node)
    child_id = await ctx.create_child_session(
        agent_name="coder",
        agent_type="native",
    )

    # With no effective parent (node.session_id is None), the method
    # falls back to generate_session_id() since create_session
    # requires a non-None parent.
    assert child_id is not None
    assert len(child_id) > 0
