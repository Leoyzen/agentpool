"""Lifecycle tests for the restructured RunHandle.

Covers the new session-level idle/wake/turn loop:
- idle -> wake -> execute -> idle cycle
- steer while idle (queue + wake)
- followup while idle (queue)
- close() during idle
- cancel() during running
- async with protocol
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn implementation for testing.

    Yields a RunStartedEvent-equivalent sequence ending with
    StreamCompleteEvent, then sets message_history.
    """

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
        # Set message history before yielding so it's available
        # even if the consumer breaks on StreamCompleteEvent.
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _make_run_handle(
    *,
    agent: Any | None = None,
    event_bus: Any | None = None,
    session: Any | None = None,
    run_id: str = "test-run",
    session_id: str = "test-session",
    agent_type: str = "native",
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
    )


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_idle_wake_execute_idle_cycle() -> None:
    """Given a RunHandle with one prompt, it executes one turn then goes idle."""
    turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg1"],
    )
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    event_bus = AsyncMock()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()

    handle = _make_run_handle(agent=agent, event_bus=event_bus, session=session)

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # After consuming the single turn, handle should be idle
    assert handle._status == RunStatus.idle

    # Close to unblock the idle wait
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._status == RunStatus.done
    assert len(events) == 1
    assert isinstance(events[0], StreamCompleteEvent)
    assert handle._message_history == ["msg1"]

    # Verify RunStartedEvent was published
    published_events = [call.args[1] for call in event_bus.publish.call_args_list]
    assert any(isinstance(e, RunStartedEvent) for e in published_events)


@pytest.mark.unit
async def test_steer_while_idle_queues_and_wakes() -> None:
    """Given an idle RunHandle, steer() queues the message and sets _idle_event."""
    turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=["msg1"],
    )
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Handle should be idle after first turn
    assert handle._status == RunStatus.idle
    assert not handle._idle_event.is_set()  # cleared when entering idle

    # Steer while idle
    result = handle.steer("steered message")
    assert result is True
    assert "steered message" in handle._message_queue
    assert handle._idle_event.is_set()

    # Let the second turn execute
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._status == RunStatus.done
    # Two turns should have executed
    assert agent.create_turn.call_count == 2


@pytest.mark.unit
async def test_followup_while_idle_queues() -> None:
    """Given an idle RunHandle, followup() queues the message."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("first")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._status == RunStatus.idle

    result = handle.followup("followup message")
    assert result is True
    assert "followup message" in handle._message_queue
    assert handle._idle_event.is_set()

    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    assert agent.create_turn.call_count == 2


@pytest.mark.unit
async def test_close_during_idle_sets_closing_and_wakes() -> None:
    """Given an idle RunHandle, close() sets _closing and wakes _idle_event."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._status == RunStatus.idle
    assert not handle._closing

    handle.close()
    assert handle._closing is True
    assert handle._idle_event.is_set()

    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._status == RunStatus.done


@pytest.mark.unit
async def test_cancel_during_running_sets_cancelled() -> None:
    """Given a running RunHandle, cancel() sets run_ctx.cancelled and wakes idle."""
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("prompt")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Handle is idle after first turn completes
    handle._status = RunStatus.running  # simulate mid-turn

    handle.cancel()
    assert handle.run_ctx.cancelled is True
    assert handle._idle_event.is_set()

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


@pytest.mark.unit
async def test_steer_returns_false_when_closing() -> None:
    """Given a closing RunHandle, steer() returns False."""
    handle = _make_run_handle()
    handle.close()

    result = handle.steer("message")
    assert result is False


@pytest.mark.unit
async def test_followup_returns_false_when_closing() -> None:
    """Given a closing RunHandle, followup() returns False."""
    handle = _make_run_handle()
    handle.close()

    result = handle.followup("message")
    assert result is False


@pytest.mark.unit
async def test_steer_while_running_with_agent_run() -> None:
    """Given a running RunHandle with active_agent_run, steer() enqueues."""
    handle = _make_run_handle()
    handle._status = RunStatus.running
    mock_agent_run = MagicMock()
    handle.active_agent_run = mock_agent_run

    result = handle.steer("inject me")
    assert result is True
    mock_agent_run.enqueue.assert_called_once_with("inject me", priority="asap")


@pytest.mark.unit
async def test_steer_while_running_without_agent_run() -> None:
    """Given a running RunHandle without active_agent_run, steer() queues to run_ctx."""
    handle = _make_run_handle()
    handle._status = RunStatus.running
    handle.active_agent_run = None

    result = handle.steer("queue me")
    assert result is True
    assert "queue me" in handle.run_ctx.queued_steer_messages


@pytest.mark.unit
async def test_async_context_manager_calls_close() -> None:
    """Given `async with RunHandle(...)`, close() is called on exit."""
    handle = _make_run_handle()
    assert handle._closing is False

    async with handle:
        assert handle._closing is False

    assert handle._closing is True


@pytest.mark.unit
async def test_start_publishes_run_error_on_turn_exception() -> None:
    """Given a turn that raises, start() publishes RunErrorEvent."""
    turn = _StubTurn(raise_exc=RuntimeError("turn boom"))
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    event_bus = AsyncMock()
    handle = _make_run_handle(agent=agent, event_bus=event_bus)

    events: list[Any] = []
    gen = handle.start("prompt")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    published = [call.args[1] for call in event_bus.publish.call_args_list]
    assert any(isinstance(e, RunErrorEvent) for e in published)
    error_event = next(e for e in published if isinstance(e, RunErrorEvent))
    assert "turn boom" in error_event.message


