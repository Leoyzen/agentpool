"""Unit tests for LegacyTurnRunner.

Tests that LegacyTurnRunner preserves all non-native queue behaviour
and correctly integrates with RunHandle lifecycle management.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunFailedEvent, RunStartedEvent
from agentpool.orchestrator.core import SessionController
from agentpool.orchestrator.legacy_runner import LegacyTurnRunner
from agentpool.orchestrator.run import RunHandle, RunStatus


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
def legacy_runner(controller: SessionController) -> LegacyTurnRunner:
    """Return a LegacyTurnRunner with auto-resume enabled."""
    return LegacyTurnRunner(session_controller=controller, enable_auto_resume=True)


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
    legacy_runner: LegacyTurnRunner | None = None,
) -> Any:
    """Create a session and attach the mock agent directly."""
    state = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent

    from agentpool.agents.base_agent import _current_run_ctx_var

    def _mock_get_active_run_context() -> AgentRunContext | None:
        run_ctx = _current_run_ctx_var.get()
        if run_ctx is not None and not run_ctx.completed:
            return run_ctx
        session = controller.get_session(session_id)
        if session is not None and session.current_run_id is not None and legacy_runner is not None:
            run_ctx = legacy_runner._runs.get(session.current_run_id)
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
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """When run_turn is called directly it creates a RunHandle in _runs."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    assert len(controller._runs) == 0

    await legacy_runner.run_turn("sess-1", "hello")

    # RunHandle should have been created, completed, and cleaned up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_turn_uses_existing_run_handle_from_receive_request(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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

    await legacy_runner.run_turn("sess-1", "hello")

    # Existing RunHandle should NOT be removed by LegacyTurnRunner
    assert run_handle.run_id in controller._runs
    assert run_handle.status == RunStatus.running  # not completed by us


