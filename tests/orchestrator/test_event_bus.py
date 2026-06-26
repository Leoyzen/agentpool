"""Unit tests for EventBus (SessionPool Group 2.10).

Tests pub/sub semantics, bounded stream dropping, EndOfStream-based
shutdown, subscriber lifecycle management, and event coalescing infrastructure.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import anyio
import pytest

from agentpool.agents.events import (
    CompactionEvent,
    CustomEvent,
    PartDeltaEvent,
    PartStartEvent,
    PlanUpdateEvent,
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    SessionResumeEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    SubAgentEvent,
    TerminalContentItem,
    TextContentItem,
    ToolCallCompleteEvent,
    ToolCallDeferredEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
    ToolResultMetadataEvent,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import (
    EventBus,
    EventEnvelope,
    _is_immediate,
    _merge_envelopes,
    _merge_key,
    _merge_progress_events,
    _merge_text_deltas,
    _merge_thinking_deltas,
    _merge_tool_call_deltas,
    _rebind,
)
from pydantic_ai import (
    PartEndEvent,
    TextPart,
    TextPartDelta,
    ThinkingPart,
    ThinkingPartDelta,
    ToolCallPartDelta,
)


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


# ---------------------------------------------------------------------------
# Event coalescing infrastructure (Task 1)
# ---------------------------------------------------------------------------


# --- _is_immediate ---


@pytest.mark.parametrize(
    "event",
    [
        RunStartedEvent(session_id="s", run_id="r"),
        RunErrorEvent(message="err"),
        RunFailedEvent(run_id="r", session_id="s", exception=ValueError("test")),
        StreamCompleteEvent(message=ChatMessage(content="done", role="assistant")),
        SpawnSessionStart(
            child_session_id="c",
            parent_session_id="p",
            spawn_mechanism="task",
            source_name="agent",
            source_type="agent",
            description="test",
        ),
        CompactionEvent(session_id="s"),
        SessionResumeEvent(session_id="s", resolved_call_count=0),
        ToolCallStartEvent(tool_call_id="tc1", tool_name="bash", title="test"),
        ToolCallCompleteEvent(
            tool_name="bash",
            tool_call_id="tc1",
            tool_input={},
            tool_result="ok",
            agent_name="a",
            message_id="m",
        ),
        ToolCallDeferredEvent(
            tool_call_id="tc1",
            tool_name="bash",
            deferred_strategy="block",
            status="pending",
        ),
    ],
    ids=[
        "run_started",
        "run_error",
        "run_failed",
        "stream_complete",
        "spawn_session_start",
        "compaction",
        "session_resume",
        "tool_call_start",
        "tool_call_complete",
        "tool_call_deferred",
    ],
)
def test_immediate_returns_true_for_lifecycle_events(event: Any) -> None:
    """All 10 lifecycle event types are classified as immediate."""
    assert _is_immediate(event) is True


def test_immediate_returns_false_for_text_delta() -> None:
    """PartDeltaEvent with TextPartDelta is not immediate."""
    event = PartDeltaEvent.text(0, "hello")
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_thinking_delta() -> None:
    """PartDeltaEvent with ThinkingPartDelta is not immediate."""
    event = PartDeltaEvent.thinking(0, "thinking")
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_tool_call_delta() -> None:
    """PartDeltaEvent with ToolCallPartDelta is not immediate."""
    event = PartDeltaEvent.tool_call(0, "args", "tc1")
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_tool_call_progress() -> None:
    """ToolCallProgressEvent is not immediate."""
    event = ToolCallProgressEvent(tool_call_id="tc1")
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_plan_update() -> None:
    """PlanUpdateEvent is not immediate."""
    event = PlanUpdateEvent(entries=[])
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_subagent_event() -> None:
    """SubAgentEvent is not immediate."""
    event = SubAgentEvent(
        source_name="agent",
        source_type="agent",
        event=RunStartedEvent(session_id="s", run_id="r"),
    )
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_custom_event() -> None:
    """CustomEvent is not immediate."""
    event = CustomEvent(event_data="test")
    assert _is_immediate(event) is False


def test_immediate_returns_false_for_tool_result_metadata() -> None:
    """ToolResultMetadataEvent is not immediate."""
    event = ToolResultMetadataEvent(tool_call_id="tc1", metadata={})
    assert _is_immediate(event) is False


# --- _merge_key (classify) ---


def test_classify_text_delta() -> None:
    """PartDeltaEvent with TextPartDelta has merge key ('delta_text', '')."""
    event = PartDeltaEvent.text(0, "hello")
    assert _merge_key(event) == ("delta_text", "")


def test_classify_thinking_delta() -> None:
    """PartDeltaEvent with ThinkingPartDelta has merge key ('delta_thinking', '')."""
    event = PartDeltaEvent.thinking(0, "thinking")
    assert _merge_key(event) == ("delta_thinking", "")


def test_classify_tool_call_delta() -> None:
    """PartDeltaEvent with ToolCallPartDelta has merge key ('delta_tool_call', tool_call_id)."""
    event = PartDeltaEvent.tool_call(0, "args", "tc1")
    assert _merge_key(event) == ("delta_tool_call", "tc1")


def test_classify_tool_call_progress() -> None:
    """ToolCallProgressEvent has merge key ('progress', 'tool_call_id:status')."""
    event = ToolCallProgressEvent(tool_call_id="tc1", status="in_progress")
    assert _merge_key(event) == ("progress", "tc1:in_progress")


def test_classify_plan_update() -> None:
    """PlanUpdateEvent has merge key ('plan', '')."""
    event = PlanUpdateEvent(entries=[])
    assert _merge_key(event) == ("plan", "")


def test_classify_subagent_returns_none() -> None:
    """SubAgentEvent is passthrough (merge key None)."""
    event = SubAgentEvent(
        source_name="agent",
        source_type="agent",
        event=RunStartedEvent(session_id="s", run_id="r"),
    )
    assert _merge_key(event) is None


def test_classify_custom_returns_none() -> None:
    """CustomEvent is passthrough (merge key None)."""
    event = CustomEvent(event_data="test")
    assert _merge_key(event) is None


def test_classify_tool_result_metadata_returns_none() -> None:
    """ToolResultMetadataEvent is passthrough (merge key None)."""
    event = ToolResultMetadataEvent(tool_call_id="tc1", metadata={})
    assert _merge_key(event) is None


def test_classify_none_delta_returns_none() -> None:
    """PartDeltaEvent with delta=None is passthrough (merge key None)."""
    event: Any = PartDeltaEvent(index=0, delta=None)  # type: ignore[call-arg]
    assert _merge_key(event) is None


# --- _merge_text_deltas ---


def test_merge_text_deltas_concatenates_content() -> None:
    """Multiple text deltas are concatenated into a single content_delta."""
    events = [
        PartDeltaEvent.text(0, "hello "),
        PartDeltaEvent.text(0, "world"),
        PartDeltaEvent.text(0, "!"),
    ]
    merged = _merge_text_deltas(events)
    assert isinstance(merged, PartDeltaEvent)
    assert isinstance(merged.delta, TextPartDelta)
    assert merged.delta.content_delta == "hello world!"


def test_merge_text_deltas_uses_first_index() -> None:
    """Merged text delta uses the first event's index."""
    events = [
        PartDeltaEvent.text(5, "a"),
        PartDeltaEvent.text(7, "b"),
    ]
    merged = _merge_text_deltas(events)
    assert merged.index == 5


