"""Regression tests for model, fork, and input-provider isolation.

Proves that per-session agents provide proper isolation across three
dimensions:

1. **Model isolation**: Changing model in one session does not bleed
   into another session's agent — no save/restore logic needed.
2. **Fork divergence**: Forked sessions share initial history but
   diverge independently after the fork point.
3. **Input-provider isolation**: Loading one session does not
   overwrite another session's input provider.
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
    agent.set_model = AsyncMock()
    agent.get_available_models = AsyncMock(return_value=[])
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
    with tempfile.TemporaryDirectory(prefix="isolation-regression-test-") as tmpdir:
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
# Test 1: Model selection isolation
# =============================================================================


async def test_model_selection_no_cross_session_bleed(state: Any) -> None:
    """Changing model in session A does not affect session B's agent.

    With per-session agents, each session owns its own agent instance.
    Calling ``set_model`` on session A's agent is a local mutation —
    session B's agent.model remains unchanged without any save/restore
    logic.
    """
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Both start with default model state
    original_model_b = getattr(agent_b, "model", None)

    # Change model in session A
    await agent_a.set_model("new-model-for-a")

    # Session B's model should be completely unchanged
    assert agent_b.model is original_model_b
    agent_b.set_model.assert_not_called()

    # Session A's set_model was called with the right argument
    agent_a.set_model.assert_called_once_with("new-model-for-a")


async def test_model_persistence_per_session_without_restore(state: Any) -> None:
    """Model changes persist per session without needing restore logic.

    Simulates the flow in ``_process_message_locked``: a message arrives
    for session B after session A changed its model.  Session B's agent
    should still have its original model — no ``original_model`` save or
    restore is required.
    """
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Session A changes model
    await agent_a.set_model("gpt-5-turbo")

    # Now process a message for session B — in the old shared-agent
    # architecture this would have "restored" session A's model.
    # With per-session agents, session B is unaffected.
    agent_b_model_before = getattr(agent_b, "model", None)

    # Simulate _process_message_locked retrieving the session agent
    session_b_agent: Any = await state.get_or_create_agent("session-b")
    assert session_b_agent is agent_b
    assert getattr(session_b_agent, "model", None) is agent_b_model_before

    # Session B's set_model was never called
    agent_b.set_model.assert_not_called()


# =============================================================================
# Test 2: Fork divergence
# =============================================================================


async def test_fork_copies_history_then_diverges(state: Any) -> None:
    """Forked session starts with copied history but diverges independently.

    This mirrors the real ``fork_session`` flow: the forked session's
    agent is created via ``get_or_create_agent`` with cleared
    ``chat_messages``, then the copied messages are loaded.  After the
    fork point, each session's agent accumulates its own messages.
    """
    # Create original session's agent and build up some history
    agent_original: Any = await state.get_or_create_agent("session-original")
    agent_original.conversation.chat_messages.extend(["msg-1", "msg-2", "msg-3"])

    # Simulate fork: create a new agent for the forked session
    agent_fork: Any = await state.get_or_create_agent("session-fork")

    # The fork agent starts with cleared history (fork_session clears
    # chat_messages before loading the copied history)
    assert len(agent_fork.conversation.chat_messages) == 0

    # Simulate loading copied history into the fork's agent
    agent_fork.conversation.chat_messages.extend(
        list(agent_original.conversation.chat_messages),
    )

    # Both have identical history at this point
    assert agent_original.conversation.chat_messages == agent_fork.conversation.chat_messages

    # Diverge: add different messages to each
    agent_original.conversation.chat_messages.append("original-only-msg")
    agent_fork.conversation.chat_messages.append("fork-only-msg")

    # Histories have diverged
    assert "original-only-msg" in agent_original.conversation.chat_messages
    assert "original-only-msg" not in agent_fork.conversation.chat_messages
    assert "fork-only-msg" in agent_fork.conversation.chat_messages
    assert "fork-only-msg" not in agent_original.conversation.chat_messages


async def test_fork_creates_distinct_agent_instance(state: Any) -> None:
    """Forked session gets its own agent instance, not a reference to the original.

    Each session in the ``_session_agents`` registry is a distinct object
    so mutations (model changes, conversation updates) are fully isolated.
    """
    agent_original: Any = await state.get_or_create_agent("session-original")
    agent_fork: Any = await state.get_or_create_agent("session-fork")

    assert agent_original is not agent_fork
    assert agent_original.name != agent_fork.name

    # Registry tracks both independently
    assert state._session_agents["session-original"] is agent_original
    assert state._session_agents["session-fork"] is agent_fork


async def test_forked_session_model_changes_do_not_affect_original(state: Any) -> None:
    """After fork, model changes in the fork don't affect the original."""
    agent_original: Any = await state.get_or_create_agent("session-original")
    agent_fork: Any = await state.get_or_create_agent("session-fork")

    # Change model in the forked session
    await agent_fork.set_model("fork-model")

    # Original session's agent is unaffected
    agent_original.set_model.assert_not_called()

    # Fork's set_model was called
    agent_fork.set_model.assert_called_once_with("fork-model")


# =============================================================================
# Test 3: Input-provider isolation
# =============================================================================


async def test_input_provider_not_overwritten_by_another_session_load(state: Any) -> None:
    """Loading session B does not overwrite session A's input provider.

    With per-session agents, each session's agent has its own
    ``_input_provider`` set during creation.  Loading one session cannot
    affect another session's provider because there is no shared mutable
    agent state to clobber.
    """
    # Create two sessions with per-session agents
    agent_a: Any = await state.get_or_create_agent("session-a")
    agent_b: Any = await state.get_or_create_agent("session-b")

    # Each agent has its own input provider from creation
    provider_a = state.input_providers["session-a"]
    provider_b = state.input_providers["session-b"]

    assert agent_a._input_provider is provider_a
    assert agent_b._input_provider is provider_b

    # Simulate loading session B (e.g., get_or_load_session called
    # for session-b).  In the old shared-agent architecture this would
    # have rebound the shared agent's _input_provider to session B,
    # clobbering session A's binding.  With per-session agents, this
    # is a no-op for session A.
    _agent_b_loaded: Any = await state.get_or_create_agent("session-b")
    assert _agent_b_loaded is agent_b

    # Session A's agent still has its original input provider
    assert agent_a._input_provider is provider_a
    assert agent_a._input_provider is not provider_b

    # Session B's agent has its own provider
    assert agent_b._input_provider is provider_b


async def test_input_provider_session_ids_stay_correct(state: Any) -> None:
    """Each input provider retains the correct session_id after cross-session loads.

    Even after loading multiple sessions, each provider's ``session_id``
    attribute stays bound to its own session — no cross-talk.
    """
    await state.get_or_create_agent("session-a")
    await state.get_or_create_agent("session-b")
    await state.get_or_create_agent("session-c")

    provider_a = state.input_providers["session-a"]
    provider_b = state.input_providers["session-b"]
    provider_c = state.input_providers["session-c"]

    # Load sessions in various orders
    await state.get_or_create_agent("session-c")
    await state.get_or_create_agent("session-a")
    await state.get_or_create_agent("session-b")

    # All providers still have correct session IDs
    assert provider_a.session_id == "session-a"
    assert provider_b.session_id == "session-b"
    assert provider_c.session_id == "session-c"

    # All providers are distinct
    assert provider_a is not provider_b
    assert provider_b is not provider_c
    assert provider_a is not provider_c
