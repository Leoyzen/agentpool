"""Unit tests for EventBus mechanism coverage (Group 5).

Tests overflow policies, cross-session isolation, event ordering, observer
defect isolation, scoped subscriptions, backpressure, and StateUpdate
filtering from ProtocolChannel.

These tests complement tests/orchestrator/test_event_bus.py (which covers
coalescing with 109 tests) by exercising mechanisms that have zero coverage:
overflow policies, cross-session isolation, scoped subscriptions,
backpressure, and StateUpdate filtering.
"""

from __future__ import annotations

from asyncio import Queue as AsyncQueue, QueueEmpty, QueueShutDown
from typing import cast

import anyio
import pytest

from agentpool.agents.events import RunStartedEvent, StateUpdate
from agentpool.lifecycle.comm_channel import ProtocolChannel
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.lifecycle.types import RunState
from agentpool.orchestrator.event_bus import EventEnvelope, EventBus, OverflowPolicy

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _drain_queue(queue: AsyncQueue[EventEnvelope]) -> list[EventEnvelope]:
    """Drain all immediately-available items from a subscriber queue.

    Returns items in FIFO order. Does not block.
    """
    items: list[EventEnvelope] = []
    while True:
        try:
            items.append(queue.get_nowait())
        except (QueueEmpty, QueueShutDown):
            break
    return items


def _make_event(run_id: str) -> RunStartedEvent:
    """Create a RunStartedEvent with the given run_id for testing."""
    return RunStartedEvent(session_id="test-sess", run_id=run_id)


def _run_ids(envelopes: list[EventEnvelope]) -> list[str]:
    """Extract run_id values from a list of EventEnvelopes wrapping RunStartedEvent."""
    return [cast(RunStartedEvent, env.event).run_id for env in envelopes]


# ---------------------------------------------------------------------------
# Test 1: Overflow policy — drop_oldest
# ---------------------------------------------------------------------------


async def test_overflow_policy_drop_oldest() -> None:
    """drop_oldest evicts the oldest queued event when the queue is full.

    Given: EventBus with max_queue_size=3 and drop_oldest policy.
    When: 5 events are published without draining.
    Then: Subscriber queue holds the 3 newest events (oldest 2 dropped).
    """
    bus = EventBus(max_queue_size=3, overflow_policy="drop_oldest")
    queue = await bus.subscribe("test-sess")

    for i in range(5):
        await bus.publish("test-sess", _make_event(f"run-{i}"))

    events = _drain_queue(queue)
    assert len(events) == 3
    assert _run_ids(events) == ["run-2", "run-3", "run-4"]


# ---------------------------------------------------------------------------
# Test 2: Overflow policy — drop_newest
# ---------------------------------------------------------------------------


async def test_overflow_policy_drop_newest() -> None:
    """drop_newest discards incoming events when the queue is full.

    Given: EventBus with max_queue_size=3 and drop_newest policy.
    When: 5 events are published without draining.
    Then: Subscriber queue holds the first 3 events (newest 2 dropped).
    """
    bus = EventBus(max_queue_size=3, overflow_policy="drop_newest")
    queue = await bus.subscribe("test-sess")

    for i in range(5):
        await bus.publish("test-sess", _make_event(f"run-{i}"))

    events = _drain_queue(queue)
    assert len(events) == 3
    assert _run_ids(events) == ["run-0", "run-1", "run-2"]


# ---------------------------------------------------------------------------
# Test 3: Overflow policy — block raises ValueError
# ---------------------------------------------------------------------------


async def test_overflow_policy_block_raises() -> None:
    """overflow_policy='block' is rejected at construction time.

    Given: An attempt to create EventBus with overflow_policy="block".
    When: The constructor is called.
    Then: ValueError is raised explaining block would deadlock the run loop.
    """
    with pytest.raises(ValueError, match="block"):
        EventBus(overflow_policy=cast(OverflowPolicy, "block"))


# ---------------------------------------------------------------------------
# Test 4: Cross-session isolation
# ---------------------------------------------------------------------------


async def test_cross_session_isolation() -> None:
    """Events published to session A do not reach subscribers of session B.

    Given: EventBus with subscribers on sessions "A" and "B".
    When: 3 events are published to session "A".
    Then: Subscriber of session "B" receives zero events from A.
    """
    bus = EventBus(max_queue_size=10)
    queue_a = await bus.subscribe("A")
    queue_b = await bus.subscribe("B")

    for i in range(3):
        await bus.publish("A", _make_event(f"run-{i}"))

    events_a = _drain_queue(queue_a)
    events_b = _drain_queue(queue_b)

    assert len(events_a) == 3
    assert len(events_b) == 0


# ---------------------------------------------------------------------------
# Test 5: Event ordering by publish order
# ---------------------------------------------------------------------------


