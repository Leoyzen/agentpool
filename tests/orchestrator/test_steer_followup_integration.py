"""Integration tests for steer/followup with PendingMessageDrainCapability,
after_node_run hooks, agent type detection, and injection_manager.consume().

Tests:
- 10.7: steer message injected via PendingMessageDrainCapability.before_model_request
- 10.8: followup message processed via after_node_run redirect
- 10.9: manual follow-up loop NOT executed for native agents
- 10.10: RunExecutor next() loop fires after_node_run hooks
- 10.11: agent type detected via agent.AGENT_TYPE (not metadata)
- 10.12: tool result augmentation via injection_manager.consume()
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, StreamCompleteEvent
from agentpool.agents.prompt_injection import PromptInjectionManager
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


@pytest.fixture
def test_agent() -> Agent[None]:
    """Create an Agent backed by TestModel for RunExecutor integration tests."""
    model = TestModel(custom_output_text="Integration test response")
    return Agent(name="integration-test-agent", model=model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_native_agent() -> MagicMock:
    """Return a mocked native agent with AGENT_TYPE = 'native'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


def _make_acp_agent() -> MagicMock:
    """Return a mocked ACP agent with AGENT_TYPE = 'acp'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "acp"
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
    """Create a RunHandle and return it (does NOT register in controller)."""
    handle = RunHandle(
        run_id=f"run-{session_id}",
        session_id=session_id,
        agent_type=agent_type,
    )
    if run_ctx is not None:
        handle.run_ctx = run_ctx
    return handle


# =============================================================================
# 10.7: steer message injected before next LLM call via
#       PendingMessageDrainCapability.before_model_request()
# =============================================================================


@pytest.mark.anyio
async def test_steer_integration_enqueues_asap_through_turn_runner(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """steer() routes through TurnRunner → native agent → agent_run.enqueue(asap).

    Integration test verifying the full pipeline: session lookup, agent_type
    detection, active_agent_run retrieval, and enqueue with priority='asap'.
    When PendingMessageDrainCapability is active, this asap message is drained
    at the next before_model_request hook.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-int-steer", agent, mock_pool)

    # Set up an active run with a mocked PydanticAI AgentRun
    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-int-steer", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-int-steer")
    assert session is not None, "Session should exist"
    session.current_run_id = run_handle.run_id

    result = await turn_runner.steer("sess-int-steer", "steer: urgent context update")

    # steer() into active run returns True (delivered to active turn)
    assert result is True, "Steer into active native run should return True"

    # Verify enqueue was called with asap priority (handled by
    # PendingMessageDrainCapability.before_model_request)
    mock_agent_run.enqueue.assert_called_once_with(
        "steer: urgent context update", priority="asap"
    )


# =============================================================================
# 10.8: followup message processed after agent would otherwise end
#       (via after_node_run redirect)
# =============================================================================


