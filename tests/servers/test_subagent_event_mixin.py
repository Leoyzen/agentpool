"""Unit tests for ProtocolEventConsumerMixin.

Tests consumer lifecycle, event dispatch, graceful shutdown,
and hook invocation for the mixin.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

from pydantic_ai import TextPartDelta
import pytest

from agentpool.agents.events.events import (
    PartDeltaEvent,
    RunErrorEvent,
    SpawnSessionStart,
)
from agentpool.orchestrator.core import EventBus
from agentpool_server.mixins import (
    ConsumerShutdown,
    ProtocolEventConsumerMixin,
)


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class _TestConsumer(ProtocolEventConsumerMixin):
    """Concrete test subclass that records all hook calls."""

    def __init__(self, event_bus: Any) -> None:
        super().__init__()
        self._event_bus = event_bus
        self.handle_event_calls: list[tuple[str, Any]] = []
        self.before_loop_calls: list[str] = []
        self.after_loop_calls: list[str] = []
        self.spawn_session_start_calls: list[tuple[str, SpawnSessionStart]] = []

    @property
    def event_bus(self) -> Any:
        return self._event_bus

    async def _handle_event(self, session_id: str, event: Any) -> None:
        self.handle_event_calls.append((session_id, event))

    async def _before_consumer_loop(self, session_id: str) -> None:
        self.before_loop_calls.append(session_id)

    async def _after_consumer_loop(self, session_id: str) -> None:
        self.after_loop_calls.append(session_id)

    async def _on_spawn_session_start(
        self, session_id: str, event: SpawnSessionStart
    ) -> None:
        self.spawn_session_start_calls.append((session_id, event))


@pytest.fixture
def mock_event_bus() -> AsyncMock:
    """Return a mock EventBus with async subscribe/unsubscribe."""
    bus = AsyncMock(spec=EventBus)
    bus.subscribe = AsyncMock(return_value=asyncio.Queue())
    bus.unsubscribe = AsyncMock(return_value=None)
    return bus


@pytest.mark.anyio
async def test_start_consumer_subscribes_and_runs_loop(mock_event_bus: AsyncMock) -> None:
    """Verify EventBus subscription and consumer task creation."""
    consumer = _TestConsumer(mock_event_bus)
    await consumer.start_event_consumer("sess-1")

    assert "sess-1" in consumer._consumer_tasks
    task = consumer._consumer_tasks["sess-1"]
    assert not task.done()

    mock_event_bus.subscribe.assert_awaited_once_with("sess-1", scope="descendants")

    await consumer.stop_event_consumer("sess-1")


@pytest.mark.anyio
async def test_start_consumer_is_idempotent(mock_event_bus: AsyncMock) -> None:
    """Calling start_event_consumer twice does not create duplicate tasks."""
    consumer = _TestConsumer(mock_event_bus)
    await consumer.start_event_consumer("sess-1")
    task1 = consumer._consumer_tasks["sess-1"]

    await consumer.start_event_consumer("sess-1")
    task2 = consumer._consumer_tasks["sess-1"]

    assert task1 is task2
    assert len(consumer._consumer_tasks) == 1

    await consumer.stop_event_consumer("sess-1")


@pytest.mark.anyio
async def test_start_consumer_is_threadsafe(mock_event_bus: AsyncMock) -> None:
    """Concurrent calls for the same session are serialized."""
    consumer = _TestConsumer(mock_event_bus)

    async def start() -> None:
        await consumer.start_event_consumer("sess-1")

    await asyncio.gather(start(), start())

    assert len(consumer._consumer_tasks) == 1

    await consumer.stop_event_consumer("sess-1")


@pytest.mark.anyio
async def test_stop_consumer_cancels_task_and_unsubscribes(
    mock_event_bus: AsyncMock,
) -> None:
    """Stopping a consumer cancels the task and unsubscribes from EventBus."""
    consumer = _TestConsumer(mock_event_bus)
    await consumer.start_event_consumer("sess-1")
    task = consumer._consumer_tasks["sess-1"]

    await consumer.stop_event_consumer("sess-1")

    assert task.done()
    assert "sess-1" not in consumer._consumer_tasks
    assert mock_event_bus.unsubscribe.await_count >= 1


@pytest.mark.anyio
async def test_stop_consumer_is_safe_when_not_running(mock_event_bus: AsyncMock) -> None:
    """Calling stop_event_consumer without starting should not raise."""
    consumer = _TestConsumer(mock_event_bus)
    await consumer.stop_event_consumer("sess-1")


@pytest.mark.anyio
async def test_handle_event_dispatches_to_subclass(mock_event_bus: AsyncMock) -> None:
    """Verify abstract _handle_event is called with correct arguments."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)
    mock_handle = AsyncMock()
    consumer._handle_event = mock_handle  # type: ignore[method-assign]

    event = PartDeltaEvent(index=0, delta=TextPartDelta(content_delta="hello"))
    await queue.put(event)

    await consumer.start_event_consumer("sess-1")

    for _ in range(100):
        if mock_handle.await_count > 0:
            break
        await asyncio.sleep(0.01)

    mock_handle.assert_awaited_once_with("sess-1", event)

    await consumer.stop_event_consumer("sess-1")


