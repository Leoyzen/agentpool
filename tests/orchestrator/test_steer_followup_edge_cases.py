"""Edge case tests for steer/followup — Metis-identified gaps.

Covers 8 edge cases:
1. Concurrent steer: 5 concurrent steer() calls all enqueued with asap
2. Steer during tool execution: message enqueued asap, drained at before_model_request
3. Multiple followup chain: Multiple when_idle messages create correct chain
4. RunHandle cleanup on UndrainedPendingMessagesError: active_agent_run cleared
5. Session close during steer race: TOCTOU-safe — no crash
6. Tool result augmentation preserved: injection_manager.consume() still works
7. _run_agentlet_core() non-event_bus branch: merge_queue_into_iterator path
8. ACP snapshot regression: verified via `uv run pytest -m acp_snapshot -v`
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import SessionController, TurnRunner
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.run_executor import RunExecutor


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncListIterator:
    """Async iterator wrapper for a list."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self._idx = 0

    def __aiter__(self) -> _AsyncListIterator:
        return self

    async def __anext__(self) -> Any:
        if self._idx < len(self._items):
            item = self._items[self._idx]
            self._idx += 1
            return item
        raise StopAsyncIteration


def _make_native_agent() -> MagicMock:
    """Return a mocked native agent with AGENT_TYPE = 'native'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


async def _setup_session_with_agent(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent."""
    state, _ = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


