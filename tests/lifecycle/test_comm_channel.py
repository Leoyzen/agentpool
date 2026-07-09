"""Tests for CommChannel dimension: DirectChannel and ProtocolChannel."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from agentpool.agents.events import (
    MessageReplacementEvent,
    PartDeltaEvent,
    PartStartEvent,
    PlanUpdateEvent,
    StateUpdate,
    ToolCallUpdateEvent,
)
from agentpool.lifecycle import (
    CommChannel,
    DirectChannel,
    Feedback,
    MemoryJournal,
    ProtocolChannel,
    RunState,
)
from agentpool.orchestrator.event_bus import EventBus


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_delta_event() -> PartDeltaEvent:
    """Create a simple PartDeltaEvent for testing."""
    return PartDeltaEvent.text(index=0, content="hello")


def _make_tool_call_update() -> ToolCallUpdateEvent:
    """Create a ToolCallUpdateEvent for testing."""
    return ToolCallUpdateEvent(
        tool_call_id="tc-123",
        tool_name="bash",
        status="completed",
    )


def _make_state_update() -> StateUpdate:
    """Create a StateUpdate event for testing."""
    return StateUpdate(
        session_id="sess-1",
        state=RunState.RUNNING,
    )


def _make_message_replacement() -> MessageReplacementEvent:
    """Create a MessageReplacementEvent for testing."""
    return MessageReplacementEvent(
        message_id="msg-1",
        content="replaced content",
    )


def _make_plan_update(tool_call_id: str | None = "tc-plan") -> PlanUpdateEvent:
    """Create a PlanUpdateEvent for testing."""
    return PlanUpdateEvent(
        entries=[],
        tool_call_id=tool_call_id,
    )


# ---------------------------------------------------------------------------
# Upsert key derivation
# ---------------------------------------------------------------------------


def test_upsert_key_tool_call_update():
    """ToolCallUpdateEvent derives key tool_call:{tool_call_id}."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_tool_call_update()
    assert _derive_upsert_key(event) == "tool_call:tc-123"


def test_upsert_key_tool_call_update_empty_id():
    """ToolCallUpdateEvent with empty tool_call_id returns None."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = ToolCallUpdateEvent(tool_call_id="", tool_name="bash")
    assert _derive_upsert_key(event) is None


def test_upsert_key_state_update():
    """StateUpdate derives key state:{session_id}."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_state_update()
    assert _derive_upsert_key(event) == "state:sess-1"


def test_upsert_key_state_update_empty_session():
    """StateUpdate with empty session_id returns None."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = StateUpdate(session_id="", state=RunState.IDLE)
    assert _derive_upsert_key(event) is None


def test_upsert_key_message_replacement():
    """MessageReplacementEvent derives key msg:{message_id}."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_message_replacement()
    assert _derive_upsert_key(event) == "msg:msg-1"


def test_upsert_key_message_replacement_empty_id():
    """MessageReplacementEvent with empty message_id returns None."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = MessageReplacementEvent(message_id="", content="x")
    assert _derive_upsert_key(event) is None


def test_upsert_key_plan_update_with_tool_call_id():
    """PlanUpdateEvent with tool_call_id derives key plan:{tool_call_id}."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_plan_update(tool_call_id="tc-plan")
    assert _derive_upsert_key(event) == "plan:tc-plan"


def test_upsert_key_plan_update_without_tool_call_id():
    """PlanUpdateEvent with None tool_call_id returns None."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_plan_update(tool_call_id=None)
    assert _derive_upsert_key(event) is None


def test_upsert_key_delta_event_returns_none():
    """PartDeltaEvent returns None (append semantics)."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = _make_delta_event()
    assert _derive_upsert_key(event) is None


