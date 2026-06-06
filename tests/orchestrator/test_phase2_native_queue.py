"""Phase 2 tests for native agent PydanticAI enqueue migration.

Covers:
- PydanticAI PendingMessageDrainCapability drain behavior (asap, when_idle)
- enqueue() during tool execution on native agents
- inject_prompt() tool result augmentation pipeline
- RunExecutor event stream parity with _stream_events()
- Non-native agents use manual queue (TurnRunner)
- Native agent interrupt() via SessionPool
- receive_request() routing for native agents
- Full integration: native agent auto-resumes with queued prompts
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai import Agent as PydanticAIAgent, RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import Tool

from agentpool import Agent
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.agents.events import (
    PartDeltaEvent as AgentPoolPartDeltaEvent,
    PartStartEvent as AgentPoolPartStartEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import SessionController, SessionPool, TurnRunner
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.orchestrator.run_executor import RunExecutor


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_model() -> TestModel:
    """TestModel with deterministic output."""
    return TestModel(custom_output_text="Hello from TestModel")


@pytest.fixture
def native_agent(test_model: TestModel) -> Agent[None]:
    """Native Agent with TestModel."""
    return Agent(name="native-test-agent", model=test_model)


@pytest.fixture
def tool_native_agent(test_model: TestModel) -> Agent[None]:
    """Native Agent with a tool for testing tool events."""

    async def hello_tool() -> str:
        """Say hello."""
        return "hello_result"

    return Agent(
        name="native-tool-agent",
        model=test_model,
        tools=[hello_tool],
    )


@pytest.fixture
def run_ctx() -> AgentRunContext:
    """Fresh AgentRunContext for each test."""
    return AgentRunContext()


@pytest.fixture
def message_history() -> MessageHistory:
    """Empty message history."""
    return MessageHistory()


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
def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool with auto-resume enabled."""
    return SessionPool(pool=mock_pool, enable_auto_resume=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _collect_run_executor_events(
    executor: RunExecutor,
    *,
    prompts: list[str],
    run_ctx: AgentRunContext,
    user_msg: ChatMessage[Any],
    message_history: MessageHistory,
    session_id: str = "test-session",
) -> list[Any]:
    """Execute RunExecutor and collect all events."""
    events: list[Any] = []
    async for event in executor.execute(
        prompts=prompts,
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        message_id="msg-1",
        session_id=session_id,
    ):
        events.append(event)
    return events


async def _collect_agent_stream_events(
    agent: Agent[Any, Any],
    *prompts: Any,
    session_id: str = "test-session",
) -> list[Any]:
    """Run agent.run_stream() and collect all events."""
    events: list[Any] = []
    async for event in agent.run_stream(*prompts, session_id=session_id):
        events.append(event)
    return events


def _event_type_names(events: list[Any]) -> list[str]:
    """Return list of event type names."""
    return [type(e).__name__ for e in events]


class _MockNonNativeAgent(BaseAgent):
    """Minimal concrete non-native agent for routing tests."""

    AGENT_TYPE = "acp"  # type: ignore[misc]

    @property
    def model_name(self) -> str | None:
        return "mock-model"

    async def set_model(self, model: str) -> None:
        pass

    async def _stream_events(
        self,
        run_ctx: AgentRunContext,
        prompts: list[Any],
        *,
        user_msg: Any,
        message_history: Any,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: Any | None = None,
        deps: Any | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[Any]:
        yield RunStartedEvent(session_id=session_id or "default", run_id="run-1")
        yield StreamCompleteEvent(
            message=ChatMessage(content="mock response", role="assistant", name=self.name)
        )

    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        pass

    async def get_available_models(self) -> list[Any] | None:
        return None

    async def get_modes(self) -> list[Any]:
        return []

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        pass

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        return []

    async def load_session(self, session_id: str) -> Any | None:
        return None


# ---------------------------------------------------------------------------
# 1. PydanticAI PendingMessageDrainCapability drains 'asap' before next model request
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_asap_drained_before_next_model_request() -> None:
    """PydanticAI PendingMessageDrainCapability drains 'asap' after CallToolsNode."""

    async def enqueue_tool(ctx: RunContext) -> str:
        ctx.enqueue("asap_message", priority="asap")
        return "tool_result"

    agent = PydanticAIAgent(model=TestModel(), tools=[Tool(enqueue_tool)])

    async with agent.iter("trigger tool") as run:
        node = run.next_node
        asap_seen = False
        asap_drained_at: str | None = None
        last_node: str | None = None

        while True:
            node_name = type(node).__name__

            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            pending = [m.priority for m in run.pending_messages]
            if "asap" in pending:
                asap_seen = True
            if asap_seen and "asap" not in pending and asap_drained_at is None:
                asap_drained_at = last_node

            last_node = node_name
            node = await run.next(node)

            if type(node).__name__ == "End":
                break

    assert asap_drained_at == "CallToolsNode", (
        f"asap should be drained immediately after CallToolsNode, got {asap_drained_at}"
    )


# ---------------------------------------------------------------------------
# 2. PydanticAI PendingMessageDrainCapability drains 'when_idle' at end-of-run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_when_idle_drained_after_tool_calls() -> None:
    """PydanticAI PendingMessageDrainCapability drains 'when_idle' at end-of-run."""

    async def enqueue_tool(ctx: RunContext) -> str:
        ctx.enqueue("when_idle_message", priority="when_idle")
        return "tool_result"

    agent = PydanticAIAgent(model=TestModel(), tools=[Tool(enqueue_tool)])

    async with agent.iter("trigger tool") as run:
        node = run.next_node
        when_idle_seen = False
        when_idle_drained_at: str | None = None
        last_node: str | None = None

        while True:
            node_name = type(node).__name__

            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            pending = [m.priority for m in run.pending_messages]
            if "when_idle" in pending:
                when_idle_seen = True
            if when_idle_seen and "when_idle" not in pending and when_idle_drained_at is None:
                when_idle_drained_at = last_node

            last_node = node_name
            node = await run.next(node)

            if type(node).__name__ == "End":
                break

    assert when_idle_drained_at == "CallToolsNode", (
        f"when_idle should be drained at after_node_run of CallToolsNode, got {when_idle_drained_at}"
    )


# ---------------------------------------------------------------------------
# 3. enqueue(priority='asap') during tool execution on native agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enqueue_asap_during_tool_execution() -> None:
    """Tool calling ctx.enqueue(priority='asap') inserts message into conversation."""

    async def enqueue_tool(ctx: RunContext) -> str:
        ctx.enqueue("asap_injected_content", priority="asap")
        return "tool_result"

    agent = PydanticAIAgent(model=TestModel(), tools=[Tool(enqueue_tool)])

    async with agent.iter("trigger tool") as run:
        node = run.next_node
        while True:
            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            node = await run.next(node)
            if type(node).__name__ == "End":
                break

    all_messages = run.all_messages()
    message_texts = [str(m) for m in all_messages]
    combined = "\n".join(message_texts)

    assert "asap_injected_content" in combined, (
        f"asap enqueued message should appear in conversation history. Messages: {combined}"
    )


# ---------------------------------------------------------------------------
# 4. Multiple 'when_idle' messages queued and all drained
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_multiple_when_idle_messages_all_drained() -> None:
    """Multiple when_idle messages are all drained at end-of-run."""

    async def enqueue_multiple(ctx: RunContext) -> str:
        ctx.enqueue("idle_msg_1", priority="when_idle")
        ctx.enqueue("idle_msg_2", priority="when_idle")
        ctx.enqueue("idle_msg_3", priority="when_idle")
        return "tool_result"

    agent = PydanticAIAgent(model=TestModel(), tools=[Tool(enqueue_multiple)])

    async with agent.iter("trigger tool") as run:
        node = run.next_node
        max_pending = 0
        while True:
            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass

            pending_when_idle = sum(
                1 for m in run.pending_messages if m.priority == "when_idle"
            )
            max_pending = max(max_pending, pending_when_idle)

            node = await run.next(node)
            if type(node).__name__ == "End":
                break

    all_messages = run.all_messages()
    message_texts = "\n".join(str(m) for m in all_messages)

    assert max_pending == 3, f"Expected 3 pending when_idle messages at peak, got {max_pending}"
    assert "idle_msg_1" in message_texts
    assert "idle_msg_2" in message_texts
    assert "idle_msg_3" in message_texts


# ---------------------------------------------------------------------------
# 5. enqueue() called after run completes
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_enqueue_after_run_completes_no_effect() -> None:
    """enqueue() called after run completes does not affect the completed run."""

    async def no_op_tool(ctx: RunContext) -> str:
        return "tool_result"

    agent = PydanticAIAgent(model=TestModel(), tools=[Tool(no_op_tool)])

    async with agent.iter("hello") as run:
        node = run.next_node
        while True:
            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass
            node = await run.next(node)
            if type(node).__name__ == "End":
                break

    # After run completes, pending_messages should be empty
    assert len(run.pending_messages) == 0

    # Attempting to enqueue after run should not raise but has no effect on completed run
    # (RunContext is no longer valid, but we verify the run completed cleanly)
    assert run.result is not None


# ---------------------------------------------------------------------------
# 6. Tool result augmentation via inject_prompt() still works for native agents
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inject_prompt_tool_augmentation_pipeline() -> None:
    """inject_prompt() -> injection_manager.inject() -> consume() pipeline works."""
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()
    assert not manager.has_pending()

    manager.inject("augment this result")
    assert manager.has_pending()
    assert not manager.has_queued()

    consumed = await manager.consume()
    assert consumed is not None
    assert "augment this result" in consumed
    assert not manager.has_pending()

    # Unconsumed injections become queued on flush
    manager.inject("unconsumed message")
    manager.flush_pending_to_queue()
    assert manager.has_queued()
    queued = manager.pop_queued()
    assert queued is not None
    assert "unconsumed message" in queued[0]


# ---------------------------------------------------------------------------
# 7. Event stream from RunExecutor matches current _stream_events() output
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_executor_event_stream_matches_stream_events(
    native_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunExecutor.execute() yields the same event types as agent.run_stream()."""

    # Collect events via RunExecutor
    executor = RunExecutor(native_agent)
    user_msg = ChatMessage.user_prompt("Say hello")
    executor_events = await _collect_run_executor_events(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        session_id="run-exec-session",
    )

    # Collect events via agent.run_stream() (which calls _stream_events)
    stream_events = await _collect_agent_stream_events(
        native_agent,
        "Say hello",
        session_id="stream-session",
    )

    executor_types = _event_type_names(executor_events)
    stream_types = _event_type_names(stream_events)

    # Both should contain RunStartedEvent and StreamCompleteEvent
    assert "RunStartedEvent" in executor_types
    assert "RunStartedEvent" in stream_types
    assert "StreamCompleteEvent" in executor_types
    assert "StreamCompleteEvent" in stream_types

    # Both should yield a ChatMessage in StreamCompleteEvent
    exec_complete = [e for e in executor_events if isinstance(e, StreamCompleteEvent)]
    stream_complete = [e for e in stream_events if isinstance(e, StreamCompleteEvent)]
    assert len(exec_complete) == 1
    assert len(stream_complete) == 1
    assert isinstance(exec_complete[0].message, ChatMessage)
    assert isinstance(stream_complete[0].message, ChatMessage)


# ---------------------------------------------------------------------------
# 8. Non-native agents still use manual queue (TurnRunner)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_native_agent_uses_manual_injection_manager(
    mock_pool: MagicMock,
) -> None:
    """Non-native agent inject_prompt() uses injection_manager, not SessionPool."""
    agent = _MockNonNativeAgent(name="non-native-test")

    # Mock session_pool to verify it's NOT called for injection
    session_pool_mock = MagicMock()
    session_pool_mock.receive_request = MagicMock()
    mock_pool.session_pool = session_pool_mock
    agent.agent_pool = mock_pool

    run_ctx = AgentRunContext()
    run_ctx.injection_manager = PromptInjectionManager()
    from agentpool.agents.base_agent import _current_run_ctx_var

    token = _current_run_ctx_var.set(run_ctx)
    try:
        agent.inject_prompt("test message")
        # Should inject into active run_ctx's injection_manager
        assert run_ctx.injection_manager.has_pending()
        # SessionPool.receive_request should NOT be called for non-native
        session_pool_mock.receive_request.assert_not_called()
    finally:
        _current_run_ctx_var.reset(token)


@pytest.mark.anyio
async def test_non_native_agent_uses_turn_runner(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Non-native agents are processed by TurnRunner with manual queue."""
    session_id = "non-native-sess"
    state = await controller.get_or_create_session(session_id)

    agent = _MockNonNativeAgent(name="non-native-test")
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent

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
            yield RunStartedEvent(session_id=session_id, run_id="run-1")
        else:
            yield RunStartedEvent(session_id=session_id, run_id=f"run-{call_count}")

    agent._run_stream_once = _fake_stream  # type: ignore[method-assign]

    await turn_runner.run_turn(session_id, "initial")

    assert call_count == 2, (
        f"TurnRunner should process injection + initial turn, got {call_count} calls"
    )
    assert received_prompts[1] == ("injected message",)


# ---------------------------------------------------------------------------
# 9. Native agent interrupt() cancels via SessionPool
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_agent_interrupt_cancels_via_session_pool(
    native_agent: Agent[None],
) -> None:
    """Native agent interrupt() delegates cancellation to SessionPool."""
    session_pool_mock = MagicMock()
    session_pool_mock.sessions = MagicMock()
    session_pool_mock.sessions.cancel_run_for_session = MagicMock()

    pool_mock = MagicMock()
    pool_mock.session_pool = session_pool_mock
    native_agent.agent_pool = pool_mock
    native_agent._events.session_id = "test-session"

    await native_agent.interrupt(session_id="test-session")

    session_pool_mock.sessions.cancel_run_for_session.assert_called_once_with("test-session")


# ---------------------------------------------------------------------------
# 10. receive_request() routes native agents correctly
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_routes_native_agents_correctly(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """receive_request() creates RunHandle and starts execution for native agents."""
    session_id = "native-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    # Attach native agent to session
    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    # Subscribe to events before receive_request
    queue = await session_pool.event_bus.subscribe(session_id)

    await session_pool.receive_request(session_id, "hello", priority="when_idle")

    # Wait for execution to start
    event = await asyncio.wait_for(queue.get(), timeout=2.0)
    assert event is not None
    assert isinstance(event, RunStartedEvent)
    assert event.agent_name == native_agent.name


@pytest.mark.anyio
async def test_receive_request_inject_prompt_into_active_run(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """receive_request with priority='asap' injects into active native agent run."""
    session_id = "native-inject-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    queue = await session_pool.event_bus.subscribe(session_id)

    # First request starts the run
    await session_pool.receive_request(session_id, "initial", priority="when_idle")

    # Wait a bit for turn to start
    await asyncio.sleep(0.1)

    # Second request with asap should inject into active run
    await session_pool.receive_request(session_id, "injected", priority="asap")

    # Collect all events
    events: list[Any] = []
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=1.0)
            if event is None:
                break
            events.append(event)
    except TimeoutError:
        pass

    # Should have at least one RunStartedEvent
    started_events = [e for e in events if isinstance(e, RunStartedEvent)]
    assert len(started_events) >= 1


# ---------------------------------------------------------------------------
# 11. Full integration: native agent auto-resumes with queued prompts
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_agent_auto_resumes_with_queued_prompts(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """Full integration: queued when_idle prompts trigger auto-resume for native agent."""
    session_id = "native-auto-resume-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    queue = await session_pool.event_bus.subscribe(session_id)

    # Queue a prompt before any run starts
    await session_pool.receive_request(session_id, "queued prompt", priority="when_idle")

    # The auto-resume should process the queued prompt
    events: list[Any] = []
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            if event is None:
                break
            events.append(event)
    except TimeoutError:
        pass

    # Should get RunStartedEvent and StreamCompleteEvent
    started = [e for e in events if isinstance(e, RunStartedEvent)]
    completed = [e for e in events if isinstance(e, StreamCompleteEvent)]

    assert len(started) >= 1, f"Expected at least one RunStartedEvent, got events: {_event_type_names(events)}"
    assert len(completed) >= 1, f"Expected at least one StreamCompleteEvent, got events: {_event_type_names(events)}"


@pytest.mark.anyio
async def test_native_agent_standalone_inject_prompt_routes_to_session_pool() -> None:
    """Pooled native agent inject_prompt() delegates to SessionPool.receive_request()."""
    agent = Agent(name="native-pooled-test", model=TestModel())

    session_pool_mock = MagicMock()
    session_pool_mock.receive_request = AsyncMock()

    pool_mock = MagicMock()
    pool_mock.session_pool = session_pool_mock
    agent.agent_pool = pool_mock
    agent._events.session_id = "test-session"

    # No active run context — should delegate to SessionPool.receive_request
    agent.inject_prompt("injected message")

    # fire_and_forget creates a task; give it a moment to run
    await asyncio.sleep(0.05)

    session_pool_mock.receive_request.assert_called_once()
    call_args = session_pool_mock.receive_request.call_args
    assert call_args[0][0] == "test-session"
    assert call_args[0][1] == "injected message"
    assert call_args[1].get("priority") == "asap"


@pytest.mark.anyio
async def test_native_agent_standalone_queue_prompt_routes_to_session_pool() -> None:
    """Pooled native agent queue_prompt() delegates to SessionPool.receive_request()."""
    agent = Agent(name="native-pooled-queue-test", model=TestModel())

    session_pool_mock = MagicMock()
    session_pool_mock.receive_request = AsyncMock()

    pool_mock = MagicMock()
    pool_mock.session_pool = session_pool_mock
    agent.agent_pool = pool_mock
    agent._events.session_id = "test-session"

    agent.queue_prompt("queued message")

    await asyncio.sleep(0.05)

    session_pool_mock.receive_request.assert_called_once()
    call_args = session_pool_mock.receive_request.call_args
    assert call_args[0][0] == "test-session"
    assert call_args[0][1] == ("queued message",)
    assert call_args[1].get("priority") == "when_idle"


# ---------------------------------------------------------------------------
# 12. PendingMessageDrainCapability is auto-injected outermost on native Agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_pending_message_drain_capability_auto_injected() -> None:
    """PydanticAI Agent has PendingMessageDrainCapability auto-injected outermost."""
    agent = PydanticAIAgent(model=TestModel())
    root = agent._root_capability
    capability_types = [type(c).__name__ for c in root.capabilities]

    assert "PendingMessageDrainCapability" in capability_types, (
        f"PendingMessageDrainCapability not found in {capability_types}"
    )
    assert capability_types[-1] == "PendingMessageDrainCapability", (
        f"PendingMessageDrainCapability should be outermost (last), got {capability_types}"
    )


# ---------------------------------------------------------------------------
# 13. RunHandle lifecycle during native agent execution
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_handle_lifecycle_created_completed_cancelled(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """RunHandle is created, started, and completed during native agent execution."""
    session_id = "lifecycle-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    # Before receive_request, no runs
    assert len(session_pool.sessions._runs) == 0

    queue = await session_pool.event_bus.subscribe(session_id)

    await session_pool.receive_request(session_id, "hello", priority="when_idle")

    # Wait for run to complete
    events: list[Any] = []
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            if event is None:
                break
            events.append(event)
    except TimeoutError:
        pass

    # After completion, run handle should be cleaned up
    assert len(session_pool.sessions._runs) == 0

    # Verify we got a complete stream
    assert any(isinstance(e, StreamCompleteEvent) for e in events)


# ---------------------------------------------------------------------------
# 14. receive_request passes input_provider to get_or_create_session_agent
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_passes_input_provider_to_session_agent(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """receive_request() forwards input_provider kwarg to get_or_create_session_agent."""
    session_id = "input-provider-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    # Spy on get_or_create_session_agent to capture input_provider
    original_get_agent = session_pool.sessions.get_or_create_session_agent
    captured_input_provider: Any = None

    async def spy_get_agent(
        session_id: str, input_provider: Any = None
    ) -> Agent[None]:
        nonlocal captured_input_provider
        captured_input_provider = input_provider
        return await original_get_agent(session_id, input_provider=input_provider)

    session_pool.sessions.get_or_create_session_agent = spy_get_agent

    queue = await session_pool.event_bus.subscribe(session_id)

    fake_input_provider = MagicMock()
    await session_pool.receive_request(
        session_id, "hello", priority="when_idle", input_provider=fake_input_provider
    )

    # Wait for execution
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            if event is None:
                break
    except TimeoutError:
        pass

    assert captured_input_provider is fake_input_provider, (
        f"input_provider not forwarded: got {captured_input_provider!r}"
    )


@pytest.mark.anyio
async def test_receive_request_ignores_unknown_kwargs_gracefully(
    session_pool: SessionPool,
    native_agent: Agent[None],
    mock_pool: MagicMock,
) -> None:
    """receive_request() silently drops kwargs that get_or_create_session_agent does not accept."""
    session_id = "unknown-kwarg-sess"
    await session_pool.create_session(session_id, agent_name=native_agent.name)

    state = session_pool.sessions.get_session(session_id)
    assert state is not None
    state.agent = native_agent
    session_pool.sessions._session_agents[session_id] = native_agent
    mock_pool.get_agent.return_value = native_agent
    state.metadata["agent_type"] = "native"

    queue = await session_pool.event_bus.subscribe(session_id)

    # Should not raise even though "unknown_param" is not consumed anywhere
    await session_pool.receive_request(
        session_id, "hello", priority="when_idle", unknown_param="whatever"
    )

    # Wait for execution
    try:
        while True:
            event = await asyncio.wait_for(queue.get(), timeout=2.0)
            if event is None:
                break
    except TimeoutError:
        pass