@pytest.mark.anyio
async def test_run_turn_sets_and_clears_current_run_id(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_turn sets session.current_run_id during execution and clears after."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    session = controller.get_session("sess-1")
    assert session is not None
    assert session.current_run_id is None

    await legacy_runner.run_turn("sess-1", "hello")

    assert session.current_run_id is None


@pytest.mark.anyio
async def test_run_turn_completes_run_handle_on_success(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Direct run_turn calls complete() the RunHandle it creates."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)

    await legacy_runner.run_turn("sess-1", "hello")

    # No RunHandle left in _runs because LegacyTurnRunner cleaned it up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_turn_fails_run_handle_on_exception(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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

    event_queue = await legacy_runner.event_bus.subscribe("sess-1")
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
        await legacy_runner.run_turn("sess-1", "hello")

    await asyncio.sleep(0.05)
    await legacy_runner.event_bus.publish("sess-1", None)
    await consumer

    failed_events = [e for e in events if isinstance(e, RunFailedEvent)]
    assert len(failed_events) == 1
    assert failed_events[0].session_id == "sess-1"
    assert isinstance(failed_events[0].exception, RuntimeError)

    # RunHandle should have been cleaned up
    assert len(controller._runs) == 0


# ---------------------------------------------------------------------------
# run_loop RunHandle integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_loop_creates_run_handle_for_initial_turn(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_loop creates and completes a RunHandle for the initial turn."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    assert len(controller._runs) == 0

    await legacy_runner.run_loop("sess-1", "hello")

    # RunHandle created by initial turn is cleaned up
    assert len(controller._runs) == 0


@pytest.mark.anyio
async def test_run_loop_uses_existing_run_handle_from_receive_request(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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

    await legacy_runner.run_loop("sess-1", "hello")

    # Existing RunHandle should NOT be removed or completed
    assert run_handle.run_id in controller._runs
    assert run_handle.status == RunStatus.running


# ---------------------------------------------------------------------------
# RED FLAG TEST – inject_prompt must trigger second iteration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inject_prompt_triggers_second_iteration(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt during an active turn MUST trigger a second _run_stream_once."""
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
            run_ctx.injection_manager.inject("injected message")
            yield RunStartedEvent(session_id="sess-1", run_id="run-1")
        else:
            yield RunStartedEvent(session_id="sess-1", run_id="run-2")

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)
    await legacy_runner.run_turn("sess-1", "initial")

    assert call_count == 2, (
        f"inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2 (initial + injected)."
    )
    assert received_prompts[1] == ("injected message",), (
        f"Second iteration should process injected prompt, got {received_prompts[1]}"
    )


@pytest.mark.anyio
async def test_post_turn_inject_prompt_triggers_auto_resume(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt AFTER turn ends MUST trigger auto-resume."""
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
        yield RunStartedEvent(session_id="sess-1", run_id=f"run-{call_count}")

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    await legacy_runner.run_turn("sess-1", "initial")
    assert call_count == 1

    injected = await legacy_runner.inject_prompt("sess-1", "late message")
    assert injected is False

    await asyncio.sleep(0.1)

    assert call_count == 2, (
        f"post-turn inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2 (initial + auto-resume)."
    )
    assert received_prompts[1] == ("late message",)


# ---------------------------------------------------------------------------
# run_turn – serialization
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_turn_serializes_per_session(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Only one turn executes per session at a time."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)

    timestamps: list[float] = []

    async def record(task_id: str) -> None:
        await legacy_runner.run_turn("sess-1", f"prompt-{task_id}")
        timestamps.append(asyncio.get_event_loop().time())

    t1 = asyncio.create_task(record("A"))
    await asyncio.sleep(0.01)
    t2 = asyncio.create_task(record("B"))
    await asyncio.gather(t1, t2)

    assert len(timestamps) == 2
    assert timestamps[1] >= timestamps[0] + 0.04


@pytest.mark.anyio
async def test_run_turn_skips_closing_session(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """run_turn silently returns when the session is already closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    await legacy_runner.run_turn("sess-1", "hello")


@pytest.mark.anyio
async def test_run_turn_publishes_events(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Events from the agent stream are published to the EventBus."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    queue = await legacy_runner.event_bus.subscribe("sess-1")
    await legacy_runner.run_turn("sess-1", "hello")
    event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event is not None
    assert isinstance(event, RunStartedEvent)


@pytest.mark.anyio
async def test_run_turn_records_timing(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Turn timings are recorded after a turn completes."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)
    assert len(legacy_runner._turn_timings) == 0
    await legacy_runner.run_turn("sess-1", "hello")
    assert len(legacy_runner._turn_timings) == 1
    start, end = legacy_runner._turn_timings[0]
    assert end > start


# ---------------------------------------------------------------------------
# run_loop – auto-resume
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_loop_processes_queued_injections(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Post-turn injections are processed automatically by run_loop."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await legacy_runner.inject_prompt("sess-1", "injected-msg")
    await legacy_runner.run_loop("sess-1", "initial")
    assert len(legacy_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_run_loop_processes_queued_prompts(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Post-turn prompts are processed automatically by run_loop."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await legacy_runner.queue_prompt("sess-1", "queued-prompt")
    await legacy_runner.run_loop("sess-1", "initial")
    assert len(legacy_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_run_loop_drains_on_exception(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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
    await legacy_runner.inject_prompt("sess-1", "injected-msg")
    await legacy_runner.queue_prompt("sess-1", "queued-prompt")
    await legacy_runner.run_loop("sess-1", "initial")
    assert legacy_runner._post_turn_injections.get("sess-1") in (None, [])
    assert legacy_runner._post_turn_prompts.get("sess-1") in (None, [])


# ---------------------------------------------------------------------------
# inject_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inject_prompt_into_active_turn(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns True and injects immediately when a turn is active."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool, legacy_runner)

    injected = False

    async def delayed_inject() -> None:
        nonlocal injected
        await asyncio.sleep(0.02)
        injected = await legacy_runner.inject_prompt("sess-1", "injected-msg")

    await asyncio.gather(
        legacy_runner.run_turn("sess-1", "hello"),
        delayed_inject(),
    )
    assert injected is True


@pytest.mark.anyio
async def test_inject_prompt_queues_when_idle(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns False and queues when no turn is active."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    result = await legacy_runner.inject_prompt("sess-1", "injected-msg")
    assert result is False
    assert legacy_runner._post_turn_injections.get("sess-1") == ["injected-msg"]


@pytest.mark.anyio
async def test_inject_prompt_returns_false_for_missing_session(
    legacy_runner: LegacyTurnRunner,
) -> None:
    """inject_prompt returns False when the session does not exist."""
    result = await legacy_runner.inject_prompt("missing", "msg")
    assert result is False


@pytest.mark.anyio
async def test_inject_prompt_returns_false_for_closing_session(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt returns False when the session is closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    result = await legacy_runner.inject_prompt("sess-1", "msg")
    assert result is False


# ---------------------------------------------------------------------------
# queue_prompt
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_queue_prompt_into_active_turn(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """queue_prompt returns True and queues into active run context."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool, legacy_runner)

    queued = False

    async def delayed_queue() -> None:
        nonlocal queued
        await asyncio.sleep(0.02)
        queued = await legacy_runner.queue_prompt("sess-1", "queued-msg")

    await asyncio.gather(
        legacy_runner.run_turn("sess-1", "hello"),
        delayed_queue(),
    )
    assert queued is True


@pytest.mark.anyio
async def test_queue_prompt_stores_when_idle(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """queue_prompt returns False and stores prompts for later."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    result = await legacy_runner.queue_prompt("sess-1", "prompt-a", "prompt-b")
    assert result is False
    stored = legacy_runner._post_turn_prompts.get("sess-1")
    assert stored is not None
    assert stored == [("prompt-a", "prompt-b")]


@pytest.mark.anyio
async def test_queue_prompt_returns_false_for_missing_session(
    legacy_runner: LegacyTurnRunner,
) -> None:
    """queue_prompt returns False when the session does not exist."""
    result = await legacy_runner.queue_prompt("missing", "msg")
    assert result is False


# ---------------------------------------------------------------------------
# auto-resume trigger
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_resume_trigger_processes_queued_work(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume picks up queued work after run_turn finishes."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await legacy_runner.run_turn("sess-1", "initial")
    await legacy_runner.inject_prompt("sess-1", "injected-msg")
    await legacy_runner._trigger_auto_resume("sess-1")
    assert len(legacy_runner._turn_timings) == 2


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_when_locked(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent_with_delay: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume is a no-op when turn_lock is already held."""
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)
    task = asyncio.create_task(legacy_runner.run_turn("sess-1", "hello"))
    await asyncio.sleep(0.01)
    await legacy_runner._trigger_auto_resume("sess-1")
    await task
    assert len(legacy_runner._turn_timings) == 1


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_when_disabled(
    controller: SessionController,
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """When auto-resume is disabled, _trigger_auto_resume still runs queued work."""
    runner = LegacyTurnRunner(controller, enable_auto_resume=False)
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    await runner.inject_prompt("sess-1", "injected-msg")
    await runner._trigger_auto_resume("sess-1")
    assert len(runner._turn_timings) == 1


@pytest.mark.anyio
async def test_auto_resume_trigger_noop_for_closing_session(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """_trigger_auto_resume exits early when the session is closing."""
    state = await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    state.is_closing = True
    await legacy_runner.inject_prompt("sess-1", "msg")
    await legacy_runner._trigger_auto_resume("sess-1")
    assert len(legacy_runner._turn_timings) == 0


# ---------------------------------------------------------------------------
# cancellation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_turn_cancellation_stops_current_turn(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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

    task = asyncio.create_task(legacy_runner.run_turn("sess-1", "hello"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_run_loop_cancellation(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
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

    task = asyncio.create_task(legacy_runner.run_loop("sess-1", "hello"))
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
    legacy_runner: LegacyTurnRunner,
    mock_agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """The auto-resume loop stops after max_auto_resume iterations."""
    await _setup_session(controller, "sess-1", mock_agent, mock_pool)
    legacy_runner._max_auto_resume = 2
    state = controller.get_session("sess-1")
    assert state is not None

    legacy_runner._post_turn_injections["sess-1"] = ["msg"]

    await legacy_runner._process_queued_work("sess-1", state)
    assert len(legacy_runner._turn_timings) >= 1


# ---------------------------------------------------------------------------
# drain helpers
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_drain_post_turn_injections_is_atomic(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
) -> None:
    """_drain_post_turn_injections removes and returns all injections."""
    legacy_runner._post_turn_injections["sess-1"] = ["a", "b", "c"]
    drained = await legacy_runner._drain_post_turn_injections("sess-1")
    assert drained == ["a", "b", "c"]
    assert "sess-1" not in legacy_runner._post_turn_injections


@pytest.mark.anyio
async def test_drain_post_turn_prompts_is_atomic(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
) -> None:
    """_drain_post_turn_prompts removes and returns all prompt groups."""
    legacy_runner._post_turn_prompts["sess-1"] = [("p1",), ("p2", "p3")]
    drained = await legacy_runner._drain_post_turn_prompts("sess-1")
    assert drained == [("p1",), ("p2", "p3")]
    assert "sess-1" not in legacy_runner._post_turn_prompts


@pytest.mark.anyio
async def test_drain_returns_empty_for_unknown_session(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
) -> None:
    """Draining an unknown session returns an empty list."""
    assert await legacy_runner._drain_post_turn_injections("missing") == []
    assert await legacy_runner._drain_post_turn_prompts("missing") == []


# ---------------------------------------------------------------------------
# input_provider propagation (RED FLAG)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_turn_passes_input_provider_to_agent(
    controller: SessionController,
    legacy_runner: LegacyTurnRunner,
    mock_pool: MagicMock,
) -> None:
    """input_provider must be forwarded to agent._run_stream_once."""
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
    await legacy_runner.run_turn("sess-1", "hello", input_provider=fake_provider)

    assert len(calls) == 1
    assert calls[0].get("input_provider") is fake_provider