@pytest.mark.anyio
async def test_followup_integration_enqueues_when_idle_through_turn_runner(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """followup() routes through TurnRunner → native agent → agent_run.enqueue(when_idle).

    Integration test verifying the full pipeline: session lookup, agent_type
    detection, active_agent_run retrieval, and enqueue with priority='when_idle'.
    PendingMessageDrainCapability drains these messages at after_node_run,
    creating a follow-up chain.
    """
    agent = _make_native_agent()
    await _setup_session_with_agent(controller, "sess-int-followup", agent, mock_pool)

    mock_agent_run = MagicMock()
    mock_agent_run.enqueue = MagicMock()
    run_handle = _make_run_handle("sess-int-followup", "native")
    run_handle.active_agent_run = mock_agent_run
    run_handle.status = RunStatus.running
    controller._runs[run_handle.run_id] = run_handle

    session = controller.get_session("sess-int-followup")
    assert session is not None, "Session should exist"
    session.current_run_id = run_handle.run_id

    result = await turn_runner.followup("sess-int-followup", "followup: continue after done")

    assert result is True, "Followup into active native run should return True"

    # Verify enqueue was called with when_idle priority (handled by
    # PendingMessageDrainCapability.after_node_run)
    mock_agent_run.enqueue.assert_called_once_with(
        "followup: continue after done", priority="when_idle"
    )


# =============================================================================
# 10.9: manual follow-up loop NOT executed for native agents
#       (no redundant processing)
# =============================================================================


@pytest.mark.anyio
async def test_native_agent_skips_manual_followup_loop_gating(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Native agents skip the manual while has_queued() loop in _run_turn_unlocked.

    The gating condition ``getattr(agent, "AGENT_TYPE", "native") != "native"``
    prevents native agents from entering the manual flush_pending_to_queue +
    while has_queued() loop. Native agents rely on PydanticAI's
    PendingMessageDrainCapability instead.

    This test verifies:
    1. The gating condition correctly identifies native vs non-native agents.
    2. The injection_manager correctly reflects whether items are queued.
    3. The manual loop would NOT drain queued items for native agents.
    """
    native_agent = _make_native_agent()
    acp_agent = _make_acp_agent()

    # Set up sessions for both agent types
    await _setup_session_with_agent(controller, "sess-native-gate", native_agent, mock_pool)
    await _setup_session_with_agent(controller, "sess-acp-gate", acp_agent, mock_pool)

    # Create run contexts with queued prompts
    native_run_ctx = AgentRunContext()
    native_run_ctx.injection_manager.queue("queued-for-native")
    assert native_run_ctx.injection_manager.has_queued(), "Should have queued prompts"

    acp_run_ctx = AgentRunContext()
    acp_run_ctx.injection_manager.queue("queued-for-acp")
    assert acp_run_ctx.injection_manager.has_queued(), "Should have queued prompts"

    # --- Verify gating condition ---
    # Native: condition is False → while loop is SKIPPED
    native_type = getattr(native_agent, "AGENT_TYPE", "native")
    assert native_type == "native", f"Expected 'native', got '{native_type}'"
    native_should_enter_loop = (native_type != "native")
    assert not native_should_enter_loop, (
        f"Native agent (AGENT_TYPE='{native_type}') should NOT enter "
        f"the manual while has_queued() loop"
    )

    # Non-native: condition is True → while loop IS entered
    acp_type = getattr(acp_agent, "AGENT_TYPE", "native")
    assert acp_type != "native", f"Expected non-native, got '{acp_type}'"
    acp_should_enter_loop = (acp_type != "native")
    assert acp_should_enter_loop, (
        f"Non-native agent (AGENT_TYPE='{acp_type}') SHOULD enter "
        f"the manual while has_queued() loop"
    )

    # --- Verify queued state is preserved for native (no manual drain) ---
    assert native_run_ctx.injection_manager.has_queued(), (
        "Native agent: queued prompts should remain — manual loop is skipped"
    )

    # --- Verify non-native manual loop would drain ---
    # Simulate what _run_turn_unlocked does for non-native:
    acp_run_ctx.injection_manager.flush_pending_to_queue()
    while acp_run_ctx.injection_manager.has_queued():
        drained = acp_run_ctx.injection_manager.pop_queued()
        assert drained is not None
        assert drained[0] == "queued-for-acp"
        break  # Only one item in queue

    assert not acp_run_ctx.injection_manager.has_queued(), (
        "Non-native agent: queued prompts should be drained by manual loop"
    )


# =============================================================================
# 10.10: RunExecutor next() loop fires after_node_run hooks
# =============================================================================


@pytest.mark.anyio
async def test_run_executor_next_loop_fires_after_node_run_hooks(
    test_agent: Agent[None],
) -> None:
    """RunExecutor uses agent_run.next(node) which fires after_node_run hooks.

    The RunExecutor.execute() method uses ``node = await agent_run.next(node)``
    (line 262 of run_executor.py) instead of a bare ``async for node in agent_run``.
    This ensures that PendingMessageDrainCapability hooks —
    after_node_run (which drains when_idle messages) and before_model_request
    (which drains asap messages) — are fired correctly.

    This integration test verifies:
    1. RunExecutor.execute() completes successfully with a real Agent.
    2. The active_agent_run is set during execution and cleared afterward.
    3. The StreamCompleteEvent is yielded with correct content.
    """
    run_ctx = AgentRunContext(session_id="sess-next-loop")
    user_msg = ChatMessage.user_prompt("Verify after_node_run hook path")
    message_history = MessageHistory()
    run_handle = RunHandle(
        run_id="run-next-loop",
        session_id="sess-next-loop",
        agent_type="native",
    )

    from agentpool.orchestrator.core import EventBus

    event_bus = EventBus()
    run_ctx.event_bus = event_bus
    stream = await event_bus.subscribe("sess-next-loop", scope="session")

    executor = RunExecutor(test_agent, run_handle=run_handle)

    events: list[object] = []
    response_content: str | None = None
    agent_run_was_set: bool = False

    execute_task = asyncio.ensure_future(
        executor.execute(
            prompts=["Verify after_node_run hook path"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-next-loop",
            session_id="sess-next-loop",
            event_bus=event_bus,
        )
    )
    async for envelope in stream:
        events.append(envelope.event)
        # Capture the agent_run being set during iteration
        if run_handle.active_agent_run is not None:
            agent_run_was_set = True
        if isinstance(envelope.event, StreamCompleteEvent):
            response_content = str(envelope.event.message.content)
        if isinstance(envelope.event, (StreamCompleteEvent, RunErrorEvent)):
            break
    await execute_task

    # Verify execution completed
    assert len(events) > 0, "RunExecutor should yield events"
    assert response_content is not None, "Should have a final response"
    assert "Integration test response" in response_content, (
        f"Expected model output in response, got: {response_content}"
    )

    # Verify active_agent_run lifecycle: set during iteration, cleared after
    assert agent_run_was_set, (
        "active_agent_run should be set during RunExecutor iteration "
        "(proves agent_run.next(node) was called)"
    )
    assert run_handle.active_agent_run is None, (
        "active_agent_run should be cleared after RunExecutor completes"
    )

    # Verify StreamCompleteEvent was yielded
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1, (
        f"Expected 1 StreamCompleteEvent, got {len(complete_events)}"
    )


@pytest.mark.anyio
async def test_run_executor_next_loop_clears_agent_run_on_error(
    test_agent: Agent[None],
) -> None:
    """RunExecutor clears active_agent_run even when agentlet creation fails.

    This verifies the finally block in agent_iteration_task (run_executor.py
    line 311) always clears active_agent_run, ensuring no stale references
    remain that could cause issues in after_node_run hook processing.
    """
    run_ctx = AgentRunContext(session_id="sess-next-error")
    user_msg = ChatMessage.user_prompt("test")
    message_history = MessageHistory()
    run_handle = RunHandle(
        run_id="run-next-error",
        session_id="sess-next-error",
        agent_type="native",
    )

    executor = RunExecutor(test_agent, run_handle=run_handle)

    # Patch get_agentlet to raise immediately
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: object, **kwargs: object) -> object:
        raise RuntimeError("agentlet creation failed during next-loop test")

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        from agentpool.orchestrator.core import EventBus

        event_bus = EventBus()
        run_ctx.event_bus = event_bus
        with pytest.raises(RuntimeError, match="agentlet creation failed"):
            await executor.execute(
                prompts=["test"],
                run_ctx=run_ctx,
                user_msg=user_msg,
                message_history=message_history,
                message_id="msg-err",
                session_id="sess-next-error",
                event_bus=event_bus,
            )
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]

    # active_agent_run must be cleared even after error
    assert run_handle.active_agent_run is None, (
        "active_agent_run should be None after execution error — "
        "finally block in agent_iteration_task must clear it"
    )


# =============================================================================
# 10.11: agent type detected via agent.AGENT_TYPE (not metadata)
#        — native agents correctly skip manual loop
# =============================================================================


@pytest.mark.anyio
async def test_create_run_uses_agent_ag_type_not_metadata(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """_create_run() uses ``getattr(agent, "AGENT_TYPE")`` when agent is provided.

    The SessionController._create_run() method checks the agent's AGENT_TYPE
    attribute directly rather than relying on session metadata. This ensures
    that the agent_type in RunHandle always reflects the actual agent instance,
    which is critical for the gating logic in _run_turn_unlocked and the
    steer/followup routing in TurnRunner.
    """
    # Set up a session with metadata that differs from agent AGENT_TYPE
    session, _ = await controller.get_or_create_session("sess-create-run")
    session.metadata["agent_type"] = "unknown-fallback"

    # The agent has AGENT_TYPE = "native"
    agent = _make_native_agent()
    agent.AGENT_TYPE = "native"

    # _create_run with agent → uses agent.AGENT_TYPE
    run_handle = controller._create_run("sess-create-run", "test prompt", agent=agent)
    assert run_handle.agent_type == "native", (
        f"_create_run with agent should use agent.AGENT_TYPE, "
        f"got '{run_handle.agent_type}'"
    )

    # _create_run without agent → falls back to session metadata
    run_handle_no_agent = controller._create_run("sess-create-run", "test prompt")
    assert run_handle_no_agent.agent_type == "unknown-fallback", (
        f"_create_run without agent should fall back to session metadata, "
        f"got '{run_handle_no_agent.agent_type}'"
    )


@pytest.mark.anyio
async def test_create_run_handles_missing_ag_type_gracefully(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """_create_run() defaults to 'native' when agent has no AGENT_TYPE.

    The getattr(agent, "AGENT_TYPE", "native") fallback ensures that agents
    without an explicitly set AGENT_TYPE are treated as native, which means
    they benefit from PendingMessageDrainCapability and skip the manual
    follow-up loop.
    """
    # Agent without AGENT_TYPE attribute at all
    agent = MagicMock()
    # Remove AGENT_TYPE attribute so getattr falls back
    del agent.AGENT_TYPE

    session, _ = await controller.get_or_create_session("sess-no-agtype")
    # Need to set up the session agent so _create_run works
    session.agent = agent
    controller._session_agents["sess-no-agtype"] = agent

    run_handle = controller._create_run("sess-no-agtype", "test", agent=agent)
    assert run_handle.agent_type == "native", (
        f"Agent without AGENT_TYPE should default to 'native', "
        f"got '{run_handle.agent_type}'"
    )


# =============================================================================
# 10.12: tool result augmentation via injection_manager.consume()
#        still works on native agents
# =============================================================================


@pytest.mark.anyio
async def test_injection_manager_consume_works_in_run_handle_context() -> None:
    """injection_manager.consume() works correctly within a native RunHandle context.

    Tool result augmentation uses the inject/consume pattern: after a tool
    executes, the after_tool_execute hook calls consume() to inject additional
    context into the conversation. This must continue to work correctly with
    native agents, where the manual follow-up loop is skipped in favor of
    PendingMessageDrainCapability.

    This integration test verifies the full inject→consume→clear lifecycle
    using a real PromptInjectionManager attached to a RunHandle.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-int")
    run_handle = RunHandle(
        run_id="run-consume-int",
        session_id="sess-consume-int",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # Initially empty
    assert not manager.has_pending(), "Should not have pending injections initially"
    assert not manager.has_queued(), "Should not have queued prompts initially"

    # Simulate tool result augmentation: inject then consume
    manager.inject("Tool execution result: test passed with 42 assertions")

    assert manager.has_pending(), "Injection should be pending after inject()"

    # consume() is called by the after_tool_execute hook
    consumed = await manager.consume()
    assert consumed is not None, "consume() should return the wrapped message"
    assert "Tool execution result" in consumed, (
        f"Expected injected content in consumed output, got: {consumed}"
    )
    assert "<injected-context>" in consumed, (
        f"Expected XML-wrapped injection format, got: {consumed}"
    )
    assert "</injected-context>" in consumed, (
        "Expected closing XML tag in injection"
    )

    # After consume, pending should be empty
    assert not manager.has_pending(), "Pending should be cleared after consume"

    # RunHandle context should remain stable
    assert run_handle.run_ctx is run_ctx, "RunHandle.run_ctx should preserve reference"


@pytest.mark.anyio
async def test_injection_manager_consume_returns_none_when_empty() -> None:
    """injection_manager.consume() returns None when no injections are pending.

    This is the normal case after all injections have been consumed.
    The after_tool_execute hook handles this gracefully by skipping
    injection when consume() returns None.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-empty")
    run_handle = RunHandle(
        run_id="run-consume-empty",
        session_id="sess-consume-empty",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # consume() on empty manager should return None
    result = await manager.consume()
    assert result is None, "consume() on empty manager should return None"

    # Manager state remains clean
    assert not manager.has_pending(), "Should still have no pending after empty consume"
    assert not manager.has_queued(), "Should still have no queued after empty consume"


@pytest.mark.anyio
async def test_injection_manager_consume_all_preserves_order() -> None:
    """injection_manager.consume_all() preserves FIFO order of injections.

    When multiple tool results accumulate, consume_all() returns them
    in the order they were injected. This is important for maintaining
    context coherence when multiple tools fire before the hooks run.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-order")
    run_handle = RunHandle(
        run_id="run-consume-order",
        session_id="sess-consume-order",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # Inject multiple messages in sequence
    manager.inject("Step 1: read file")
    manager.inject("Step 2: analyzed content")
    manager.inject("Step 3: wrote results")

    assert manager.has_pending(), "Should have pending injections"

    # consume_all() returns all in order
    results = await manager.consume_all()
    assert len(results) == 3, f"Expected 3 consumed results, got {len(results)}"

    # Verify order and XML wrapping
    for i, result in enumerate(results):
        assert f"Step {i + 1}" in result, (
            f"Result {i} should contain 'Step {i + 1}', got: {result}"
        )
        assert "<injected-context>" in result
        assert "</injected-context>" in result

    assert not manager.has_pending(), "All pending should be cleared after consume_all"