def test_upsert_key_part_start_event_returns_none():
    """PartStartEvent returns None (append semantics)."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    event = PartStartEvent.text(index=0, content="start")
    assert _derive_upsert_key(event) is None


def test_upsert_key_arbitrary_object_returns_none():
    """Arbitrary objects return None (append semantics)."""
    from agentpool.lifecycle.comm_channel import _derive_upsert_key

    assert _derive_upsert_key("not an event") is None
    assert _derive_upsert_key(42) is None
    assert _derive_upsert_key({"key": "value"}) is None


# ---------------------------------------------------------------------------
# DirectChannel — Protocol conformance
# ---------------------------------------------------------------------------


def test_direct_channel_protocol_conformance():
    """DirectChannel satisfies the CommChannel Protocol."""
    channel = DirectChannel(MemoryJournal())
    assert isinstance(channel, CommChannel)


# ---------------------------------------------------------------------------
# DirectChannel — publish
# ---------------------------------------------------------------------------


async def test_direct_channel_publish_enqueues_event():
    """publish() enqueues the event to the internal queue."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_delta_event()

    await channel.publish(event)

    assert channel.queue.qsize() == 1
    dequeued = channel.queue.get_nowait()
    assert dequeued is event


async def test_direct_channel_publish_journals_append_for_delta():
    """publish() journals via append for delta events."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_delta_event()

    await channel.publish(event)

    assert len(journal._entries) == 1
    assert journal._entries[0][1] is event
    assert len(journal._upserts) == 0


async def test_direct_channel_publish_journals_upsert_for_tool_call_update():
    """publish() journals via upsert for ToolCallUpdateEvent."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_tool_call_update()

    await channel.publish(event)

    assert len(journal._upserts) == 1
    assert "tool_call:tc-123" in journal._upserts
    assert journal._upserts["tool_call:tc-123"][1] is event
    assert len(journal._entries) == 0


async def test_direct_channel_publish_journals_upsert_for_state_update():
    """publish() journals via upsert for StateUpdate."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_state_update()

    await channel.publish(event)

    assert len(journal._upserts) == 1
    assert "state:sess-1" in journal._upserts


async def test_direct_channel_publish_journals_upsert_for_message_replacement():
    """publish() journals via upsert for MessageReplacementEvent."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_message_replacement()

    await channel.publish(event)

    assert len(journal._upserts) == 1
    assert "msg:msg-1" in journal._upserts


async def test_direct_channel_publish_journals_upsert_for_plan_update():
    """publish() journals via upsert for PlanUpdateEvent with tool_call_id."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_plan_update(tool_call_id="tc-plan")

    await channel.publish(event)

    assert len(journal._upserts) == 1
    assert "plan:tc-plan" in journal._upserts


async def test_direct_channel_publish_journals_append_for_plan_update_no_id():
    """publish() journals via append for PlanUpdateEvent without tool_call_id."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    event = _make_plan_update(tool_call_id=None)

    await channel.publish(event)

    assert len(journal._entries) == 1
    assert len(journal._upserts) == 0