@pytest.mark.unit
async def test_followup_while_running_does_not_set_idle_event() -> None:
    """Given a running RunHandle, followup() queues but does not set idle event."""
    handle = _make_run_handle()
    handle._status = RunStatus.running
    handle._idle_event.clear()

    result = handle.followup("queued")
    assert result is True
    assert "queued" in handle._message_queue
    assert not handle._idle_event.is_set()


@pytest.mark.unit
async def test_initial_status_is_idle() -> None:
    """Given a freshly created RunHandle, _status is idle and _idle_event is set."""
    handle = RunHandle(run_id="r", session_id="s", agent_type="native")
    assert handle._status == RunStatus.idle
    assert handle._idle_event.is_set()
    assert handle._closing is False
    assert handle._message_queue == []
    assert handle._message_history == []


@pytest.mark.unit
async def test_cancel_with_cancel_fn_delegates() -> None:
    """Given a RunHandle with _cancel_fn set, cancel() calls the cancel function."""
    handle = _make_run_handle()
    cancel_called = False

    def _cancel_fn() -> None:
        nonlocal cancel_called
        cancel_called = True

    handle._cancel_fn = _cancel_fn
    mock_task = MagicMock()
    mock_task.done.return_value = False
    handle.run_ctx.current_task = mock_task

    handle.cancel()

    assert cancel_called is True
    assert handle.run_ctx.cancelled is True
    assert handle._idle_event.is_set()
    # current_task.cancel() should NOT be called because _cancel_fn took priority
    mock_task.cancel.assert_not_called()


@pytest.mark.unit
async def test_cancel_with_current_task_cancels_task() -> None:
    """Given a RunHandle with current_task set and no _cancel_fn, cancel()
    cancels the task.
    """
    handle = _make_run_handle()
    mock_task = MagicMock()
    mock_task.done.return_value = False
    handle.run_ctx.current_task = mock_task

    handle.cancel()

    assert handle.run_ctx.cancelled is True
    assert handle._idle_event.is_set()
    mock_task.cancel.assert_called_once()


@pytest.mark.unit
async def test_cancel_with_done_task_does_not_cancel() -> None:
    """Given a RunHandle with current_task already done, cancel() does not
    re-cancel it.
    """
    handle = _make_run_handle()
    mock_task = MagicMock()
    mock_task.done.return_value = True
    handle.run_ctx.current_task = mock_task

    handle.cancel()

    assert handle.run_ctx.cancelled is True
    assert handle._idle_event.is_set()
    mock_task.cancel.assert_not_called()


@pytest.mark.unit
async def test_start_raises_when_agent_none() -> None:
    """Given a RunHandle with agent=None, start() raises RuntimeError."""
    handle = _make_run_handle()
    handle.agent = None

    with pytest.raises(RuntimeError, match="agent must be set"):
        # start() is an async generator; need to step into it
        gen = handle.start("hello")
        await gen.__anext__()


@pytest.mark.unit
async def test_start_raises_when_event_bus_none() -> None:
    """Given a RunHandle with event_bus=None, start() raises RuntimeError."""
    handle = _make_run_handle()
    handle.event_bus = None

    with pytest.raises(RuntimeError, match="event_bus must be set"):
        gen = handle.start("hello")
        await gen.__anext__()


@pytest.mark.unit
async def test_start_raises_when_session_none() -> None:
    """Given a RunHandle with session=None, start() raises RuntimeError."""
    handle = _make_run_handle()
    handle.session = None

    with pytest.raises(RuntimeError, match="session must be set"):
        gen = handle.start("hello")
        await gen.__anext__()


@pytest.mark.unit
async def test_multiple_followups_queued_all_become_next_turn_prompts() -> None:
    """Given multiple followup() calls while idle, all messages become
    prompts for the next turn.
    """
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async def _consume() -> None:
        async for event in gen:
            events.append(event)

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._status == RunStatus.idle

    # Queue two followups
    assert handle.followup("first followup") is True
    assert handle.followup("second followup") is True

    # Both should be in the queue
    assert "first followup" in handle._message_queue
    assert "second followup" in handle._message_queue

    # Let the second turn execute with both prompts
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # Two turns total: initial + combined followups
    assert agent.create_turn.call_count == 2

    # Second turn should have received both followup messages as prompts
    second_call = agent.create_turn.call_args_list[1]
    prompts = second_call.kwargs["prompts"]
    assert "first followup" in prompts
    assert "second followup" in prompts


@pytest.mark.unit
async def test_close_is_idempotent() -> None:
    """Given close() called twice, the second call does not crash and
    _closing remains True.
    """
    handle = _make_run_handle()

    handle.close()
    assert handle._closing is True
    assert handle._idle_event.is_set()

    # Second close should not raise
    handle.close()
    assert handle._closing is True


@pytest.mark.unit
async def test_steer_returns_false_when_done_status() -> None:
    """Given a RunHandle with _status=done (post-close), steer() returns False."""
    handle = _make_run_handle()
    handle._status = RunStatus.done
    handle._closing = True

    result = handle.steer("message")
    assert result is False


@pytest.mark.unit
async def test_followup_returns_false_when_done_status() -> None:
    """Given a RunHandle with _status=done (post-close), followup() returns False."""
    handle = _make_run_handle()
    handle._status = RunStatus.done
    handle._closing = True

    result = handle.followup("message")
    assert result is False


@pytest.mark.unit
async def test_cancelled_property_reflects_run_ctx() -> None:
    """Given run_ctx.cancelled is set, the cancelled property reflects it."""
    handle = _make_run_handle()
    assert handle.cancelled is False

    handle.run_ctx.cancelled = True
    assert handle.cancelled is True
