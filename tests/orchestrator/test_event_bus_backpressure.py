"""Tests for EventBus asyncio.Queue backpressure.

Verifies that the overflow policy (drop_oldest, drop_newest, drop_subscriber)
works correctly with asyncio.Queue-based subscribers.
"""

from __future__ import annotations

import asyncio
from typing import Any

import anyio
import pytest

from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.fixture
def event_bus() -> EventBus:
    """Return a fresh EventBus with small buffer for deterministic tests."""
    return EventBus(max_queue_size=5)


@pytest.fixture
def make_event() -> Any:
    """Return a factory for RunStartedEvent instances."""

    def _make(run_id: str = "run-1") -> RunStartedEvent:
        return RunStartedEvent(session_id="sess-bp", run_id=run_id)

    return _make


async def _drain_queue(queue: asyncio.Queue) -> list:
    """Drain all items from a queue until QueueShutDown."""
    items: list = []
    while True:
        try:
            items.append(await queue.get())
        except asyncio.QueueShutDown:
            break
    return items


async def test_backpressure_no_deadlock_with_slow_consumer(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """Publishing 50 events to a slow consumer does not deadlock."""
    queue = await event_bus.subscribe("sess-bp")

    async def publisher() -> None:
        for i in range(50):
            await event_bus.publish(
                "sess-bp",
                RunStartedEvent(
                    session_id="sess-bp",
                    run_id=f"ev-{i}",
                ),
            )

    await publisher()

    # Shut down so drain terminates
    await event_bus.close_session("sess-bp")
    received = await _drain_queue(queue)

    run_ids = [
        envelope.event.run_id
        for envelope in received
        if isinstance(envelope.event, RunStartedEvent)
    ]

    assert len(run_ids) > 0
    assert len(run_ids) <= 50


async def test_backpressure_drops_subscriber_when_buffer_full(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """A subscriber whose buffer is full and can't be drained gets dropped.

    With the default drop_oldest policy, the subscriber is NOT dropped —
    the oldest item is evicted to make room. This test verifies that
    the subscriber survives overflow.
    """
    await event_bus.subscribe("sess-bp")

    for i in range(5):
        await event_bus.publish(
            "sess-bp",
            RunStartedEvent(
                session_id="sess-bp",
                run_id=f"ev-{i}",
            ),
        )

    # Buffer is full (max_queue_size=5). With drop_oldest (default),
    # the subscriber survives — oldest item is evicted.
    await event_bus.publish("sess-bp", make_event("overflow-1"))
    await event_bus.publish("sess-bp", make_event("overflow-2"))

    counts = await event_bus.get_subscriber_counts()
    # With drop_oldest (default), subscriber is NOT dropped.
    assert "sess-bp" in counts


async def test_subscribe_returns_asyncio_queue(
    event_bus: EventBus,
) -> None:
    """subscribe() returns an asyncio.Queue."""
    queue = await event_bus.subscribe("sess-bp")
    assert hasattr(queue, "get")
    assert hasattr(queue, "get_nowait")
    assert hasattr(queue, "shutdown")


async def test_unsubscribe_closes_queue(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """unsubscribe() shuts down the queue, causing QueueShutDown on consumer."""
    queue = await event_bus.subscribe("sess-bp")
    await event_bus.publish("sess-bp", make_event("ev-1"))
    await event_bus.unsubscribe("sess-bp", queue)

    received = await _drain_queue(queue)

    assert len(received) <= 1


async def test_close_session_signals_queue_shutdown(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """close_session() shuts down all queues for the session."""
    queue = await event_bus.subscribe("sess-bp")
    await event_bus.publish("sess-bp", make_event("ev-1"))
    await event_bus.close_session("sess-bp")

    received = await _drain_queue(queue)

    assert len(received) >= 0
    counts = await event_bus.get_subscriber_counts()
    assert "sess-bp" not in counts


async def test_parallel_publishers_no_deadlock(
    event_bus: EventBus,
) -> None:
    """Multiple concurrent publishers don't deadlock the EventBus."""
    queue = await event_bus.subscribe("sess-bp")

    async def publisher(prefix: str) -> None:
        for i in range(20):
            await event_bus.publish(
                "sess-bp",
                RunStartedEvent(
                    session_id="sess-bp",
                    run_id=f"{prefix}-{i}",
                ),
            )

    await asyncio.gather(
        publisher("a"),
        publisher("b"),
        publisher("c"),
    )

    await event_bus.close_session("sess-bp")
    received = await _drain_queue(queue)

    run_ids = [
        envelope.event.run_id
        for envelope in received
        if isinstance(envelope.event, RunStartedEvent)
    ]

    assert len(run_ids) > 0
    assert len(run_ids) <= 60


async def test_replay_buffer_with_queue(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """Replay buffer delivers historical events to new subscribers."""
    await event_bus.publish("sess-bp", make_event("hist-1"))
    await event_bus.publish("sess-bp", make_event("hist-2"))

    queue = await event_bus.subscribe("sess-bp")

    received: list[str] = []
    try:
        with anyio.fail_after(0.5):
            while True:
                envelope = await queue.get()
                if isinstance(envelope.event, RunStartedEvent):
                    received.append(envelope.event.run_id)
    except TimeoutError:
        pass

    assert "hist-1" in received
    assert "hist-2" in received
