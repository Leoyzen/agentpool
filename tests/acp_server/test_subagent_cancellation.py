"""Tests for foreground child session cancellation propagation in ACPSession (T9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.session import ACPSession


@pytest.fixture
def agent_pool_with_agent() -> tuple[AgentPool, Agent]:
    """Create a pool with a simple test agent."""
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    return pool, agent


@pytest.fixture
def acp_session(agent_pool_with_agent: tuple[AgentPool, Agent]) -> ACPSession:
    """Create an ACPSession with mocked dependencies for unit testing."""
    _pool, agent = agent_pool_with_agent
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()

    session = ACPSession(
        session_id="test-session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
    )

    # Mock acp_env to avoid real cleanup errors in close()
    session.acp_env = MagicMock()
    session.acp_env.__aexit__ = AsyncMock()

    # Mock manager with cancel_session
    mock_manager = MagicMock()
    mock_manager.cancel_session = AsyncMock()
    session.manager = mock_manager

    # Mock agent.interrupt to avoid side effects
    agent.interrupt = AsyncMock()  # type: ignore[method-assign]

    return session


async def test_cancel_cancels_foreground_children(acp_session: ACPSession):
    """cancel() should cancel all foreground child sessions via the manager."""
    session = acp_session
    session._foreground_children = {"child-1", "child-2"}

    await session.cancel()

    assert session._cancelled is True
    assert session.manager is not None
    assert session.manager.cancel_session.call_count == 2  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("child-1")  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("child-2")  # type: ignore[attr-defined]


async def test_background_children_survive_parent_cancellation(acp_session: ACPSession):
    """Background child sessions should NOT be cancelled when parent is cancelled."""
    session = acp_session
    # Only foreground children are tracked in _foreground_children
    session._foreground_children = {"foreground-child"}
    # A background child would never be added to _foreground_children

    await session.cancel()

    assert session.manager is not None
    # Only foreground child should be cancelled
    session.manager.cancel_session.assert_called_once_with("foreground-child")  # type: ignore[attr-defined]


async def test_close_cancels_foreground_children(acp_session: ACPSession):
    """close() should cancel all foreground child sessions before cleanup."""
    session = acp_session
    session._foreground_children = {"child-a", "child-b"}

    await session.close()

    assert session.manager is not None
    assert session.manager.cancel_session.call_count == 2  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("child-a")  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("child-b")  # type: ignore[attr-defined]


async def test_cancel_reads_foreground_children_from_active_converter(acp_session: ACPSession):
    """cancel() should read foreground children from the active event converter."""
    session = acp_session
    # Set up an active converter with foreground children
    converter = ACPEventConverter()
    converter._foreground_children = {"converter-child-1", "converter-child-2"}
    session._current_converter = converter

    await session.cancel()

    assert session.manager is not None
    assert session.manager.cancel_session.call_count == 2  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("converter-child-1")  # type: ignore[attr-defined]
    session.manager.cancel_session.assert_any_call("converter-child-2")  # type: ignore[attr-defined]


async def test_cancel_skips_when_no_manager(acp_session: ACPSession):
    """cancel() should not fail when manager is None."""
    session = acp_session
    session.manager = None
    session._foreground_children = {"orphan-child"}

    # Should not raise
    await session.cancel()

    assert session._cancelled is True


async def test_close_skips_when_no_manager(acp_session: ACPSession):
    """close() should not fail when manager is None."""
    session = acp_session
    session.manager = None
    session._foreground_children = {"orphan-child"}

    # Should not raise
    await session.close()


async def test_cancel_uses_list_to_avoid_mutation_during_iteration(acp_session: ACPSession):
    """cancel() should use list() to avoid mutation-during-iteration issues."""
    session = acp_session
    session._foreground_children = {"child-1", "child-2", "child-3"}

    # Make cancel_session mutate the set to simulate a side effect
    async def side_effect_cancel(child_id: str) -> None:
        session._foreground_children.discard(child_id)

    assert session.manager is not None
    session.manager.cancel_session.side_effect = side_effect_cancel  # type: ignore[attr-defined]

    # Should not raise RuntimeError about set size changed during iteration
    await session.cancel()

    assert len(session._foreground_children) == 0


async def test_close_uses_list_to_avoid_mutation_during_iteration(acp_session: ACPSession):
    """close() should use list() to avoid mutation-during-iteration issues."""
    session = acp_session
    session._foreground_children = {"child-1", "child-2", "child-3"}

    async def side_effect_cancel(child_id: str) -> None:
        session._foreground_children.discard(child_id)

    assert session.manager is not None
    session.manager.cancel_session.side_effect = side_effect_cancel  # type: ignore[attr-defined]

    # Should not raise RuntimeError about set size changed during iteration
    await session.close()

    assert len(session._foreground_children) == 0


async def test_manager_cancel_session_delegates_to_session_cancel():
    """ACPSessionManager.cancel_session() should delegate to the session's cancel()."""
    from agentpool_server.acp_server.session_manager import ACPSessionManager

    pool = AgentPool()
    manager = ACPSessionManager(pool=pool)
    mock_session = MagicMock()
    mock_session.cancel = AsyncMock()
    manager._active["session-1"] = mock_session

    await manager.cancel_session("session-1")

    mock_session.cancel.assert_awaited_once()


async def test_manager_cancel_session_is_noop_for_missing_session():
    """ACPSessionManager.cancel_session() should be a no-op for non-existent sessions."""
    from agentpool_server.acp_server.session_manager import ACPSessionManager

    pool = AgentPool()
    manager = ACPSessionManager(pool=pool)

    # Should not raise
    await manager.cancel_session("non-existent-session")


async def test_background_children_survive_parent_close(acp_session: ACPSession):
    """Background child sessions should NOT be cancelled when parent is closed."""
    session = acp_session
    # Only foreground children are tracked in _foreground_children
    session._foreground_children = {"foreground-child"}
    # A background child would never be added to _foreground_children

    await session.close()

    assert session.manager is not None
    # Only foreground child should be cancelled
    session.manager.cancel_session.assert_called_once_with("foreground-child")  # type: ignore[attr-defined]


async def test_background_mode_advertised_in_capabilities():
    """Phase 2: background=True and prompt_delegation=True must be advertised."""
    from acp.schema import InitializeRequest
    from agentpool import Agent
    from agentpool.delegation import AgentPool
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent

    pool = AgentPool()
    agent = Agent.from_callback(
        name="test_agent",
        callback=lambda msg: f"Test: {msg}",
        agent_pool=pool,
    )
    pool.register("test_agent", agent)

    mock_connection = MagicMock()
    acp_agent = AgentPoolACPAgent(client=mock_connection, default_agent=agent)
    acp_agent._initialized = False

    request = InitializeRequest(protocol_version=1)
    response = await acp_agent.initialize(request)

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.subagents is not None
    assert response.agent_capabilities.subagents.prompt_delegation is True
    assert response.agent_capabilities.subagents.background is True


async def test_cancel_preserves_existing_interrupt_behavior(acp_session: ACPSession):
    """cancel() should still call agent.interrupt() and set _cancelled flag."""
    session = acp_session

    await session.cancel()

    assert session._cancelled is True
    session.agent.interrupt.assert_awaited_once()  # type: ignore[attr-defined]
