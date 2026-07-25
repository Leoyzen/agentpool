"""Unit tests for EventProcessor double-terminal guard.

Verifies that when RunErrorEvent is followed by StreamCompleteEvent,
only one terminal signal (SessionErrorEvent) is emitted, not a
duplicate SessionStatusEvent. Also verifies the guard resets between turns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from agentpool.agents.events.events import RunErrorEvent, StreamCompleteEvent
from agentpool.messaging.messages import ChatMessage
from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    SessionErrorEvent,
    SessionStatusEvent,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def _make_ctx(server_state: ServerState) -> EventProcessorContext:
    """Create a minimal EventProcessorContext for testing."""
    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-1",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    return EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        state=server_state,
        working_dir="/tmp",
    )


def _make_stream_complete_event(
    cancelled: bool = False,
) -> StreamCompleteEvent[str]:
    """Create a minimal StreamCompleteEvent for testing."""
    msg = ChatMessage(content="test response", role="assistant")
    return StreamCompleteEvent(message=msg, cancelled=cancelled)


# ---------------------------------------------------------------------------
# Test 1: RunErrorEvent + StreamCompleteEvent → no double SessionStatusEvent
# ---------------------------------------------------------------------------


async def test_run_error_then_stream_complete_no_double_terminal(
    server_state: ServerState,
) -> None:
    """RunErrorEvent followed by StreamCompleteEvent does not emit double terminal.

    The guard flag prevents the StreamCompleteEvent from emitting a
    SessionStatusEvent after RunErrorEvent already emitted SessionErrorEvent.
    """
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # Step 1: Process RunErrorEvent — should emit SessionErrorEvent
    error_event = RunErrorEvent(message="Something went wrong", code="TestError")
    error_events = [e async for e in processor.process(error_event, ctx)]
    error_session_errors = [e for e in error_events if isinstance(e, SessionErrorEvent)]
    assert len(error_session_errors) == 1

    # Guard flag should now be True
    assert processor._run_error_emitted is True

    # Step 2: Process StreamCompleteEvent — should NOT emit SessionStatusEvent
    stream_event = _make_stream_complete_event(cancelled=True)
    stream_events = [e async for e in processor.process(stream_event, ctx)]
    stream_status_events = [e for e in stream_events if isinstance(e, SessionStatusEvent)]
    assert len(stream_status_events) == 0, (
        "StreamCompleteEvent should not emit SessionStatusEvent after RunErrorEvent"
    )

    # Guard flag should be reset after StreamCompleteEvent
    assert processor._run_error_emitted is False


# ---------------------------------------------------------------------------
# Test 2: Normal StreamCompleteEvent (no preceding RunErrorEvent) → emits SessionStatusEvent
# ---------------------------------------------------------------------------


async def test_normal_stream_complete_emits_session_status(
    server_state: ServerState,
) -> None:
    """StreamCompleteEvent without preceding RunErrorEvent emits SessionStatusEvent."""
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    stream_event = _make_stream_complete_event(cancelled=False)
    events = [e async for e in processor.process(stream_event, ctx)]
    status_events = [e for e in events if isinstance(e, SessionStatusEvent)]

    assert len(status_events) == 1


# ---------------------------------------------------------------------------
# Test 3: New EventProcessor instance has guard = False
# ---------------------------------------------------------------------------


async def test_new_processor_has_guard_false() -> None:
    """A freshly created EventProcessor has _run_error_emitted = False."""
    processor = EventProcessor()
    assert processor._run_error_emitted is False


# ---------------------------------------------------------------------------
# Test 4: Guard resets after StreamCompleteEvent (next turn works normally)
# ---------------------------------------------------------------------------


async def test_guard_resets_after_stream_complete(
    server_state: ServerState,
) -> None:
    """After RunErrorEvent + StreamCompleteEvent, the next turn works normally.

    The guard is reset in the StreamCompleteEvent handler, so a subsequent
    turn's StreamCompleteEvent (without preceding RunErrorEvent) should
    emit SessionStatusEvent as expected.
    """
    processor = EventProcessor()
    ctx = _make_ctx(server_state)

    # Turn 1: RunErrorEvent + StreamCompleteEvent
    error_event = RunErrorEvent(message="Error in turn 1")
    error_events = [e async for e in processor.process(error_event, ctx)]
    assert any(isinstance(e, SessionErrorEvent) for e in error_events)
    assert processor._run_error_emitted is True

    stream_event_1 = _make_stream_complete_event(cancelled=True)
    # Drain all events from stream complete
    _ = [e async for e in processor.process(stream_event_1, ctx)]
    assert processor._run_error_emitted is False

    # Turn 2: Normal StreamCompleteEvent — should emit SessionStatusEvent
    ctx2 = _make_ctx(server_state)  # New context for new turn
    stream_event_2 = _make_stream_complete_event(cancelled=False)
    events = [e async for e in processor.process(stream_event_2, ctx2)]
    status_events = [e for e in events if isinstance(e, SessionStatusEvent)]
    assert len(status_events) == 1, "Second turn StreamCompleteEvent should emit SessionStatusEvent"
