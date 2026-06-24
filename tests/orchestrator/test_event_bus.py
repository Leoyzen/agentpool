"""Unit tests for EventBus (SessionPool Group 2.10).

Tests pub/sub semantics, bounded stream dropping, EndOfStream-based
shutdown, and subscriber lifecycle management.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import anyio
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
    """Return a fresh EventBus with small buffer for deterministic tests."""
    return EventBus(max_queue_size=3)


@pytest.fixture
def sample_event() -> RunStartedEvent:
    """Return a sample RichAgentStreamEvent for publishing."""
    return RunStartedEvent(session_id="sess-1", run_id="run-1")


async def _drain_stream(stream: anyio.abc.ObjectReceiveStream[Any]) -> list[Any]:
    """Drain all available items from a memory receive stream without blocking."""
    items: list[Any] = []
    while True:
        try:
            items.append(stream.receive_nowait())
        except (anyio.WouldBlock, anyio.EndOfStream, anyio.ClosedResourceError):
            break
    return items


async def _receive_one(
    stream: anyio.abc.ObjectReceiveStream[Any], timeout: float = 0.5
) -> Any | None:
    """Receive one item from a stream with a timeout."""
    try:
        with anyio.fail_after(timeout):
            return await stream.receive()
    except TimeoutError:
        return None


# ---------------------------------------------------------------------------
# Subscribe / Unsubscribe
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_subscribe_creates_receive_stream(event_bus: EventBus) -> None:
    """subscribe() returns a memory object receive stream."""
    stream = await event_bus.subscribe("sess-1")
    assert hasattr(stream, "receive")
    assert hasattr(stream, "receive_nowait")


@pytest.mark.anyio
async def test_subscribe_multiple_streams_same_session(event_bus: EventBus) -> None:
    """Multiple subscribers for the same session each get their own stream."""
    s1 = await event_bus.subscribe("sess-1")
    s2 = await event_bus.subscribe("sess-1")
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 2
    assert s1 is not s2


@pytest.mark.anyio
async def test_unsubscribe_removes_stream(event_bus: EventBus) -> None:
    """unsubscribe() removes the specific stream and cleans up empty lists."""
    s1 = await event_bus.subscribe("sess-1")
    s2 = await event_bus.subscribe("sess-1")
    await event_bus.unsubscribe("sess-1", s1)
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 1
    await event_bus.unsubscribe("sess-1", s2)
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


@pytest.mark.anyio
async def test_unsubscribe_unknown_session_noop(event_bus: EventBus) -> None:
    """Unsubscribing from a non-existent session is a no-op."""
    send, recv = anyio.create_memory_object_stream(max_buffer_size=10)
    await event_bus.unsubscribe("missing", recv)
    counts = await event_bus.get_subscriber_counts()
    assert counts == {}


@pytest.mark.anyio
async def test_unsubscribe_wrong_stream_noop(event_bus: EventBus) -> None:
    """Unsubscribing a stream that was never subscribed is a no-op."""
    s_real = await event_bus.subscribe("sess-1")
    send, recv_fake = anyio.create_memory_object_stream(max_buffer_size=10)
    await event_bus.unsubscribe("sess-1", recv_fake)
    counts = await event_bus.get_subscriber_counts()
    assert counts["sess-1"] == 1
    _ = s_real


# ---------------------------------------------------------------------------
# Publish – single & multiple subscribers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_single_subscriber(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """A published event reaches the subscriber stream."""
    stream = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    received = await _receive_one(stream)
    assert received is not None
    assert isinstance(received.event, RunStartedEvent)
    assert received.event.run_id == "run-1"


@pytest.mark.anyio
async def test_publish_multiple_subscribers(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Each subscriber receives an independent shallow copy of the event."""
    s1 = await event_bus.subscribe("sess-1")
    s2 = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    ev1 = await _receive_one(s1)
    ev2 = await _receive_one(s2)
    assert ev1 is not None
    assert ev2 is not None
    assert ev1 == ev2
    assert isinstance(ev1.event, RunStartedEvent)
    assert isinstance(ev2.event, RunStartedEvent)
    assert ev1.event.run_id == ev2.event.run_id


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
    """Events are only delivered to streams for the matching session_id."""
    s1 = await event_bus.subscribe("sess-1")
    s2 = await event_bus.subscribe("sess-2")
    await event_bus.publish("sess-1", sample_event)
    received = await _receive_one(s1)
    assert received is not None
    s2_items = await _drain_stream(s2)
    assert len(s2_items) == 0


