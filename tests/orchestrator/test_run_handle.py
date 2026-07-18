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
import contextlib
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, AgentsManifest
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.delegation import AgentPool
from agentpool.lifecycle import ProtocolChannel, RunOutcome, RunState
from agentpool.lifecycle.comm_channel import DirectChannel
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionController, SessionState
from agentpool.orchestrator.run import RunHandle
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


class _BlockingTurn(Turn):
    """Turn that blocks until run_ctx.cancelled, then returns without StreamCompleteEvent."""

    def __init__(self, run_ctx: AgentRunContext) -> None:
        self._run_ctx = run_ctx

    async def execute(self):  # type: ignore[override]
        self._message_history = []
        self._final_message = ChatMessage(content="blocked", role="assistant")
        while not self._run_ctx.cancelled:
            await asyncio.sleep(0.01)
        return
        yield  # makes this an async generator


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


async def _consume_gen(gen: Any) -> None:
    """Consume an async generator to completion, discarding all events."""
    async for _ in gen:
        pass


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
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # After consuming the single turn, handle should be idle
    assert handle._run_state == RunState.IDLE

    # Close to unblock the idle wait
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._run_state == RunState.DONE
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
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    # Handle should be idle after first turn
    assert handle._run_state == RunState.IDLE
    assert not handle._idle_event.is_set()  # cleared when entering idle

    # Steer while idle
    result = handle.steer("steered message")
    assert result is not None
    assert "steered message" in handle._message_queue
    assert handle._idle_event.is_set()

    # Let the second turn execute
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._run_state == RunState.DONE
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
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._run_state == RunState.IDLE

    result = handle.followup("followup message")
    assert result is not None
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
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._run_state == RunState.IDLE
    assert not handle._closing

    handle.close()
    assert handle._closing is True
    assert handle._idle_event.is_set()

    await asyncio.sleep(0.05)
    await consumer_task

    assert handle._run_state == RunState.DONE


@pytest.mark.unit
async def test_steer_returns_false_when_closing() -> None:
    """Given a closing RunHandle, steer() returns False."""
    handle = _make_run_handle()
    handle.close()

    result = handle.steer("message")
    assert result is None


@pytest.mark.unit
async def test_followup_returns_false_when_closing() -> None:
    """Given a closing RunHandle, followup() returns False."""
    handle = _make_run_handle()
    handle.close()

    result = handle.followup("message")
    assert result is None


@pytest.mark.unit
async def test_steer_while_running_with_agent_run() -> None:
    """Given a running RunHandle with active_agent_run, steer() enqueues."""
    handle = _make_run_handle()
    handle._run_state = RunState.RUNNING
    mock_agent_run = MagicMock()
    handle.active_agent_run = mock_agent_run

    result = handle.steer("inject me")
    assert result is not None
    mock_agent_run.enqueue.assert_called_once_with("inject me", priority="asap")


@pytest.mark.unit
async def test_steer_while_running_without_agent_run() -> None:
    """Given a running RunHandle without active_agent_run, steer() queues to run_ctx."""
    handle = _make_run_handle()
    handle._run_state = RunState.RUNNING
    handle.active_agent_run = None

    result = handle.steer("queue me")
    assert result is not None
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
        events.extend([event async for event in gen])

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
    handle._run_state = RunState.RUNNING
    handle._idle_event.clear()

    result = handle.followup("queued")
    assert result is not None
    assert "queued" in handle._message_queue
    assert not handle._idle_event.is_set()


@pytest.mark.unit
async def test_initial_status_is_idle() -> None:
    """Given a freshly created RunHandle, _status is idle and _idle_event is set."""
    handle = RunHandle(run_id="r", session_id="s", agent_type="native")
    assert handle._run_state == RunState.IDLE
    assert handle._idle_event.is_set()
    assert handle._closing is False
    assert handle._message_queue == []
    assert handle._message_history == []


@pytest.mark.unit
async def test_cancel_with_cancel_fn_delegates() -> None:
    """Given a RunHandle with _cancel_fn set, cancel() calls the cancel function.

    Also force-cancels current_task to break through __aexit__ hangs.
    """
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
    # current_task.cancel() IS called to break through __aexit__ hangs
    mock_task.cancel.assert_called_once()


@pytest.mark.unit
async def test_cancel_does_not_cancel_current_task() -> None:
    """Given a RunHandle with current_task set and no _cancel_fn, cancel().

    Force-cancels current_task to break through __aexit__ hangs. The
    CancelledError is caught by start()'s except handler which preserves
    message history and continues to idle.
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
    """Given a RunHandle with current_task already done, cancel() does not.

    cancel it (cancel() never cancels current_task regardless of state).
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

    # start() is an async generator; need to step into it
    gen = handle.start("hello")
    with pytest.raises(RuntimeError, match="agent must be set"):
        await gen.__anext__()