def test_merge_text_deltas_single_event() -> None:
    """Merging a single text delta returns the same content."""
    events = [PartDeltaEvent.text(0, "solo")]
    merged = _merge_text_deltas(events)
    assert isinstance(merged.delta, TextPartDelta)
    assert merged.delta.content_delta == "solo"


# --- _merge_thinking_deltas ---


def test_merge_thinking_deltas_concatenates_content() -> None:
    """Multiple thinking deltas are concatenated into a single content_delta."""
    events = [
        PartDeltaEvent.thinking(0, "think "),
        PartDeltaEvent.thinking(0, "more"),
    ]
    merged = _merge_thinking_deltas(events)
    assert isinstance(merged, PartDeltaEvent)
    assert isinstance(merged.delta, ThinkingPartDelta)
    assert merged.delta.content_delta == "think more"


def test_merge_thinking_deltas_uses_first_index() -> None:
    """Merged thinking delta uses the first event's index."""
    events = [
        PartDeltaEvent.thinking(3, "a"),
        PartDeltaEvent.thinking(7, "b"),
    ]
    merged = _merge_thinking_deltas(events)
    assert merged.index == 3


# --- _merge_tool_call_deltas ---


def test_merge_tool_call_deltas_concatenates_args() -> None:
    """Multiple tool_call deltas are concatenated into a single args_delta."""
    events = [
        PartDeltaEvent.tool_call(0, '{"path"', "tc1"),
        PartDeltaEvent.tool_call(0, ': "foo"}', "tc1"),
    ]
    merged = _merge_tool_call_deltas(events)
    assert isinstance(merged, PartDeltaEvent)
    assert isinstance(merged.delta, ToolCallPartDelta)
    assert merged.delta.args_delta == '{"path": "foo"}'


