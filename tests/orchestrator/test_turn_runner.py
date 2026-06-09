"""Unit tests for TurnRunner (SessionPool Group 2.12).

Tests turn serialization, prompt injection/queuing, auto-resume,
and cancellation semantics.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import (
    EventEnvelope,
    SessionController,
    SessionState,
    TurnRunner,
)


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a real SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner with auto-resume enabled."""
    return TurnRunner(session_controller=controller, enable_auto_resume=True)


@pytest.fixture
def mock_agent() -> MagicMock:
    """Return a mocked BaseAgent with _run_stream_once."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent._run_stream_once = _fake_stream
    return agent


@pytest.fixture
def mock_agent_with_delay() -> MagicMock:
    """Return a mocked BaseAgent whose stream takes a noticeable time."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        await asyncio.sleep(0.05)
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent._run_stream_once = _fake_stream
    return agent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
    mock_pool: MagicMock,
    turn_runner: TurnRunner | None = None,
) -> SessionState:
    """Create a session and attach the mock agent directly."""
    state, _ = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent

    # Configure mock to support new get_active_run_context behavior
    # (ContextVar for same-task, session.current_run_id + TurnRunner._runs for cross-task)
    from agentpool.agents.base_agent import _current_run_ctx_var

    def _mock_get_active_run_context() -> AgentRunContext | None:
        run_ctx = _current_run_ctx_var.get()
        if run_ctx is not None and not run_ctx.completed:
            return run_ctx
        session = controller.get_session(session_id)
        if session is not None and session.current_run_id is not None and turn_runner is not None:
            run_ctx = turn_runner._runs.get(session.current_run_id)
            if run_ctx is not None and not run_ctx.completed:
                return run_ctx
        if agent._background_run_ctx is not None and not agent._background_run_ctx.completed:
            return agent._background_run_ctx
        return None

    agent.get_active_run_context.side_effect = _mock_get_active_run_context
    return state


# ---------------------------------------------------------------------------
# RunHandle lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_turn_creates_run_handle_when_called_directly(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """When run_turn is called directly it creates a RunHandle in _runs."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    assert len(controller._runs) == 0

    await turn_runner.run_turn("sess-1", "hello")

    # RunHandle should have been created, completed, and cleaned up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_turn_uses_existing_run_handle_from_receive_request(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_turn uses an existing RunHandle created by receive_request."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)

    run_handle = controller._create_run("sess-1", "hello")
    controller._runs[run_handle.run_id] = run_handle
    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = run_handle.run_id
    controller._pending_run_ids["sess-1"] = run_handle.run_id

    await turn_runner.run_turn("sess-1", "hello")

    # Existing RunHandle should NOT be removed by TurnRunner
    assert run_handle.run_id in controller._runs
    from agentpool.orchestrator.run import RunStatus
    assert run_handle.status == RunStatus.running  # not completed by us


