"""Regression test: EventBus replay buffer must not deliver stale.

StreamCompleteEvent from a previous turn to the current turn's consumer.

Bug: When turn 2 subscribes to EventBus, the replay buffer from turn 1
(which contains StreamCompleteEvent) was being replayed. The consumer
saw the stale StreamCompleteEvent, broke out of the loop, and cancelled
the native runner via ``tg.cancel_scope.cancel()`` — causing a
CancelledError in ``agentlet.iter()`` before the LLM was ever called.

Fix: ``_run_turn_unlocked`` now calls ``event_bus.clear_replay_buffer()``
at the start of each turn, ensuring new subscribers only receive events
from the current turn.
"""

from __future__ import annotations

import anyio
import pytest

from agentpool.agents.events.events import (
    PartDeltaEvent,
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, EventEnvelope


def _make_run_started(session_id: str = "s1") -> RunStartedEvent:
    return RunStartedEvent(run_id="r1", session_id=session_id)


def _make_stream_complete(session_id: str = "s1") -> StreamCompleteEvent:
    return StreamCompleteEvent(
        message=ChatMessage(role="assistant", content="done"),
        session_id=session_id,
    )


def _make_part_delta(session_id: str = "s1") -> PartDeltaEvent:
    return PartDeltaEvent.text(index=0, content="hello")


@pytest.mark.unit
async def test_clear_replay_buffer_prevents_stale_events() -> None:
    """After clear_replay_buffer, new subscribers must NOT receive.

    events from previous turns.

    """
    bus = EventBus()

    # Simulate turn 1 publishing events including StreamCompleteEvent
    for event in [_make_run_started(), _make_stream_complete()]:
        await bus._send("s1", EventEnvelope(event=event, source_session_id="s1"))

    assert "s1" in bus._replay_buffers
    assert len(bus._replay_buffers["s1"]) == 2

    # Clear replay buffer (as _run_turn_unlocked now does)
    bus.clear_replay_buffer("s1")
    assert "s1" not in bus._replay_buffers

    # New subscriber should NOT receive any replayed events
    stream = await bus.subscribe("s1", scope="session")

    # Publish a new event (turn 2's RunStartedEvent)
    new_event = _make_run_started()
    await bus._send("s1", EventEnvelope(event=new_event, source_session_id="s1"))

    # Consumer should only receive the NEW event
    received: list = []
    with anyio.fail_after(1.0):
        async for envelope in stream:
            received.append(envelope.event)
            break

    assert len(received) == 1
    assert isinstance(received[0], RunStartedEvent)


@pytest.mark.unit
async def test_replay_buffer_replays_stale_without_clear() -> None:
    """Without clear_replay_buffer, new subscribers DO receive stale events.

    This documents the bug behavior that the fix prevents.

    """
    bus = EventBus()

    # Turn 1 publishes events including StreamCompleteEvent
    for event in [_make_run_started(), _make_stream_complete()]:
        await bus._send("s1", EventEnvelope(event=event, source_session_id="s1"))

    # New subscriber WITHOUT clearing replay buffer
    stream = await bus.subscribe("s1", scope="session")

    # Consumer WILL receive stale StreamCompleteEvent from replay
    received: list = []
    with anyio.fail_after(1.0):
        async for envelope in stream:
            received.append(envelope.event)
            if isinstance(envelope.event, (StreamCompleteEvent, RunErrorEvent)):
                break

    assert len(received) == 2
    assert isinstance(received[0], RunStartedEvent)
    assert isinstance(received[1], StreamCompleteEvent)


@pytest.mark.unit
async def test_clear_replay_buffer_preserves_active_subscribers() -> None:
    """clear_replay_buffer must NOT close active subscriber streams."""
    bus = EventBus()

    # Create a subscriber BEFORE clearing
    stream1 = await bus.subscribe("s1", scope="session")

    # Clear replay buffer
    bus.clear_replay_buffer("s1")

    # Publish a new event
    new_event = _make_part_delta()
    await bus._send("s1", EventEnvelope(event=new_event, source_session_id="s1"))

    # Existing subscriber should still receive the new event
    received: list = []
    with anyio.fail_after(1.0):
        async for envelope in stream1:
            received.append(envelope.event)
            break

    assert len(received) == 1
    assert isinstance(received[0], PartDeltaEvent)


@pytest.mark.unit
async def test_clear_replay_buffer_idempotent() -> None:
    """clear_replay_buffer should be safe to call on non-existent session."""
    bus = EventBus()
    bus.clear_replay_buffer("nonexistent")
    bus.clear_replay_buffer("nonexistent")