def test_merge_tool_call_deltas_uses_first_index_and_tool_call_id() -> None:
    """Merged tool_call delta uses first event's index and tool_call_id."""
    events = [
        PartDeltaEvent.tool_call(2, "a", "tc-first"),
        PartDeltaEvent.tool_call(5, "b", "tc-second"),
    ]
    merged = _merge_tool_call_deltas(events)
    assert merged.index == 2
    assert isinstance(merged.delta, ToolCallPartDelta)
    assert merged.delta.tool_call_id == "tc-first"


# --- _merge_progress_events ---


def test_merge_progress_events_concatenates_items() -> None:
    """Items from all progress events are concatenated."""
    events = [
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="in_progress",
            items=[TerminalContentItem(terminal_id="t1")],
        ),
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="in_progress",
            items=[TextContentItem(text="output")],
        ),
    ]
    merged = _merge_progress_events(events)
    assert len(merged.items) == 2
    assert isinstance(merged.items[0], TerminalContentItem)
    assert isinstance(merged.items[1], TextContentItem)


def test_merge_progress_events_uses_last_fields() -> None:
    """Merged progress event uses last event's title, status, replace_content, tool_name."""
    events = [
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="in_progress",
            title="first",
            replace_content=False,
            tool_name="bash",
            items=[],
        ),
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="completed",
            title="last",
            replace_content=True,
            tool_name="read",
            items=[],
        ),
    ]
    merged = _merge_progress_events(events)
    assert merged.title == "last"
    assert merged.status == "completed"
    assert merged.replace_content is True
    assert merged.tool_name == "read"


def test_merge_progress_events_keeps_duplicate_terminal_ids() -> None:
    """Duplicate terminal_id items are kept (no dedup)."""
    events = [
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="in_progress",
            items=[TerminalContentItem(terminal_id="t1")],
        ),
        ToolCallProgressEvent(
            tool_call_id="tc1",
            status="in_progress",
            items=[TerminalContentItem(terminal_id="t1")],
        ),
    ]
    merged = _merge_progress_events(events)
    assert len(merged.items) == 2
    # Both items should be TerminalContentItem with terminal_id="t1"
    for item in merged.items:
        assert isinstance(item, TerminalContentItem)
        assert item.terminal_id == "t1"


# --- _merge_envelopes ---


def test_merge_envelopes_groups_consecutive_text_deltas() -> None:
    """Consecutive text deltas are merged into a single envelope."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "hello ")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "world")),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 1
    assert isinstance(result[0].event, PartDeltaEvent)
    assert isinstance(result[0].event.delta, TextPartDelta)
    assert result[0].event.delta.content_delta == "hello world"
    assert result[0].source_session_id == "s1"


def test_merge_envelopes_type_change_creates_separate_groups() -> None:
    """Type change (text→thinking) creates two separate merged groups."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "text")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.thinking(0, "think")),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 2
    assert isinstance(result[0].event.delta, TextPartDelta)
    assert result[0].event.delta.content_delta == "text"
    assert isinstance(result[1].event.delta, ThinkingPartDelta)
    assert result[1].event.delta.content_delta == "think"


