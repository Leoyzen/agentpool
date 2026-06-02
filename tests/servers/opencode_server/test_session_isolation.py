"""Tests for session isolation after per-session agent refactoring (RFC-0026 Task 3).

Validates that session-scoped agents provide proper isolation:
- Two sessions use different agent instances (no cross-talk)
- Forked sessions keep history but diverge independently
- Abort targets only the correct session's agent
"""

from __future__ import annotations

import tempfile
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Helpers
# =============================================================================


def _make_session_mock(session_id: str, counter: int) -> Mock:
    """Build a Mock that satisfies the session-agent contract.

    Returns a plain ``Mock`` so we can use ``assert_called_once`` and
    other mock assertions without pyright complaining about real
    ``BaseAgent`` method signatures.
    """
    agent: Any = Mock()
    agent.name = f"session-agent-{counter}"
    agent.session_id = session_id
    agent._input_provider = None
    agent.conversation = Mock()
    agent.conversation.chat_messages: list[str] = []
    agent.interrupt = AsyncMock()
    agent.load_session = AsyncMock(return_value=None)
    agent.__aexit__ = AsyncMock(return_value=False)
    return agent


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
    pool.manifest.config_file_path = "/tmp/test-pool"
    pool.skill_commands = None
    pool.sessions = Mock()
    pool.sessions.store = None
    pool.session_pool = Mock()
    pool.session_pool.sessions = Mock()
    pool.session_pool.sessions.store = None
    pool.file_ops = Mock()
    pool.file_ops.changes = []
    pool.todos = Mock()
    pool.todos.entries = []
    return pool


@pytest.fixture
def shared_agent(mock_env: Mock, mock_pool: Mock) -> Mock:
    """Create the shared (default) mock agent."""
    agent: Any = Mock()
    agent.name = "test-agent"
    agent.env = mock_env
    agent._input_provider = None
    agent.agent_pool = mock_pool
    agent.storage = None
    agent.interrupt = AsyncMock()
    return agent


@pytest.fixture
def state(shared_agent: Mock, mock_pool: Mock) -> Any:
    """Create a ServerState with per-session mock agents.

    Patches ``_create_session_agent`` so each call returns a fresh mock
    agent with a distinct ``session_id`` and its own conversation state.

    Returns ``Any`` because ``yield``-based fixtures confuse pyright
    about the actual return type.
    """
    with tempfile.TemporaryDirectory(prefix="session-isolation-test-") as tmpdir:
        st = ServerState(working_dir=tmpdir, agent=shared_agent)
        call_count = 0

        def _fake_create(session_id: str) -> Mock:
            nonlocal call_count
            call_count += 1
            agent = _make_session_mock(session_id, call_count)
            # Mirror the real _create_session_agent which calls
            # ensure_input_provider and sets it on the agent.
            agent._input_provider = st.ensure_input_provider(session_id)
            return agent

        st._create_session_agent = _fake_create  # type: ignore[method-assign]
        yield st


# =============================================================================
# Test 1: Two sessions without cross-talk
# =============================================================================


async def test_two_sessions_have_different_agents(state: Any) -> None:
    """Two sessions get distinct agent instances — no shared mutable state."""
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")
    assert agent_a is not agent_b


async def test_input_provider_isolation(state: Any) -> None:
    """Binding an input provider to one session doesn't affect another.

    Each session agent gets its own input provider via
    ``state.ensure_input_provider()``.
    """
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    provider_a = state.ensure_input_provider("session-a")
    provider_b = state.ensure_input_provider("session-b")

    # Different providers for different sessions
    assert provider_a is not provider_b

    # Each agent should have its own input provider set
    assert agent_a._input_provider is not None
    assert agent_b._input_provider is not None

    # Setting provider on one doesn't leak to the other
    assert agent_a._input_provider is provider_a
    assert agent_b._input_provider is provider_b


async def test_conversation_isolation(state: Any) -> None:
    """Adding messages to one session's agent doesn't affect another's."""
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Simulate adding messages to session A
    agent_a.conversation.chat_messages.append("msg-a")

    # Session B's conversation should be unaffected
    assert len(agent_a.conversation.chat_messages) == 1
    assert len(agent_b.conversation.chat_messages) == 0


# =============================================================================
# Test 2: Fork keeps history but diverges
# =============================================================================


async def test_fork_session_creates_distinct_agent(state: Any) -> None:
    """A forked session gets its own agent instance.

    The fork agent starts with cleared chat_messages so it can receive
    the copied conversation history independently.
    """
    # Create the original session's agent and add some messages
    agent_original: Any = await state.get_or_create_agent("session-original")
    agent_original.conversation.chat_messages.extend(["msg-1", "msg-2"])

    # Simulate fork: create a new agent for the forked session
    agent_fork: Any = await state.get_or_create_agent("session-fork")

    # Fork agent should be a different object
    assert agent_fork is not agent_original

    # Fork agent starts with cleared history (chat_messages was cleared
    # in fork_session before loading copied history)
    assert len(agent_fork.conversation.chat_messages) == 0


async def test_fork_history_diverges(state: Any) -> None:
    """After fork, adding messages to one session doesn't affect the other."""
    # Both sessions have agents
    agent_original: Any = await state.get_or_create_agent("session-original")
    agent_fork: Any = await state.get_or_create_agent("session-fork")

    # Simulate that both start with the same history (as fork would copy)
    agent_original.conversation.chat_messages.extend(["shared-1", "shared-2"])
    agent_fork.conversation.chat_messages.extend(["shared-1", "shared-2"])

    # Diverge: add different messages to each
    agent_original.conversation.chat_messages.append("original-only")
    agent_fork.conversation.chat_messages.append("fork-only")

    # Each agent has its own diverged history
    assert "original-only" in agent_original.conversation.chat_messages
    assert "original-only" not in agent_fork.conversation.chat_messages
    assert "fork-only" in agent_fork.conversation.chat_messages
    assert "fork-only" not in agent_original.conversation.chat_messages


# =============================================================================
# Test 3: Abort targets correct session
# =============================================================================


async def test_abort_targets_correct_session_agent(state: Any) -> None:
    """Aborting one session only interrupts that session's agent."""
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Simulate abort_session logic: look up the correct session agent
    session_agent: Any = state._session_agents.get("session-a", state.agent)
    await session_agent.interrupt()

    # Only agent_a's interrupt was called
    agent_a.interrupt.assert_called_once()
    agent_b.interrupt.assert_not_called()


async def test_abort_falls_back_to_shared_agent(state: Any) -> None:
    """If a session has no registered agent, abort falls back to shared agent."""
    # Don't create a session agent for "session-c"
    assert "session-c" not in state._session_agents

    # The fallback returns the shared agent
    session_agent: Any = state._session_agents.get("session-c", state.agent)
    assert session_agent is state.agent

    await session_agent.interrupt()
    state.agent.interrupt.assert_called_once()


async def test_abort_does_not_affect_other_session(state: Any) -> None:
    """Aborting session A leaves session B's agent completely untouched."""
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Abort session A
    await agent_a.interrupt()

    # Verify session B is untouched
    agent_a.interrupt.assert_called_once()
    agent_b.interrupt.assert_not_called()

    # Session B can still be used normally
    assert agent_b.session_id == "session-b"