@pytest.mark.anyio
async def test_consumer_shutdown_gracefully_stops_loop(
    mock_event_bus: AsyncMock,
) -> None:
    """_handle_event raises ConsumerShutdown, loop exits gracefully."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)
    consumer._handle_event = AsyncMock(  # type: ignore[method-assign]
        side_effect=ConsumerShutdown()
    )

    event = RunErrorEvent(message="shutdown-test")
    await queue.put(event)

    await consumer.start_event_consumer("sess-1")

    task = consumer._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
    assert "sess-1" not in consumer._consumer_tasks
    assert "sess-1" in consumer.after_loop_calls
    mock_event_bus.unsubscribe.assert_awaited()


@pytest.mark.anyio
async def test_unhandled_exception_unsubscribes_in_finally(
    mock_event_bus: AsyncMock,
) -> None:
    """_handle_event raises generic Exception; unsubscribe and after hook run."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)
    consumer._handle_event = AsyncMock(  # type: ignore[method-assign]
        side_effect=RuntimeError("boom")
    )

    event = RunErrorEvent(message="boom-test")
    await queue.put(event)

    await consumer.start_event_consumer("sess-1")

    task = consumer._consumer_tasks["sess-1"]
    with pytest.raises(RuntimeError, match="boom"):
        await asyncio.wait_for(task, timeout=0.5)

    assert "sess-1" not in consumer._consumer_tasks
    assert "sess-1" in consumer.after_loop_calls
    mock_event_bus.unsubscribe.assert_awaited()


@pytest.mark.anyio
async def test_cancelled_error_reraised_after_cleanup(
    mock_event_bus: AsyncMock,
) -> None:
    """Cancel the consumer task mid-loop; CancelledError propagates, cleanup runs."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)
    await consumer.start_event_consumer("sess-1")
    # Ensure the task has reached queue.get() before we cancel it.
    await asyncio.sleep(0.01)

    task = consumer._consumer_tasks["sess-1"]
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert "sess-1" not in consumer._consumer_tasks
    mock_event_bus.unsubscribe.assert_awaited()


@pytest.mark.anyio
async def test_none_sentinel_stops_loop(mock_event_bus: AsyncMock) -> None:
    """Put None in queue; loop exits gracefully and after hook runs."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)
    await queue.put(None)

    await consumer.start_event_consumer("sess-1")

    task = consumer._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert task.done()
    assert "sess-1" not in consumer._consumer_tasks
    assert "sess-1" in consumer.after_loop_calls
    mock_event_bus.unsubscribe.assert_awaited()


@pytest.mark.anyio
async def test_spawn_session_start_calls_hook(mock_event_bus: AsyncMock) -> None:
    """SpawnSessionStart triggers _on_spawn_session_start then _handle_event."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)

    event = SpawnSessionStart(
        child_session_id="child-1",
        parent_session_id="sess-1",
        source_name="test-agent",
        source_type="agent",
        depth=1,
        description="test spawn",
        spawn_mechanism="spawn",
    )
    await queue.put(event)
    await queue.put(None)

    await consumer.start_event_consumer("sess-1")

    task = consumer._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert len(consumer.spawn_session_start_calls) == 1
    assert consumer.spawn_session_start_calls[0] == ("sess-1", event)
    assert consumer.handle_event_calls == [("sess-1", event)]


@pytest.mark.anyio
async def test_before_after_hooks_called_in_order(mock_event_bus: AsyncMock) -> None:
    """_before_consumer_loop runs before loop, _after_consumer_loop after exit."""
    queue = asyncio.Queue()
    mock_event_bus.subscribe = AsyncMock(return_value=queue)

    consumer = _TestConsumer(mock_event_bus)

    await consumer.start_event_consumer("sess-1")
    await asyncio.sleep(0.05)

    assert consumer.before_loop_calls == ["sess-1"]

    await queue.put(None)

    task = consumer._consumer_tasks["sess-1"]
    await asyncio.wait_for(task, timeout=0.5)

    assert consumer.after_loop_calls == ["sess-1"]