def test_merge_envelopes_drops_none_delta() -> None:
    """PartDeltaEvent with delta=None is dropped, not merged or dispatched."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "keep")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent(index=0, delta=None)),  # type: ignore[call-arg]
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "this")),
    ]
    result = _merge_envelopes(envelopes)
    # None delta is dropped; remaining two text deltas are merged
    assert len(result) == 1
    assert isinstance(result[0].event.delta, TextPartDelta)
    assert result[0].event.delta.content_delta == "keepthis"


def test_merge_envelopes_plan_last_wins() -> None:
    """PlanUpdateEvent groups use last-wins strategy."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PlanUpdateEvent(entries=[])),
        EventEnvelope(source_session_id="s1", event=PlanUpdateEvent(entries=[])),
        EventEnvelope(source_session_id="s1", event=PlanUpdateEvent(entries=[])),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 1
    assert isinstance(result[0].event, PlanUpdateEvent)


def test_merge_envelopes_passthrough_extends_without_merging() -> None:
    """Passthrough events (SubAgentEvent, CustomEvent) are not merged."""
    sub_event = SubAgentEvent(
        source_name="agent",
        source_type="agent",
        event=RunStartedEvent(session_id="s", run_id="r"),
    )
    custom_event = CustomEvent(event_data="test")
    envelopes = [
        EventEnvelope(source_session_id="s1", event=sub_event),
        EventEnvelope(source_session_id="s1", event=custom_event),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 2
    assert isinstance(result[0].event, SubAgentEvent)
    assert isinstance(result[1].event, CustomEvent)


def test_merge_envelopes_empty_list_returns_empty() -> None:
    """Empty envelope list returns empty result."""
    result = _merge_envelopes([])
    assert result == []


def test_merge_envelopes_tool_call_progress_merged() -> None:
    """Consecutive ToolCallProgressEvents with same key are merged."""
    envelopes = [
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc1",
                status="in_progress",
                title="first",
                items=[TerminalContentItem(terminal_id="t1")],
            ),
        ),
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc1",
                status="in_progress",
                title="second",
                items=[TextContentItem(text="out")],
            ),
        ),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 1
    assert isinstance(result[0].event, ToolCallProgressEvent)
    assert len(result[0].event.items) == 2
    assert result[0].event.title == "second"


def test_merge_envelopes_different_tool_call_ids_not_merged() -> None:
    """ToolCallProgressEvents with different tool_call_id are not merged."""
    envelopes = [
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc1",
                status="in_progress",
                items=[],
            ),
        ),
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc2",
                status="in_progress",
                items=[],
            ),
        ),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 2


def test_merge_envelopes_different_status_not_merged() -> None:
    """ToolCallProgressEvents with different status are not merged."""
    envelopes = [
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc1",
                status="in_progress",
                items=[],
            ),
        ),
        EventEnvelope(
            source_session_id="s1",
            event=ToolCallProgressEvent(
                tool_call_id="tc1",
                status="completed",
                items=[],
            ),
        ),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 2


