"""Tests for session cleanup semantics.

Validates that:
- Deleting a session releases its per-session agent (``__aexit__`` called,
  agent removed from registry)
- Server shutdown closes every created session agent
- Cleanup is safe for partially-initialized sessions (agent ``__aexit__``
  raises — should not prevent other cleanup)
"""

from __future__ import annotations

import tempfile
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_env() -> Mock:
    """Create a mock agent environment."""
    from upathtools.filesystems import AsyncLocalFileSystem

    env = Mock()
    fs = AsyncLocalFileSystem()
    env.get_fs = Mock(return_value=fs)
    env.cwd = "/tmp/test"
    return env


@pytest.fixture
def mock_pool() -> Mock:
    """Create a mock agent pool with minimal attributes."""
    pool = Mock()
    pool.manifest = Mock()
    pool.manifest.agents = {}
    pool.skill_commands = None
    return pool


@pytest.fixture
def shared_agent(mock_env: Mock, mock_pool: Mock) -> Mock:
    """Create the shared (default) mock agent."""
    agent = Mock()
    agent.name = "test-agent"
    agent.env = mock_env
    agent._input_provider = None
    agent.agent_pool = mock_pool
    agent.storage = None
    return agent


@pytest.fixture
def state(shared_agent: Mock) -> ServerState:
    """Create a ServerState with a shared mock agent.

    Patches ``_create_session_agent`` so each call returns a fresh mock
    agent with a trackable ``__aexit__`` method.
    """
    with tempfile.TemporaryDirectory(prefix="session-cleanup-test-") as tmpdir:
        st = ServerState(working_dir=tmpdir, agent=shared_agent)
        call_count = 0

        def _fake_create(session_id: str) -> Mock:
            nonlocal call_count
            call_count += 1
            agent = Mock()
            agent.name = f"session-agent-{call_count}"
            agent.session_id = session_id
            agent.__aexit__ = AsyncMock(return_value=False)
            return agent

        st._create_session_agent = _fake_create  # type: ignore[method-assign]
        yield st


# =============================================================================
# Test 1: Deleting a session releases its agent
# =============================================================================


async def test_delete_session_releases_agent(state: ServerState) -> None:
    """Deleting a session calls ``__aexit__`` on its agent and removes it
    from the registry.
    """
    agent = await state.get_or_create_agent("session-a")
    assert "session-a" in state._session_agents

    await state.remove_session_agent("session-a")

    # Agent's __aexit__ was called
    agent.__aexit__.assert_called_once_with(None, None, None)
    # Agent removed from registry
    assert "session-a" not in state._session_agents
    # Lock removed
    assert "session-a" not in state._session_agent_locks


# =============================================================================
# Test 2: Shutdown closes all session agents
# =============================================================================


async def test_shutdown_closes_all_session_agents(state: ServerState) -> None:
    """``cleanup_all_session_agents`` closes every registered agent and
    clears both registries.
    """
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    agent_c = await state.get_or_create_agent("session-c")
    assert len(state._session_agents) == 3

    await state.cleanup_all_session_agents()

    # Every agent had __aexit__ called
    agent_a.__aexit__.assert_called_once_with(None, None, None)
    agent_b.__aexit__.assert_called_once_with(None, None, None)
    agent_c.__aexit__.assert_called_once_with(None, None, None)
    # Both registries are empty
    assert len(state._session_agents) == 0
    assert len(state._session_agent_locks) == 0


# =============================================================================
# Test 3: Cleanup is safe for partially-initialized sessions
# =============================================================================


async def test_cleanup_safe_for_partial_init(state: ServerState) -> None:
    """If one agent's ``__aexit__`` raises, other agents are still cleaned
    up and the registry is still cleared.

    This simulates a partially-initialized session where the agent was
    registered but its internal state is broken.
    """
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")

    # Make agent_a's __aexit__ raise (simulates partial init)
    agent_a.__aexit__ = AsyncMock(side_effect=RuntimeError("broken agent"))

    # cleanup_all_session_agents should NOT raise, and agent_b should
    # still be cleaned up
    await state.cleanup_all_session_agents()

    # agent_a's __aexit__ was attempted
    agent_a.__aexit__.assert_called_once_with(None, None, None)
    # agent_b's __aexit__ was still called despite agent_a's failure
    agent_b.__aexit__.assert_called_once_with(None, None, None)
    # Registry is cleared regardless
    assert len(state._session_agents) == 0
    assert len(state._session_agent_locks) == 0


async def test_remove_session_agent_safe_when_aexit_raises(state: ServerState) -> None:
    """If a single agent's ``__aexit__`` raises during
    ``remove_session_agent``, the agent is still removed from the registry
    and the method does not propagate the exception.
    """
    agent = await state.get_or_create_agent("session-a")
    agent.__aexit__ = AsyncMock(side_effect=RuntimeError("broken agent"))

    # Should not raise
    await state.remove_session_agent("session-a")

    # Agent was still removed from registry
    assert "session-a" not in state._session_agents
    assert "session-a" not in state._session_agent_locks


async def test_cleanup_all_safe_with_mixed_failures(state: ServerState) -> None:
    """Multiple agents with some failing ``__aexit__`` — all are removed,
    all have ``__aexit__`` attempted.
    """
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    agent_c = await state.get_or_create_agent("session-c")

    # Two fail, one succeeds
    agent_a.__aexit__ = AsyncMock(side_effect=RuntimeError("a broken"))
    agent_b.__aexit__ = AsyncMock(return_value=False)  # succeeds
    agent_c.__aexit__ = AsyncMock(side_effect=ValueError("c broken"))

    await state.cleanup_all_session_agents()

    # All __aexit__ were attempted
    agent_a.__aexit__.assert_called_once_with(None, None, None)
    agent_b.__aexit__.assert_called_once_with(None, None, None)
    agent_c.__aexit__.assert_called_once_with(None, None, None)
    # Registry fully cleared
    assert len(state._session_agents) == 0
    assert len(state._session_agent_locks) == 0
