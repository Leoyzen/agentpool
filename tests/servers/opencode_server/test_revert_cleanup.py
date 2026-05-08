"""Tests for revert marker cleanup when sending a new message.

Validates that after a user performs /undo (revert) and then sends a new
message, the ``session.revert`` marker is cleared so the frontend stops
filtering messages.  This mirrors opencode-native's ``revert.cleanup()``
which is called in ``prompt()`` before each new turn.

Bug: ESC → Undo → Resend produced no message rendering because
``session.revert`` was never cleared, causing the frontend filter
``message.id >= revert().messageID`` to hide all new messages.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.models import (
    MessageRequest,
    SessionRevert,
    TextPartInput,
)
from agentpool_server.opencode_server.routes.message_routes import _process_message
from agentpool_server.opencode_server.state import ServerState


# =============================================================================
# Per-session mock agent factory (same pattern as test_message_isolation.py)
# =============================================================================


def _mock_create_session_agent(
    state: ServerState,
    session_id: str,
) -> Mock:
    """Create a mock agent for a specific session."""
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
    agent.run_stream = _make_run_stream(session_id)  # type: ignore[method-assign]
    return agent


def _make_run_stream(session_id: str) -> Any:
    """Create a ``run_stream`` method that returns an async generator."""

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
    """Create a ServerState with per-session agent creation."""
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
# Test: revert marker is cleared when sending a new message
# =============================================================================


async def test_revert_marker_cleared_on_new_message(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Sending a message after /undo clears session.revert.

    This is the core bug fix: the revert marker must be cleared so the
    frontend stops filtering messages with ``message.id >= revert.messageID``.

    Steps:
    1. Create a session and add a revert marker (simulating /undo)
    2. Send a new message
    3. Verify session.revert is None (marker cleared)
    4. Verify reverted_messages for this session is also cleaned up
    """
    state = isolated_state
    session_id = "session-revert-cleanup"

    # Step 1: Create session and simulate a revert state
    session = await state.ensure_session(session_id)
    assert session.revert is None, "Session should start without revert marker"

    # Simulate what /undo does: set session.revert and store reverted messages
    revert_marker = SessionRevert(message_id="msg_0001")
    updated_session = session.model_copy(update={"revert": revert_marker})
    state.sessions[session_id] = updated_session

    # Also simulate reverted_messages storage (what revert_session stores)
    from agentpool_server.opencode_server.models import (
        MessageWithParts,
        TimeCreated,
        UserMessage,
    )

    fake_reverted_msg = MessageWithParts(
        info=UserMessage(
            id="msg_0001",
            session_id=session_id,
            time=TimeCreated.now(),
            agent="default",
        )
    )
    state.reverted_messages[session_id] = [fake_reverted_msg]

    # Verify the precondition: revert marker IS set
    assert state.sessions[session_id].revert is not None
    assert session_id in state.reverted_messages

    # Step 2: Send a new message (this should clear the revert marker)
    _result = await _process_message(session_id, sample_request, state)

    # Step 3: Verify session.revert is None
    assert state.sessions[session_id].revert is None, (
        "session.revert must be cleared when sending a new message after /undo"
    )

    # Step 4: Verify reverted_messages is also cleaned up
    assert state.reverted_messages.get(session_id, []) == [], (
        "state.reverted_messages must be cleared when sending a new message after /undo"
    )


async def test_revert_marker_clear_broadcasts_session_updated(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Clearing revert marker broadcasts session.updated event.

    The frontend relies on SSE events to update its state. When the
    revert marker is cleared, a ``session.updated`` event must be
    broadcast so the frontend re-renders messages without the filter.
    """
    state = isolated_state
    session_id = "session-revert-broadcast"

    # Set up session with revert marker
    session = await state.ensure_session(session_id)
    revert_marker = SessionRevert(message_id="msg_0001")
    state.sessions[session_id] = session.model_copy(update={"revert": revert_marker})

    # Capture broadcast events
    broadcast_events: list[Any] = []
    original_broadcast = state.broadcast_event

    async def capturing_broadcast(event: Any) -> None:
        broadcast_events.append(event)
        await original_broadcast(event)

    state.broadcast_event = capturing_broadcast  # type: ignore[method-assign]

    # Send a new message
    await _process_message(session_id, sample_request, state)

    # Verify a session.updated event was broadcast with revert=None
    session_updated_events = [e for e in broadcast_events if e.type == "session.updated"]
    assert len(session_updated_events) >= 1, (
        "At least one session.updated event should be broadcast when revert is cleared"
    )

    # The session in the event should have revert=None
    last_session_event = session_updated_events[-1]
    assert last_session_event.properties.info.revert is None, (
        "session.updated event should carry session with revert=None"
    )


async def test_no_revert_marker_no_side_effects(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Sending a message without prior revert does not cause side effects.

    The cleanup logic should be a no-op when there is no revert marker,
    so normal message flow is unaffected.
    """
    state = isolated_state
    session_id = "session-no-revert"

    session = await state.ensure_session(session_id)
    assert session.revert is None

    # Send a message without any prior revert
    _result = await _process_message(session_id, sample_request, state)

    # Session should still have no revert marker
    assert state.sessions[session_id].revert is None

    # Messages should be recorded normally (1 user + 1 assistant)
    assert len(state.messages[session_id]) == 2


async def test_revert_cleanup_happens_before_processing(
    isolated_state: ServerState,
    sample_request: MessageRequest,
) -> None:
    """Revert cleanup occurs before agent processing begins.

    This ensures that if the agent itself inspects session.revert (e.g.,
    for context), it sees a clean state. The cleanup must happen at the
    beginning of _process_message_locked, not after.
    """
    state = isolated_state
    session_id = "session-revert-ordering"

    # Set up session with revert marker
    session = await state.ensure_session(session_id)
    revert_marker = SessionRevert(message_id="msg_0001")
    state.sessions[session_id] = session.model_copy(update={"revert": revert_marker})

    # Track when the agent's run_stream is called vs when revert is cleared
    agent = await state.get_or_create_agent(session_id)
    run_stream_called = False
    original_run_stream = agent.run_stream

    def tracking_run_stream(*args: Any, **kwargs: Any) -> Any:
        nonlocal run_stream_called
        run_stream_called = True
        # At the point the agent is called, revert should already be cleared
        assert state.sessions[session_id].revert is None, (
            "session.revert must be cleared BEFORE the agent processes the message"
        )
        return original_run_stream(*args, **kwargs)

    agent.run_stream = tracking_run_stream  # type: ignore[method-assign]

    # Send a new message
    await _process_message(session_id, sample_request, state)

    # Verify the agent was actually called
    assert run_stream_called, "Agent run_stream should have been called"
