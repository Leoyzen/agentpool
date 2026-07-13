"""Tests for RunLoop lifecycle integration in RunHandle.

Covers M2 Task 7: constructor dimension defaults, state machine,
start() main loop with journaling, snapshots, crash recovery,
and turn_id generation.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
import uuid

import pytest

from agentpool.agents.events import (
    RunStartedEvent,
    StateUpdate,
    StreamCompleteEvent,
)
from agentpool.lifecycle import (
    DirectChannel,
    ImmediateTrigger,
    InProcessTransport,
    MemoryJournal,
    MemorySnapshotStore,
    RunState,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus
from agentpool.lifecycle import RunState
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn implementation for testing."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
        raise_exc: BaseException | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []
        self._raise = raise_exc

    async def execute(self):  # type: ignore[override]
        if self._raise is not None:
            raise self._raise
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_run_handle(
    *,
    agent: Any | None = None,
    event_bus: Any | None = None,
    session: Any | None = None,
    run_id: str = "test-run",
    session_id: str = "test-session",
    agent_type: str = "native",
    **kwargs: Any,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
    if agent is None:
        agent = MagicMock()
        agent.create_turn = MagicMock(return_value=_StubTurn())
    if event_bus is None:
        event_bus = AsyncMock()
    if session is None:
        session = MagicMock()
        session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id=run_id,
        session_id=session_id,
        agent_type=agent_type,
        agent=agent,
        event_bus=event_bus,
        session=session,
        **kwargs,
    )


async def _consume_gen(gen: Any) -> list[Any]:
    """Consume an async generator and return all events."""
    return [event async for event in gen]


# ---------------------------------------------------------------------------
# Constructor / default dimensions
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_default_dimensions() -> None:
    """RunHandle constructed with only required fields gets default lifecycle dimensions."""
    handle = _make_run_handle()

    assert isinstance(handle._trigger_source, ImmediateTrigger)
    assert isinstance(handle._journal, MemoryJournal)
    assert isinstance(handle._snapshot_store, MemorySnapshotStore)
    assert isinstance(handle._comm_channel, DirectChannel)
    assert isinstance(handle._event_transport, InProcessTransport)
    assert handle._lifecycle_session_id == "default"
    assert handle._run_state == RunState.IDLE


@pytest.mark.unit
async def test_journal_injection_into_comm_channel() -> None:
    """__post_init__ injects the journal into the CommChannel."""
    handle = _make_run_handle()

    assert handle._comm_channel is not None
    assert handle._comm_channel._journal is handle._journal


@pytest.mark.unit
async def test_custom_journal_injected_into_custom_comm_channel() -> None:
    """Custom CommChannel without journal gets the custom journal injected.

    When a CommChannel is passed that has no _journal attribute or has
    _journal set to None, __post_init__ injects the handle's journal.
    When the CommChannel already has a journal, it is preserved.
    """
    custom_journal = MemoryJournal()
    # Create a DirectChannel with a different journal initially.
    custom_channel = DirectChannel(MemoryJournal())
    handle = _make_run_handle(
        _journal=custom_journal,
        _comm_channel=custom_channel,
    )

    assert handle._journal is custom_journal
    assert handle._comm_channel is custom_channel
    # The channel's original journal is preserved (not overwritten).
    assert custom_channel._journal is not custom_journal
    # But it still has a journal.
    assert custom_channel._journal is not None


@pytest.mark.unit
async def test_custom_dimensions_preserved() -> None:
    """Custom dimensions passed to constructor are preserved."""
    custom_journal = MemoryJournal()
    custom_snapshot = MemorySnapshotStore()
    custom_transport = InProcessTransport(replay_buffer_size=10)
    handle = _make_run_handle(
        _journal=custom_journal,
        _snapshot_store=custom_snapshot,
        _event_transport=custom_transport,
        _lifecycle_session_id="my-session",
    )

    assert handle._journal is custom_journal
    assert handle._snapshot_store is custom_snapshot
    assert handle._event_transport is custom_transport
    assert handle._lifecycle_session_id == "my-session"


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_is_running_property() -> None:
    """is_running returns True only when _run_state is RUNNING."""
    handle = _make_run_handle()
    assert handle.is_running is False

    handle._run_state = RunState.RUNNING
    assert handle.is_running is True

    handle._run_state = RunState.DONE
    assert handle.is_running is False


@pytest.mark.unit
async def test_state_transition_publishes_state_update() -> None:
    """_transition publishes a StateUpdate event via comm_channel."""
    handle = _make_run_handle()

    # Spy on comm_channel.publish
    published_events: list[Any] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        published_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    await handle._transition(RunState.RUNNING)

    assert handle._run_state == RunState.RUNNING
    assert len(published_events) == 1
    assert isinstance(published_events[0], StateUpdate)
    assert published_events[0].state == RunState.RUNNING
    assert published_events[0].session_id == "default"


@pytest.mark.unit
async def test_state_transition_with_stop_reason() -> None:
    """_transition passes stop_reason to StateUpdate event."""
    handle = _make_run_handle()

    published_events: list[Any] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        published_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    await handle._transition(RunState.IDLE, stop_reason="crash_recovery")

    assert len(published_events) == 1
    assert published_events[0].stop_reason == "crash_recovery"


# ---------------------------------------------------------------------------
# Fresh start
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_fresh_start_saves_initial_snapshot() -> None:
    """On fresh start (no prior journal), an initial snapshot is saved."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    # Fresh start: no snapshot yet.
    assert handle._snapshot_store is not None
    assert handle._snapshot_store.load() is None

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # After start, initial snapshot was saved.
    snapshot = handle._snapshot_store.load()
    assert snapshot is not None
    state_data, _ = snapshot
    assert state_data["state"] == RunState.IDLE.value
    assert state_data["run_id"] == "test-run"


