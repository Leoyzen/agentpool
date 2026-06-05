"""Unit tests for EventBus (SessionPool Group 2.10).

Tests pub/sub semantics, bounded queue dropping, sentinel-based
shutdown, and subscriber lifecycle management.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def event_bus() -> EventBus:
    """Return a fresh EventBus with small queue for deterministic tests."""
    return EventBus(max_queue_size=3)


@pytest.fixture
def sample_event() -> RunStartedEvent:
    """Return a sample RichAgentStreamEvent for publishing."""
    return RunStartedEvent(session_id="sess-1", run_id="run-1")


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subscribe_creates_queue(event_bus: EventBus) -> None:
    """subscribe() returns an asyncio.Queue bound to the session."""
    queue = await event_bus.subscribe("sess-1")
    assert isinstance(queue, asyncio.Queue)
    assert queue.maxsize == 3


@pytest.mark.anyio
async def test_subscribe_multiple_queues_same_session(event_bus: EventBus) -> None:
    """Multiple subscribers for the same session each get their own queue."""
    q1 = await event_bus.subscribe("sess-1")
    q2 = await event_bus.subscribe("sess-1")
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 2
    assert q1 is not q2


@pytest.mark.anyio
async def test_unsubscribe_removes_queue(event_bus: EventBus) -> None:
    """unsubscribe() removes the specific queue and cleans up empty lists."""
    q1 = await event_bus.subscribe("sess-1")
    q2 = await event_bus.subscribe("sess-1")
    await event_bus.unsubscribe("sess-1", q1)
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 1
    await event_bus.unsubscribe("sess-1", q2)
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


@pytest.mark.anyio
async def test_unsubscribe_unknown_session_noop(event_bus: EventBus) -> None:
    """Unsubscribing from a non-existent session is a no-op."""
    q = asyncio.Queue()
    await event_bus.unsubscribe("missing", q)
    counts = await event_bus.get_subscriber_counts()
    assert counts == {}


@pytest.mark.anyio
async def test_unsubscribe_wrong_queue_noop(event_bus: EventBus) -> None:
    """Unsubscribing a queue that was never subscribed is a no-op."""
    q_real = await event_bus.subscribe("sess-1")
    q_fake = asyncio.Queue()
    await event_bus.unsubscribe("sess-1", q_fake)
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 1
    _ = q_real  # keep reference for type checker


# ---------------------------------------------------------------------------
# Publish – single & multiple subscribers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_single_subscriber(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """A published event reaches the subscriber queue."""
    queue = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-1"


@pytest.mark.anyio
async def test_publish_multiple_subscribers(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Each subscriber receives an independent shallow copy of the event."""
    q1 = await event_bus.subscribe("sess-1")
    q2 = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    ev1 = await asyncio.wait_for(q1.get(), timeout=0.5)
    ev2 = await asyncio.wait_for(q2.get(), timeout=0.5)
    assert ev1 is not None
    assert ev2 is not None
    assert ev1 is not ev2  # shallow copy
    assert isinstance(ev1, RunStartedEvent)
    assert isinstance(ev2, RunStartedEvent)
    assert ev1.run_id == ev2.run_id


