"""TDD tests for ProtocolEventConsumerMixin (RED phase).

These tests define the expected behavior of ProtocolEventConsumerMixin.
All tests should FAIL because the mixin methods in
src/agentpool_server/mixins.py are currently skeleton implementations (``...``).
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.events import PartDeltaEvent, SpawnSessionStart
from agentpool.agents.events.events import TextPartDelta
from agentpool_server.mixins import ProtocolEventConsumerMixin


class EventConsumerMixinStub(ProtocolEventConsumerMixin):
    """Concrete mixin subclass for testing."""

    agent_pool: Any

    def __init__(self) -> None:
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._consumer_queues: dict[str, asyncio.Queue[Any]] = {}
        self.handled_events: list[tuple[str, Any]] = []
        self.handled_spawns: list[tuple[str, SpawnSessionStart]] = []

    async def _handle_event(self, session_id: str, event: Any) -> None:
        self.handled_events.append((session_id, event))

    async def _handle_spawn_session_start(
        self, session_id: str, event: SpawnSessionStart
    ) -> None:
        self.handled_spawns.append((session_id, event))


@pytest.fixture
def mock_event_bus():
    """Return a mock EventBus with async subscribe/unsubscribe."""
    bus = MagicMock()
    bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    bus.unsubscribe = AsyncMock(return_value=None)
    return bus


@pytest.fixture
def mixin(mock_event_bus):
    """Return a EventConsumerMixinStub wired to a mock event bus."""
    m = EventConsumerMixinStub()
    m.agent_pool = MagicMock()
    m.agent_pool.session_pool = MagicMock()
    m.agent_pool.session_pool.event_bus = mock_event_bus
    return m


@pytest.mark.asyncio
async def test_start_stop_consumer(mixin, mock_event_bus):
    """Start creates a task and subscribes; stop unsubscribes and cleans up."""
    session_id = "sess-1"

    await mixin.start_event_consumer(session_id)

    mock_event_bus.subscribe.assert_awaited_once_with(
        session_id=session_id,
        scope="descendants",
    )
    assert session_id in mixin._consumer_tasks
    assert isinstance(mixin._consumer_tasks[session_id], asyncio.Task)

    await mixin.stop_event_consumer(session_id)

    mock_event_bus.unsubscribe.assert_awaited_once()
    assert session_id not in mixin._consumer_tasks


@pytest.mark.asyncio
async def test_consumer_forwards_events(mixin, mock_event_bus):
    """Consumer loop reads events from the queue and dispatches to _handle_event."""
    session_id = "sess-1"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    await mixin.start_event_consumer(session_id)

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await queue.put(event)
    await asyncio.sleep(0.05)

    assert len(mixin.handled_events) == 1
    assert mixin.handled_events[0] == (session_id, event)

    await mixin.stop_event_consumer(session_id)


@pytest.mark.asyncio
async def test_consumer_handles_spawn_session_start(mixin, mock_event_bus):
    """SpawnSessionStart triggers _handle_spawn_session_start and child consumer."""
    session_id = "sess-parent"
    child_session_id = "sess-child"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    await mixin.start_event_consumer(session_id)

    event = SpawnSessionStart(
        child_session_id=child_session_id,
        parent_session_id=session_id,
        source_name="test-agent",
        source_type="agent",
        spawn_mechanism="spawn",
        description="test spawn",
    )
    await queue.put(event)
    await asyncio.sleep(0.05)

    assert len(mixin.handled_spawns) == 1
    assert mixin.handled_spawns[0] == (session_id, event)
    assert child_session_id in mixin._consumer_tasks

    await mixin.stop_event_consumer(session_id)
    await mixin.stop_event_consumer(child_session_id)


@pytest.mark.asyncio
async def test_consumer_cleanup_on_none_sentinel(mixin, mock_event_bus):
    """Putting None in the queue causes the consumer loop to exit and clean up."""
    session_id = "sess-1"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    await mixin.start_event_consumer(session_id)
    task = mixin._consumer_tasks[session_id]

    await queue.put(None)
    await asyncio.wait_for(task, timeout=1.0)

    mock_event_bus.unsubscribe.assert_awaited_once()
    assert session_id not in mixin._consumer_tasks


@pytest.mark.asyncio
async def test_consumer_cleanup_on_cancel(mixin, mock_event_bus):
    """Cancelling the consumer task triggers unsubscribe cleanup."""
    session_id = "sess-1"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    await mixin.start_event_consumer(session_id)
    task = mixin._consumer_tasks[session_id]

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    mock_event_bus.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio
async def test_converter_error_resilience(mixin, mock_event_bus):
    """If _handle_event raises, the loop continues processing remaining events."""
    session_id = "sess-1"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    call_count = 0

    async def failing_handle(sid, evt):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("boom")
        mixin.handled_events.append((sid, evt))

    mixin._handle_event = failing_handle

    with patch("agentpool_server.mixins.logger") as mock_logger:
        await mixin.start_event_consumer(session_id)

        event1 = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="first"))
        event2 = PartDeltaEvent(index=1, delta=TextPartDelta(content_delta="second"))
        await queue.put(event1)
        await queue.put(event2)
        await queue.put(None)

        task = mixin._consumer_tasks[session_id]
        await asyncio.wait_for(task, timeout=1.0)

    assert call_count == 2
    assert len(mixin.handled_events) == 1
    assert mixin.handled_events[0][1].delta.content_delta == "second"
    mock_logger.exception.assert_called()


@pytest.mark.asyncio
async def test_no_leaked_subscriptions(mixin, mock_event_bus):
    """Starting and stopping the same session multiple times leaves no leaked state."""
    session_id = "sess-1"
    queue = asyncio.Queue()
    mock_event_bus.subscribe.return_value = queue

    for _ in range(3):
        await mixin.start_event_consumer(session_id)
        await mixin.stop_event_consumer(session_id)

    # After each stop, state should be fully cleaned up.
    assert session_id not in mixin._consumer_tasks
    assert session_id not in mixin._consumer_queues
    # Subscribe and unsubscribe counts must match (no orphaned subscriptions).
    assert mock_event_bus.subscribe.await_count == mock_event_bus.unsubscribe.await_count
    # Each iteration should have produced exactly one subscribe and one unsubscribe.
    assert mock_event_bus.subscribe.await_count == 3
