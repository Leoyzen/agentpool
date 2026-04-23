"""Tests for per-session agent registry in ServerState.

Validates that the session-agent registry provides:
- Per-session agent isolation (different sessions get different agents)
- Same-session idempotency (same session always returns same agent)
- Race-free concurrent creation (double-check locking)
- Proper cleanup (individual and bulk)
"""

from __future__ import annotations

import asyncio
from typing import Any
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
def state(shared_agent: Mock, tmp_path: Any) -> ServerState:
    """Create a ServerState with a shared mock agent.

    Patches ``_create_session_agent`` so each call returns a fresh mock
    agent, enabling per-session isolation testing without a real
    ``NativeAgentConfig``.
    """
    import tempfile

    with tempfile.TemporaryDirectory(prefix="session-agent-test-") as tmpdir:
        st = ServerState(working_dir=tmpdir, agent=shared_agent)
        # Override _create_session_agent to produce distinct mock agents
        # per call — this simulates NativeAgentConfig.get_agent() returning
        # new instances.
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
# Scenario 1: Per-session agent isolation
# =============================================================================


async def test_same_session_returns_same_agent(state: ServerState) -> None:
    """Calling get_or_create_agent twice for the same session returns the same object."""
    agent_a1 = await state.get_or_create_agent("session-a")
    agent_a2 = await state.get_or_create_agent("session-a")
    assert agent_a1 is agent_a2


async def test_different_sessions_return_different_agents(state: ServerState) -> None:
    """Different sessions get distinct agent instances."""
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    assert agent_a is not agent_b


async def test_session_agent_has_correct_session_id(state: ServerState) -> None:
    """Each session agent is bound to the correct session_id."""
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    assert agent_a.session_id == "session-a"
    assert agent_b.session_id == "session-b"


async def test_registry_tracks_all_sessions(state: ServerState) -> None:
    """The _session_agents dict contains entries for all created sessions."""
    await state.get_or_create_agent("session-a")
    await state.get_or_create_agent("session-b")
    assert "session-a" in state._session_agents
    assert "session-b" in state._session_agents
    assert len(state._session_agents) == 2


# =============================================================================
# Scenario 2: Race-free concurrent creation
# =============================================================================


async def test_concurrent_creation_race_free(state: ServerState) -> None:
    """Two concurrent calls for the same session_id return the same agent.

    This validates the double-check locking pattern in get_or_create_agent.
    Without it, two concurrent callers could both pass the initial check
    and create duplicate agents.
    """
    results = await asyncio.gather(
        state.get_or_create_agent("concurrent-session"),
        state.get_or_create_agent("concurrent-session"),
    )
    assert results[0] is results[1]
    # Registry should have exactly 1 entry for this session
    assert "concurrent-session" in state._session_agents
    assert len([k for k in state._session_agents if k == "concurrent-session"]) == 1


async def test_concurrent_different_sessions_no_blocking(state: ServerState) -> None:
    """Concurrent calls for different sessions don't block each other.

    Each session has its own lock, so creating agents for different
    sessions should proceed in parallel.
    """
    results = await asyncio.gather(
        state.get_or_create_agent("session-x"),
        state.get_or_create_agent("session-y"),
        state.get_or_create_agent("session-z"),
    )
    # All three should be distinct
    assert results[0] is not results[1]
    assert results[1] is not results[2]
    assert results[0] is not results[2]
    assert len(state._session_agents) == 3


async def test_lock_created_per_session(state: ServerState) -> None:
    """A per-session lock is created in _session_agent_locks on first access."""
    assert "new-session" not in state._session_agent_locks
    await state.get_or_create_agent("new-session")
    assert "new-session" in state._session_agent_locks


# =============================================================================
# Scenario 3: Cleanup helpers
# =============================================================================


async def test_cleanup_all_closes_every_agent(state: ServerState) -> None:
    """cleanup_all_session_agents cleans up all agents and clears the registry."""
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    await state.cleanup_all_session_agents()
    # All agents should have had __aexit__ called
    agent_a.__aexit__.assert_called_once()
    agent_b.__aexit__.assert_called_once()
    # Registry should be empty
    assert len(state._session_agents) == 0
    assert len(state._session_agent_locks) == 0


async def test_remove_session_agent_cleans_up_target(state: ServerState) -> None:
    """remove_session_agent cleans up the target session's agent only."""
    agent_a = await state.get_or_create_agent("session-a")
    agent_b = await state.get_or_create_agent("session-b")
    await state.remove_session_agent("session-a")
    # Agent A should be cleaned up
    agent_a.__aexit__.assert_called_once()
    # Agent B should NOT be cleaned up
    agent_b.__aexit__.assert_not_called()
    # Registry should only contain session-b
    assert "session-a" not in state._session_agents
    assert "session-b" in state._session_agents
    # Lock for session-a should be removed
    assert "session-a" not in state._session_agent_locks
    assert "session-b" in state._session_agent_locks


async def test_remove_nonexistent_session_is_safe(state: ServerState) -> None:
    """Removing a session_id that doesn't exist should not raise."""
    # Should not raise KeyError or any other exception
    await state.remove_session_agent("nonexistent-session")


async def test_cleanup_all_with_empty_registry(state: ServerState) -> None:
    """cleanup_all_session_agents on an empty registry is a no-op."""
    await state.cleanup_all_session_agents()
    assert len(state._session_agents) == 0


async def test_remove_then_recreate_gets_new_agent(state: ServerState) -> None:
    """After removing a session's agent, get_or_create_agent creates a new one."""
    agent_a1 = await state.get_or_create_agent("session-a")
    await state.remove_session_agent("session-a")
    agent_a2 = await state.get_or_create_agent("session-a")
    # Should be a different agent instance after recreation
    assert agent_a1 is not agent_a2


# =============================================================================
# Non-session-scoped dependency access
# =============================================================================


async def test_pool_accessible_without_agent(state: ServerState, mock_pool: Mock) -> None:
    """The pool property returns the cached _pool without going through self.agent."""
    assert state.pool is mock_pool
    # Even if we access pool multiple times, it returns the same cached object
    assert state.pool is state._pool


async def test_storage_accessible_without_agent(shared_agent: Mock, mock_pool: Mock) -> None:
    """The storage property returns the cached _storage without going through self.agent."""
    from agentpool.storage import StorageManager
    from agentpool_config.storage import MemoryStorageConfig, StorageConfig

    storage_manager = StorageManager(config=StorageConfig(providers=[MemoryStorageConfig()]))
    shared_agent.storage = storage_manager

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        st = ServerState(working_dir=tmpdir, agent=shared_agent)
        assert st.storage is storage_manager
        assert st.storage is st._storage