# ---------------------------------------------------------------------------
# Bounded stream dropping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_drops_subscriber_when_buffer_full(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """When a subscriber buffer is full and can't drain, subscriber is dropped."""
    stream = await event_bus.subscribe("sess-1")
    for i in range(3):
        await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="ev3"))
    await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="ev4"))

    items = await _drain_stream(stream)
    run_ids = [e.event.run_id for e in items if isinstance(e.event, RunStartedEvent)]
    assert len(run_ids) <= 3
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


@pytest.mark.anyio
async def test_publish_removes_dead_subscriber_on_broken_resource(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """Subscribers with broken send streams are removed."""
    stream = await event_bus.subscribe("sess-1")
    await stream.aclose()
    await event_bus.publish("sess-1", sample_event)
    counts = await event_bus.get_subscriber_counts()
    assert "sess-1" not in counts


# ---------------------------------------------------------------------------
# close_session / EndOfStream shutdown
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_signals_end_of_stream(
    event_bus: EventBus,
    sample_event: RunStartedEvent,
) -> None:
    """close_session() closes send streams, causing EndOfStream on consumers."""
    s1 = await event_bus.subscribe("sess-1")
    s2 = await event_bus.subscribe("sess-1")
    await event_bus.publish("sess-1", sample_event)
    await event_bus.close_session("sess-1")

    received1: list[Any] = []
    async for envelope in s1:
        received1.append(envelope)

    received2: list[Any] = []
    async for envelope in s2:
        received2.append(envelope)

    assert len(received1) >= 1
    assert len(received2) >= 1


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
    run_ids = [e.event.run_id for e in buffer]
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
    run_ids = [e.event.run_id for e in buffer]
    assert run_ids == ["ev0", "ev1", "ev2", "ev3", "ev4"]


@pytest.mark.anyio
async def test_replay_buffer_per_session_isolated(event_bus: EventBus) -> None:
    """Each session has its own independent replay buffer."""
    await event_bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="a"))
    await event_bus.publish("sess-2", RunStartedEvent(session_id="sess-2", run_id="b"))
    assert event_bus._replay_buffers["sess-1"][0].event.run_id == "a"
    assert event_bus._replay_buffers["sess-2"][0].event.run_id == "b"


@pytest.mark.anyio
async def test_replay_buffer_custom_size() -> None:
    """EventBus accepts a custom replay_buffer_size."""
    bus = EventBus(max_queue_size=3, replay_buffer_size=10)
    for i in range(15):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))
    assert len(bus._replay_buffers["sess-1"]) == 10
    assert bus._replay_buffers["sess-1"][0].event.run_id == "ev5"
    assert bus._replay_buffers["sess-1"][-1].event.run_id == "ev14"


# ---------------------------------------------------------------------------
# Replay protocol
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_replay_protocol_new_subscriber_gets_historical() -> None:
    """New subscriber receives last N buffered events as replay."""
    bus = EventBus(max_queue_size=10)
    for i in range(5):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    stream = await bus.subscribe("sess-1")
    received = await _drain_stream(stream)

    assert len(received) == 5
    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    assert run_ids == ["ev0", "ev1", "ev2", "ev3", "ev4"]


