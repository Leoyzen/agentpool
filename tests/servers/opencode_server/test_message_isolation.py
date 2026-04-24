"""Tests for per-session agent isolation in message processing.

Validates that message processing with per-session agents provides:
- Concurrent message processing across sessions without interference
- Session-local model changes that don't affect other sessions
- Persistent model changes within a session (no restore pattern)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.models import (
    MessageRequest,
    ModelRef,
    TextPartInput,
)
from agentpool_server.opencode_server.routes.message_routes import _process_message
from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Per-session mock agent factory
# =============================================================================


def _mock_create_session_agent(
    state: ServerState,
    session_id: str,
) -> Mock:
    """Create a mock agent for a specific session.

    Each call produces a distinct Mock with its own ``model_name``,
    ``conversation``, and ``run_stream`` so sessions are fully isolated.

    The mock uses a plain method for ``run_stream`` (not AsyncMock)
    because ``_process_message_locked`` iterates over the result with
    ``async for``, which requires an async generator, not a coroutine.
    """
    agent = Mock()
    agent.name = f"test-agent-{session_id[:8]}"
    agent.session_id = session_id
    agent._input_provider = None
    agent.conversation = Mock()
    agent.conversation.chat_messages = []
    agent.add_chat_messages = Mock()
    agent.model_name = "test-model"
    agent.set_model = AsyncMock()
    agent.set_mode = AsyncMock()
    agent.interrupt = AsyncMock()
    agent.get_available_models = AsyncMock(return_value=[])
    agent.load_session = AsyncMock(return_value=None)
    agent.__aexit__ = AsyncMock(return_value=False)
    # run_stream must be a regular method returning an async generator,
    # NOT an AsyncMock. AsyncMock wraps the return value in a coroutine
    # which causes "'async for' requires an object with __aiter__" errors.
    agent.run_stream = _make_run_stream(session_id)  # type: ignore[method-assign]
    return agent


def _make_run_stream(session_id: str) -> Any:
    """Create a ``run_stream`` method that returns an async generator.

    This mirrors how real agents work: ``run_stream()`` returns an
    async iterator (not a coroutine).
    """

    def run_stream(*args: Any, session_id: str | None = None, **kwargs: Any) -> Any:
        async def _stream() -> Any:
            from agentpool.agents.events import StreamCompleteEvent
            from agentpool.messaging import ChatMessage

            msg = ChatMessage(role="assistant", content=f"reply-{session_id}")
            yield StreamCompleteEvent(message=msg)

        return _stream()

    return run_stream


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def isolated_state(tmp_project_dir, mock_agent, mock_pool) -> ServerState:
    """Create a ServerState with per-session agent creation.

    Overrides ``_create_session_agent`` so each session receives a
    distinct mock agent, enabling true isolation testing without a
    real ``NativeAgentConfig``.
    """
    st = ServerState(working_dir=str(tmp_project_dir), agent=mock_agent)

    def _factory(sid: str) -> Mock:
        return _mock_create_session_agent(st, sid)

    st._create_session_agent = _factory  # type: ignore[method-assign]
    return st


@pytest.fixture
def sample_request() -> MessageRequest:
    """Create a sample message request."""
    return MessageRequest(parts=[TextPartInput(text="Hello")], agent="default")


# =============================================================================
# Test 1: Concurrent message processing across sessions
# =============================================================================


async def test_concurrent_messages_across_sessions(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Two sessions can process messages concurrently without interference.

    Each session has its own agent instance, so processing in one session
    does not block or corrupt the other. Both sessions complete successfully
    and their message histories remain isolated.
    """
    state = isolated_state
    session_a = "session-concurrent-a"
    session_b = "session-concurrent-b"

    await state.ensure_session(session_a)
    await state.ensure_session(session_b)

    results = await asyncio.gather(
        _process_message(session_a, sample_request, state),
        _process_message(session_b, sample_request, state),
        return_exceptions=True,
    )

    for result in results:
        assert not isinstance(result, Exception), f"Unexpected error: {result}"

    # Each session should have exactly 2 messages (1 user + 1 assistant)
    assert len(state.messages[session_a]) == 2
    assert len(state.messages[session_b]) == 2

    # Session A's messages must not appear in session B and vice versa
    ids_a = {msg.info.id for msg in state.messages[session_a]}
    ids_b = {msg.info.id for msg in state.messages[session_b]}
    assert ids_a.isdisjoint(ids_b), "Session message histories must be isolated"