def test_merge_envelopes_retains_event_type() -> None:
    """Merged events retain their original event type (no wrapper)."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "a")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "b")),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 1
    # The merged event should still be a PartDeltaEvent, not a wrapper
    assert type(result[0].event) is PartDeltaEvent


def test_merge_envelopes_non_consecutive_same_key_not_merged() -> None:
    """Events with same merge key but separated by different key are not merged."""
    envelopes = [
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "a")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.thinking(0, "b")),
        EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "c")),
    ]
    result = _merge_envelopes(envelopes)
    # Three groups: [text "a"], [thinking "b"], [text "c"]
    assert len(result) == 3
    assert isinstance(result[0].event.delta, TextPartDelta)
    assert result[0].event.delta.content_delta == "a"
    assert isinstance(result[1].event.delta, ThinkingPartDelta)
    assert result[1].event.delta.content_delta == "b"
    assert isinstance(result[2].event.delta, TextPartDelta)
    assert result[2].event.delta.content_delta == "c"


def test_merge_envelopes_preserves_source_session_id() -> None:
    """Merged envelopes preserve the source_session_id from the template."""
    envelopes = [
        EventEnvelope(source_session_id="custom-session", event=PartDeltaEvent.text(0, "a")),
        EventEnvelope(source_session_id="custom-session", event=PartDeltaEvent.text(0, "b")),
    ]
    result = _merge_envelopes(envelopes)
    assert len(result) == 1
    assert result[0].source_session_id == "custom-session"


# --- _rebind ---


def test_rebind_preserves_source_session_id() -> None:
    """_rebind creates new envelope with same source_session_id."""
    template = EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "old"))
    new_event = PartDeltaEvent.text(0, "new")
    result = _rebind(template, new_event)
    assert result.source_session_id == "s1"
    assert result.event is new_event


def test_rebind_uses_new_event() -> None:
    """_rebind uses the provided new_event, not the template's event."""
    template = EventEnvelope(
        source_session_id="s1",
        event=RunStartedEvent(session_id="s", run_id="old"),
    )
    new_event = RunStartedEvent(session_id="s", run_id="new")
    result = _rebind(template, new_event)
    assert result.event is new_event
    assert result.event.run_id == "new"


def test_rebind_creates_new_envelope_instance() -> None:
    """_rebind returns a new EventEnvelope, not the template."""
    template = EventEnvelope(source_session_id="s1", event=PartDeltaEvent.text(0, "old"))
    new_event = PartDeltaEvent.text(0, "new")
    result = _rebind(template, new_event)
    assert result is not template


# ---------------------------------------------------------------------------
# Event coalescing publish (Task 2)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_coalescing_type_change_flush() -> None:
    """Text deltas are flushed when a thinking delta arrives (type change)."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    # Publish text deltas — buffered, not yet sent
    await bus.publish("sess-1", PartDeltaEvent.text(0, "hello "))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "world"))

    # Nothing received yet — text deltas are buffered
    assert len(await _drain_stream(stream)) == 0

    # Publish thinking delta — type change triggers flush of text batch
    await bus.publish("sess-1", PartDeltaEvent.thinking(0, "thinking..."))

    # Subscriber receives merged text delta
    items = await _drain_stream(stream)
    assert len(items) == 1
    assert isinstance(items[0].event, PartDeltaEvent)
    assert isinstance(items[0].event.delta, TextPartDelta)
    assert items[0].event.delta.content_delta == "hello world"

    # Thinking delta is still buffered — flush via immediate event
    await bus.publish(
        "sess-1", StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))
    )

    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event, PartDeltaEvent)
    assert isinstance(items[0].event.delta, ThinkingPartDelta)
    assert items[0].event.delta.content_delta == "thinking..."
    assert isinstance(items[1].event, StreamCompleteEvent)


@pytest.mark.anyio
async def test_coalescing_buffer_cap_flush() -> None:
    """Buffer cap (3) triggers flush when exceeded."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=3)
    stream = await bus.subscribe("sess-1")

    # Publish 3 text deltas — all buffered (cap not yet reached)
    for i in range(3):
        await bus.publish("sess-1", PartDeltaEvent.text(0, f"chunk{i}"))

    assert len(await _drain_stream(stream)) == 0

    # 4th text delta — cap reached, flush previous 3
    await bus.publish("sess-1", PartDeltaEvent.text(0, "chunk3"))

    items = await _drain_stream(stream)
    assert len(items) == 1
    assert isinstance(items[0].event, PartDeltaEvent)
    assert isinstance(items[0].event.delta, TextPartDelta)
    assert items[0].event.delta.content_delta == "chunk0chunk1chunk2"


