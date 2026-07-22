"""Integration test reproducing the first-user-message duplication bug.

Reproduction scenario:
1. User sends message → EventProcessor broadcasts MessageUpdatedEvent +
   PartUpdatedEvent to SSE subscribers AND EventBus replay buffer
2. TUI's SSE connection is established AFTER events were published
   (race: events published during ~880ms consumer startup)
3. EventBus replay buffer re-delivers the same events to the late subscriber
4. TUI also calls sync() → loads the same message + parts from DB
5. TUI has parts from BOTH replay buffer (SSE) and sync() (DB) → DUPLICATE

Fix: sync() endpoint clears the replay buffer so events published before
sync() are not re-delivered to new SSE subscribers.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from agentpool.orchestrator.core import EventBus, EventEnvelope
from agentpool_server.opencode_server.models.common import TimeCreated
from agentpool_server.opencode_server.models import (
    MessageUpdatedEvent,
    MessageWithParts,
    PartUpdatedEvent,
    TextPart,
    UserMessage,
)


pytestmark = pytest.mark.integration


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _make_user_message(session_id: str = "sess-dup") -> MessageWithParts:
    """Create a user message with a text part (simulating DB content)."""
    text_part = TextPart(
        id="part-text-1",
        message_id="msg-user-1",
        session_id=session_id,
        text="你好你是谁",
        synthetic=False,
    )
    return MessageWithParts(
        info=UserMessage(
            id="msg-user-1",
            session_id=session_id,
            time=TimeCreated(created=1784726591675),
        ),
        parts=[text_part],
    )


def _wrap_sse_event(data: Any, session_id: str) -> EventEnvelope:
    """Wrap an SSE event as the event bridge does (CustomEvent wrapper)."""
    from agentpool.agents.events.events import CustomEvent

    return EventEnvelope(
        source_session_id=session_id,
        event=CustomEvent(source="opencode_event_bridge", event_data=data),
    )


async def _drain_queue(queue: asyncio.Queue[Any]) -> list[Any]:
    """Drain all currently-available items from an async queue."""
    items: list[Any] = []
    while not queue.empty():
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


@pytest.mark.anyio
async def test_first_message_duplicated_without_replay_buffer_clear() -> None:
    """Reproduce: TUI sees duplicate parts when replay buffer + sync() both deliver.

    This test demonstrates the duplication:
    1. Events published to EventBus (user sends message before TUI SSE connects)
    2. Late SSE subscriber receives events from replay buffer
    3. sync() loads same parts from DB
    4. Parts exist in BOTH sources → duplication
    """
    session_id = "sess-dup-no-clear"
    event_bus = EventBus()

    user_msg = _make_user_message(session_id)

    # Step 1: User sends message → EventProcessor broadcasts events
    # (simulating what happens during the ~880ms before SSE consumer starts)
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            MessageUpdatedEvent.create(user_msg.info), session_id
        ).event,
    )
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            PartUpdatedEvent.create(user_msg.parts[0]), session_id
        ).event,
    )

    # Step 2: TUI's SSE connects AFTER events were published
    # EventBus replay buffer delivers the events to the new subscriber
    sse_queue = await event_bus.subscribe(session_id)
    replayed_events = await _drain_queue(sse_queue)

    # Extract PartUpdatedEvent from replayed events
    replayed_parts = [
        e.event.event_data
        for e in replayed_events
        if hasattr(e.event, "event_data")
        and isinstance(e.event.event_data, PartUpdatedEvent)
    ]

    # Step 3: TUI also calls sync() → loads from DB → gets same parts
    db_parts = user_msg.parts  # Simulate sync() loading from DB

    # Step 4: DUPLICATION — parts exist in BOTH replay buffer and DB
    assert len(replayed_parts) > 0, "Replay buffer should have PartUpdatedEvent"
    assert len(db_parts) > 0, "DB should have parts"

    # The part IDs match → TUI cannot deduplicate (no replayedParts in TUI app)
    # PartUpdatedEvent wraps the part in .properties.part
    replayed_part_ids = {p.properties.part.id for p in replayed_parts}
    db_part_ids = {p.id for p in db_parts}
    assert replayed_part_ids & db_part_ids, (
        "Part IDs overlap between replay buffer and DB → TUI renders duplicate"
    )


@pytest.mark.anyio
async def test_replay_buffer_clear_prevents_duplication() -> None:
    """Fix: sync() clears replay buffer → no duplicate parts.

    1. Events published to EventBus (user sends message before TUI connects)
    2. sync() called → clears replay buffer → loads from DB
    3. SSE subscribes AFTER replay buffer is cleared
    4. SSE subscriber does NOT receive old events from replay buffer
    5. Parts only come from DB (via sync()) → no duplication
    """
    session_id = "sess-dup-clear"
    event_bus = EventBus()

    user_msg = _make_user_message(session_id)

    # Step 1: Events published before TUI connects
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            MessageUpdatedEvent.create(user_msg.info), session_id
        ).event,
    )
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            PartUpdatedEvent.create(user_msg.parts[0]), session_id
        ).event,
    )

    # Step 2: TUI calls sync() → clears replay buffer (the fix)
    event_bus.clear_replay_buffer(session_id)
    db_parts = user_msg.parts  # sync() loads from DB

    # Step 3: SSE subscribes AFTER replay buffer is cleared
    sse_queue = await event_bus.subscribe(session_id)
    replayed_events = await _drain_queue(sse_queue)

    # Step 4: No old events from replay buffer
    replayed_parts = [
        e.event.event_data
        for e in replayed_events
        if hasattr(e.event, "event_data")
        and isinstance(e.event.event_data, PartUpdatedEvent)
    ]
    assert len(replayed_parts) == 0, (
        "Replay buffer should be empty after clear → no duplicate parts via SSE"
    )

    # Step 5: Parts only from DB → no duplication
    assert len(db_parts) > 0, "DB should have parts"
    # Only one source of parts → no duplication


@pytest.mark.anyio
async def test_live_events_still_delivered_after_replay_buffer_clear() -> None:
    """Regression: clearing replay buffer must not block live events.

    After sync() clears the replay buffer, NEW events published
    afterwards must still be delivered to SSE subscribers.
    """
    session_id = "sess-dup-live"
    event_bus = EventBus()

    user_msg = _make_user_message(session_id)

    # Subscribe to SSE first
    sse_queue = await event_bus.subscribe(session_id)

    # Clear replay buffer (sync() call)
    event_bus.clear_replay_buffer(session_id)

    # Publish a NEW event after clear (e.g., second user message)
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            PartUpdatedEvent.create(user_msg.parts[0]), session_id
        ).event,
    )

    # Live event should be delivered
    try:
        event = await asyncio.wait_for(sse_queue.get(), timeout=1.0)
        assert hasattr(event, "event"), "Should receive an event envelope"
        assert isinstance(event.event.event_data, PartUpdatedEvent), (
            "Should receive PartUpdatedEvent from live publish"
        )
    except TimeoutError:
        pytest.fail("Live event not delivered after replay buffer clear")


@pytest.mark.anyio
async def test_timing_race_consumer_startup_delays_delivery() -> None:
    """Reproduce the actual race: events published during consumer startup.

    Real-world timeline:
    1. Session created (t=0)
    2. REST handler sends user message → events published to EventBus (t=~50ms)
    3. Event consumer starts (t=~880ms) → subscribes to EventBus
    4. Consumer receives events from replay buffer (published 830ms earlier)
    5. Consumer also receives live events (assistant response)

    If sync() also loads from DB, the user message parts are duplicated.
    """
    session_id = "sess-dup-race"
    event_bus = EventBus()

    user_msg = _make_user_message(session_id)

    # t=0: Events published (user message processed by EventProcessor)
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            MessageUpdatedEvent.create(user_msg.info), session_id
        ).event,
    )
    await event_bus.publish(
        session_id,
        _wrap_sse_event(
            PartUpdatedEvent.create(user_msg.parts[0]), session_id
        ).event,
    )

    # t=~880ms: Consumer subscribes (simulated)
    # Without fix: replay buffer delivers old events → duplicate with sync()
    sse_no_clear = await event_bus.subscribe(session_id)
    events_no_clear = await _drain_queue(sse_no_clear)
    parts_no_clear = [
        e.event.event_data
        for e in events_no_clear
        if hasattr(e.event, "event_data")
        and isinstance(e.event.event_data, PartUpdatedEvent)
    ]
    assert len(parts_no_clear) == 1, (
        "Without fix: replay buffer delivers PartUpdatedEvent"
    )

    # With fix: sync() clears replay buffer before consumer subscribes
    event_bus.clear_replay_buffer(session_id)
    sse_with_clear = await event_bus.subscribe(session_id)
    events_with_clear = await _drain_queue(sse_with_clear)
    parts_with_clear = [
        e.event.event_data
        for e in events_with_clear
        if hasattr(e.event, "event_data")
        and isinstance(e.event.event_data, PartUpdatedEvent)
    ]
    assert len(parts_with_clear) == 0, (
        "With fix: replay buffer cleared → no duplicate PartUpdatedEvent"
    )