@pytest.mark.unit
async def test_start_raises_when_event_bus_none() -> None:
    """Given a RunHandle with event_bus=None, start() raises RuntimeError."""
    handle = _make_run_handle()
    handle.event_bus = None

    gen = handle.start("hello")
    with pytest.raises(RuntimeError, match="event_bus must be set"):
        await gen.__anext__()


@pytest.mark.unit
async def test_start_raises_when_session_none() -> None:
    """Given a RunHandle with session=None, start() raises RuntimeError."""
    handle = _make_run_handle()
    handle.session = None

    gen = handle.start("hello")
    with pytest.raises(RuntimeError, match="session must be set"):
        await gen.__anext__()


@pytest.mark.unit
async def test_multiple_followups_queued_all_become_next_turn_prompts() -> None:
    """Given multiple followup() calls while idle, all messages become.

    prompts for the next turn.
    """
    turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    handle = _make_run_handle(agent=agent)

    events: list[Any] = []
    gen = handle.start("initial")

    async def _consume() -> None:
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    assert handle._run_state == RunState.IDLE

    # Queue two followups
    assert handle.followup("first followup") is not None
    assert handle.followup("second followup") is not None

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
    """Given close() called twice, the second call does not crash and.

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
    handle._run_state = RunState.DONE
    handle._closing = True

    result = handle.steer("message")
    assert result is None


@pytest.mark.unit
async def test_followup_returns_false_when_done_status() -> None:
    """Given a RunHandle with _status=done (post-close), followup() returns False."""
    handle = _make_run_handle()
    handle._run_state = RunState.DONE
    handle._closing = True

    result = handle.followup("message")
    assert result is None


@pytest.mark.unit
async def test_cancelled_property_reflects_turn_cancel_state() -> None:
    """Cancelled property returns _turn_was_cancelled, not live run_ctx.cancelled.

    The property captures the cancelled state at the moment _turn_complete_event
    is set, so handle_prompt() can observe it even after the loop resets
    run_ctx.cancelled for the next turn.
    """
    handle = _make_run_handle()
    assert handle.cancelled is False

    handle._turn_was_cancelled = True
    assert handle.cancelled is True


# ---------------------------------------------------------------------------
# Tests from PR #64 review (RunHandle lifecycle fixes)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_event_set_after_start_completes() -> None:
    """RunHandle.start() must set complete_event when it finishes.

    Without this, close_session() hangs for 30s waiting for
    complete_event.wait() when closing sessions started via
    process_prompt or run_stream.
    """
    agent = Agent(
        name="test-complete-event",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ce-session",
            agent_name="test-complete-event",
        )
        run_ctx = AgentRunContext(
            session_id="test-ce-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-ce-run",
            session_id="test-ce-session",
            agent_type="test-complete-event",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Drive start() — close after first turn to terminate the loop
        gen = run_handle.start("hello")
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent):
                    run_handle.close()
                    break
        finally:
            # Ensure generator is properly closed so finally block runs
            await gen.aclose()

        # complete_event must be set
        assert run_handle.complete_event.is_set(), (
            "complete_event was not set after start() completed — close_session() will hang for 30s"
        )


@pytest.mark.asyncio
async def test_complete_event_set_when_start_cancelled() -> None:
    """complete_event must be set even if start() is cancelled."""
    agent = Agent(
        name="test-ce-cancel",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ce-cancel-session",
            agent_name="test-ce-cancel",
        )
        run_ctx = AgentRunContext(
            session_id="test-ce-cancel-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-ce-cancel-run",
            session_id="test-ce-cancel-session",
            agent_type="test-ce-cancel",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        gen = run_handle.start("hello")
        task = asyncio.create_task(gen.__anext__())
        await asyncio.sleep(0.1)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        with contextlib.suppress(Exception, GeneratorExit):
            await gen.aclose()

        # Even on cancel, complete_event should be set
        assert run_handle.complete_event.is_set(), (
            "complete_event was not set after start() was cancelled"
        )


@pytest.mark.asyncio
async def test_run_error_event_yielded_to_consumer() -> None:
    """RunHandle.start() must yield RunErrorEvent when turn.execute() raises.

    Without yielding, create_run_stream and other direct consumers
    hang indefinitely waiting for an event that never arrives.
    """
    agent = Agent(
        name="test-error-yield",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-err-session",
            agent_name="test-error-yield",
        )
        run_ctx = AgentRunContext(
            session_id="test-err-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-err-run",
            session_id="test-err-session",
            agent_type="test-error-yield",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Patch agent.create_turn to return a turn that raises
        class FailingTurn:
            async def execute(self) -> Any:
                raise RuntimeError("turn failed")
                yield  # make it an async generator

        agent.create_turn = MagicMock(return_value=FailingTurn())  # type: ignore[method-assign]

        events: list[Any] = []
        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, RunErrorEvent):
                        run_handle.close()
                        break
        except TimeoutError:
            pytest.fail(
                "start() hung waiting for RunErrorEvent — it was published "
                "to EventBus but never yielded to the consumer"
            )
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        # RunErrorEvent must have been yielded
        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected 1 RunErrorEvent, got {len(error_events)}. "
            f"Events: {[type(e).__name__ for e in events]}"
        )
        assert "turn failed" in error_events[0].message


@pytest.mark.asyncio
async def test_input_provider_contextvar_set_during_turn() -> None:
    """RunHandle.start() must set _current_input_provider ContextVar.

    MCP elicitation depends on this ContextVar. Without it,
    _current_input_provider.get() returns None during turn execution.
    """
    from agentpool.mcp_server.manager import _current_input_provider

    captured_provider: list[Any] = []

    def capture_tool() -> str:
        """Tool that captures the current input provider."""
        captured_provider.append(_current_input_provider.get())
        return "captured"

    agent = Agent(
        name="test-ctxvar",
        model=TestModel(call_tools=["capture_tool"], custom_output_text="ok"),
        tools=[capture_tool],
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-ctxvar-session",
            agent_name="test-ctxvar",
        )
        run_ctx = AgentRunContext(
            session_id="test-ctxvar-session",
            event_bus=event_bus,
        )

        mock_provider = MagicMock()
        session.input_provider = mock_provider

        run_handle = RunHandle(
            run_id="test-ctxvar-run",
            session_id="test-ctxvar-session",
            agent_type="test-ctxvar",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        gen = run_handle.start("test")
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent):
                    run_handle.close()
                    break
        finally:
            await gen.aclose()

        # The tool should have captured the input provider
        assert len(captured_provider) > 0, "Tool was never called"
        assert captured_provider[0] is mock_provider, (
            f"ContextVar was not set — got {captured_provider[0]!r}, expected {mock_provider!r}"
        )

        # Note: We intentionally do NOT reset _current_input_provider.
        # start() runs inside an asyncio.Task which copies the parent
        # Context, so set() only affects this task's private context copy.
        # When the task ends the context is discarded. Calling reset()
        # is unnecessary and can raise ValueError when the async generator
        # is GC-collected in a different Context (race between task
        # cancellation and generator suspension at a yield point).


@pytest.mark.asyncio
async def test_turn_failure_breaks_loop_not_continue_to_idle() -> None:
    """When turn.execute() raises, start() must break, not continue to idle.

    Without the break, the loop continues: current_prompts becomes empty
    → idle → _idle_event.wait() → deadlock for legacy clients that wait
    on complete_event (which is only set after start() returns).
    """
    agent = Agent(
        name="test-turn-fail-break",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-fail-break-session",
            agent_name="test-turn-fail-break",
        )
        run_ctx = AgentRunContext(
            session_id="test-fail-break-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-fail-break-run",
            session_id="test-fail-break-session",
            agent_type="test-turn-fail-break",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        class FailingTurn:
            async def execute(self) -> Any:
                raise RuntimeError("turn failed")
                yield

        agent.create_turn = MagicMock(return_value=FailingTurn())  # type: ignore[method-assign]

        events: list[Any] = []
        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, RunErrorEvent):
                        break
        except TimeoutError:
            pytest.fail(
                "start() hung after turn failure — loop continued to idle instead of breaking"
            )
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1

        # complete_event must be set (loop exited, not stuck in idle)
        assert run_handle.complete_event.is_set(), (
            "complete_event not set — loop is stuck in idle after turn failure"
        )


@pytest.mark.asyncio
async def test_run_error_event_sets_turn_failed_and_breaks_loop() -> None:
    """When turn.execute() yields RunErrorEvent, turn_failed must be True.

    Without setting turn_failed, the loop breaks from the inner async-for
    but then continues to the idle branch instead of breaking the outer
    while-loop. This causes a deadlock for clients waiting on complete_event.
    """
    agent = Agent(
        name="test-runevent-break",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-runevent-session",
            agent_name="test-runevent-break",
        )
        run_ctx = AgentRunContext(
            session_id="test-runevent-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-runevent-run",
            session_id="test-runevent-session",
            agent_type="test-runevent-break",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        class ErrorTurn:
            async def execute(self) -> Any:
                yield RunErrorEvent(
                    message="simulated error",
                    run_id="test-runevent-run",
                    agent_name="test-runevent-break",
                )

        agent.create_turn = MagicMock(return_value=ErrorTurn())  # type: ignore[method-assign]

        events: list[Any] = []
        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, RunErrorEvent):
                        break
        except TimeoutError:
            pytest.fail(
                "start() hung after RunErrorEvent — loop continued to idle "
                "instead of breaking because turn_failed was not set"
            )
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1

        # complete_event must be set (loop exited, not stuck in idle)
        assert run_handle.complete_event.is_set(), (
            "complete_event not set — loop is stuck in idle after RunErrorEvent"
        )


def test_start_sets_current_task() -> None:
    """RunHandle.start() must set run_ctx.current_task.

    Without this, cancel() in _interrupt() gets None for current_task
    and cannot interrupt the running turn.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    assert "current_task" in source, (
        "run_ctx.current_task must be set in start() so cancel() can interrupt the running turn"
    )
    assert "asyncio.current_task()" in source, "current_task must be set to asyncio.current_task()"


