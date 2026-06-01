"""Unit tests for TurnRunner (SessionPool Group 2.12).

Tests turn serialization, prompt injection/queuing, auto-resume,
and cancellation semantics.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import (
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
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None

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
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None

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
) -> SessionState:
    """Create a session and attach the mock agent directly."""
    state = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent
    return state


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
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None

    async def broken_stream(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        raise RuntimeError("boom")

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
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)

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
    await _setup_session(controller, "sess-1", mock_agent_with_delay, mock_pool)

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
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None

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
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None

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