@pytest.mark.anyio
async def test_coalescing_immediate_event_drains_buffer() -> None:
    """StreamCompleteEvent (immediate) drains buffer before sending itself."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    # Buffer some text deltas
    await bus.publish("sess-1", PartDeltaEvent.text(0, "hello "))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "world"))

    assert len(await _drain_stream(stream)) == 0

    # Publish immediate event — drains buffer first
    await bus.publish(
        "sess-1", StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))
    )

    items = await _drain_stream(stream)
    assert len(items) == 2
    # First: drained text deltas (merged)
    assert isinstance(items[0].event, PartDeltaEvent)
    assert isinstance(items[0].event.delta, TextPartDelta)
    assert items[0].event.delta.content_delta == "hello world"
    # Second: the immediate event itself
    assert isinstance(items[1].event, StreamCompleteEvent)


@pytest.mark.anyio
async def test_coalescing_immediate_event_empty_buffer() -> None:
    """Immediate event with empty buffer is a no-op drain + direct send."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    await bus.publish(
        "sess-1", StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))
    )

    items = await _drain_stream(stream)
    assert len(items) == 1
    assert isinstance(items[0].event, StreamCompleteEvent)


@pytest.mark.anyio
async def test_coalescing_per_session_isolation() -> None:
    """Draining session A's buffer does not affect session B."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream_a = await bus.subscribe("sess-a")
    stream_b = await bus.subscribe("sess-b")

    # Buffer text deltas in both sessions
    await bus.publish("sess-a", PartDeltaEvent.text(0, "hello-a"))
    await bus.publish("sess-b", PartDeltaEvent.text(0, "hello-b"))

    # Nothing received yet
    assert len(await _drain_stream(stream_a)) == 0
    assert len(await _drain_stream(stream_b)) == 0

    # Publish immediate event to A — drains A's buffer only
    await bus.publish("sess-a", RunStartedEvent(session_id="sess-a", run_id="r1"))

    # A receives merged text + immediate
    items_a = await _drain_stream(stream_a)
    assert len(items_a) == 2
    assert isinstance(items_a[0].event, PartDeltaEvent)
    assert items_a[0].event.delta.content_delta == "hello-a"
    assert isinstance(items_a[1].event, RunStartedEvent)

    # B still has buffered event, nothing received
    assert len(await _drain_stream(stream_b)) == 0


@pytest.mark.anyio
async def test_coalescing_passthrough_subagent_drains_buffer() -> None:
    """SubAgentEvent (passthrough) drains buffer before sending itself."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    await bus.publish("sess-1", PartDeltaEvent.text(0, "hello "))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "world"))
    assert len(await _drain_stream(stream)) == 0

    sub_event = SubAgentEvent(
        source_name="agent",
        source_type="agent",
        event=RunStartedEvent(session_id="s", run_id="r"),
    )
    await bus.publish("sess-1", sub_event)

    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event, PartDeltaEvent)
    assert items[0].event.delta.content_delta == "hello world"
    assert isinstance(items[1].event, SubAgentEvent)


@pytest.mark.anyio
async def test_coalescing_passthrough_custom_drains_buffer() -> None:
    """CustomEvent (passthrough) drains buffer before sending itself."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    await bus.publish("sess-1", PartDeltaEvent.text(0, "data"))
    assert len(await _drain_stream(stream)) == 0

    await bus.publish("sess-1", CustomEvent(event_data="test"))

    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event, PartDeltaEvent)
    assert items[0].event.delta.content_delta == "data"
    assert isinstance(items[1].event, CustomEvent)


@pytest.mark.anyio
async def test_coalescing_passthrough_tool_result_metadata_drains_buffer() -> None:
    """ToolResultMetadataEvent (passthrough) drains buffer before sending itself."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    await bus.publish("sess-1", PartDeltaEvent.text(0, "data"))
    assert len(await _drain_stream(stream)) == 0

    await bus.publish("sess-1", ToolResultMetadataEvent(tool_call_id="tc1", metadata={}))

    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event, PartDeltaEvent)
    assert items[0].event.delta.content_delta == "data"
    assert isinstance(items[1].event, ToolResultMetadataEvent)


