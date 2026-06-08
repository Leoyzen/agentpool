"""Unit tests for EventBus (SessionPool Group 2.10).

Tests pub/sub semantics, bounded queue dropping, sentinel-based
shutdown, and subscriber lifecycle management.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.agents.events import PartDeltaEvent, PartStartEvent, RunStartedEvent
from agentpool.orchestrator.core import EventBus
from pydantic_ai import PartEndEvent, TextPart, TextPartDelta


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


# ---------------------------------------------------------------------------
# Replay buffer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replay_buffer_bounds(event_bus: EventBus) -> None:
    """Publishing more events than replay_buffer_size drops oldest."""
    for i in range(150):
        await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    buffer = event_bus._replay_buffers["sess-1"]
    assert len(buffer) == 100
    run_ids = [e.run_id for e in buffer]
    assert run_ids[0] == "ev50"
    assert run_ids[-1] == "ev149"


@pytest.mark.anyio
async def test_replay_buffer_cleared_on_session_close(event_bus: EventBus) -> None:
    """close_session removes the replay buffer for the session."""
    await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="ev1"))
    assert "sess-1" in event_bus._replay_buffers
    await event_bus.close_session("sess-1")
    assert "sess-1" not in event_bus._replay_buffers


@pytest.mark.anyio
async def test_replay_buffer_events_in_order(event_bus: EventBus) -> None:
    """Events in the replay buffer are stored oldest-to-newest."""
    for i in range(5):
        await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    buffer = event_bus._replay_buffers["sess-1"]
    assert len(buffer) == 5
    run_ids = [e.run_id for e in buffer]
    assert run_ids == ["ev0", "ev1", "ev2", "ev3", "ev4"]


@pytest.mark.anyio
async def test_replay_buffer_per_session_isolated(event_bus: EventBus) -> None:
    """Each session has its own independent replay buffer."""
    await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="a"))
    await event_bus.publish("sess-2", RunStartedEvent(session_id="sess-2", run_id="b"))
    assert event_bus._replay_buffers["sess-1"][0].run_id == "a"
    assert event_bus._replay_buffers["sess-2"][0].run_id == "b"


@pytest.mark.anyio
async def test_replay_buffer_custom_size() -> None:
    """EventBus accepts a custom replay_buffer_size."""
    bus = EventBus(max_queue_size=3, replay_buffer_size=10)
    for i in range(15):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    assert len(bus._replay_buffers["sess-1"]) == 10
    assert bus._replay_buffers["sess-1"][0].run_id == "ev5"
    assert bus._replay_buffers["sess-1"][-1].run_id == "ev14"


# ---------------------------------------------------------------------------
# Replay protocol
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replay_protocol_new_subscriber_gets_historical() -> None:
    """New subscriber receives last N buffered events as replay."""
    bus = EventBus(max_queue_size=10)
    for i in range(5):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}")
        )

    queue = await bus.subscribe("sess-1")

    # Drain all events from the queue
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    assert len(received) == 5
    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    assert run_ids == ["ev0", "ev1", "ev2", "ev3", "ev4"]


@pytest.mark.anyio
async def test_replay_protocol_ordering() -> None:
    """Replayed events precede live events in the queue."""
    bus = EventBus(max_queue_size=10)
    for i in range(3):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"hist-{i}")
        )

    queue = await bus.subscribe("sess-1")

    # Publish more events after subscription
    for i in range(2):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"live-{i}")
        )

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    assert run_ids == ["hist-0", "hist-1", "hist-2", "live-0", "live-1"]


@pytest.mark.anyio
async def test_replay_protocol_no_duplicates() -> None:
    """No duplicate events when publish happens during subscribe replay."""
    bus = EventBus(max_queue_size=10)
    for i in range(5):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}")
        )

    queue = await bus.subscribe("sess-1")

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"


@pytest.mark.anyio
async def test_replay_protocol_race_condition() -> None:
    """Subscribe concurrently with publishes; all events arrive in order."""
    bus = EventBus(max_queue_size=10)

    # Publish initial historical events
    for i in range(3):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"hist-{i}")
        )

    # Start subscribe concurrently with more publishes
    subscribe_task = asyncio.create_task(bus.subscribe("sess-1"))
    publish_tasks = [
        asyncio.create_task(
            bus.publish(
                "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"race-{i}")
            )
        )
        for i in range(3)
    ]

    queue = await subscribe_task
    await asyncio.gather(*publish_tasks)

    # Publish final live events
    for i in range(2):
        await bus.publish(
            "sess-1", RunStartedEvent(session_id="sess-1", run_id=f"live-{i}")
        )

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]

    # All 8 events should be present
    assert len(run_ids) == 8, f"Expected 8 events, got {len(run_ids)}: {run_ids}"

    # No duplicates
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"

    # Historical events come first (oldest three)
    assert run_ids[:3] == ["hist-0", "hist-1", "hist-2"]


# ---------------------------------------------------------------------------
# SSE event ordering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_event_ordering_replay_then_live() -> None:
    """Replayed PartStart→PartDelta→PartEnd events precede live events in queue."""
    bus = EventBus(max_queue_size=10)

    # Publish initial SSE sequence (will be replayed)
    await bus.publish(
        "sess-1",
        PartStartEvent(index=0, part=TextPart(content="hello")),
    )
    await bus.publish(
        "sess-1",
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world")),
    )
    await bus.publish(
        "sess-1",
        PartEndEvent(index=0, part=TextPart(content="hello world")),
    )

    queue = await bus.subscribe("sess-1")

    # Publish live SSE sequence after subscription
    await bus.publish(
        "sess-1",
        PartStartEvent(index=1, part=TextPart(content="goodbye")),
    )
    await bus.publish(
        "sess-1",
        PartDeltaEvent(index=1, delta=TextPartDelta(content_delta=" world")),
    )
    await bus.publish(
        "sess-1",
        PartEndEvent(index=1, part=TextPart(content="goodbye world")),
    )

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    # Verify 6 events total
    assert len(received) == 6

    # Verify replayed events come first, then live events
    assert isinstance(received[0], PartStartEvent)
    assert received[0].index == 0
    assert isinstance(received[1], PartDeltaEvent)
    assert received[1].index == 0
    assert isinstance(received[2], PartEndEvent)
    assert received[2].index == 0

    assert isinstance(received[3], PartStartEvent)
    assert received[3].index == 1
    assert isinstance(received[4], PartDeltaEvent)
    assert received[4].index == 1
    assert isinstance(received[5], PartEndEvent)
    assert received[5].index == 1


@pytest.mark.anyio
async def test_event_ordering_no_gaps_in_replay() -> None:
    """Replay buffer eviction drops oldest events; subscriber sees contiguous range."""
    bus = EventBus(max_queue_size=200, replay_buffer_size=100)

    # Publish 100 events (fills buffer exactly)
    for i in range(100):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    # Publish 50 more (evicts oldest 50: ev0-ev49)
    for i in range(100, 150):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    queue = await bus.subscribe("sess-1")

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    # Should receive exactly 100 events (ev50-ev149)
    assert len(received) == 100

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    expected = [f"ev{i}" for i in range(50, 150)]
    assert run_ids == expected

    # Verify no gaps and strict monotonic ordering
    for i, rid in enumerate(run_ids):
        assert rid == f"ev{i + 50}"


@pytest.mark.anyio
async def test_event_ordering_concurrent_publish() -> None:
    """Concurrent publishers preserve per-task event ordering in replay buffer."""
    bus = EventBus(max_queue_size=200, replay_buffer_size=100)

    async def publisher(task_id: int, count: int) -> None:
        for i in range(count):
            await bus.publish(
                "sess-1",
                RunStartedEvent(session_id="sess-1", run_id=f"task{task_id}-ev{i}"),
            )

    # Launch 5 concurrent publishers, each emitting 20 events
    tasks = [asyncio.create_task(publisher(tid, 20)) for tid in range(5)]
    await asyncio.gather(*tasks)

    queue = await bus.subscribe("sess-1")

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    # All 100 events should be present
    assert len(received) == 100

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    assert len(run_ids) == 100
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"

    # Verify each task's events are in relative order
    for tid in range(5):
        task_events = [rid for rid in run_ids if rid.startswith(f"task{tid}-")]
        expected = [f"task{tid}-ev{i}" for i in range(20)]
        assert task_events == expected, (
            f"Task {tid} events out of order: {task_events}"
        )


@pytest.mark.anyio
async def test_event_ordering_mixed_sessions() -> None:
    """Events from different sessions are isolated; subscriber sees only its session."""
    bus = EventBus(max_queue_size=10)

    # Interleave events across three sessions
    for i in range(5):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"s1-ev{i}"))
        await bus.publish("sess-2", RunStartedEvent(session_id="sess-2", run_id=f"s2-ev{i}"))
        await bus.publish("sess-3", RunStartedEvent(session_id="sess-3", run_id=f"s3-ev{i}"))

    queue = await bus.subscribe("sess-1")

    # Drain all events
    received: list[Any] = []
    while not queue.empty():
        received.append(queue.get_nowait())

    # Should receive only sess-1 events
    assert len(received) == 5

    run_ids = [e.run_id for e in received if isinstance(e, RunStartedEvent)]
    assert run_ids == ["s1-ev0", "s1-ev1", "s1-ev2", "s1-ev3", "s1-ev4"]

    # Verify no cross-session leakage
    for e in received:
        if isinstance(e, RunStartedEvent):
            assert e.session_id == "sess-1"


# ---------------------------------------------------------------------------
# Descendants scope
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_child_events_visible_with_descendants_scope(event_bus: EventBus) -> None:
    """Parent subscriber with scope='descendants' receives child session events."""
    event_bus._session_tree["parent"] = ["child"]
    queue = await event_bus.subscribe("parent", scope="descendants")
    child_event = RunStartedEvent(session_id="child", run_id="run-child")
    await event_bus.publish("child", child_event)
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-child"


@pytest.mark.anyio
async def test_child_events_not_visible_with_session_scope(event_bus: EventBus) -> None:
    """Parent subscriber with scope='session' does NOT receive child session events."""
    event_bus._session_tree["parent"] = ["child"]
    queue = await event_bus.subscribe("parent", scope="session")
    child_event = RunStartedEvent(session_id="child", run_id="run-child")
    await event_bus.publish("child", child_event)
    assert queue.empty()


@pytest.mark.anyio
async def test_event_ordering_parent_and_child() -> None:
    """Events from parent and child arrive in correct interleaved order."""
    event_bus = EventBus(max_queue_size=10)
    event_bus._session_tree["parent"] = ["child"]
    queue = await event_bus.subscribe("parent", scope="descendants")
    events = [
        ("parent", "run-1"),
        ("child", "run-2"),
        ("parent", "run-3"),
        ("child", "run-4"),
        ("parent", "run-5"),
    ]
    for session_id, run_id in events:
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=run_id)
        )
    received: list[str] = []
    for _ in events:
        ev = await asyncio.wait_for(queue.get(), timeout=0.5)
        assert isinstance(ev, RunStartedEvent)
        received.append(ev.run_id)
    assert received == ["run-1", "run-2", "run-3", "run-4", "run-5"]


@pytest.mark.anyio
async def test_grandchild_events_visible_with_descendants_scope(
    event_bus: EventBus,
) -> None:
    """Parent subscriber with scope='descendants' receives grandchild events."""
    event_bus._session_tree["parent"] = ["child"]
    event_bus._session_tree["child"] = ["grandchild"]
    queue = await event_bus.subscribe("parent", scope="descendants")
    grandchild_event = RunStartedEvent(
        session_id="grandchild", run_id="run-grandchild"
    )
    await event_bus.publish("grandchild", grandchild_event)
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-grandchild"