@pytest.mark.anyio
async def test_replay_protocol_ordering() -> None:
    """Replayed events precede live events in the stream."""
    bus = EventBus(max_queue_size=10)
    for i in range(3):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"hist-{i}"))

    stream = await bus.subscribe("sess-1")

    for i in range(2):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"live-{i}"))

    received = await _drain_stream(stream)
    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    assert run_ids == ["hist-0", "hist-1", "hist-2", "live-0", "live-1"]


@pytest.mark.anyio
async def test_replay_protocol_no_duplicates() -> None:
    """No duplicate events when publish happens during subscribe replay."""
    bus = EventBus(max_queue_size=10)
    for i in range(5):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    stream = await bus.subscribe("sess-1")
    received = await _drain_stream(stream)

    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"


@pytest.mark.anyio
async def test_replay_protocol_race_condition() -> None:
    """Subscribe concurrently with publishes; all events arrive in order."""
    bus = EventBus(max_queue_size=10)

    for i in range(3):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"hist-{i}"))

    subscribe_task = asyncio.create_task(bus.subscribe("sess-1"))
    publish_tasks = [
        asyncio.create_task(
            bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"race-{i}"))
        )
        for i in range(3)
    ]

    stream = await subscribe_task
    await asyncio.gather(*publish_tasks)

    for i in range(2):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"live-{i}"))

    received = await _drain_stream(stream)
    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]

    assert len(run_ids) == 8, f"Expected 8 events, got {len(run_ids)}: {run_ids}"
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"
    assert run_ids[:3] == ["hist-0", "hist-1", "hist-2"]


# ---------------------------------------------------------------------------
# SSE event ordering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_event_ordering_replay_then_live() -> None:
    """Replayed PartStart→PartDelta→PartEnd events precede live events in stream."""
    bus = EventBus(max_queue_size=10)

    await bus.publish("sess-1", PartStartEvent(index=0, part=TextPart(content="hello")))
    await bus.publish(
        "sess-1", PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world"))
    )
    await bus.publish("sess-1", PartEndEvent(index=0, part=TextPart(content="hello world")))

    stream = await bus.subscribe("sess-1")

    await bus.publish("sess-1", PartStartEvent(index=1, part=TextPart(content="goodbye")))
    await bus.publish(
        "sess-1", PartDeltaEvent(index=1, delta=TextPartDelta(content_delta=" world"))
    )
    await bus.publish("sess-1", PartEndEvent(index=1, part=TextPart(content="goodbye world")))

    received = await _drain_stream(stream)

    assert len(received) == 6

    assert isinstance(received[0].event, PartStartEvent)
    assert received[0].event.index == 0
    assert isinstance(received[1].event, PartDeltaEvent)
    assert received[1].event.index == 0
    assert isinstance(received[2].event, PartEndEvent)
    assert received[2].event.index == 0

    assert isinstance(received[3].event, PartStartEvent)
    assert received[3].event.index == 1
    assert isinstance(received[4].event, PartDeltaEvent)
    assert received[4].event.index == 1
    assert isinstance(received[5].event, PartEndEvent)
    assert received[5].event.index == 1


@pytest.mark.anyio
async def test_event_ordering_no_gaps_in_replay() -> None:
    """Replay buffer eviction drops oldest events; subscriber sees contiguous range."""
    bus = EventBus(max_queue_size=200, replay_buffer_size=100)

    for i in range(100):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    for i in range(100, 150):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"ev{i}"))

    stream = await bus.subscribe("sess-1")
    received = await _drain_stream(stream)

    assert len(received) == 100

    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    expected = [f"ev{i}" for i in range(50, 150)]
    assert run_ids == expected

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

    tasks = [asyncio.create_task(publisher(tid, 20)) for tid in range(5)]
    await asyncio.gather(*tasks)

    stream = await bus.subscribe("sess-1")
    received = await _drain_stream(stream)

    assert len(received) == 100

    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    assert len(run_ids) == 100
    assert len(run_ids) == len(set(run_ids)), f"Duplicate run_ids found: {run_ids}"

    for tid in range(5):
        task_events = [rid for rid in run_ids if rid.startswith(f"task{tid}-")]
        expected = [f"task{tid}-ev{i}" for i in range(20)]
        assert task_events == expected, f"Task {tid} events out of order: {task_events}"