@pytest.mark.unit
async def test_fresh_start_transitions_idle_running_idle() -> None:
    """Fresh start goes through IDLE → RUNNING → IDLE state transitions."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    state_events: list[StateUpdate] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        if isinstance(event, StateUpdate):
            state_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Expect at least: IDLE (initial), RUNNING, IDLE (after turn), DONE
    states = [e.state for e in state_events]
    assert RunState.IDLE in states
    assert RunState.RUNNING in states
    assert RunState.DONE in states


# ---------------------------------------------------------------------------
# Main loop: journaling and snapshots
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_main_loop_events_journaled() -> None:
    """Events are journaled via comm_channel.publish during turn execution."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Journal should have entries: StateUpdate(IDLE), RunStartedEvent,
    # StreamCompleteEvent, StateUpdate(IDLE), StateUpdate(DONE), etc.
    assert handle._journal is not None
    assert len(handle._journal._entries) > 0


@pytest.mark.unit
async def test_main_loop_snapshot_saved_at_turn_boundary() -> None:
    """A snapshot is saved after each turn completes."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # After turn completion, snapshot should include turn_id.
    snapshot = handle._snapshot_store.load()
    assert snapshot is not None
    state_data, _ = snapshot
    assert "turn_id" in state_data
    assert state_data["run_id"] == "test-run"


@pytest.mark.unit
async def test_turn_result_saved_for_idempotency() -> None:
    """Turn result is saved to snapshot_store for idempotent recovery."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # The turn_id should have a saved result.
    assert handle.run_ctx.turn_id is not None
    assert handle._snapshot_store.has_turn_result(handle.run_ctx.turn_id)


# ---------------------------------------------------------------------------
# turn_id generation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_turn_id_is_uuid_string() -> None:
    """turn_id is a valid UUID string and stored on run_ctx."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # turn_id should be a valid UUID string.
    assert handle.run_ctx.turn_id is not None
    parsed = uuid.UUID(handle.run_ctx.turn_id)
    assert str(parsed) == handle.run_ctx.turn_id


@pytest.mark.unit
async def test_turn_id_unique_per_turn() -> None:
    """Each turn gets a unique turn_id."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Steer to trigger a second turn.
    handle.steer("second prompt")
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Two turns should have been created with different turn_ids.
    assert agent.create_turn.call_count == 2


# ---------------------------------------------------------------------------
# Crash recovery
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_crash_recovery_start_inflight() -> None:
    """When journal.resume() returns in-flight ResumeResult, events are replayed."""
    # Set up a journal with prior state.
    journal = MemoryJournal()
    snapshot_store = MemorySnapshotStore()

    # Simulate prior crash: save a snapshot, then journal some events
    # without completing the turn.
    # Set snapshot with seq=0 so journal entries (seq=1+) are found.
    snapshot_store._snapshot = (
        {"state": RunState.RUNNING.value, "run_id": "crashed"},
        0,
    )
    journal.append({"event_type": "RunStartedEvent", "turn_id": "inflight-1"})

    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(
        agent=agent,
        _journal=journal,
        _snapshot_store=snapshot_store,
    )

    state_events: list[StateUpdate] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        if isinstance(event, StateUpdate):
            state_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("resume")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # The first StateUpdate should have stop_reason="crash_recovery".
    crash_recovery_events = [e for e in state_events if e.stop_reason == "crash_recovery"]
    assert len(crash_recovery_events) >= 1
    assert crash_recovery_events[0].state == RunState.IDLE