@pytest.mark.anyio
async def test_coalescing_non_consecutive_same_key_not_merged() -> None:
    """text→thinking→text produces 3 separate dispatches (not merged)."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    await bus.publish("sess-1", PartDeltaEvent.text(0, "a"))
    await bus.publish("sess-1", PartDeltaEvent.thinking(0, "b"))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "c"))

    # text "a" flushed by thinking, thinking "b" flushed by text "c"
    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event.delta, TextPartDelta)
    assert items[0].event.delta.content_delta == "a"
    assert isinstance(items[1].event.delta, ThinkingPartDelta)
    assert items[1].event.delta.content_delta == "b"

    # text "c" is still buffered — flush via immediate
    await bus.publish(
        "sess-1", StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))
    )

    items = await _drain_stream(stream)
    assert len(items) == 2
    assert isinstance(items[0].event.delta, TextPartDelta)
    assert items[0].event.delta.content_delta == "c"
    assert isinstance(items[1].event, StreamCompleteEvent)


@pytest.mark.anyio
async def test_coalescing_none_delta_dropped() -> None:
    """PartDeltaEvent with delta=None is dropped entirely (not sent or buffered)."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    none_delta: Any = PartDeltaEvent(index=0, delta=None)  # type: ignore[call-arg]
    await bus.publish("sess-1", none_delta)

    assert len(await _drain_stream(stream)) == 0


@pytest.mark.anyio
async def test_coalescing_plan_update_last_wins() -> None:
    """Multiple PlanUpdateEvents merge to last-wins when flushed."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    # Buffer multiple plan updates
    await bus.publish("sess-1", PlanUpdateEvent(entries=[]))
    await bus.publish("sess-1", PlanUpdateEvent(entries=[]))
    await bus.publish("sess-1", PlanUpdateEvent(entries=[]))

    assert len(await _drain_stream(stream)) == 0

    # Flush via immediate event
    await bus.publish("sess-1", RunStartedEvent(session_id="sess-1", run_id="r1"))

    items = await _drain_stream(stream)
    # Only 1 PlanUpdateEvent (last-wins) + 1 RunStartedEvent
    assert len(items) == 2
    assert isinstance(items[0].event, PlanUpdateEvent)
    assert isinstance(items[1].event, RunStartedEvent)


@pytest.mark.anyio
async def test_close_session_drains_buffer() -> None:
    """close_session() drains buffered events before closing streams."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    # Buffer some text deltas
    await bus.publish("sess-1", PartDeltaEvent.text(0, "hello "))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "world"))

    assert len(await _drain_stream(stream)) == 0

    # Close session — should drain buffer first
    await bus.close_session("sess-1")

    received: list[Any] = []
    async for envelope in stream:
        received.append(envelope)

    assert len(received) == 1
    assert isinstance(received[0].event, PartDeltaEvent)
    assert received[0].event.delta.content_delta == "hello world"


@pytest.mark.anyio
async def test_drain_buffer_idempotent() -> None:
    """Second _drain_buffer call gets empty list (idempotent)."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    stream = await bus.subscribe("sess-1")

    # Buffer some text deltas
    await bus.publish("sess-1", PartDeltaEvent.text(0, "hello "))
    await bus.publish("sess-1", PartDeltaEvent.text(0, "world"))

    # First drain — sends merged batch
    await bus._drain_buffer("sess-1")
    items = await _drain_stream(stream)
    assert len(items) == 1
    assert items[0].event.delta.content_delta == "hello world"

    # Second drain — empty buffer, no send
    await bus._drain_buffer("sess-1")
    assert len(await _drain_stream(stream)) == 0


@pytest.mark.anyio
async def test_concurrent_publish_and_close_session_no_deadlock() -> None:
    """Concurrent publish() and close_session() complete without deadlock."""
    bus = EventBus(max_queue_size=100, max_coalesce_buffer=20)
    _ = await bus.subscribe("sess-1")

    async def publish_loop() -> None:
        for i in range(10):
            await bus.publish("sess-1", PartDeltaEvent.text(0, f"chunk{i}"))

    async def close_loop() -> None:
        await bus.close_session("sess-1")

    with anyio.fail_after(5):
        async with anyio.create_task_group() as tg:
            tg.start_soon(publish_loop)
            tg.start_soon(close_loop)
