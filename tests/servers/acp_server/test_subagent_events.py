"""Integration tests for subagent event forwarding in ACP handler.

Tests that ACPProtocolHandler correctly forwards subagent events
through the ProtocolEventConsumerMixin to the ACP client.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.schema import AgentMessageChunk
from agentpool.agents.events import (
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.messaging import ChatMessage
from agentpool_server.acp_server.handler import ACPProtocolHandler


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _async_iter(items: list[Any]):
    """Yield items from a list asynchronously."""
    for item in items:
        yield item


async def _put_event_and_wait(
    queue: asyncio.Queue, event: Any, delay: float = 0.05
) -> None:
    """Put an event into the queue and yield control to the consumer."""
    await queue.put(event)
    await asyncio.sleep(delay)


def _stream_complete_event(content: str = "done") -> StreamCompleteEvent:
    """Create a StreamCompleteEvent with a minimal ChatMessage."""
    msg = ChatMessage(content=content, role="assistant")
    return StreamCompleteEvent(message=msg)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    """Return a mocked ACP client with async session_update."""
    client = MagicMock()
    client.session_update = AsyncMock()
    return client


@pytest.fixture
def mock_event_converter():
    """Return a mocked ACPEventConverter template."""
    converter = MagicMock()
    converter.subagent_display_mode = "legacy"
    return converter


@pytest.fixture
def mock_pool():
    """Return a mocked AgentPool with SessionPool enabled."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.metadata = {"use_session_pool": True}
    pool.session_pool = MagicMock()
    pool.session_pool.event_bus = MagicMock()
    pool.session_pool.event_bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    pool.session_pool.event_bus.unsubscribe = AsyncMock()
    pool.session_pool.event_bus.close_session = AsyncMock()
    pool.session_pool.create_session = AsyncMock()
    pool.session_pool.receive_request = AsyncMock()
    pool.session_pool.close_session = AsyncMock()
    return pool


@pytest.fixture
def handler(mock_pool, mock_event_converter, mock_client):
    """Return an ACPProtocolHandler wired to mocks."""
    return ACPProtocolHandler(mock_pool, mock_event_converter, mock_client)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subagent_events_reach_client(handler, mock_pool, mock_client):
    """Converted events reach the ACP client via session_update.

    A StreamCompleteEvent placed on the EventBus queue is consumed by the
    mixin loop, converted by the handler, and sent to the client via
    session_update as a SessionNotification.
    """
    session_id = "sess-main"
    queue = asyncio.Queue()
    mock_pool.session_pool.event_bus.subscribe.return_value = queue

    # Pre-populate the converter so _handle_event finds it
    mock_update = AgentMessageChunk.text("test")
    mock_converter = MagicMock()
    mock_converter.convert = lambda _event: _async_iter([mock_update])
    handler._session_converters[session_id] = mock_converter

    await handler.start_event_consumer(session_id)

    event = _stream_complete_event()
    await _put_event_and_wait(queue, event)

    mock_client.session_update.assert_awaited()
    notification = mock_client.session_update.await_args[0][0]
    assert notification.session_id == session_id
    assert notification.update is mock_update

    await handler.stop_event_consumer(session_id)


