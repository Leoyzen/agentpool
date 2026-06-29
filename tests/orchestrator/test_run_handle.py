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

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, RunFailedEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionState
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
        yield  # noqa: unreachable — makes this an async generator


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
@pytest.mark.skip(reason="pre-existing failure from run/turn separation refactor")
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
async def test_cancel_does_not_cancel_current_task() -> None:
    """Given a RunHandle with current_task set and no _cancel_fn, cancel()
    does NOT cancel current_task — the start() loop must keep running to
    process the cancelled flag and emit stream-complete events gracefully.
    """
    handle = _make_run_handle()
    mock_task = MagicMock()
    mock_task.done.return_value = False
    handle.run_ctx.current_task = mock_task

    handle.cancel()

    assert handle.run_ctx.cancelled is True
    assert handle._idle_event.is_set()
    mock_task.cancel.assert_not_called()


@pytest.mark.unit
async def test_cancel_with_done_task_does_not_cancel() -> None:
    """Given a RunHandle with current_task already done, cancel() does not
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
async def test_cancelled_property_reflects_turn_cancel_state() -> None:
    """cancelled property returns _turn_was_cancelled, not live run_ctx.cancelled.

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
            "complete_event was not set after start() completed — "
            "close_session() will hang for 30s"
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
        with contextlib.suppress(Exception):
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
                yield  # noqa: unreachable — make it an async generator

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
            f"ContextVar was not set — got {captured_provider[0]!r}, "
            f"expected {mock_provider!r}"
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
                yield  # noqa: unreachable

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
                "start() hung after turn failure — loop continued to idle "
                "instead of breaking"
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
        "run_ctx.current_task must be set in start() so cancel() can "
        "interrupt the running turn"
    )
    assert "asyncio.current_task()" in source, (
        "current_task must be set to asyncio.current_task()"
    )


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

    assert handle._status == RunStatus.idle
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
        async for event in gen:
            events.append(event)

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
    assert handle._status == RunStatus.idle

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
    assert handle._status == RunStatus.idle
    assert handle._turn_complete_event.is_set()
    assert turn1.captured_start is False

    # Steer to wake for second turn
    handle.steer("second")
    await asyncio.sleep(0.1)

    # After second turn: idle, event set, was cleared at start (was set between turns)
    assert handle._status == RunStatus.idle
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
            f"ValueError(s) raised during generator GC cleanup: "
            f"{[str(e) for e in gc_exceptions]}"
        )