@pytest.mark.anyio
async def test_publish_no_subscribers_is_noop(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Publishing to a session with no subscribers does not raise."""
    await event_bus.publish("sess-1", sample_event)
    counts = await event_bus.get_subscriber_counts()
    assert counts == {}


@pytest.mark.anyio
async def test_publish_different_sessions_isolated(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Events are only delivered to queues for the matching session_id."""
    q1 = await event_bus.subscribe("sess-1")
    q2 = await event_bus.subscribe("sess-2")
    await event_bus.publish("sess-1", sample_event)
    received = await asyncio.wait_for(q1.get(), timeout=0.5)
    assert received is not None
    assert q2.empty()
    _ = q2  # silence unused-variable warning


# ---------------------------------------------------------------------------
# Bounded queue dropping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_drops_oldest_when_queue_full(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """When a subscriber queue is full, the oldest event is dropped."""
    queue = await event_bus.subscribe("sess-1")
    ev_old = RunStartedEvent(session_id="sess-1", run_id="old")
    ev_mid = RunStartedEvent(session_id="sess-1", run_id="mid")
    ev_new = RunStartedEvent(session_id="sess-1", run_id="new")
    # Fill queue to capacity (maxsize=3)
    await event_bus.publish("sess-1", ev_old)
    await event_bus.publish("sess-1", ev_mid)
    await event_bus.publish("sess-1", sample_event)
    # Queue is now full; publish another -> oldest dropped
    await event_bus.publish("sess-1", ev_new)
    # Drain queue
    items: list[Any] = []
    while not queue.empty():
        items.append(await queue.get())
    run_ids = []
    for e in items:
        if isinstance(e, RunStartedEvent):
            run_ids.append(e.run_id)
    assert "old" not in run_ids
    assert run_ids == ["mid", "run-1", "new"]


@pytest.mark.anyio
async def test_publish_removes_dead_queue_after_drop_failure(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """If dropping + re-adding fails repeatedly, the subscriber is removed."""
    queue = await event_bus.subscribe("sess-1")
    # Fill queue
    for i in range(3):
        await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    # Now make the queue "broken" by replacing put_nowait with a raiser
    original_put = queue.put_nowait

    def broken_put(_item: Any) -> None:
        raise asyncio.QueueFull

    def broken_get() -> Any:
        raise asyncio.QueueEmpty

    queue.put_nowait = broken_put  # type: ignore[method-assign]
    queue.get_nowait = broken_get  # type: ignore[method-assign]
    await event_bus.publish("sess-1", sample_event)
    # Restore so we can inspect
    queue.put_nowait = original_put  # type: ignore[method-assign]
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


@pytest.mark.anyio
async def test_publish_exception_removes_dead_subscriber(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Subscribers that raise arbitrary exceptions on put are removed."""
    queue = await event_bus.subscribe("sess-1")

    def raiser(_item: Any) -> None:
        raise RuntimeError("boom")

    queue.put_nowait = raiser  # type: ignore[method-assign]
    await event_bus.publish("sess-1", sample_event)
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


# ---------------------------------------------------------------------------
# close_session / sentinel shutdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_sends_sentinel(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """close_session() puts None sentinel into every subscriber queue."""
    q1 = await event_bus.subscribe("sess-1")
    q2 = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    await event_bus.close_session("sess-1")
    for q in (q1, q2):
        ev = await asyncio.wait_for(q.get(), timeout=0.5)
        assert ev is not None
        sentinel = await asyncio.wait_for(q.get(), timeout=0.5)
        assert sentinel is None


@pytest.mark.anyio
async def test_close_session_drains_full_queue_to_fit_sentinel(
    event_bus: EventBus,
) -> None:
    """If queue is full, close_session drains events until sentinel fits."""
    queue = await event_bus.subscribe("sess-1")
    for i in range(3):
        await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    assert queue.full()
    await event_bus.close_session("sess-1")
    # Drain everything
    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    assert items[-1] is None


@pytest.mark.anyio
async def test_close_session_removes_all_subscribers(
    event_bus: EventBus,
) -> None:
    """After close_session, no subscribers remain for that session."""
    await event_bus.subscribe("sess-1")
    await event_bus.subscribe("sess-1")
    await event_bus.close_session("sess-1")
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


@pytest.mark.anyio
async def test_close_session_unknown_session_noop(event_bus: EventBus) -> None:
    """Closing a session that never had subscribers is a no-op."""
    await event_bus.close_session("missing")
    counts = await event_bus.get_subscriber_counts()
    assert counts == {}


# ---------------------------------------------------------------------------
# get_subscriber_counts
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_get_subscriber_counts_returns_snapshot(event_bus: EventBus) -> None:
    """get_subscriber_counts returns a snapshot of subscriber counts."""
    await event_bus.subscribe("sess-a")
    await event_bus.subscribe("sess-a")
    await event_bus.subscribe("sess-b")
    counts = await event_bus.get_subscriber_counts()
    assert counts == {"sess-a": 2, "sess-b": 1}