@pytest.mark.anyio
async def test_event_ordering_mixed_sessions() -> None:
    """Events from different sessions are isolated; subscriber sees only its session."""
    bus = EventBus(max_queue_size=10)

    for i in range(5):
        await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id=f"s1-ev{i}"))
        await bus.publish("sess-2", RunStartedEvent(session_id="sess-2", run_id=f"s2-ev{i}"))
        await bus.publish("sess-3", RunStartedEvent(session_id="sess-3", run_id=f"s3-ev{i}"))

    stream = await bus.subscribe("sess-1")
    received = await _drain_stream(stream)

    assert len(received) == 5

    run_ids = [e.event.run_id for e in received if isinstance(e.event, RunStartedEvent)]
    assert run_ids == ["s1-ev0", "s1-ev1", "s1-ev2", "s1-ev3", "s1-ev4"]

    for e in received:
        if isinstance(e.event, RunStartedEvent):
            assert e.source_session_id == "sess-1"


# ---------------------------------------------------------------------------
# Descendants scope
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_child_events_visible_with_descendants_scope(event_bus: EventBus) -> None:
    """Parent subscriber with scope='descendants' receives child session events."""
    event_bus._session_tree["parent"] = ["child"]
    stream = await event_bus.subscribe("parent", scope="descendants")
    child_event = RunStartedEvent(session_id="child", run_id="run-child")
    await event_bus.publish("child", child_event)
    received = await _receive_one(stream)
    assert received is not None
    assert isinstance(received.event, RunStartedEvent)
    assert received.event.run_id == "run-child"


@pytest.mark.anyio
async def test_child_events_not_visible_with_session_scope(event_bus: EventBus) -> None:
    """Parent subscriber with scope='session' does NOT receive child session events."""
    event_bus._session_tree["parent"] = ["child"]
    stream = await event_bus.subscribe("parent", scope="session")
    child_event = RunStartedEvent(session_id="child", run_id="run-child")
    await event_bus.publish("child", child_event)
    items = await _drain_stream(stream)
    assert len(items) == 0


@pytest.mark.anyio
async def test_event_ordering_parent_and_child() -> None:
    """Events from parent and child arrive in correct interleaved order."""
    bus = EventBus(max_queue_size=10)
    bus._session_tree["parent"] = ["child"]
    stream = await bus.subscribe("parent", scope="descendants")
    events = [
        ("parent", "run-1"),
        ("child", "run-2"),
        ("parent", "run-3"),
        ("child", "run-4"),
        ("parent", "run-5"),
    ]
    for session_id, run_id in events:
        await bus.publish(session_id, RunStartedEvent(session_id=session_id, run_id=run_id))
    received: list[str] = []
    for _ in events:
        ev = await _receive_one(stream)
        assert ev is not None
        assert isinstance(ev.event, RunStartedEvent)
        received.append(ev.event.run_id)
    assert received == ["run-1", "run-2", "run-3", "run-4", "run-5"]


@pytest.mark.anyio
async def test_grandchild_events_visible_with_descendants_scope(
    event_bus: EventBus,
) -> None:
    """Parent subscriber with scope='descendants' receives grandchild events."""
    event_bus._session_tree["parent"] = ["child"]
    event_bus._session_tree["child"] = ["grandchild"]
    stream = await event_bus.subscribe("parent", scope="descendants")
    grandchild_event = RunStartedEvent(session_id="grandchild", run_id="run-grandchild")
    await event_bus.publish("grandchild", grandchild_event)
    received = await _receive_one(stream)
    assert received is not None
    assert isinstance(received.event, RunStartedEvent)
    assert received.event.run_id == "run-grandchild"
