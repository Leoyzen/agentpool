"""Integration tests for subagent event forwarding in OpenCode handler.

Tests that OpenCodeProtocolHandler correctly forwards subagent events
through the ProtocolEventConsumerMixin to the OpenCode frontend.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.events import (
    PartDeltaEvent,
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.agents.events.events import TextPartDelta
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.handler import OpenCodeProtocolHandler
from agentpool_server.opencode_server.models.events import (
    SessionErrorEvent,
    SessionIdleEvent,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_state():
    """Return a mock server state with async broadcast_event."""
    state = MagicMock()
    state.broadcast_event = AsyncMock()
    return state


@pytest.fixture
def mock_agent_pool():
    """Return a mock agent pool with session pool and event bus."""
    pool = MagicMock()
    pool.manifest.opencode.use_session_pool = True
    pool.manifest.agents = {}
    pool.session_pool = MagicMock()
    pool.session_pool.event_bus = MagicMock()
    pool.session_pool.event_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    pool.session_pool.event_bus.unsubscribe = AsyncMock()
    pool.session_pool.create_session = AsyncMock()
    pool.session_pool.receive_request = AsyncMock()
    pool.session_pool.close_session = AsyncMock()
    return pool


@pytest.fixture
def handler(mock_agent_pool, mock_state):
    """Return an OpenCodeProtocolHandler wired to mocks."""
    return OpenCodeProtocolHandler(mock_agent_pool, state=mock_state)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _stream_complete_event(content: str = "done") -> StreamCompleteEvent:
    """Create a StreamCompleteEvent with a minimal ChatMessage."""
    msg = ChatMessage(content=content, role="assistant")
    return StreamCompleteEvent(message=msg)


async def _put_event_and_wait(queue: asyncio.Queue, event: Any, delay: float = 0.05) -> None:
    """Put an event into the queue and yield control to the consumer."""
    await queue.put(event)
    await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subagent_events_reach_frontend(handler, mock_agent_pool, mock_state):
    """Converted events are broadcast to the OpenCode frontend.

    A StreamCompleteEvent placed on the EventBus queue is consumed by the
    mixin loop, converted to a SessionIdleEvent by the handler, and
    broadcast via state.broadcast_event.
    """
    session_id = "sess-main"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)

    event = _stream_complete_event()
    await _put_event_and_wait(queue, event)

    mock_state.broadcast_event.assert_awaited_once()
    broadcasted = mock_state.broadcast_event.await_args[0][0]
    assert isinstance(broadcasted, SessionIdleEvent)
    assert broadcasted.properties.session_id == session_id

    await handler.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_unconverted_events_not_broadcast(handler, mock_agent_pool, mock_state):
    """Events with no conversion mapping do not trigger broadcast_event.

    PartDeltaEvent is not yet mapped in _convert_event, so putting it on
    the queue should result in no frontend broadcast.
    """
    session_id = "sess-main"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await _put_event_and_wait(queue, event)

    mock_state.broadcast_event.assert_not_awaited()

    await handler.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_nested_subagent_events(handler, mock_agent_pool, mock_state):
    """SpawnSessionStart starts child and grandchild consumers recursively.

    When a SpawnSessionStart event is received, the mixin automatically
    starts a consumer for the child session. When the child session
    spawns a grandchild, that consumer is also started. Events on each
    queue are processed independently with the correct session_id.
    """
    parent_id = "sess-parent"
    child_id = "sess-child"
    grandchild_id = "sess-grandchild"
    parent_queue = asyncio.Queue()
    child_queue = asyncio.Queue()
    grandchild_queue = asyncio.Queue()

    async def _subscribe_side_effect(session_id: str, scope: str = "descendants"):
        if session_id == parent_id:
            return parent_queue
        if session_id == child_id:
            return child_queue
        return grandchild_queue

    mock_agent_pool.session_pool.event_bus.subscribe = AsyncMock(
        side_effect=_subscribe_side_effect
    )

    await handler.start_event_consumer(parent_id)

    # Spawn child from parent
    spawn_child = SpawnSessionStart(
        child_session_id=child_id,
        parent_session_id=parent_id,
        source_name="child-agent",
        source_type="agent",
        spawn_mechanism="spawn",
        description="child spawn",
    )
    await _put_event_and_wait(parent_queue, spawn_child)

    # Child consumer should be registered
    assert child_id in handler._consumer_tasks
    assert isinstance(handler._consumer_tasks[child_id], asyncio.Task)

    # Spawn grandchild from child
    spawn_grandchild = SpawnSessionStart(
        child_session_id=grandchild_id,
        parent_session_id=child_id,
        source_name="grandchild-agent",
        source_type="agent",
        spawn_mechanism="spawn",
        description="grandchild spawn",
    )
    await _put_event_and_wait(child_queue, spawn_grandchild)

    # Grandchild consumer should be registered
    assert grandchild_id in handler._consumer_tasks
    assert isinstance(handler._consumer_tasks[grandchild_id], asyncio.Task)

    # Put completion events on child and grandchild queues
    await _put_event_and_wait(child_queue, _stream_complete_event("child-done"))
    await _put_event_and_wait(grandchild_queue, _stream_complete_event("grandchild-done"))

    # Both child and grandchild events should have been broadcast
    assert mock_state.broadcast_event.await_count == 2
    calls = mock_state.broadcast_event.await_args_list
    session_ids = {calls[0][0][0].properties.session_id, calls[1][0][0].properties.session_id}
    assert session_ids == {child_id, grandchild_id}

    await handler.stop_event_consumer(parent_id)
    await handler.stop_event_consumer(child_id)
    await handler.stop_event_consumer(grandchild_id)


@pytest.mark.asyncio
async def test_subagent_completion_updates_status(handler, mock_agent_pool, mock_state):
    """StreamCompleteEvent on the queue results in SessionIdleEvent broadcast.

    This signals to the OpenCode frontend that the session is idle again.
    """
    session_id = "sess-complete"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)

    await _put_event_and_wait(queue, _stream_complete_event())

    mock_state.broadcast_event.assert_awaited_once()
    broadcasted = mock_state.broadcast_event.await_args[0][0]
    assert isinstance(broadcasted, SessionIdleEvent)
    assert broadcasted.type == "session.idle"
    assert broadcasted.properties.session_id == session_id

    await handler.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_subagent_error_updates_status(handler, mock_agent_pool, mock_state):
    """RunErrorEvent on the queue results in SessionErrorEvent broadcast.

    This signals to the OpenCode frontend that an error occurred.
    """
    session_id = "sess-error"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)

    error_event = RunErrorEvent(message="something went wrong")
    await _put_event_and_wait(queue, error_event)

    mock_state.broadcast_event.assert_awaited_once()
    broadcasted = mock_state.broadcast_event.await_args[0][0]
    assert isinstance(broadcasted, SessionErrorEvent)
    assert broadcasted.type == "session.error"
    assert broadcasted.properties.session_id == session_id
    assert broadcasted.properties.error is not None
    assert broadcasted.properties.error.name == "Exception"
    assert broadcasted.properties.error.data == {"message": "something went wrong"}

    await handler.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_no_events_leaked_after_session_close(handler, mock_agent_pool, mock_state):
    """Closing a session cleans up all consumer state and unsubscribes.

    After close_session is called, no tasks or queues should remain for
    that session, and the event bus unsubscribe must have been awaited.
    """
    session_id = "sess-close"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)
    assert session_id in handler._consumer_tasks
    assert session_id in handler._consumer_queues

    await handler.close_session(session_id)

    assert session_id not in handler._consumer_tasks
    assert session_id not in handler._consumer_queues
    mock_agent_pool.session_pool.event_bus.unsubscribe.assert_awaited()
    mock_agent_pool.session_pool.close_session.assert_awaited_once_with(session_id)


@pytest.mark.asyncio
async def test_handler_ensure_event_consumer_idempotent(handler, mock_agent_pool, mock_state):
    """_ensure_event_consumer is idempotent for the same session.

    Multiple calls for the same session must not create duplicate consumers.
    """
    session_id = "sess-idem"
    queue = asyncio.Queue()
    mock_agent_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler._ensure_event_consumer(session_id)
    first_task = handler._consumer_tasks[session_id]

    await handler._ensure_event_consumer(session_id)
    second_task = handler._consumer_tasks[session_id]

    assert first_task is second_task
    assert mock_agent_pool.session_pool.event_bus.subscribe.await_count == 1

    await handler.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_handler_ensure_event_consumer_skips_when_disabled(mock_agent_pool, mock_state):
    """_ensure_event_consumer does nothing when use_session_pool is False.

    The global manifest flag disables SessionPool, so no consumer is started.
    """
    mock_agent_pool.manifest.opencode.use_session_pool = False
    handler = OpenCodeProtocolHandler(mock_agent_pool, state=mock_state)
    session_id = "sess-disabled"

    await handler._ensure_event_consumer(session_id)

    assert session_id not in handler._consumer_tasks
    mock_agent_pool.session_pool.event_bus.subscribe.assert_not_awaited()