# =============================================================================
# Test 2: Session-local model change does not affect other sessions
# =============================================================================


async def test_session_local_model_change_does_not_restore_globally(
    isolated_state: ServerState,
) -> None:
    """Model change in session A does not affect session B's agent model.

    Before per-session agents, a model switch was temporary (save/restore)
    because the agent was shared globally. With per-session agents, model
    changes are permanent for that session's agent and do not leak to
    other sessions.
    """
    state = isolated_state
    session_a = "session-model-a"
    session_b = "session-model-b"

    await state.ensure_session(session_a)
    await state.ensure_session(session_b)

    # Get per-session agents and record their initial model names
    agent_a = await state.get_or_create_agent(session_a)
    agent_b = await state.get_or_create_agent(session_b)
    initial_model_b = agent_b.model_name

    # Process a message in session A with a model switch
    request_with_model = MessageRequest(
        parts=[TextPartInput(text="Switch model")],
        agent="default",
        model=ModelRef(model_id="new-model", provider_id="test-provider"),
    )

    # We need the model switch to succeed — patch the validation logic
    # by making agent_a.get_available_models return a matching model
    mock_model = Mock()
    mock_model.id = "new-model"
    mock_model.id_override = None
    agent_a.get_available_models = AsyncMock(return_value=[mock_model])  # type: ignore[method-assign]

    # Patch pool manifest to also recognize the model variant
    if state._pool is not None:
        state._pool.manifest.model_variants = {"new-model": Mock()}

    _result = await _process_message(session_a, request_with_model, state)

    # Session A's agent should have had set_model called
    agent_a.set_model.assert_called_with("new-model")  # type: ignore[union-attr]

    # Session B's agent should NOT have had set_model called
    agent_b.set_model.assert_not_called()  # type: ignore[union-attr]

    # Session B's model name should be unchanged
    assert agent_b.model_name == initial_model_b


# =============================================================================
# Test 3: Model change is permanent for session
# =============================================================================


async def test_model_change_is_permanent_for_session(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Model change persists across messages within the same session.

    With per-session agents, there is no save/restore pattern. A model
    switch in one message should remain in effect for subsequent messages
    in the same session.
    """
    state = isolated_state
    session_id = "session-model-persist"

    await state.ensure_session(session_id)

    agent = await state.get_or_create_agent(session_id)

    # Process first message with a model switch
    mock_model = Mock()
    mock_model.id = "persistent-model"
    mock_model.id_override = None
    agent.get_available_models = AsyncMock(return_value=[mock_model])  # type: ignore[method-assign]

    if state._pool is not None:
        state._pool.manifest.model_variants = {"persistent-model": Mock()}

    request_with_model = MessageRequest(
        parts=[TextPartInput(text="Switch model")],
        agent="default",
        model=ModelRef(model_id="persistent-model", provider_id="test-provider"),
    )

    await _process_message(session_id, request_with_model, state)

    # Model should have been switched
    agent.set_model.assert_called_with("persistent-model")  # type: ignore[union-attr]

    # Reset mock to track further calls
    agent.set_model.reset_mock()  # type: ignore[union-attr]

    # Process second message WITHOUT a model switch
    await _process_message(session_id, sample_request, state)

    # set_model should NOT have been called again (no restore, no re-switch)
    agent.set_model.assert_not_called()  # type: ignore[union-attr]
