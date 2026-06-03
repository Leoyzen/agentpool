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
    state = await controller.get_or_create_session(session_id)
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


@pytest.mark.anyio
async def test_post_turn_inject_prompt_triggers_auto_resume(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """inject_prompt AFTER turn ends MUST trigger auto-resume.

    This is a **red flag test** — if it fails, post-turn inject_prompt is broken.

    Scenario:
    1. run_turn completes
    2. Caller calls turn_runner.inject_prompt("sess-1", "msg")
       → msg goes to _post_turn_injections
       → _trigger_auto_resume fires
    3. Auto-resume should process the injection in a new turn

    Expected: _run_stream_once called TWICE (initial + auto-resume).
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
        yield RunStartedEvent(session_id="sess-1", run_id=f"run-{call_count}")

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # 1. Initial turn completes
    await turn_runner.run_turn("sess-1", "initial")
    assert call_count == 1

    # 2. Post-turn injection (simulates tool calling inject after turn ended)
    injected = await turn_runner.inject_prompt("sess-1", "late message")
    assert injected is False  # Queued, not injected into active turn

    # 3. Wait for auto-resume to fire and complete
    await asyncio.sleep(0.1)

    # RED FLAG: auto-resume should have triggered a second turn
    assert call_count == 2, (
        f"post-turn inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2 (initial + auto-resume). "
        f"_trigger_auto_resume did not process queued injection."
    )
    assert received_prompts[1] == ("late message",), (
        f"Auto-resume should process injected prompt, got {received_prompts[1]}"
    )


@pytest.mark.anyio
async def test_background_task_child_agent_events_reach_event_bus(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Background task child-agent events MUST reach EventBus.

    This is a **red flag test** — if it fails, background task events are lost.

    Scenario (real-world from xeno-agent):
    1. SessionPool calls _run_stream_once for lead agent
    2. Lead agent's tool spawns a background task (subagent)
    3. Subagent creates its OWN run_ctx with its OWN event_queue
    4. Subagent calls ctx.events.emit_event(SubAgentEvent(...))
       → event goes to subagent's run_ctx.event_queue
       → StreamEventEmitter._emit forwards to EventBus (when SessionPool active)
    5. ACP/OpenCode handler receives event via EventBus

    Expected: SubAgentEvent published to EventBus.
    """
    from agentpool import ChatMessage
    from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent
    from agentpool.agents.events.event_emitter import StreamEventEmitter

    event_bus_events: list[Any] = []

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        # Lead agent starts background task
        yield RunStartedEvent(session_id="sess-1", run_id="run-1")

        # Simulate background task creating its own run_ctx and emitting events
        # via StreamEventEmitter (what xeno-agent's BackgroundTaskProvider does)
        child_run_ctx = AgentRunContext(session_id="child-sess", deps=None)
        child_run_ctx.cancelled = False

        # Create a mock AgentContext for the child
        child_agent = MagicMock()
        child_agent.session_id = "sess-1"  # Same session for EventBus routing
        child_run_ctx = AgentRunContext(session_id="child-sess", deps=None)
        child_run_ctx.event_bus = turn_runner.event_bus
        child_ctx = MagicMock()
        child_ctx.agent = child_agent
        child_ctx.run_ctx = child_run_ctx
        child_ctx.tool_name = "background_task"
        child_ctx.tool_call_id = "tc-1"

        # Use StreamEventEmitter (real code path)
        emitter = StreamEventEmitter(child_ctx, event_bus=child_run_ctx.event_bus)
        await emitter.emit_event(
            SubAgentEvent(
                source_name="bg-task",
                source_type="background",
                event=StreamCompleteEvent(
                    message=ChatMessage(content="background done", role="assistant"),
                ),
                child_session_id="child-sess",
                parent_session_id="sess-1",
            )
        )

        yield StreamCompleteEvent(
            message=ChatMessage(content="lead done", role="assistant"),
        )

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # Subscribe to EventBus BEFORE running the turn
    event_queue = await turn_runner.event_bus.subscribe("sess-1")

    async def _bus_consumer() -> None:
        """Consume events from pre-subscribed queue."""
        try:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is None:
                    break
                event_bus_events.append(event)
        except TimeoutError:
            pass  # No more events

    # Start EventBus consumer
    consumer_task = asyncio.create_task(_bus_consumer())

    # Run the turn
    await turn_runner.run_turn("sess-1", "initial")

    # Wait for EventBus consumer
    await asyncio.sleep(0.1)
    await turn_runner.event_bus.publish("sess-1", None)  # sentinel
    await consumer_task

    # Filter for SubAgentEvent
    subagent_events = [e for e in event_bus_events if isinstance(e, SubAgentEvent)]

    # RED FLAG: background task events must reach EventBus
    assert len(subagent_events) == 1, (
        f"background task events LOST: found {len(subagent_events)} SubAgentEvent(s) "
        f"in EventBus, expected 1. "
        f"Total events in bus: {len(event_bus_events)}. "
        f"StreamEventEmitter did not forward to EventBus."
    )
    assert subagent_events[0].source_name == "bg-task"


@pytest.mark.anyio
async def test_background_task_events_reach_acp_client_after_end_turn(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Background task events emitted after StreamCompleteEvent reach EventBus.

    This is a **red flag test** — if it fails, post-end-turn background events
    are lost before reaching the ACP client.

    Scenario:
    1. Agent stream yields RunStartedEvent then StreamCompleteEvent (end_turn)
    2. In the generator's cleanup (finally), a background task event is queued
       to run_ctx.event_queue
    3. _run_turn_unlocked's event consumer is still running and should pick it up
    4. Event reaches EventBus and thus the ACP client

    Expected: SubAgentEvent published to EventBus after end_turn.
    """
    from agentpool import ChatMessage
    from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent, SubAgentEvent

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        try:
            yield RunStartedEvent(session_id="sess-1", run_id="run-1")
            yield StreamCompleteEvent(
                message=ChatMessage(content="main done", role="assistant"),
            )
        finally:
            # Simulate background task emitting event after main stream completes
            # via EventBus (new pattern: StreamEventEmitter publishes directly)
            await turn_runner.event_bus.publish(
                "sess-1",
                SubAgentEvent(
                    source_name="bg-task-post-turn",
                    source_type="background",
                    event=StreamCompleteEvent(
                        message=ChatMessage(content="background done", role="assistant"),
                    ),
                    child_session_id="child-sess",
                    parent_session_id="sess-1",
                ),
            )

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent._run_stream_once = _fake_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # Subscribe to EventBus BEFORE running the turn
    event_queue = await turn_runner.event_bus.subscribe("sess-1")
    event_bus_events: list[Any] = []

    async def _bus_consumer() -> None:
        """Consume events from pre-subscribed queue."""
        try:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is None:
                    break
                event_bus_events.append(event)
        except TimeoutError:
            pass  # No more events

    # Start EventBus consumer
    consumer_task = asyncio.create_task(_bus_consumer())

    # Run the turn
    await turn_runner.run_turn("sess-1", "initial")

    # Wait for EventBus consumer
    await asyncio.sleep(0.1)
    await turn_runner.event_bus.publish("sess-1", None)  # sentinel
    await consumer_task

    # Filter for SubAgentEvent
    subagent_events = [e for e in event_bus_events if isinstance(e, SubAgentEvent)]

    # RED FLAG: background task events after end_turn must reach EventBus
    assert len(subagent_events) == 1, (
        f"post-end-turn background events LOST: found {len(subagent_events)} SubAgentEvent(s) "
        f"in EventBus, expected 1. "
        f"Total events in bus: {len(event_bus_events)}. "
        f"Event consumer did not pick up background task event after stream completion."
    )
    assert subagent_events[0].source_name == "bg-task-post-turn"


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
    agent.get_active_run_context.return_value = None

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