@pytest.mark.asyncio
async def test_current_task_set_during_start_execution() -> None:
    """Verify run_ctx.current_task is populated during start() execution."""
    agent = Agent(
        name="test-current-task",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-current-task-session",
            agent_name="test-current-task",
        )
        run_ctx = AgentRunContext(
            session_id="test-current-task-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-current-task-run",
            session_id="test-current-task-session",
            agent_type="test-current-task",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        captured_tasks: list[Any] = []

        class CapturingTurn:
            async def execute(self) -> Any:
                # Capture current_task from run_ctx during turn execution
                captured_tasks.append(run_ctx.current_task)
                yield StreamCompleteEvent(message=MagicMock())

        agent.create_turn = MagicMock(return_value=CapturingTurn())  # type: ignore[method-assign]

        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    if isinstance(event, StreamCompleteEvent):
                        break
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        assert len(captured_tasks) == 1
        assert captured_tasks[0] is not None, (
            "run_ctx.current_task was not set during start() execution"
        )
        assert captured_tasks[0] is asyncio.current_task(), (
            "run_ctx.current_task should be the current asyncio task"
        )


# ---------------------------------------------------------------------------
# Task 8: Cancel returns to idle + cancel during LLM call
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_cancel_returns_to_idle() -> None:
    """After cancel, RunHandle returns to idle (not done) and _turn_complete_event is set."""
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    stub_turn = _StubTurn(events=[_stream_complete_event()], message_history=["m"])
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    handle.cancel()
    await asyncio.sleep(0.1)

    assert handle._run_state == RunState.IDLE
    assert handle._turn_complete_event.is_set()

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


@pytest.mark.unit
async def test_cancel_during_llm_call() -> None:
    """Cancel during LLM call: no StreamCompleteEvent, RunFailedEvent published, returns to idle."""
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    # Second turn has empty events — no StreamCompleteEvent.
    # This proves the cancelled turn ended via RunFailedEvent, not StreamCompleteEvent.
    stub_turn = _StubTurn(events=[], message_history=["m"])
    handle.agent.create_turn = MagicMock(side_effect=[blocking_turn, stub_turn])

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        events.extend([event async for event in gen])

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.cancel()
    await asyncio.sleep(0.1)

    # No StreamCompleteEvent from cancelled turn (or subsequent turn)
    assert not any(isinstance(e, StreamCompleteEvent) for e in events)

    # RunFailedEvent was published
    published = [call.args[1] for call in handle.event_bus.publish.call_args_list]
    assert any(isinstance(e, RunFailedEvent) for e in published)

    # Returns to idle
    assert handle._run_state == RunState.IDLE

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


# ---------------------------------------------------------------------------
# Task 10: No double turn_complete on cancel
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_no_double_turn_complete_on_cancel() -> None:
    """Cancel publishes RunFailedEvent but NOT StreamCompleteEvent."""
    handle = _make_run_handle()
    blocking_turn = _BlockingTurn(handle.run_ctx)
    handle.agent.create_turn = MagicMock(return_value=blocking_turn)

    gen = handle.start("hello")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    handle.cancel()
    handle.close()  # Prevent second turn from starting
    await asyncio.sleep(0.1)
    await consumer_task

    published = [call.args[1] for call in handle.event_bus.publish.call_args_list]
    assert any(isinstance(e, RunFailedEvent) for e in published)
    assert not any(isinstance(e, StreamCompleteEvent) for e in published)


# ---------------------------------------------------------------------------
# Task 11: _turn_complete_event reset between turns
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_turn_complete_event_reset_between_turns() -> None:
    """_turn_complete_event is cleared at turn start and set at turn end across turns."""

    class _CapturingTurn(Turn):
        """Turn that captures _turn_complete_event state at execute() start."""

        def __init__(self, tce: asyncio.Event) -> None:
            self._tce = tce
            self.captured_start: bool | None = None

        async def execute(self):  # type: ignore[override]
            self.captured_start = self._tce.is_set()
            self._message_history = ["m"]
            self._final_message = ChatMessage(content="done", role="assistant")
            yield _stream_complete_event()

    handle = _make_run_handle()
    turn1 = _CapturingTurn(handle._turn_complete_event)
    turn2 = _CapturingTurn(handle._turn_complete_event)
    handle.agent.create_turn = MagicMock(side_effect=[turn1, turn2])

    gen = handle.start("first")
    consumer_task = asyncio.create_task(_consume_gen(gen))
    await asyncio.sleep(0.05)

    # After first turn: idle, event set, was cleared at start
    assert handle._run_state == RunState.IDLE
    assert handle._turn_complete_event.is_set()
    assert turn1.captured_start is False

    # Steer to wake for second turn
    handle.steer("second")
    await asyncio.sleep(0.1)

    # After second turn: idle, event set, was cleared at start (was set between turns)
    assert handle._run_state == RunState.IDLE
    assert handle._turn_complete_event.is_set()
    assert turn2.captured_start is False

    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task


# ---------------------------------------------------------------------------
# Regression: ContextVar cross-context ValueError on generator GC
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_value_error_when_generator_abandoned_in_different_context() -> None:
    """No ValueError when async generator is GC'd in a different Context.

    Regression test for the bug where ``_current_input_provider.reset(token)``
    in the ``finally`` block of ``start()`` raised ``ValueError`` when the
    async generator was GC-collected in a different asyncio Context.

    The race occurs when:
    1. ``start()`` runs inside an ``asyncio.create_task()`` (Path A via
       ``_consume_run``), which copies the parent Context.
    2. ``set()`` creates a token bound to the task's Context copy.
    3. The task is cancelled between ``__anext__()`` calls, leaving the
       generator suspended at a ``yield`` point.
    4. GC later runs ``athrow(GeneratorExit)`` in a fresh Context.
    5. ``finally`` calls ``reset(token)`` → ``ValueError`` because the
       token was created in a different Context.

    Fix: remove ``reset()`` entirely. ``set()`` only affects the task's
    private Context copy, which is discarded when the task ends.
    """
    agent = Agent(
        name="test-gc-ctxvar",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-gc-session",
            agent_name="test-gc-ctxvar",
        )
        session.input_provider = MagicMock()

        run_handle = RunHandle(
            run_id="test-gc-run",
            session_id="test-gc-session",
            agent_type="test-gc-ctxvar",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=AgentRunContext(
                session_id="test-gc-session",
                event_bus=event_bus,
            ),
        )

        # Capture unhandled exceptions from GC-driven generator cleanup
        gc_exceptions: list[BaseException] = []
        loop = asyncio.get_event_loop()
        original_handler = loop.get_exception_handler()

        def _exception_handler(loop: Any, context: Any) -> None:
            exc = context.get("exception")
            if exc and "_current_input_provider" in str(exc):
                gc_exceptions.append(exc)

        loop.set_exception_handler(_exception_handler)

        try:
            gen = run_handle.start("test")
            # Step into the generator so set() is called and it suspends
            # at the first yield (an event from turn.execute()).
            task = asyncio.create_task(gen.__anext__())
            await asyncio.sleep(0.1)

            # Cancel the task — generator is left suspended at yield.
            # This simulates the race: task cancelled between __anext__()
            # calls, generator abandoned.
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            # Do NOT call aclose() — let GC collect the generator.
            # Before the fix, this would trigger athrow(GeneratorExit)
            # in a fresh Context, causing reset(token) to raise
            # ValueError.
            del gen
            import gc

            gc.collect()
            # Yield to event loop so any GC callbacks can fire
            await asyncio.sleep(0.05)
        finally:
            loop.set_exception_handler(original_handler)

        assert not gc_exceptions, (
            f"ValueError(s) raised during generator GC cleanup: {[str(e) for e in gc_exceptions]}"
        )


# ---------------------------------------------------------------------------
# Merged from test_run_handle_message_id.py
# ---------------------------------------------------------------------------


class _StubTurnV2(Turn):
    """Minimal Turn implementation for testing (from message_id tests)."""

    def __init__(self) -> None:
        self._message_history: list[Any] = []
        self._final_message = ChatMessage(content="done", role="assistant")

    async def execute(self):
        yield


def _make_handle(
    *,
    comm_channel: Any | None = None,
    run_state: Any = None,
    agent: Any | None = None,
    session: Any | None = None,
    event_bus: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
    if agent is None:
        agent = MagicMock()
        agent.create_turn = MagicMock(return_value=_StubTurnV2())
        agent.name = "test-agent"
        agent.conversation = MagicMock()
    if event_bus is None:
        event_bus = AsyncMock()
    if session is None:
        session = MagicMock()
        session.turn_lock = asyncio.Lock()
        session.parent_session_id = None
    run_ctx = AgentRunContext(session_id="test-session")
    handle = RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx,
    )
    if comm_channel is not None:
        handle._comm_channel = comm_channel
    if run_state is not None:
        handle._run_state = run_state
    return handle


def _make_protocol_channel() -> ProtocolChannel:
    """Create a ProtocolChannel with real EventBus."""
    journal = MemoryJournal()
    event_bus = EventBus()
    return ProtocolChannel(journal=journal, event_bus=event_bus, session_id="test-session")


@pytest.mark.unit
async def test_steer_with_explicit_message_id() -> None:
    """steer() with explicit message_id returns that ID."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.steer("hello", message_id="custom-msg-001")
    assert result == "custom-msg-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "custom-msg-001"
    assert fb.content == "hello"
    assert fb.content_blocks is None
    assert fb.is_steer is True


@pytest.mark.unit
async def test_steer_auto_generates_message_id() -> None:
    """steer() without message_id auto-generates a UUID string."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.steer("hello")
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0
    assert len(result) == 36


@pytest.mark.unit
async def test_steer_with_list_content_blocks() -> None:
    """steer() with list message stores in content_blocks, content=''."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    blocks: list[Any] = ["text part", {"type": "image", "url": "http://example.com/img.png"}]
    result = handle.steer(blocks, message_id="list-msg-001")
    assert result == "list-msg-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "list-msg-001"
    assert fb.content == ""
    assert fb.content_blocks == blocks
    assert fb.is_steer is True


@pytest.mark.unit
async def test_steer_returns_none_when_closing() -> None:
    """steer() returns None when handle is closing."""
    handle = _make_handle()
    handle._closing = True
    result = handle.steer("message")
    assert result is None


@pytest.mark.unit
async def test_steer_raises_after_close() -> None:
    """steer() raises RuntimeError after close()."""
    handle = _make_handle()
    handle._closed = True
    with pytest.raises(RuntimeError, match="Cannot steer after close"):
        handle.steer("message")


@pytest.mark.unit
async def test_followup_with_explicit_message_id() -> None:
    """followup() with explicit message_id returns that ID."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.followup("next prompt", message_id="followup-001")
    assert result == "followup-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "followup-001"
    assert fb.content == "next prompt"
    assert fb.is_steer is False


@pytest.mark.unit
async def test_followup_with_list_content_blocks() -> None:
    """followup() with list message stores in content_blocks."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    blocks: list[Any] = ["text", {"type": "image"}]
    result = handle.followup(blocks, message_id="followup-list-001")
    assert result == "followup-list-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.content == ""
    assert fb.content_blocks == blocks
    assert fb.is_steer is False


@pytest.mark.unit
async def test_followup_directchannel_preserves_message_id() -> None:
    """followup() with DirectChannel constructs Feedback and returns message_id.

    D17 BLOCKER 2 fix: Feedback is constructed BEFORE deliver_feedback(),
    so message_id is preserved even when DirectChannel returns False.
    """
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    result = handle.followup("standalone prompt", message_id="direct-001")
    assert result == "direct-001"
    assert handle._message_queue == ["standalone prompt"]


@pytest.mark.unit
async def test_followup_directchannel_content_blocks_in_queue() -> None:
    """followup() with DirectChannel and list content appends content_blocks to queue."""
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    blocks: list[Any] = ["text", {"type": "image"}]
    result = handle.followup(blocks, message_id="direct-list-001")
    assert result == "direct-list-001"
    assert handle._message_queue == [blocks]


@pytest.mark.unit
async def test_followup_returns_none_when_closing() -> None:
    """followup() returns None when handle is closing."""
    handle = _make_handle()
    handle._closing = True
    result = handle.followup("message")
    assert result is None


@pytest.mark.unit
async def test_revoke_delegates_to_protocol_channel() -> None:
    """revoke() delegates to ProtocolChannel.revoke()."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    msg_id = handle.steer("hello", message_id="revoke-test-001")
    assert msg_id == "revoke-test-001"
    result = handle.revoke("revoke-test-001")
    assert result is True
    assert "revoke-test-001" in channel._revoked


@pytest.mark.unit
async def test_revoke_returns_false_for_direct_channel() -> None:
    """revoke() returns False when CommChannel is DirectChannel."""
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    result = handle.revoke("some-id")
    assert result is False


@pytest.mark.unit
async def test_revoke_returns_false_when_no_comm_channel() -> None:
    """revoke() returns False when comm_channel is None."""
    handle = _make_handle()
    handle._comm_channel = None
    result = handle.revoke("some-id")
    assert result is False


@pytest.mark.unit
async def test_revoke_after_delivery_returns_false() -> None:
    """revoke() returns False for already-delivered messages."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    msg_id = handle.steer("hello", message_id="delivered-001")
    assert msg_id == "delivered-001"
    fb = channel.recv()
    assert fb is not None
    result = handle.revoke("delivered-001")
    assert result is False


@pytest.mark.unit
async def test_revoke_unknown_returns_true() -> None:
    """revoke() returns True for unknown message_id (idempotent)."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.revoke("unknown-id")
    assert result is True


@pytest.mark.unit
async def test_start_empty_prompt_produces_empty_list() -> None:
    """start('') produces current_prompts=[] not [''], triggering _idle_loop().

    This is the CRITICAL D17 fix: without this, [""] is a non-empty
    list that bypasses _idle_loop() and executes a spurious empty-prompt turn.
    """
    handle = _make_handle()
    handle.followup("queued prompt", message_id="d17-001")
    gen = handle.start("")
    events: list[Any] = []
    try:
        async with asyncio.timeout(5):
            async for event in gen:
                events.append(event)
                if len(events) > 10:
                    break
    except (TimeoutError, asyncio.CancelledError):
        pass
    handle.close()
    agent = handle.agent
    assert agent is not None
    agent.create_turn.assert_called_once()
    call_kwargs = agent.create_turn.call_args
    prompts = call_kwargs.kwargs.get("prompts", call_kwargs.args[0] if call_kwargs.args else [])
    assert prompts != [""]
    assert "queued prompt" in prompts


@pytest.mark.unit
async def test_steer_callback_wrapper_returns_message_id() -> None:
    """_steer_callback_wrapper returns str|None (message_id), not bool."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = await handle._steer_callback_wrapper("session-id", "steer me")
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


@pytest.mark.unit
def test_active_agent_run_property_matches_field() -> None:
    """_active_agent_run property returns same value as active_agent_run field."""
    handle = _make_handle()
    assert handle._active_agent_run is None
    assert handle._active_agent_run is handle.active_agent_run
    mock_run = MagicMock()
    handle.active_agent_run = mock_run
    assert handle._active_agent_run is mock_run


# ---------------------------------------------------------------------------
# Merged from test_runhandle_checkpoint.py
# ---------------------------------------------------------------------------


def test_checkpointed_status_exists() -> None:
    """RunOutcome.CHECKPOINTED must be a member of the enum."""
    assert RunOutcome.CHECKPOINTED is not None
    assert isinstance(RunOutcome.CHECKPOINTED, RunOutcome)


def test_checkpointed_is_distinct() -> None:
    """RunOutcome.CHECKPOINTED must differ from existing states."""
    assert RunOutcome.CHECKPOINTED != RunState.IDLE
    assert RunOutcome.CHECKPOINTED != RunState.RUNNING
    assert RunOutcome.CHECKPOINTED != RunOutcome.COMPLETED
    assert RunOutcome.CHECKPOINTED != RunOutcome.FAILED


def test_checkpoint_method_exists() -> None:
    """RunHandle must have a checkpoint() method."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    assert callable(handle.checkpoint)


def test_checkpoint_transitions_from_running() -> None:
    """checkpoint() transitions from running to checkpointed."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    assert handle.is_running
    handle.checkpoint()
    assert handle._run_state == RunState.DONE
    assert handle.outcome == RunOutcome.CHECKPOINTED


def test_checkpoint_sets_complete_event() -> None:
    """checkpoint() must set complete_event."""
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    assert not handle.complete_event.is_set()
    handle.checkpoint()
    assert handle.complete_event.is_set()


def test_checkpoint_invokes_cleanup_callback() -> None:
    """checkpoint() calls _cleanup_callback before setting complete_event."""
    cleanup_calls: list[str] = []

    def cleanup(run_id: str) -> None:
        cleanup_calls.append(run_id)
        assert not handle.complete_event.is_set()

    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native", _cleanup_callback=cleanup)
    handle._start_task()
    handle.checkpoint()
    assert cleanup_calls == ["r1"]
    assert handle.complete_event.is_set()


def test_checkpoint_does_not_emit_run_failed_event() -> None:
    """checkpoint() must NOT emit RunFailedEvent.

    Unlike fail(), checkpoint() is a normal lifecycle transition and
    should not publish a failure event to the event bus.
    """
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    MagicMock()
    handle.checkpoint()
    assert handle._run_state == RunState.DONE
    assert handle.outcome == RunOutcome.CHECKPOINTED
    assert handle.complete_event.is_set()


def test_checkpoint_rejects_event_bus_parameter() -> None:
    """checkpoint() signature must NOT accept an event_bus parameter.

    This is a deliberate design choice: checkpointing is a normal
    lifecycle transition, unlike fail() which emits RunFailedEvent.
    """
    import inspect

    sig = inspect.signature(RunHandle.checkpoint)
    assert "event_bus" not in sig.parameters


def test_resume_creates_fresh_run_handle() -> None:
    """A resumed session must start with a new RunHandle in running status.

    This test verifies the contract: when checkpoints are restored,
    a fresh RunHandle is created rather than reusing the checkpointed one.
    """
    old_handle = RunHandle(run_id="old-run", session_id="s1", agent_type="native")
    old_handle._start_task()
    old_handle.checkpoint()
    assert old_handle._run_state == RunState.DONE
    assert old_handle.outcome == RunOutcome.CHECKPOINTED
    new_handle = RunHandle(run_id="new-run", session_id="s1", agent_type="native")
    new_handle._start_task()
    assert new_handle.is_running
    assert new_handle.run_id != old_handle.run_id


@pytest.mark.anyio
async def test_session_controller_skips_fail_on_checkpointed() -> None:
    """SessionController must skip fail() when RunHandle is checkpointed.

    This tests the guard in ``_run_turn_unlocked`` that checks
    ``run_handle.outcome not in (RunOutcome.COMPLETED, RunOutcome.FAILED,
    RunOutcome.CHECKPOINTED)`` before calling ``run_handle.fail()``.
    """
    manifest = AgentsManifest()
    pool = MagicMock(spec=AgentPool)
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main"
    pool.manifest = manifest
    SessionController(pool)
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    handle.checkpoint()
    assert handle._run_state == RunState.DONE
    assert handle.outcome == RunOutcome.CHECKPOINTED
    should_skip = handle.outcome in (RunOutcome.COMPLETED, RunOutcome.FAILED)
    should_fail = handle.outcome not in (
        RunOutcome.COMPLETED,
        RunOutcome.FAILED,
        RunOutcome.CHECKPOINTED,
    )
    assert not should_fail, "checkpointed runs must not transition to failed in except"
    assert not should_skip, "checkpointed status should be excluded from fail path"


@pytest.mark.anyio
async def test_run_loop_finally_skips_complete_on_checkpointed() -> None:
    """Run loop must NOT call complete() when RunHandle is checkpointed.

    This tests the guard in ``_run_turn_unlocked``'s finally block that checks
    ``run_handle.outcome not in (RunOutcome.COMPLETED, RunOutcome.FAILED)``
    before calling ``run_handle.complete()``.
    """
    assert RunOutcome.CHECKPOINTED not in (RunOutcome.COMPLETED, RunOutcome.FAILED)
    handle = RunHandle(run_id="r1", session_id="s1", agent_type="native")
    handle._start_task()
    handle.checkpoint()
    assert handle._run_state == RunState.DONE
    assert handle.outcome == RunOutcome.CHECKPOINTED
    guard_ok = handle.outcome in (RunOutcome.COMPLETED, RunOutcome.FAILED)
    guard_with_checkpointed = handle.outcome in (
        RunOutcome.COMPLETED,
        RunOutcome.FAILED,
        RunOutcome.CHECKPOINTED,
    )
    assert not guard_ok, "guard without checkpointed would incorrectly fall through"
    assert guard_with_checkpointed, "guard must include checkpointed to skip complete()"