async def test_event_ordering_by_publish_order() -> None:
    """Events arrive at the subscriber in the same order they were published.

    Given: A single subscriber on session "test-sess".
    When: 10 events are published sequentially.
    Then: Draining the subscriber queue yields events in publish order.
    """
    bus = EventBus(max_queue_size=100)
    queue = await bus.subscribe("test-sess")

    for i in range(10):
        await bus.publish("test-sess", _make_event(f"run-{i:02d}"))

    events = _drain_queue(queue)
    assert len(events) == 10
    assert _run_ids(events) == [f"run-{i:02d}" for i in range(10)]


# ---------------------------------------------------------------------------
# Test 6: Observer defect isolation
# ---------------------------------------------------------------------------


async def test_observer_defect_isolation() -> None:
    """A raising consumer does not prevent delivery to other subscribers.

    Given: Two subscribers on the same session.
    When: 3 events are published, and the first consumer raises while
        draining its queue.
    Then: The second consumer's queue still contains all 3 events.
    """
    bus = EventBus(max_queue_size=10)
    queue_a = await bus.subscribe("test-sess")
    queue_b = await bus.subscribe("test-sess")

    for i in range(3):
        await bus.publish("test-sess", _make_event(f"run-{i}"))

    # Consumer A raises on the first event — simulate a buggy consumer.
    try:
        queue_a.get_nowait()
        raise RuntimeError("consumer A defect")
    except RuntimeError:
        pass  # Consumer A crashed; its queue may still have remaining events.

    # Consumer B should still have all 3 events regardless of A's failure.
    events_b = _drain_queue(queue_b)
    assert len(events_b) == 3
    assert _run_ids(events_b) == ["run-0", "run-1", "run-2"]


# ---------------------------------------------------------------------------
# Test 7: Scoped subscription — descendants
# ---------------------------------------------------------------------------


async def test_scoped_subscription_descendants() -> None:
    """descendants scope receives events from child sessions.

    Given: EventBus with a parent-child session hierarchy established
        via the internal session tree.
    When: An event is published to the child session.
    Then: A subscriber on the parent with scope="descendants" receives it.
    """
    bus = EventBus(max_queue_size=10)
    # Establish parent → child relationship in the internal session tree.
    # This is the fallback path used when no SessionController is configured.
    bus._session_tree = {"parent": ["child"]}

    parent_queue = await bus.subscribe("parent", scope="descendants")

    await bus.publish("child", _make_event("run-child"))

    events = _drain_queue(parent_queue)
    assert len(events) == 1
    assert cast(RunStartedEvent, events[0].event).run_id == "run-child"


# ---------------------------------------------------------------------------
# Test 8: Backpressure through consumer loop
# ---------------------------------------------------------------------------


async def test_backpressure_through_consumer_loop() -> None:
    """EventBus does not block under backpressure; overflow policy applies.

    Given: EventBus with max_queue_size=2 and drop_oldest policy.
    When: 5 events are published faster than the consumer drains (no draining
        during publishing).
    Then: All publish() calls complete without blocking, and the subscriber
        queue holds the 2 newest events.
    """
    bus = EventBus(max_queue_size=2, overflow_policy="drop_oldest")
    queue = await bus.subscribe("test-sess")

    # Publish all 5 events without draining — simulates a slow consumer.
    for i in range(5):
        await bus.publish("test-sess", _make_event(f"run-{i}"))

    # If publish() blocked, we would never reach this assertion.
    events = _drain_queue(queue)
    assert len(events) == 2
    assert _run_ids(events) == ["run-3", "run-4"]


# ---------------------------------------------------------------------------
# Test 9: StateUpdate filtered from EventBus
# ---------------------------------------------------------------------------


async def test_stateupdate_filtered_from_eventbus() -> None:
    """ProtocolChannel journals StateUpdate but does not publish it to EventBus.

    Given: A ProtocolChannel with a MemoryJournal and EventBus, and a
        subscriber on the EventBus.
    When: channel.publish(StateUpdate(...)) is called.
    Then:
        (a) The StateUpdate IS recorded in the journal (upserted).
        (b) The EventBus subscriber does NOT receive the StateUpdate.
    """
    journal = MemoryJournal()
    bus = EventBus(max_queue_size=10)
    channel = ProtocolChannel(journal=journal, event_bus=bus, session_id="test-sess")

    # Subscribe BEFORE publishing to capture live events.
    queue = await bus.subscribe("test-sess")

    state_update = StateUpdate(session_id="test-sess", state=RunState.IDLE)
    await channel.publish(state_update)

    # (a) Verify the StateUpdate was journaled.
    journal_events: list[object] = []
    async for evt in journal.replay():
        journal_events.append(evt)

    state_updates_in_journal: list[StateUpdate] = [
        evt for evt in journal_events if isinstance(evt, StateUpdate)
    ]
    assert len(state_updates_in_journal) == 1
    assert state_updates_in_journal[0].session_id == "test-sess"
    assert state_updates_in_journal[0].state == RunState.IDLE

    # (b) Verify the EventBus subscriber did NOT receive the StateUpdate.
    bus_events = _drain_queue(queue)
    assert len(bus_events) == 0