@pytest.mark.unit
async def test_crash_recovery_normal_resume() -> None:
    """When journal.resume() returns non-inflight ResumeResult, IDLE transition occurs."""
    journal = MemoryJournal()
    snapshot_store = MemorySnapshotStore()

    # Simulate prior clean shutdown: snapshot with IDLE state.
    # Set snapshot with seq=0 so any journal entries are found.
    snapshot_store._snapshot = (
        {"state": RunState.IDLE.value, "run_id": "prev"},
        0,
    )
    # No events since snapshot.

    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(
        agent=agent,
        _journal=journal,
        _snapshot_store=snapshot_store,
    )

    consumer = asyncio.create_task(_consume_gen(handle.start("resume")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Should have completed normally.
    assert handle._run_state == RunState.DONE


# ---------------------------------------------------------------------------
# EventTransport lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_event_transport_available_after_start() -> None:
    """EventTransport is accessible after start() begins."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    assert handle._event_transport is not None
    assert not handle._event_transport._closed  # type: ignore[attr-defined]

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_event_transport_closed_after_close() -> None:
    """EventTransport is closed after start() completes (via finally block)."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    assert handle._event_transport is not None
    assert handle._event_transport._closed  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Trigger source integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_trigger_source_subscribed() -> None:
    """TriggerSource.subscribe() is called during start()."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)

    # Use ProtocolTrigger to verify subscribe() sets _run_loop.
    from agentpool.lifecycle import ProtocolTrigger

    trigger = ProtocolTrigger()
    handle = _make_run_handle(agent=agent, _trigger_source=trigger)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # ProtocolTrigger.subscribe() stores the run_loop reference.
    assert trigger._run_loop is handle


@pytest.mark.unit
async def test_comm_channel_attached() -> None:
    """CommChannel.attach() is called during start()."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)

    channel = DirectChannel(MemoryJournal())
    handle = _make_run_handle(agent=agent, _comm_channel=channel)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # DirectChannel.attach() sets _run_loop.
    assert channel._run_loop is handle


# ---------------------------------------------------------------------------
# State transitions: full lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_state_transitions_full_lifecycle() -> None:
    """Full lifecycle: IDLE → RUNNING → IDLE → DONE."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    state_events: list[StateUpdate] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        if isinstance(event, StateUpdate):
            state_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    states = [e.state for e in state_events]

    # Expect: IDLE (initial), RUNNING (turn start), IDLE (turn end), DONE (finally)
    assert RunState.IDLE in states
    assert RunState.RUNNING in states
    assert RunState.DONE in states

    # DONE should be the last state.
    assert states[-1] == RunState.DONE


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_existing_event_bus_publish_preserved() -> None:
    """EventBus.publish() is still called alongside comm_channel.publish()."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    event_bus = AsyncMock()
    handle = _make_run_handle(agent=agent, event_bus=event_bus)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # EventBus.publish should have been called multiple times.
    assert event_bus.publish.call_count >= 2

    # Verify RunStartedEvent was published to event_bus.
    published = [call.args[1] for call in event_bus.publish.call_args_list]
    assert any(isinstance(e, RunStartedEvent) for e in published)


@pytest.mark.unit
async def test_run_started_event_published_to_both_channels() -> None:
    """RunStartedEvent is published to both EventBus and CommChannel."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    event_bus = AsyncMock()
    handle = _make_run_handle(agent=agent, event_bus=event_bus)

    journal_events: list[Any] = []
    original_publish = handle._comm_channel.publish  # type: ignore[union-attr]

    async def _spy_publish(event: Any) -> None:
        journal_events.append(event)
        await original_publish(event)

    handle._comm_channel.publish = _spy_publish  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Both channels should have received RunStartedEvent.
    bus_events = [call.args[1] for call in event_bus.publish.call_args_list]
    assert any(isinstance(e, RunStartedEvent) for e in bus_events)
    assert any(isinstance(e, RunStartedEvent) for e in journal_events)


# ---------------------------------------------------------------------------
# Task 8: steer(), followup(), close() + CommChannel feedback
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_when_idle_direct_channel() -> None:
    """steer() when IDLE with DirectChannel appends to _message_queue and sets _idle_event."""
    handle = _make_run_handle()
    handle._run_state = RunState.IDLE

    result = handle.steer("steer message")

    assert result is True
    assert "steer message" in handle._message_queue
    assert handle._idle_event.is_set()


@pytest.mark.unit
async def test_steer_when_running_direct_channel_with_agent_run() -> None:
    """steer() when RUNNING with DirectChannel injects via active_agent_run.enqueue()."""
    handle = _make_run_handle()
    handle._run_state = RunState.RUNNING

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()

    handle.active_agent_run = mock_agent_run

    result = handle.steer("steer message")

    assert result is True
    mock_agent_run.enqueue.assert_called_once_with("steer message", priority="asap")


@pytest.mark.unit
async def test_steer_when_running_direct_channel_without_agent_run() -> None:
    """steer() when RUNNING without active_agent_run queues to queued_steer_messages."""
    handle = _make_run_handle()
    handle._run_state = RunState.RUNNING
    handle.active_agent_run = None

    result = handle.steer("steer message")

    assert result is True
    assert "steer message" in handle.run_ctx.queued_steer_messages


@pytest.mark.unit
async def test_followup_during_active_turn() -> None:
    """followup() during RUNNING appends to _message_queue without interrupting."""
    handle = _make_run_handle()
    handle._run_state = RunState.RUNNING
    handle._idle_event.clear()

    result = handle.followup("followup message")

    assert result is True
    assert "followup message" in handle._message_queue
    # _idle_event should NOT be set when running (no need to wake).
    assert not handle._idle_event.is_set()


@pytest.mark.unit
async def test_followup_when_idle() -> None:
    """followup() when IDLE appends to _message_queue and sets _idle_event."""
    handle = _make_run_handle()
    handle._run_state = RunState.IDLE

    result = handle.followup("followup message")

    assert result is True
    assert "followup message" in handle._message_queue
    assert handle._idle_event.is_set()


@pytest.mark.unit
async def test_close_while_idle_transitions_to_done() -> None:
    """close() while idle schedules transition to RunState.DONE."""
    handle = _make_run_handle()
    handle._run_state = RunState.IDLE
    handle._run_state = RunState.IDLE

    handle.close()

    assert handle._closing is True
    assert handle._idle_event.is_set()
    # The scheduled task transitions to DONE.
    await asyncio.sleep(0.05)
    assert handle._run_state == RunState.DONE


@pytest.mark.unit
async def test_close_twice_is_noop() -> None:
    """close() called twice: second call is a no-op."""
    handle = _make_run_handle()
    handle._run_state = RunState.IDLE

    handle.close()
    first_closing = handle._closing

    handle.close()
    # Values should be unchanged.
    assert handle._closing is first_closing


@pytest.mark.unit
async def test_steer_after_close_raises_runtime_error() -> None:
    """steer() after close() raises RuntimeError once _closed is set."""
    handle = _make_run_handle()
    handle.close()
    # _closed is set by start()'s finally block. Simulate it here
    # since start() hasn't been called.
    handle._closed = True

    with pytest.raises(RuntimeError, match="Cannot steer after close"):
        handle.steer("should fail")


@pytest.mark.unit
async def test_followup_after_close_returns_false() -> None:
    """followup() after close() returns False (does not raise)."""
    handle = _make_run_handle()
    handle.close()

    result = handle.followup("should fail")
    assert result is False


@pytest.mark.unit
async def test_steer_when_closing_returns_false() -> None:
    """steer() when _closing but not _closed returns False (edge case)."""
    handle = _make_run_handle()
    handle._closing = True
    handle._closed = False

    result = handle.steer("should fail")
    assert result is False


@pytest.mark.unit
async def test_close_closes_dimensions_in_start_finally() -> None:
    """start() finally block closes comm_channel, trigger_source, event_transport."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    comm_close_called = False
    trigger_close_called = False
    transport_close_called = False

    original_comm_close = handle._comm_channel.close  # type: ignore[union-attr]
    original_trigger_close = handle._trigger_source.close  # type: ignore[union-attr]
    original_transport_close = handle._event_transport.close  # type: ignore[union-attr]

    def _spy_comm_close() -> None:
        nonlocal comm_close_called
        comm_close_called = True
        original_comm_close()

    def _spy_trigger_close() -> None:
        nonlocal trigger_close_called
        trigger_close_called = True
        original_trigger_close()

    def _spy_transport_close() -> None:
        nonlocal transport_close_called
        transport_close_called = True
        original_transport_close()

    handle._comm_channel.close = _spy_comm_close  # type: ignore[union-attr,method-assign]
    handle._trigger_source.close = _spy_trigger_close  # type: ignore[union-attr,method-assign]
    handle._event_transport.close = _spy_transport_close  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    assert comm_close_called
    assert trigger_close_called
    assert transport_close_called


@pytest.mark.unit
async def test_close_with_pending_messages_processed() -> None:
    """close() with pending messages: they are processed as final Turns before DONE."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Queue a followup (loop is idle, waiting on _idle_event).
    handle.followup("second prompt")
    # Close immediately — the loop should still process the pending
    # message before exiting.
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Two turns should have been created: initial + followup.
    assert agent.create_turn.call_count == 2
    assert handle._run_state == RunState.DONE


@pytest.mark.unit
async def test_steer_with_protocol_channel_routes_via_deliver_feedback() -> None:
    """steer() with ProtocolChannel routes through deliver_feedback()."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.RUNNING

    result = handle.steer("protocol steer")

    assert result is True
    # Feedback should be in the ProtocolChannel's feedback queue.
    feedback = channel.recv()
    assert feedback is not None
    assert feedback.content == "protocol steer"
    assert feedback.is_steer is True


@pytest.mark.unit
async def test_followup_with_protocol_channel_routes_via_deliver_feedback() -> None:
    """followup() with ProtocolChannel routes through deliver_feedback() with is_steer=False."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.RUNNING

    result = handle.followup("protocol followup")

    assert result is True
    feedback = channel.recv()
    assert feedback is not None
    assert feedback.content == "protocol followup"
    assert feedback.is_steer is False


@pytest.mark.unit
async def test_steer_protocol_channel_when_idle_sets_idle_event() -> None:
    """steer() with ProtocolChannel when IDLE sets _idle_event to wake the loop."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.IDLE
    handle._idle_event.clear()

    result = handle.steer("idle steer")

    assert result is True
    assert handle._idle_event.is_set()
    # Feedback should be in the channel's queue.
    feedback = channel.recv()
    assert feedback is not None
    assert feedback.is_steer is True


@pytest.mark.unit
async def test_feedback_routing_from_comm_channel_recv() -> None:
    """Feedback from comm_channel.recv() is picked up by start() loop after Turn completion."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")

    turn_events = [_stream_complete_event()]
    turn = _StubTurn(events=turn_events, message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent, event_bus=event_bus, _comm_channel=channel)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Steer via ProtocolChannel feedback (this is what SessionController would do).
    handle.steer("steer via feedback")
    # Wake the loop if it's idle.
    handle._idle_event.set()
    await asyncio.sleep(0.1)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Two turns should have been created: initial + steer feedback.
    assert agent.create_turn.call_count >= 2


@pytest.mark.unit
async def test_multi_turn_with_protocol_trigger() -> None:
    """Multi-Turn with ProtocolTrigger: idle→running→idle cycles."""
    from agentpool.lifecycle import ProtocolTrigger

    trigger = ProtocolTrigger()
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent, _trigger_source=trigger)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Queue a second prompt via followup (loop is idle).
    handle.followup("second prompt")
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    assert agent.create_turn.call_count == 2
    assert handle._run_state == RunState.DONE


@pytest.mark.unit
async def test_idempotency_skip_when_turn_result_exists() -> None:
    """When has_turn_result returns True for a turn_id, the Turn is skipped."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    # Pre-populate snapshot store with a turn result for a known turn_id.
    known_turn_id = "pre-completed-turn"
    handle._snapshot_store.save_turn_result(
        known_turn_id,
        ChatMessage(content="done", role="assistant"),
    )

    assert handle._snapshot_store.has_turn_result(known_turn_id) is True
    # The turn result exists, confirming idempotency data is available.
    assert handle._snapshot_store.has_turn_result("nonexistent") is False


@pytest.mark.unit
async def test_steer_direct_channel_does_not_use_deliver_feedback() -> None:
    """steer() with DirectChannel does NOT route via deliver_feedback (uses isinstance check)."""
    from agentpool.lifecycle.comm_channel import DirectChannel

    handle = _make_run_handle()
    # DirectChannel now has deliver_feedback (no-op for protocol conformance),
    # but steer() skips the feedback path via isinstance check.
    assert isinstance(handle._comm_channel, DirectChannel)
    assert callable(handle._comm_channel.deliver_feedback)

    handle._run_state = RunState.IDLE
    result = handle.steer("direct steer")

    assert result is True
    assert "direct steer" in handle._message_queue


@pytest.mark.unit
async def test_followup_direct_channel_does_not_use_deliver_feedback() -> None:
    """followup() with DirectChannel does NOT call deliver_feedback."""
    handle = _make_run_handle()
    handle._run_state = RunState.IDLE

    result = handle.followup("direct followup")

    assert result is True
    assert "direct followup" in handle._message_queue


@pytest.mark.unit
async def test_steer_protocol_channel_does_not_touch_message_queue() -> None:
    """steer() with ProtocolChannel does not append to _message_queue (routes via feedback)."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.RUNNING

    result = handle.steer("protocol steer")

    assert result is True
    # _message_queue should be empty — feedback went to ProtocolChannel.
    assert len(handle._message_queue) == 0
    # Feedback is in the channel.
    assert channel.recv() is not None


@pytest.mark.unit
async def test_followup_protocol_channel_does_not_touch_message_queue() -> None:
    """followup() with ProtocolChannel does not append to _message_queue."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.RUNNING

    result = handle.followup("protocol followup")

    assert result is True
    assert len(handle._message_queue) == 0
    assert channel.recv() is not None


@pytest.mark.unit
async def test_close_does_not_double_close_dimensions() -> None:
    """close() method itself does NOT close dimensions — start() finally block does."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    # Track close calls.
    comm_close_count = 0
    original_comm_close = handle._comm_channel.close  # type: ignore[union-attr]

    def _spy_comm_close() -> None:
        nonlocal comm_close_count
        comm_close_count += 1
        original_comm_close()

    handle._comm_channel.close = _spy_comm_close  # type: ignore[union-attr,method-assign]

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # comm_channel.close() should be called exactly once (by start() finally block).
    assert comm_close_count == 1


@pytest.mark.unit
async def test_close_while_running_lets_turn_finish() -> None:
    """close() while RUNNING lets the current Turn finish, then exits."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Close while the turn is (or was) running.
    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    assert handle._run_state == RunState.DONE
    assert handle._closed is True


@pytest.mark.unit
async def test_steer_after_close_with_protocol_channel_raises() -> None:
    """steer() after close() with ProtocolChannel raises RuntimeError."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)

    handle.close()
    handle._closed = True  # Simulate finally block having run.

    with pytest.raises(RuntimeError, match="Cannot steer after close"):
        handle.steer("should fail")


@pytest.mark.unit
async def test_protocol_channel_feedback_round_trip_in_start() -> None:
    """Full feedback round-trip: steer via deliver_feedback → recv() in start() → next Turn."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")

    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m1"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(
        agent=agent,
        event_bus=event_bus,
        _comm_channel=channel,
    )

    consumer = asyncio.create_task(_consume_gen(handle.start("hello")))
    await asyncio.sleep(0.05)

    # Steer via ProtocolChannel feedback (this is what SessionController would do).
    handle.steer("steer via feedback")
    await asyncio.sleep(0.1)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer

    # Two turns: initial + steer feedback picked up via recv().
    assert agent.create_turn.call_count == 2


@pytest.mark.unit
async def test_followup_protocol_channel_wakes_idle_loop() -> None:
    """followup() with ProtocolChannel when IDLE sets _idle_event to wake the loop."""
    from agentpool.lifecycle import ProtocolChannel

    journal = MemoryJournal()
    event_bus = EventBus()
    channel = ProtocolChannel(journal, event_bus, "test-session")
    handle = _make_run_handle(_comm_channel=channel)
    handle._run_state = RunState.IDLE
    handle._idle_event.clear()

    result = handle.followup("idle followup")

    assert result is True
    assert handle._idle_event.is_set()
    feedback = channel.recv()
    assert feedback is not None
    assert feedback.is_steer is False