async def test_direct_channel_publish_skips_journaling_when_replaying():
    """publish() skips journaling when _replaying is True."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)
    channel._replaying = True
    event = _make_delta_event()

    await channel.publish(event)

    assert len(journal._entries) == 0
    assert len(journal._upserts) == 0
    assert channel.queue.qsize() == 1


async def test_direct_channel_publish_multiple_events():
    """publish() enqueues multiple events in order."""
    journal = MemoryJournal()
    channel = DirectChannel(journal)

    for i in range(5):
        await channel.publish(PartDeltaEvent.text(index=i, content=f"chunk-{i}"))

    assert channel.queue.qsize() == 5
    for _i in range(5):
        event = channel.queue.get_nowait()
        assert isinstance(event, PartDeltaEvent)


# ---------------------------------------------------------------------------
# DirectChannel — recv
# ---------------------------------------------------------------------------


def test_direct_channel_recv_always_none():
    """recv() always returns None for DirectChannel."""
    channel = DirectChannel(MemoryJournal())
    assert channel.recv() is None


async def test_direct_channel_recv_none_after_publish():
    """recv() returns None even after events have been published."""
    channel = DirectChannel(MemoryJournal())
    await channel.publish(_make_delta_event())
    assert channel.recv() is None


# ---------------------------------------------------------------------------
# DirectChannel — close
# ---------------------------------------------------------------------------


async def test_direct_channel_close_prevents_publish():
    """close() prevents further publish (RuntimeError)."""
    channel = DirectChannel(MemoryJournal())
    await channel.publish(_make_delta_event())
    channel.close()

    with pytest.raises(RuntimeError, match="closed"):
        await channel.publish(_make_delta_event())


async def test_direct_channel_close_drains_queue():
    """close() drains the internal queue."""
    channel = DirectChannel(MemoryJournal())
    await channel.publish(_make_delta_event())
    await channel.publish(_make_delta_event())
    assert channel.queue.qsize() == 2

    channel.close()
    assert channel.queue.empty()


# ---------------------------------------------------------------------------
# DirectChannel — attach and on_state_change
# ---------------------------------------------------------------------------


def test_direct_channel_attach_is_noop():
    """attach() stores run_loop without crashing."""
    channel = DirectChannel(MemoryJournal())
    channel.attach("fake_run_loop")
    assert channel._run_loop == "fake_run_loop"


def test_direct_channel_on_state_change_is_noop():
    """on_state_change() tracks state without crashing."""
    channel = DirectChannel(MemoryJournal())
    channel.on_state_change(RunState.RUNNING)
    assert channel._state == RunState.RUNNING

    channel.on_state_change(RunState.IDLE)
    assert channel._state == RunState.IDLE


# ---------------------------------------------------------------------------
# DirectChannel — queue property
# ---------------------------------------------------------------------------


def test_direct_channel_queue_property_accessible():
    """Queue property returns the internal asyncio.Queue."""
    channel = DirectChannel(MemoryJournal())
    assert isinstance(channel.queue, type(channel._queue))
    assert channel.queue is channel._queue


# ---------------------------------------------------------------------------
# ProtocolChannel — Protocol conformance
# ---------------------------------------------------------------------------


def test_protocol_channel_protocol_conformance():
    """ProtocolChannel satisfies the CommChannel Protocol."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)
    assert isinstance(channel, CommChannel)


# ---------------------------------------------------------------------------
# ProtocolChannel — construction
# ---------------------------------------------------------------------------


def test_protocol_channel_requires_journal_and_event_bus():
    """ProtocolChannel requires both journal and event_bus."""
    mock_bus = AsyncMock(spec=EventBus)
    journal = MemoryJournal()
    channel = ProtocolChannel(journal, mock_bus)

    assert channel._journal is journal
    assert channel._event_bus is mock_bus


# ---------------------------------------------------------------------------
# ProtocolChannel — publish
# ---------------------------------------------------------------------------


async def test_protocol_channel_publish_delivers_to_event_bus():
    """publish() delivers events to EventBus."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus, session_id="sess-1")
    event = _make_delta_event()

    await channel.publish(event)

    mock_bus.publish.assert_awaited_once_with("sess-1", event)


async def test_protocol_channel_publish_journals_before_delivery():
    """publish() journals the event before delivering to EventBus."""
    journal = MemoryJournal()
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(journal, mock_bus, session_id="sess-1")
    event = _make_delta_event()

    await channel.publish(event)

    assert len(journal._entries) == 1
    assert journal._entries[0][1] is event


async def test_protocol_channel_publish_journals_upsert_for_tool_call():
    """publish() uses upsert for entity-state events."""
    journal = MemoryJournal()
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(journal, mock_bus, session_id="sess-1")
    event = _make_tool_call_update()

    await channel.publish(event)

    assert len(journal._upserts) == 1
    assert "tool_call:tc-123" in journal._upserts


async def test_protocol_channel_publish_skips_journaling_when_replaying():
    """publish() skips journaling when _replaying is True."""
    journal = MemoryJournal()
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(journal, mock_bus, session_id="sess-1")
    channel._replaying = True
    event = _make_delta_event()

    await channel.publish(event)

    assert len(journal._entries) == 0
    assert len(journal._upserts) == 0
    mock_bus.publish.assert_awaited_once_with("sess-1", event)


# ---------------------------------------------------------------------------
# ProtocolChannel — feedback queue
# ---------------------------------------------------------------------------


def test_protocol_channel_recv_returns_none_when_empty():
    """recv() returns None when no feedback is available."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)
    assert channel.recv() is None