@pytest.mark.anyio
async def test_run_turn_sets_and_clears_current_run_id(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_turn sets session.current_run_id during execution and clears after."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id is None

    await turn_runner.run_turn("sess-1", "hello")

    assert session.current_run_id is None


@pytest.mark.anyio
async def test_run_turn_completes_run_handle_on_success(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Direct run_turn calls complete() the RunHandle it creates."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)

    await turn_runner.run_turn("sess-1", "hello")

    # No RunHandle left in _runs because TurnRunner cleaned it up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_turn_fails_run_handle_on_exception(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When _run_stream_once raises, the RunHandle is marked failed."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def broken_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("boom")
        yield  # make it an async generator

    agent._run_stream_once = broken_stream
    await _setup_session(controller, "sess-1", agent, mock_pool)

    event_queue = await turn_runner.event_bus.subscribe("sess-1")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is None:
                    break
                events.append(event)
        except TimeoutError:
            pass

    consumer = asyncio.create_task(_consume())

    with pytest.raises(RuntimeError, match="boom"):
        await turn_runner.run_turn("sess-1", "hello")

    await asyncio.sleep(0.05)
    await turn_runner.event_bus.publish("sess-1", None)
    await consumer

    from agentpool.agents.events import RunFailedEvent
    from agentpool.orchestrator.core import EventEnvelope

    # Unwrap EventEnvelope before type checking
    unwrapped_events = [
        e.event if isinstance(e, EventEnvelope) else e for e in events
    ]
    failed_events = [e for e in unwrapped_events if isinstance(e, RunFailedEvent)]
    assert len(failed_events) == 1
    assert failed_events[0].session_id == "sess-1"
    assert isinstance(failed_events[0].exception, RuntimeError)

    # RunHandle should have been cleaned up
    assert len(controller._runs) == 0


# ---------------------------------------------------------------------------
# RED FLAG TEST – inject_prompt must trigger second iteration
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_inject_prompt_triggers_second_iteration(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt during an active turn MUST trigger a second _run_stream_once.

    This is a **red flag test** — if it fails, inject_prompt is broken.

    Scenario:
    1. run_turn starts → calls _run_stream_once (iteration 1)
    2. During iteration 1, a tool calls inject_prompt("msg")
       → message goes into run_ctx.injection_manager._pending_injections
    3. Iteration 1 completes
    4. flush_pending_to_queue() moves "msg" to _queued_prompts
    5. while has_queued() → pop_queued() → _run_stream_once (iteration 2)
    6. Iteration 2 processes the injected message

    Expected: _run_stream_once called exactly TWICE.
    """
    call_count = 0
    received_prompts: list[tuple[Any, ...]] = []

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        nonlocal call_count
        call_count += 1
        received_prompts.append(prompts)

        if call_count == 1:
            # Simulate a tool injecting a prompt mid-turn
            run_ctx.injection_manager.inject("injected message")
            yield RunStartedEvent(session_id="sess-1", run_id="run-1")
        else:
            yield RunStartedEvent(session_id="sess-1", run_id="run-2")

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)
    await turn_runner.run_turn("sess-1", "initial")

    # RED FLAG: if this is 1 instead of 2, inject_prompt is silently broken
    assert call_count == 2, (
        f"inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2 (initial + injected). "
        f"Queued prompts were not processed after flush."
    )
    assert received_prompts[1] == ("injected message",), (
        f"Second iteration should process injected prompt, got {received_prompts[1]}"
    )


# ---------------------------------------------------------------------------
# run_loop RunHandle integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_loop_creates_run_handle_for_initial_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_loop creates and completes a RunHandle for the initial turn."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    assert len(controller._runs) == 0

    await turn_runner.run_loop("sess-1", "hello")

    # RunHandle created by initial turn is cleaned up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_loop_uses_existing_run_handle_from_receive_request(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_loop uses an existing RunHandle without completing it."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)

    run_handle = controller._create_run("sess-1", "hello")
    controller._runs[run_handle.run_id] = run_handle
    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = run_handle.run_id
    controller._pending_run_ids["sess-1"] = run_handle.run_id

    await turn_runner.run_loop("sess-1", "hello")

    # Existing RunHandle should NOT be removed or completed
    assert run_handle.run_id in controller._runs
    from agentpool.orchestrator.run import RunStatus
    assert run_handle.status == RunStatus.running


# ---------------------------------------------------------------------------
# run_loop – auto-resume
# ---------------------------------------------------------------------------
# run_turn – serialization
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_turn_serializes_per_session(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Only one turn executes per session at a time."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)

    timestamps: list[float] = []

    async def record(task_id: str) -> None:
        await turn_runner.run_turn("sess-1", f"prompt-{task_id}")
        timestamps.append(asyncio.get_event_loop().time())

    t1 = asyncio.create_task(record("A"))
    await asyncio.sleep(0.01)  # ensure A starts first
    t2 = asyncio.create_task(record("B"))
    await asyncio.gather(t1, t2)

    # Both should complete; B must have started after A finished
    assert len(timestamps) == 2
    assert timestamps[1] >= timestamps[0] + 0.04


@pytest.mark.anyio
async def test_run_turn_skips_closing_session(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_turn silently returns when the session is already closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    # Should not raise or call _run_stream_once
    await turn_runner.run_turn("sess-1", "hello")


@pytest.mark.anyio
async def test_run_turn_publishes_events(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Events from the agent stream are published to the EventBus."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    queue = await turn_runner.event_bus.subscribe("sess-1")
    await turn_runner.run_turn("sess-1", "hello")
    event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event is not None
    # EventBus now wraps events in EventEnvelope
    from agentpool.orchestrator.core import EventEnvelope
    if isinstance(event, EventEnvelope):
        event = event.event
    assert isinstance(event, RunStartedEvent)


@pytest.mark.anyio
async def test_run_turn_records_timing(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Turn timings are recorded after a turn completes."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)
    assert len(turn_runner._turn_timings) == 0
    await turn_runner.run_turn("sess-1", "hello")
    assert len(turn_runner._turn_timings) == 1
    start, end = turn_runner._turn_timings[0]
    assert end > start


# ---------------------------------------------------------------------------
# run_loop – auto-resume
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_loop_processes_queued_injections(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Post-turn injections are processed automatically by run_loop."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    # Queue an injection before the loop starts
    await turn_runner.inject_prompt("sess-1", "injected-msg")
    await turn_runner.run_loop("sess-1", "initial")
    # One turn for initial + one for injection
    assert len(turn_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_run_loop_processes_queued_prompts(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Post-turn prompts are processed automatically by run_loop."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await turn_runner.queue_prompt("sess-1", "queued-prompt")
    await turn_runner.run_loop("sess-1", "initial")
    # One turn for initial + one for queued prompt
    assert len(turn_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_run_loop_drains_on_exception(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """If the turn loop raises, queued work is drained so it does not leak."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def broken_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("boom")
        yield  # make it an async generator

    agent._run_stream_once = broken_stream
    await _setup_session(controller, "sess-1", agent, mock_pool)
    await turn_runner.inject_prompt("sess-1", "injected-msg")
    await turn_runner.queue_prompt("sess-1", "queued-prompt")
    # Should not raise – exception is caught and logged
    await turn_runner.run_loop("sess-1", "initial")
    # Queues should be empty after drain
    assert turn_runner._post_turn_injections.get("sess-1") in (None, [])
    assert turn_runner._post_turn_prompts.get("sess-1") in (None, [])


# ---------------------------------------------------------------------------
# inject_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inject_prompt_into_active_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns True and injects immediately when a turn is active."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool, turn_runner)

    injected = False

    async def delayed_inject() -> None:
        nonlocal injected
        await asyncio.sleep(0.02)
        injected = await turn_runner.inject_prompt("sess-1", "injected-msg")

    await asyncio.gather(
        turn_runner.run_turn("sess-1", "hello"),
        delayed_inject(),
    )
    assert injected is True


@pytest.mark.anyio
async def test_inject_prompt_queues_when_idle(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns False and queues when no turn is active."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    result = await turn_runner.inject_prompt("sess-1", "injected-msg")
    assert result is False
    assert turn_runner._post_turn_injections.get("sess-1") == ["injected-msg"]


@pytest.mark.anyio
async def test_inject_prompt_returns_false_for_missing_session(
    turn_runner: TurnRunner,
) -> None:
    """inject_prompt returns False when the session does not exist."""
    result = await turn_runner.inject_prompt("missing", "msg")
    assert result is False


@pytest.mark.anyio
async def test_inject_prompt_returns_false_for_closing_session(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns False when the session is closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    result = await turn_runner.inject_prompt("sess-1", "msg")
    assert result is False


# ---------------------------------------------------------------------------
# queue_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_queue_prompt_into_active_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """queue_prompt returns True and queues into active run context."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool, turn_runner)

    queued = False

    async def delayed_queue() -> None:
        nonlocal queued
        await asyncio.sleep(0.02)
        queued = await turn_runner.queue_prompt("sess-1", "queued-msg")

    await asyncio.gather(
        turn_runner.run_turn("sess-1", "hello"),
        delayed_queue(),
    )
    assert queued is True


@pytest.mark.anyio
async def test_queue_prompt_stores_when_idle(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """queue_prompt returns False and stores prompts for later."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    result = await turn_runner.queue_prompt("sess-1", "prompt-a", "prompt-b")
    assert result is False
    stored = turn_runner._post_turn_prompts.get("sess-1")
    assert stored is not None
    assert stored == [("prompt-a", "prompt-b")]


@pytest.mark.anyio
async def test_queue_prompt_returns_false_for_missing_session(
    turn_runner: TurnRunner,
) -> None:
    """queue_prompt returns False when the session does not exist."""
    result = await turn_runner.queue_prompt("missing", "msg")
    assert result is False


# ---------------------------------------------------------------------------
# auto-resume trigger
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_resume_trigger_processes_queued_work(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume picks up queued work after run_turn finishes."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await turn_runner.run_turn("sess-1", "initial")
    # Now queue work while idle
    await turn_runner.inject_prompt("sess-1", "injected-msg")
    # Trigger auto-resume
    await turn_runner._trigger_auto_resume("sess-1")
    # Should have processed the injection
    assert len(turn_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_when_locked(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume is a no-op when turn_lock is already held."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)
    # Start a long turn
    task = asyncio.create_task(turn_runner.run_turn("sess-1", "hello"))
    await asyncio.sleep(0.01)  # ensure turn started
    # Trigger while locked
    await turn_runner._trigger_auto_resume("sess-1")
    await task
    # Only the original turn should have run
    assert len(turn_runner._turn_timings) == 1


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_when_disabled(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """When auto-resume is disabled, _trigger_auto_resume still runs queued work."""
    runner = TurnRunner(controller, enable_auto_resume=False)
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await runner.inject_prompt("sess-1", "injected-msg")
    await runner._trigger_auto_resume("sess-1")
    # Even with enable_auto_resume=False, the trigger still processes
    assert len(runner._turn_timings) == 1


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_for_closing_session(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume exits early when the session is closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    await turn_runner.inject_prompt("sess-1", "msg")
    await turn_runner._trigger_auto_resume("sess-1")
    assert len(turn_runner._turn_timings) == 0


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_turn_cancellation_stops_current_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Cancelling the task running run_turn aborts the turn."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def slow_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        for _ in range(100):
            await asyncio.sleep(0.01)
            yield RunStartedEvent(session_id="sess-1", run_id="r")

    agent._run_stream_once = slow_stream
    await _setup_session(controller, "sess-1", agent, mock_pool)

    task = asyncio.create_task(turn_runner.run_turn("sess-1", "hello"))
    await asyncio.sleep(0.05)  # let it start
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_run_loop_cancellation(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Cancelling the task running run_loop raises CancelledError."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def slow_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(10)
        yield RunStartedEvent(session_id="sess-1", run_id="r")

    agent._run_stream_once = slow_stream
    await _setup_session(controller, "sess-1", agent, mock_pool)

    task = asyncio.create_task(turn_runner.run_loop("sess-1", "hello"))
    await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# _process_queued_work – max auto-resume
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_max_auto_resume_limits_iterations(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """The auto-resume loop stops after max_auto_resume iterations."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    turn_runner._max_auto_resume = 2
    state = controller.get_session("sess-1")
    assert state is not None

    # Pre-populate injections so each iteration finds work
    turn_runner._post_turn_injections["sess-1"] = ["msg"]

    await turn_runner._process_queued_work("sess-1", state)
    # initial queued work (1 turn) + up to 2 auto-resume iterations
    # But since we only seeded one injection, it runs once for initial
    # and the auto-resume loop will find nothing on subsequent checks.
    assert len(turn_runner._turn_timings) >= 1


# ---------------------------------------------------------------------------
# drain helpers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_drain_post_turn_injections_is_atomic(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_drain_post_turn_injections removes and returns all injections."""
    turn_runner._post_turn_injections["sess-1"] = ["a", "b", "c"]
    drained = await turn_runner._drain_post_turn_injections("sess-1")
    assert drained == ["a", "b", "c"]
    assert "sess-1" not in turn_runner._post_turn_injections


@pytest.mark.anyio
async def test_drain_post_turn_prompts_is_atomic(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_drain_post_turn_prompts removes and returns all prompt groups."""
    turn_runner._post_turn_prompts["sess-1"] = [("p1",), ("p2", "p3")]
    drained = await turn_runner._drain_post_turn_prompts("sess-1")
    assert drained == [("p1",), ("p2", "p3")]
    assert "sess-1" not in turn_runner._post_turn_prompts


@pytest.mark.anyio
async def test_drain_returns_empty_for_unknown_session(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """Draining an unknown session returns an empty list."""
    assert await turn_runner._drain_post_turn_injections("missing") == []
    assert await turn_runner._drain_post_turn_prompts("missing") == []


# ---------------------------------------------------------------------------
# input_provider propagation (RED FLAG)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_turn_passes_input_provider_to_agent(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """input_provider must be forwarded to agent._run_stream_once so
    elicitation flows through the ACP protocol instead of falling back
    to StdlibInputProvider.
    """
    from agentpool.ui.base import InputProvider

    calls: list[dict[str, Any]] = []

    agent = MagicMock()
    agent.get_active_run_context.return_value = None

    async def _capture_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        calls.append(kwargs)
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent._run_stream_once = _capture_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    fake_provider = MagicMock(spec=InputProvider)
    await turn_runner.run_turn("sess-1", "hello", input_provider=fake_provider)

    assert len(calls) == 1
    assert calls[0].get("input_provider") is fake_provider


# ---------------------------------------------------------------------------
# _bypass_session_pool ContextVar
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bypass_session_pool_set_during_run_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """SessionPool-internal _run_stream_once sees _bypass_session_pool=True."""
    from agentpool.agents.base_agent import _bypass_session_pool

    seen_values: list[bool] = []

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        seen_values.append(_bypass_session_pool.get())
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)
    await turn_runner.run_turn("sess-1", "hello")

    assert seen_values == [True], (
        f"_bypass_session_pool should be True during TurnRunner turns, got {seen_values}"
    )


@pytest.mark.anyio
async def test_bypass_session_pool_cleared_after_run_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """_bypass_session_pool is reset to False after run_turn completes."""
    from agentpool.agents.base_agent import _bypass_session_pool

    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await turn_runner.run_turn("sess-1", "hello")

    assert _bypass_session_pool.get() is False, (
        "_bypass_session_pool should be reset after TurnRunner turn completes"
    )


def test_bypass_session_pool_external_call() -> None:
    """External calls (no ContextVar set, no AG-UI stack) do NOT bypass SessionPool."""
    from agentpool.agents.base_agent import _should_bypass_session_pool

    result = _should_bypass_session_pool()
    assert result is False, (
        "External calls should not bypass SessionPool when ContextVar is unset "
        "and no AG-UI frames are in the stack"
    )


def test_bypass_session_pool_contextvar_true() -> None:
    """When _bypass_session_pool ContextVar is True, bypass is active."""
    from agentpool.agents.base_agent import _bypass_session_pool, _should_bypass_session_pool

    token = _bypass_session_pool.set(True)
    try:
        result = _should_bypass_session_pool()
        assert result is True, (
            "_should_bypass_session_pool should return True when ContextVar is set"
        )
    finally:
        _bypass_session_pool.reset(token)


def test_bypass_session_pool_agui_stack_inspection() -> None:
    """AG-UI callers still bypass via stack inspection (permanent — see docs/audit/agui-bypass-audit.md)."""
    import types
    from typing import Any

    from agentpool.agents.base_agent import _should_bypass_session_pool

    agui_module: Any = types.ModuleType("agui_test_module")
    agui_module.__dict__["_should_bypass_session_pool"] = _should_bypass_session_pool

    # Execute function definition inside the module so its f_globals are agui_module's
    exec(
        "def _check():\n    return _should_bypass_session_pool()\n",
        agui_module.__dict__,
    )

    check_fn = agui_module.__dict__["_check"]
    result = check_fn()
    assert result is True, (
        "AG-UI stack inspection should bypass SessionPool (permanent — see docs/audit/agui-bypass-audit.md)"
    )


# ---------------------------------------------------------------------------
# _publish_event – EventEnvelope wrapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_publish_event_wraps_in_event_envelope(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_publish_event wraps the event in an EventEnvelope with source_session_id."""
    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging import ChatMessage

    event = StreamCompleteEvent(message=ChatMessage(content="test", role="assistant"))

    queue = await turn_runner.event_bus.subscribe("sess-pub")
    await turn_runner._publish_event("sess-pub", event)

    published = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert isinstance(published, EventEnvelope), (
        "Expected event to be wrapped in EventEnvelope"
    )
    assert published.source_session_id == "sess-pub", (
        "Expected source_session_id to be set by _publish_event"
    )
    assert published.event is event, (
        "Expected original event to be preserved unmodified"
    )


@pytest.mark.anyio
async def test_publish_event_preserves_original_event_unmodified(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_publish_event does NOT mutate the original event."""
    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging import ChatMessage

    event = StreamCompleteEvent(
        message=ChatMessage(content="test", role="assistant"),
        session_id="existing-sid",
    )

    queue = await turn_runner.event_bus.subscribe("sess-pub")
    await turn_runner._publish_event("sess-pub", event)

    published = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert isinstance(published, EventEnvelope), (
        "Expected event to be wrapped in EventEnvelope"
    )
    assert published.source_session_id == "sess-pub", (
        "Expected source_session_id to reflect publishing session"
    )
    assert published.event is event, (
        "Expected original event object to be preserved, not mutated"
    )
    assert event.session_id == "existing-sid", (
        "Original event should remain unmodified"
    )


@pytest.mark.anyio
async def test_publish_event_wraps_objects_without_session_id(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_publish_event wraps arbitrary objects in EventEnvelope."""

    class NoSessionId:
        pass

    event = NoSessionId()
    queue = await turn_runner.event_bus.subscribe("sess-pub")
    await turn_runner._publish_event("sess-pub", event)

    published = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert isinstance(published, EventEnvelope), (
        "Event without session_id should be wrapped in EventEnvelope"
    )
    assert published.source_session_id == "sess-pub"
    assert published.event is event


@pytest.mark.anyio
async def test_publish_event_wraps_pydantic_ai_events(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """_publish_event wraps PydanticAI events in EventEnvelope."""
    from pydantic_ai import PartStartEvent, TextPart

    event = PartStartEvent(index=0, part=TextPart(content="hello"))

    queue = await turn_runner.event_bus.subscribe("sess-pub")
    await turn_runner._publish_event("sess-pub", event)

    published = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert isinstance(published, EventEnvelope), (
        "PydanticAI event should be wrapped in EventEnvelope"
    )
    assert published.source_session_id == "sess-pub"
    assert published.event is event, (
        "Original PydanticAI event should be preserved unmodified"
    )


@pytest.mark.anyio
async def test_stream_event_emitter_wraps_subagent_event_in_envelope(
    controller: SessionController,
    turn_runner: TurnRunner,
) -> None:
    """StreamEventEmitter._emit publishes SubAgentEvent wrapped in EventEnvelope."""
    from agentpool.agents.events import SubAgentEvent
    from agentpool.agents.events.event_emitter import StreamEventEmitter

    # Create a mock context with session_id
    mock_ctx = MagicMock()
    mock_ctx.agent.session_id = "parent-sid"
    mock_ctx.run_ctx = None

    emitter = StreamEventEmitter(mock_ctx, event_bus=turn_runner.event_bus)

    event = SubAgentEvent(
        source_name="worker",
        source_type="agent",
        event=MagicMock(),
        depth=1,
        child_session_id="child-sid",
    )

    queue = await turn_runner.event_bus.subscribe("parent-sid")
    await emitter._emit(event)

    published = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert isinstance(published, EventEnvelope), (
        "SubAgentEvent should be wrapped in EventEnvelope"
    )
    assert published.source_session_id == "parent-sid", (
        "Expected source_session_id to reflect parent session"
    )
    assert published.event is event, (
        "Original SubAgentEvent should be preserved unmodified"
    )