@pytest.mark.anyio
async def test_nested_subagent_events(handler, mock_pool, mock_client):
    """SpawnSessionStart starts child and grandchild consumers recursively.

    When a SpawnSessionStart event is received, the mixin automatically
    starts a consumer for the child session. When the child session
    spawns a grandchild, that consumer is also started. Events on each
    queue are processed and routed with the correct session_id.
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

    mock_pool.session_pool.event_bus.subscribe = AsyncMock(
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

    # Set up converters for child and grandchild
    mock_update_child = AgentMessageChunk.text("child-test")
    mock_converter_child = MagicMock()
    mock_converter_child.convert = lambda _event: _async_iter([mock_update_child])
    handler._session_converters[child_id] = mock_converter_child

    mock_update_grandchild = AgentMessageChunk.text("grandchild-test")
    mock_converter_grandchild = MagicMock()
    mock_converter_grandchild.convert = lambda _event: _async_iter([mock_update_grandchild])
    handler._session_converters[grandchild_id] = mock_converter_grandchild

    # Put completion events on child and grandchild queues
    await _put_event_and_wait(child_queue, _stream_complete_event("child-done"))
    await _put_event_and_wait(grandchild_queue, _stream_complete_event("grandchild-done"))

    # Both child and grandchild events should have been sent
    assert mock_client.session_update.await_count == 2
    calls = mock_client.session_update.await_args_list
    session_ids = {calls[0][0][0].session_id, calls[1][0][0].session_id}
    assert session_ids == {child_id, grandchild_id}

    await handler.stop_event_consumer(parent_id)
    await handler.stop_event_consumer(child_id)
    await handler.stop_event_consumer(grandchild_id)


@pytest.mark.anyio
async def test_subagent_completion_sent(handler, mock_pool, mock_client):
    """StreamCompleteEvent on the queue results in session_update call.

    The notification must carry the correct session_id.
    """
    session_id = "sess-complete"
    queue = asyncio.Queue()
    mock_pool.session_pool.event_bus.subscribe.return_value = queue

    mock_update = AgentMessageChunk.text("complete-test")
    mock_converter = MagicMock()
    mock_converter.convert = lambda _event: _async_iter([mock_update])
    handler._session_converters[session_id] = mock_converter

    await handler.start_event_consumer(session_id)

    await _put_event_and_wait(queue, _stream_complete_event())

    mock_client.session_update.assert_awaited_once()
    notification = mock_client.session_update.await_args[0][0]
    assert notification.session_id == session_id
    assert notification.update is mock_update

    await handler.stop_event_consumer(session_id)


@pytest.mark.anyio
async def test_subagent_error_sent(handler, mock_pool, mock_client):
    """RunErrorEvent on the queue results in session_update call.

    The notification must carry the correct session_id.
    """
    session_id = "sess-error"
    queue = asyncio.Queue()
    mock_pool.session_pool.event_bus.subscribe.return_value = queue

    mock_update = AgentMessageChunk.text("error-test")
    mock_converter = MagicMock()
    mock_converter.convert = lambda _event: _async_iter([mock_update])
    handler._session_converters[session_id] = mock_converter

    await handler.start_event_consumer(session_id)

    error_event = RunErrorEvent(message="something went wrong")
    await _put_event_and_wait(queue, error_event)

    mock_client.session_update.assert_awaited_once()
    notification = mock_client.session_update.await_args[0][0]
    assert notification.session_id == session_id
    assert notification.update is mock_update

    await handler.stop_event_consumer(session_id)


@pytest.mark.anyio
async def test_no_events_leaked_after_session_close(handler, mock_pool, mock_client):
    """Closing a session cleans up all consumer state and unsubscribes.

    After close_session is called, no tasks or queues should remain for
    that session, and the event bus unsubscribe must have been awaited.
    """
    session_id = "sess-close"
    queue = asyncio.Queue()
    mock_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler.start_event_consumer(session_id)
    assert session_id in handler._consumer_tasks
    assert session_id in handler._consumer_queues

    # Pre-populate converter so close_session can pop it
    handler._session_converters[session_id] = MagicMock()

    await handler.close_session(session_id)

    assert session_id not in handler._consumer_tasks
    assert session_id not in handler._consumer_queues
    assert session_id not in handler._session_converters
    mock_pool.session_pool.event_bus.unsubscribe.assert_awaited()


@pytest.mark.anyio
async def test_handler_ensure_event_consumer_idempotent(handler, mock_pool, mock_client):
    """_ensure_event_consumer is idempotent for the same session.

    Multiple calls for the same session must not create duplicate consumers.
    """
    session_id = "sess-idem"
    queue = asyncio.Queue()
    mock_pool.session_pool.event_bus.subscribe.return_value = queue

    await handler._ensure_event_consumer(session_id)
    first_task = handler._consumer_tasks[session_id]

    await handler._ensure_event_consumer(session_id)
    second_task = handler._consumer_tasks[session_id]

    assert first_task is second_task
    assert mock_pool.session_pool.event_bus.subscribe.await_count == 1

    await handler.stop_event_consumer(session_id)


@pytest.mark.anyio
async def test_handler_ensure_event_consumer_skips_when_disabled(
    mock_pool, mock_event_converter, mock_client
):
    """_ensure_event_consumer does nothing when use_session_pool is False.

    The per-agent canary flag disables SessionPool, so no consumer is started.
    """
    mock_pool.main_agent.metadata = {"use_session_pool": False}
    handler = ACPProtocolHandler(mock_pool, mock_event_converter, mock_client)
    session_id = "sess-disabled"

    await handler._ensure_event_consumer(session_id)

    assert session_id not in handler._consumer_tasks
    mock_pool.session_pool.event_bus.subscribe.assert_not_awaited()
