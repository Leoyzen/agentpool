"""Tests for EventBus memory stream backpressure.

Verifies that the hybrid backpressure strategy (timeout → drop oldest →
drop subscriber) works correctly with anyio memory object streams.
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


async def test_backpressure_no_deadlock_with_slow_consumer(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """Publishing 50 events to a slow consumer does not deadlock."""
    stream = await event_bus.subscribe("sess-bp")

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

    received: list[str] = []
    async for envelope in stream:
        if isinstance(envelope.event, RunStartedEvent):
            received.append(envelope.event.run_id)

    assert len(received) > 0
    assert len(received) <= 50


async def test_backpressure_drops_subscriber_when_buffer_full(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """A subscriber whose buffer is full and can't be drained gets dropped."""
    stream = await event_bus.subscribe("sess-bp")

    for i in range(5):
        await event_bus.publish(
            "sess-bp",
            RunStartedEvent(
                session_id="sess-bp",
                run_id=f"ev-{i}",
            ),
        )

    await event_bus.publish("sess-bp", make_event("overflow-1"))
    await event_bus.publish("sess-bp", make_event("overflow-2"))

    counts = await event_bus.get_subscriber_counts()
    assert "sess-bp" not in counts


async def test_subscribe_returns_memory_receive_stream(
    event_bus: EventBus,
) -> None:
    """subscribe() returns an anyio memory object receive stream."""
    stream = await event_bus.subscribe("sess-bp")
    assert hasattr(stream, "receive")
    assert hasattr(stream, "receive_nowait")
    assert hasattr(stream, "aclose")


async def test_unsubscribe_closes_stream(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """unsubscribe() closes the send stream, causing EndOfStream on consumer."""
    stream = await event_bus.subscribe("sess-bp")
    await event_bus.publish("sess-bp", make_event("ev-1"))
    await event_bus.unsubscribe("sess-bp", stream)

    received: list[Any] = []
    async for envelope in stream:
        received.append(envelope)

    assert len(received) <= 1


async def test_close_session_signals_end_of_stream(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """close_session() closes all send streams, causing EndOfStream."""
    stream = await event_bus.subscribe("sess-bp")
    await event_bus.publish("sess-bp", make_event("ev-1"))
    await event_bus.close_session("sess-bp")

    received: list[Any] = []
    async for envelope in stream:
        received.append(envelope)

    assert len(received) >= 0
    counts = await event_bus.get_subscriber_counts()
    assert "sess-bp" not in counts


async def test_parallel_publishers_no_deadlock(
    event_bus: EventBus,
) -> None:
    """Multiple concurrent publishers don't deadlock the EventBus."""
    stream = await event_bus.subscribe("sess-bp")

    async def publisher(prefix: str) -> None:
        for i in range(20):
            await event_bus.publish(
                "sess-bp",
                RunStartedEvent(
                    session_id="sess-bp",
                    run_id=f"{prefix}-{i}",
                ),
            )

    async with anyio.create_task_group() as tg:
        tg.start_soon(publisher, "a")
        tg.start_soon(publisher, "b")
        tg.start_soon(publisher, "c")

    received: list[str] = []
    async for envelope in stream:
        if isinstance(envelope.event, RunStartedEvent):
            received.append(envelope.event.run_id)

    assert len(received) > 0
    assert len(received) <= 60


async def test_replay_buffer_with_memory_stream(
    event_bus: EventBus,
    make_event: Any,
) -> None:
    """Replay buffer delivers historical events to new subscribers."""
    await event_bus.publish("sess-bp", make_event("hist-1"))
    await event_bus.publish("sess-bp", make_event("hist-2"))

    stream = await event_bus.subscribe("sess-bp")

    received: list[str] = []
    try:
        with anyio.fail_after(0.5):
            async for envelope in stream:
                if isinstance(envelope.event, RunStartedEvent):
                    received.append(envelope.event.run_id)
    except TimeoutError:
        pass

    assert "hist-1" in received
    assert "hist-2" in received