def test_protocol_channel_feedback_round_trip():
    """deliver_feedback() then recv() returns the Feedback."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)
    feedback = Feedback(content="steer left", is_steer=True)

    channel.deliver_feedback(feedback)
    result = channel.recv()

    assert result is feedback


def test_protocol_channel_feedback_multiple_round_trip():
    """Multiple feedback items are dequeued in order."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    fb1 = Feedback(content="first", is_steer=True)
    fb2 = Feedback(content="second", is_steer=False)

    channel.deliver_feedback(fb1)
    channel.deliver_feedback(fb2)

    assert channel.recv() is fb1
    assert channel.recv() is fb2
    assert channel.recv() is None


# ---------------------------------------------------------------------------
# ProtocolChannel — on_state_change
# ---------------------------------------------------------------------------


def test_protocol_channel_on_state_change_tracks_state():
    """on_state_change() tracks the RunLoop state."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    assert channel._state is None

    channel.on_state_change(RunState.RUNNING)
    assert channel._state == RunState.RUNNING

    channel.on_state_change(RunState.IDLE)
    assert channel._state == RunState.IDLE

    channel.on_state_change(RunState.DONE)
    assert channel._state == RunState.DONE


# ---------------------------------------------------------------------------
# ProtocolChannel — attach
# ---------------------------------------------------------------------------


def test_protocol_channel_attach_stores_run_loop():
    """attach() stores the run_loop reference."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    channel.attach("fake_run_loop")
    assert channel._run_loop == "fake_run_loop"


# ---------------------------------------------------------------------------
# ProtocolChannel — close
# ---------------------------------------------------------------------------


async def test_protocol_channel_close_prevents_publish():
    """close() prevents further publish (RuntimeError)."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    channel.close()

    with pytest.raises(RuntimeError, match="closed"):
        await channel.publish(_make_delta_event())


def test_protocol_channel_close_drains_feedback_queue():
    """close() drains the feedback queue."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    channel.deliver_feedback(Feedback(content="fb1", is_steer=True))
    channel.deliver_feedback(Feedback(content="fb2", is_steer=False))
    assert channel._feedback_queue.qsize() == 2

    channel.close()
    assert channel._feedback_queue.empty()


def test_protocol_channel_recv_none_after_close():
    """recv() returns None after close()."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)

    channel.deliver_feedback(Feedback(content="fb", is_steer=True))
    channel.close()
    assert channel.recv() is None


# ---------------------------------------------------------------------------
# DirectChannel — integration with real EventBus for ProtocolChannel
# ---------------------------------------------------------------------------


async def test_protocol_channel_with_real_event_bus():
    """ProtocolChannel works with a real EventBus instance."""
    from agentpool.orchestrator.event_bus import EventBus

    bus = EventBus()
    journal = MemoryJournal()
    channel = ProtocolChannel(journal, bus, session_id="test-sess")

    # Subscribe to receive events
    queue = await bus.subscribe("test-sess")

    event = _make_delta_event()
    await channel.publish(event)

    # The event should arrive wrapped in an EventEnvelope
    envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
    assert envelope.event is event


# ---------------------------------------------------------------------------
# _replaying flag
# ---------------------------------------------------------------------------


def test_direct_channel_replaying_default_false():
    """_replaying defaults to False."""
    channel = DirectChannel(MemoryJournal())
    assert channel._replaying is False


def test_protocol_channel_replaying_default_false():
    """_replaying defaults to False."""
    mock_bus = AsyncMock(spec=EventBus)
    channel = ProtocolChannel(MemoryJournal(), mock_bus)
    assert channel._replaying is False