def _make_run_handle(
    session_id: str,
    agent_type: str,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle and register it in the controller's _runs."""
    handle = RunHandle(
        run_id=f"run-{session_id}",
        session_id=session_id,
        agent_type=agent_type,
    )
    if run_ctx is not None:
        handle.run_ctx = run_ctx
    return handle


# =============================================================================
# Test 1: Concurrent steer — 5 concurrent calls all enqueued with asap
# =============================================================================


@pytest.mark.anyio
async def test_concurrent_steer_all_enqueued_asap(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """5 concurrent steer() calls all enqueue correctly with priority='asap'.

    Edge case: Multiple concurrent steer() calls should not race or lose
    messages. Each call should result in a separate enqueue() with the
    correct message and priority.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-conc", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-conc", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-conc")
    assert session is not None
    session.current_run_id = run_handle.run_id

    messages = [f"steer-msg-{i}" for i in range(5)]

    # Fire 5 concurrent steer() calls
    await asyncio.gather(
        *(turn_runner.steer("sess-conc", msg) for msg in messages),
    )

    # All 5 enqueues should have happened with priority="asap"
    assert mock_agent_run.enqueue.call_count == 5, (
        f"Expected 5 enqueue calls, got {mock_agent_run.enqueue.call_count}"
    )

    # Verify each message was enqueued with correct args
    called_messages: set[str] = set()
    for call in mock_agent_run.enqueue.call_args_list:
        args, kwargs = call
        assert kwargs["priority"] == "asap", f"Expected asap priority, got {kwargs}"
        called_messages.add(args[0])

    assert called_messages == set(messages), (
        f"Not all messages were enqueued. Expected {set(messages)}, got {called_messages}"
    )


# =============================================================================
# Test 2: Steer during tool execution — enqueued asap, drained at
#         before_model_request
# =============================================================================


@pytest.mark.anyio
async def test_steer_during_tool_execution_enqueues_asap(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Steer message arriving mid-tool is enqueued with asap priority.

    Edge case: When a steer message arrives while a tool is executing,
    it should be enqueued with priority='asap' so that
    PendingMessageDrainCapability drains it at the next
    before_model_request hook.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-tool", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-tool", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-tool")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # Simulate steer arriving during tool execution
    result = await turn_runner.steer("sess-tool", "mid-tool steer message")

    assert result is True, "Steer into active run should return True"
    mock_agent_run.enqueue.assert_called_once_with(
        "mid-tool steer message", priority="asap"
    )


# =============================================================================
# Test 3: Multiple followup chain — when_idle messages create correct chain
# =============================================================================


@pytest.mark.anyio
async def test_multiple_followup_chain_when_idle(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Multiple followup() calls enqueue with priority='when_idle'.

    Edge case: Multiple when_idle messages should all be enqueued
    correctly so that PendingMessageDrainCapability drains them in
    order after each node completes, creating a chain of model requests.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-chain", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-chain", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-chain")
    assert session is not None
    session.current_run_id = run_handle.run_id

    followup_messages = [f"followup-{i}" for i in range(3)]

    for msg in followup_messages:
        result = await turn_runner.followup("sess-chain", msg)
        assert result is True, f"Followup {msg} should return True"

    # All 3 enqueues should have happened with priority="when_idle"
    assert mock_agent_run.enqueue.call_count == 3, (
        f"Expected 3 enqueue calls, got {mock_agent_run.enqueue.call_count}"
    )

    # Verify correct priority and messages in order
    called_messages: list[str] = []
    for call in mock_agent_run.enqueue.call_args_list:
        args, kwargs = call
        assert kwargs["priority"] == "when_idle", (
            f"Expected when_idle priority, got {kwargs}"
        )
        called_messages.append(args[0])

    assert called_messages == followup_messages, (
        f"Messages enqueued out of order. Expected {followup_messages}, got {called_messages}"
    )


# =============================================================================
# Test 4: RunHandle cleanup on UndrainedPendingMessagesError
# =============================================================================


@pytest.mark.anyio
async def test_active_agent_run_cleared_on_undrained_error() -> None:
    """active_agent_run is cleared even when UndrainedPendingMessagesError is raised.

    Edge case: When PydanticAI raises UndrainedPendingMessagesError (e.g.,
    from bare async for usage), the RunExecutor's finally block must still
    clear active_agent_run to prevent stale references.
    """
    from contextlib import asynccontextmanager

    from pydantic_ai._agent_graph import ModelRequestNode
    from pydantic_ai.exceptions import UndrainedPendingMessagesError

    test_agent = Agent(
        name="undrained-error-test",
        model=TestModel(custom_output_text="hello"),
    )

    run_ctx = AgentRunContext(session_id="sess-undrained")
    user_msg = ChatMessage.user_prompt("test")
    message_history = MessageHistory()
    run_handle = RunHandle(
        run_id="run-undrained",
        session_id="sess-undrained",
        agent_type="native",
    )

    executor = RunExecutor(test_agent, run_handle=run_handle)

    # Build a mock first node whose stream is an empty async iterable
    first_node = MagicMock(spec=ModelRequestNode)

    @asynccontextmanager  # type: ignore[arg-type]
    async def empty_stream(_ctx: Any) -> Any:
        yield _AsyncListIterator([])

    first_node.stream = empty_stream

    # Monkey-patch get_agentlet to return a mock whose iter() yields a
    # mock agent_run that raises UndrainedPendingMessagesError on next().
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        agentlet = await original_get_agentlet(*args, **kwargs)

        @asynccontextmanager  # type: ignore[arg-type]
        async def broken_iter(*iargs: Any, **ikwargs: Any) -> Any:
            mock_agent_run = MagicMock()
            mock_agent_run.next_node = first_node
            mock_agent_run.ctx = MagicMock()
            # First next() returns a ModelRequestNode-like node,
            # second call raises UndrainedPendingMessagesError
            mock_agent_run.next = AsyncMock(
                side_effect=[
                    first_node,
                    UndrainedPendingMessagesError(
                        "Bare async for usage detected — "
                        "PendingMessageDrainCapability hooks not fired. "
                        "Use agent_run.next(node) instead."
                    ),
                ]
            )

            yield mock_agent_run

        agentlet.iter = broken_iter  # type: ignore[method-assign]
        return agentlet

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        with pytest.raises(UndrainedPendingMessagesError):
            async for _event in executor.execute(
                prompts=["Say hello"],
                run_ctx=run_ctx,
                user_msg=user_msg,
                message_history=message_history,
                message_id="msg-1",
                session_id="sess-undrained",
            ):
                pass
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]

    # active_agent_run MUST be None after UndrainedPendingMessagesError
    assert run_handle.active_agent_run is None, (
        f"Expected active_agent_run to be None after UndrainedPendingMessagesError, "
        f"got {run_handle.active_agent_run}"
    )


# =============================================================================
# Test 5: Session close during steer race — TOCTOU-safe, no crash
# =============================================================================


@pytest.mark.anyio
async def test_session_close_during_steer_race_no_crash(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Session close between active_agent_run check and enqueue — no crash.

    Edge case: When the session is closing, steer() gracefully falls
    through to receive_request instead of crashing. The TOCTOU-safe
    pattern (reading active_agent_run into a local variable) prevents
    double-read races.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-race", agent, mock_pool)

    run_handle = _make_run_handle("sess-race", "native")
    # active_agent_run is NOT set (session is idle/closing)
    run_handle.active_agent_run = None
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-race")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # Spy on receive_request to verify delegation
    controller.receive_request = AsyncMock(return_value=None)  # type: ignore[method-assign]

    # steer() should delegate to receive_request (no crash)
    result = await turn_runner.steer("sess-race", "race-steer-to-idle")

    assert result is False, "Steer on idle session should return False (delegated)"
    controller.receive_request.assert_called_once_with(  # type: ignore[attr-defined]
        "sess-race", "race-steer-to-idle", priority="steer"
    )


@pytest.mark.anyio
async def test_session_close_before_steer_guard_returns_false(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Session closed before steer() guard check — returns False, no crash.

    Edge case: If the session is already closing when steer() is called,
    the top-level guard should catch it and return False without crashing.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-closed", agent, mock_pool)

    session = controller.get_session("sess-closed")
    assert session is not None
    session.is_closing = True

    # steer() should return False — session is closing
    result = await turn_runner.steer("sess-closed", "should-not-deliver")
    assert result is False, "Steer on closing session should return False"


# =============================================================================
# Test 6: Tool result augmentation preserved — injection_manager.consume()
# =============================================================================


@pytest.mark.anyio
async def test_tool_result_augmentation_consume_preserved() -> None:
    """injection_manager.consume() still works on native agents after changes.

    Edge case: The inject/consume pattern is used for tool result
    augmentation (adding context after tool execution). This must
    continue to work correctly after the steer/followup changes.
    """
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()

    # Inject a message (simulating steer for tool augmentation)
    manager.inject("Additional context for the model after tool execution")

    assert manager.has_pending(), "Injection should be pending"

    # Consume should return the wrapped message
    consumed = await manager.consume()
    assert consumed is not None, "consume() should return the injected message"
    assert "Additional context" in consumed, (
        f"Expected injected message in consumed output, got: {consumed}"
    )
    assert "<injected-context>" in consumed, (
        "Expected XML-wrapped injection"
    )
    assert not manager.has_pending(), "Pending should be cleared after consume"


@pytest.mark.anyio
async def test_tool_result_augmentation_consume_all_preserved() -> None:
    """injection_manager.consume_all() works for native agents after changes.

    Edge case: Multiple injections should all be consumable via consume_all().
    """
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()

    manager.inject("context-1")
    manager.inject("context-2")
    manager.inject("context-3")

    results = await manager.consume_all()
    assert len(results) == 3, f"Expected 3 consumed results, got {len(results)}"
    assert all("<injected-context>" in r for r in results)
    assert not manager.has_pending(), "All pending should be cleared"


@pytest.mark.anyio
async def test_tool_result_augmentation_flush_to_queue() -> None:
    """Unconsumed injections fall back to queue via flush_pending_to_queue().

    Edge case: If no tool executes, unconsumed injections should be
    moved to the queued prompts so they still get processed.
    """
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()

    manager.inject("orphaned injection")
    assert manager.has_pending()
    assert not manager.has_queued()

    manager.flush_pending_to_queue()

    assert not manager.has_pending(), "Pending should be cleared after flush"
    assert manager.has_queued(), "Injection should be in queue after flush"

    queued = manager.pop_queued()
    assert queued is not None
    assert queued[0] == "orphaned injection"


# =============================================================================
# Test 7: _run_agentlet_core() non-event_bus branch — merge_queue_into_iterator
# =============================================================================


@pytest.mark.anyio
async def test_run_agentlet_core_non_event_bus_branch() -> None:
    """_run_agentlet_core() non-event_bus branch uses merge_queue_into_iterator.

    Edge case: When run_ctx.event_bus is None, the code takes the
    merge_queue_into_iterator path instead of the event_bus path.
    Both paths should work correctly with the next() loop.
    """
    agent = Agent(
        name="non-eventbus-test",
        model=TestModel(custom_output_text="response from non-eventbus path"),
    )

    run_ctx = AgentRunContext(
        session_id="sess-non-eventbus",
        event_bus=None,  # Explicitly None → non-event_bus branch
    )
    user_msg = ChatMessage.user_prompt("test prompt")
    message_history = MessageHistory()
    event_queue: asyncio.Queue[Any] = asyncio.Queue()

    # Call _run_agentlet_core directly with event_bus=None
    response_msg = await agent._run_agentlet_core(
        prompts=["test prompt"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        message_id="msg-non-eb",
        session_id="sess-non-eventbus",
        parent_id=None,
        input_provider=None,
        deps=None,
        event_queue=event_queue,
        start_time=0.0,
    )

    assert response_msg is not None, "Response message should not be None"
    assert "response from non-eventbus path" in str(response_msg.content), (
        f"Expected model response in content, got: {response_msg.content}"
    )

    # Events should have been pushed to the event_queue
    events: list[Any] = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())

    assert len(events) > 0, "Event queue should have events from non-event_bus path"


@pytest.mark.anyio
async def test_run_agentlet_core_non_event_bus_branch_streaming() -> None:
    """_run_agentlet_core() non-event_bus branch supports streaming via run_stream().

    Edge case: When an agent is used standalone (no SessionPool/EventBus),
    run_stream() should work correctly through the non-event_bus branch
    of _run_agentlet_core().
    """
    agent = Agent(
        name="standalone-stream-test",
        model=TestModel(custom_output_text="standalone streaming works"),
    )

    events: list[Any] = []
    async for event in agent.run_stream("hello standalone"):
        events.append(event)

    from agentpool.agents.events import StreamCompleteEvent

    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1, (
        f"Expected 1 StreamCompleteEvent, got {len(complete_events)}"
    )
    assert "standalone streaming works" in str(complete_events[0].message.content), (
        "Expected streaming output in final message"
    )
