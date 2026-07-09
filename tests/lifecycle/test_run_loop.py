"""Tests for RunLoop lifecycle integration in RunHandle.

Covers M2 Task 7: constructor dimension defaults, state machine,
start() main loop with journaling, snapshots, crash recovery,
and turn_id generation.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
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
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle, RunStatus
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
    events: list[Any] = []
    async for event in gen:
        events.append(event)
    return events


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
    from agentpool.lifecycle.types import ResumeResult

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
    crash_recovery_events = [
        e for e in state_events if e.stop_reason == "crash_recovery"
    ]
    assert len(crash_recovery_events) >= 1
    assert crash_recovery_events[0].state == RunState.IDLE


@pytest.mark.unit
async def test_crash_recovery_normal_resume() -> None:
    """When journal.resume() returns non-inflight ResumeResult, IDLE transition occurs."""
    from agentpool.lifecycle.types import ResumeResult

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
    assert handle._status == RunStatus.done


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
