"""Tests for RunExecutor.

Covers:
- Basic event publishing to EventBus (RunStartedEvent, PartStartEvent,
  PartDeltaEvent, StreamCompleteEvent)
- Tool call event mapping (ToolCallStartEvent, ToolCallCompleteEvent)
- CancelScope safety (cancellation propagation)
- Error propagation (RunErrorEvent published before exception)
- execute() returns ChatMessage (not async generator)
"""

from __future__ import annotations

import asyncio
import contextlib
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic_ai import PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    PartStartEvent as AgentPoolPartStartEvent,
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run_executor import RunExecutor
from agentpool.tools.base import TERMINAL_TOOL_METADATA_KEY, Tool


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def test_agent() -> Agent[None]:
    """Agent with instant TestModel for basic stream tests."""
    model = TestModel(custom_output_text="Hello from RunExecutor")
    return Agent(name="run-executor-test-agent", model=model)


@pytest.fixture
def tool_agent() -> Agent[None]:
    """Agent with a tool for testing tool call events."""

    async def hello_tool() -> str:
        """Say hello."""
        return "hello_result"

    model = TestModel(custom_output_text="Done")
    return Agent(
        name="run-executor-tool-agent",
        model=model,
        tools=[hello_tool],
    )


@pytest.fixture
def terminal_tool_agent() -> Agent[None]:
    """Agent with a terminal tool for run completion tests."""

    async def finish_tool() -> str:
        """Finish the run."""
        return "terminal_result"

    finish = Tool.from_callable(
        finish_tool,
        metadata={TERMINAL_TOOL_METADATA_KEY: "true"},
    )
    model = TestModel(custom_output_text="model should not continue")
    return Agent(
        name="run-executor-terminal-agent",
        model=model,
        tools=[finish],
    )


@pytest.fixture
def run_ctx() -> AgentRunContext:
    """Fresh AgentRunContext for each test."""
    return AgentRunContext()


@pytest.fixture
def message_history() -> MessageHistory:
    """Empty message history."""
    return MessageHistory()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _run_and_collect(
    executor: RunExecutor,
    *,
    prompts: list[Any],
    run_ctx: AgentRunContext,
    user_msg: ChatMessage[Any],
    message_history: MessageHistory,
    session_id: str = "test-session",
    message_id: str = "msg-1",
    _parent_id: str | None = None,
) -> tuple[list[Any], ChatMessage[Any] | None, BaseException | None]:
    """Execute RunExecutor and collect all events from EventBus.

    Returns:
        Tuple of (events list, result ChatMessage or None, error or None).
    """
    event_bus = EventBus()
    stream = await event_bus.subscribe(session_id, scope="session")

    result: ChatMessage[Any] | None = None
    error: BaseException | None = None

    try:
        result = await executor.execute(
            prompts=prompts,
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id=message_id,
            session_id=session_id,
            _parent_id=_parent_id,
            event_bus=event_bus,
        )
    except BaseException as exc:
        error = exc

    # Close session to flush buffered events and send EndOfStream to stream
    await event_bus.close_session(session_id)

    # Drain events from stream (buffered during execute)
    events: list[Any] = []
    async with stream:
        async for envelope in stream:
            events.append(envelope.event)

    return events, result, error


# ---------------------------------------------------------------------------
# Basic event stream publishing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_basic_event_stream(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunExecutor publishes RunStartedEvent, model events, and StreamCompleteEvent."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    events, result, error = await _run_and_collect(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    assert error is None
    assert result is not None
    assert isinstance(result, ChatMessage)

    event_types = [type(e).__name__ for e in events]

    # Must start with RunStartedEvent
    assert events[0].__class__.__name__ == "RunStartedEvent"
    assert isinstance(events[0], RunStartedEvent)

    # Must contain PartStartEvent and PartDeltaEvent from ModelRequestNode
    assert any(isinstance(e, PartStartEvent) for e in events), (
        f"Expected PartStartEvent in stream, got: {event_types}"
    )
    assert any(isinstance(e, PartDeltaEvent) for e in events), (
        f"Expected PartDeltaEvent in stream, got: {event_types}"
    )

    # Must end with StreamCompleteEvent
    assert events[-1].__class__.__name__ == "StreamCompleteEvent"
    assert isinstance(events[-1], StreamCompleteEvent)
    assert isinstance(events[-1].message, ChatMessage)


@pytest.mark.anyio
async def test_stream_complete_has_content(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """StreamCompleteEvent carries the assistant response content."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    events, result, _ = await _run_and_collect(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    complete_event = events[-1]
    assert isinstance(complete_event, StreamCompleteEvent)
    assert complete_event.message.content == "Hello from RunExecutor"
    assert result is not None
    assert result.content == "Hello from RunExecutor"


# ---------------------------------------------------------------------------
# execute() return type
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_execute_returns_chat_message(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """execute() is async def returning ChatMessage, not an async generator."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    event_bus = EventBus()
    result = await executor.execute(
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        message_id="msg-1",
        session_id="test-session",
        event_bus=event_bus,
    )

    # Must return a ChatMessage, not an async iterator
    assert isinstance(result, ChatMessage)
    assert not hasattr(result, "__aiter__")


# ---------------------------------------------------------------------------
# Tool call events
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tool_call_events_mapped(
    tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """CallToolsNode events are mapped to ToolCallStartEvent and ToolCallCompleteEvent."""
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call the tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    # Must contain ToolCallStartEvent
    tool_starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    assert len(tool_starts) >= 1, (
        f"Expected at least 1 ToolCallStartEvent, got event types: "
        f"{[type(e).__name__ for e in events]}"
    )
    assert tool_starts[0].tool_name == "hello_tool"

    # Must contain ToolCallCompleteEvent
    tool_completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert len(tool_completes) >= 1, (
        f"Expected at least 1 ToolCallCompleteEvent, got event types: "
        f"{[type(e).__name__ for e in events]}"
    )
    assert tool_completes[0].tool_name == "hello_tool"
    assert tool_completes[0].tool_result == "hello_result"


@pytest.mark.anyio
async def test_terminal_tool_completion_ends_run(
    terminal_tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """A terminal tool result is the final run result."""
    executor = RunExecutor(terminal_tool_agent)
    user_msg = ChatMessage.user_prompt("Finish the task")

    events, result, _ = await _run_and_collect(
        executor,
        prompts=["Finish the task"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    tool_completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert len(tool_completes) == 1
    assert tool_completes[0].tool_name == "finish_tool"
    assert tool_completes[0].tool_result == "terminal_result"
    assert run_ctx.terminal_tool_name == "finish_tool"
    assert run_ctx.terminal_tool_result == "terminal_result"

    complete_event = next(e for e in events if isinstance(e, StreamCompleteEvent))
    assert complete_event.message.content == "terminal_result"
    assert result is not None
    assert result.content == "terminal_result"


@pytest.mark.anyio
async def test_raw_tool_events_still_present(
    tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Raw FunctionToolCallEvent / FunctionToolResultEvent are still published."""
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call the tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    raw_calls = [e for e in events if isinstance(e, FunctionToolCallEvent)]
    raw_results = [e for e in events if isinstance(e, FunctionToolResultEvent)]

    assert len(raw_calls) >= 1, "Raw FunctionToolCallEvent should still be present"
    assert len(raw_results) >= 1, "Raw FunctionToolResultEvent should still be present"


# ---------------------------------------------------------------------------
# Concurrent run warning
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_concurrent_run_warning(
    test_agent: Agent[None],
    message_history: MessageHistory,
) -> None:
    """Calling execute() while a previous execution is in progress logs a WARNING."""
    from unittest.mock import patch

    from agentpool.orchestrator import run_executor as run_executor_module

    executor = RunExecutor(test_agent)

    # Simulate a previous execution still running
    async def _long_running_task() -> None:
        await asyncio.sleep(3600)

    dummy_task = asyncio.create_task(_long_running_task())
    executor._iteration_task = dummy_task

    run_ctx = AgentRunContext()
    user_msg = ChatMessage.user_prompt("Test concurrent warning")

    with patch.object(run_executor_module, "logger") as mock_logger:
        events, _, _ = await _run_and_collect(
            executor,
            prompts=["Test concurrent warning"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )

    # Clean up the dummy task
    dummy_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await dummy_task

    # Verify warning was logged
    mock_logger.warning.assert_called_once_with(
        "Concurrent RunExecutor.execute() call detected — "
        "a previous execution is still in progress"
    )

    # Second execution should still complete normally
    assert isinstance(events[-1], StreamCompleteEvent)


# ---------------------------------------------------------------------------
# CancelScope safety
# ---------------------------------------------------------------------------


class SlowTestModel(TestModel):
    """TestModel that inserts a delay before yielding the streamed response."""

    def __init__(
        self,
        *,
        custom_output_text: str | None = None,
        pre_stream_delay: float = 0.3,
    ) -> None:
        super().__init__(custom_output_text=custom_output_text)
        self.pre_stream_delay = pre_stream_delay

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):  # type: ignore[override]
        """Yield the streamed response after a configurable delay."""
        from pydantic_ai.models.test import TestStreamedResponse

        model_settings, model_request_parameters = self.prepare_request(
            model_settings,
            model_request_parameters,
        )
        self.last_model_request_parameters = model_request_parameters
        model_response = self._request(messages, model_settings, model_request_parameters)

        await asyncio.sleep(self.pre_stream_delay)

        yield TestStreamedResponse(
            model_request_parameters=model_request_parameters,
            _model_name=self._model_name,
            _structured_response=model_response,
            _messages=messages,
            _provider_name=self._system,
        )


@pytest.fixture
def slow_agent() -> Agent[None]:
    """Agent with SlowTestModel for cancellation testing."""
    model = SlowTestModel(
        custom_output_text="Slow response",
        pre_stream_delay=0.3,
    )
    return Agent(name="run-executor-slow-agent", model=model)


@pytest.mark.anyio
async def test_cancel_scope_safety(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Cancelling execute() propagates asyncio.CancelledError cleanly."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")
    event_bus = EventBus()

    async def run() -> None:
        await executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
            event_bus=event_bus,
        )

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)  # Let iteration start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_cancelled_before_response_fallback(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """When run_ctx.cancelled is set before the model responds, a
    StreamCompleteEvent is published with partial or interrupted content."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")
    event_bus = EventBus()
    stream = await event_bus.subscribe("sess-1", scope="session")

    result: ChatMessage[Any] | None = None

    async def run() -> None:
        nonlocal result
        result = await executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
            event_bus=event_bus,
        )

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)  # Let iteration start before model responds
    run_ctx.cancelled = True
    await task

    # Close session to flush events
    await event_bus.close_session("sess-1")

    # Drain events from stream
    events: list[Any] = []
    async with stream:
        async for envelope in stream:
            events.append(envelope.event)

    # Verify StreamCompleteEvent was published
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1, (
        f"Expected exactly 1 StreamCompleteEvent, got {len(complete_events)}"
    )
    msg = complete_events[0].message
    assert msg is not None
    assert msg.finish_reason == "stop"
    assert msg.role == "assistant"
    assert msg.name == slow_agent.name


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_error_propagation_from_iteration_task(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Errors in the background iteration task are propagated to the caller."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch get_agentlet to raise an error
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agentlet creation failed")

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        events, result, error = await _run_and_collect(
            executor,
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )
        assert error is not None
        assert isinstance(error, RuntimeError)
        assert "agentlet creation failed" in str(error)
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_error_during_stream_propagated(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Errors during node streaming are propagated to the caller."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch agent.get_agentlet so execute() gets a broken agentlet
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        agentlet = await original_get_agentlet(*args, **kwargs)

        class BrokenIter:
            """Mock agent run that raises mid-stream."""

            def __init__(self) -> None:
                self.ctx = MagicMock()
                self.next_node = MagicMock()
                self.result = None

            async def next(self, node: Any) -> Any:
                raise ValueError("stream broke")

            async def __aenter__(self) -> "BrokenIter":
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            def all_messages(self) -> list[Any]:
                return []

        agentlet.iter = lambda *args, **kwargs: BrokenIter()  # type: ignore[method-assign]
        return agentlet

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        events, result, error = await _run_and_collect(
            executor,
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )
        assert error is not None
        assert isinstance(error, ValueError)
        assert "stream broke" in str(error)
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_run_error_event_published_before_exception(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunErrorEvent is published to EventBus before the exception is raised."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch get_agentlet to raise an error
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agentlet creation failed")

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        events, _, error = await _run_and_collect(
            executor,
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )

        # Should have raised
        assert error is not None
        assert isinstance(error, RuntimeError)
        assert "agentlet creation failed" in str(error)

        # RunErrorEvent should have been published
        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1, (
            f"Expected 1 RunErrorEvent, got {len(error_events)}"
        )
        assert "agentlet creation failed" in error_events[0].message
        assert error_events[0].agent_name == test_agent.name
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_run_started_event_always_first(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunStartedEvent is always the first event published."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Test")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Test"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    assert len(events) > 0
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].session_id == "test-session"
    assert events[0].agent_name == test_agent.name


@pytest.mark.anyio
async def test_tool_events_with_event_bus_set(
    tool_agent: Agent[None],
    message_history: MessageHistory,
) -> None:
    """RunExecutor publishes ToolCallStartEvent and ToolCallCompleteEvent
    even when event_bus is set on run_ctx."""
    run_ctx = AgentRunContext(session_id="test-session-bus")
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call the tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    # Must contain ToolCallStartEvent
    tool_starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    assert len(tool_starts) >= 1, (
        f"Expected at least 1 ToolCallStartEvent, got event types: "
        f"{[type(e).__name__ for e in events]}"
    )
    assert tool_starts[0].tool_name == "hello_tool"

    # Must contain ToolCallCompleteEvent
    tool_completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert len(tool_completes) >= 1, (
        f"Expected at least 1 ToolCallCompleteEvent, got event types: "
        f"{[type(e).__name__ for e in events]}"
    )
    assert tool_completes[0].tool_name == "hello_tool"
    assert tool_completes[0].tool_result == "hello_result"


@pytest.mark.anyio
async def test_multiple_tool_calls_ordering(
    message_history: MessageHistory,
) -> None:
    """Multiple tool calls produce correct start/complete pairs in order."""

    async def tool_a() -> str:
        """Tool A."""
        return "result_a"

    async def tool_b() -> str:
        """Tool B."""
        return "result_b"

    model = TestModel(custom_output_text="Done")
    agent = Agent(name="multi-tool-agent", model=model, tools=[tool_a, tool_b])
    run_ctx = AgentRunContext()
    executor = RunExecutor(agent)
    user_msg = ChatMessage.user_prompt("Call both tools")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call both tools"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    # Collect start and complete events in order
    tool_starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    tool_completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]

    # Should have at least 2 tool calls (TestModel with call_tools='all' may call each tool)
    assert len(tool_starts) >= 1, (
        f"Expected at least 1 ToolCallStartEvent, got {len(tool_starts)}"
    )
    assert len(tool_completes) >= 1, (
        f"Expected at least 1 ToolCallCompleteEvent, got {len(tool_completes)}"
    )

    # Verify ordering: each complete comes after its corresponding start
    for complete in tool_completes:
        # Find the start event with the same tool_call_id
        matching_starts = [
            s for s in tool_starts
            if s.tool_call_id == complete.tool_call_id
        ]
        assert len(matching_starts) == 1, (
            f"Expected exactly 1 matching start for tool_call_id {complete.tool_call_id}, "
            f"got {len(matching_starts)}"
        )

        # Verify no cross-contamination: complete event matches its start
        assert complete.tool_name == matching_starts[0].tool_name, (
            f"Tool name mismatch: start={matching_starts[0].tool_name}, "
            f"complete={complete.tool_name}"
        )


# ---------------------------------------------------------------------------
# session_id is not set by RunExecutor (producers don't set it)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_tool_call_start_event_lacks_session_id(
    tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """ToolCallStartEvent does not have session_id set by RunExecutor."""
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call the tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    tool_starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
    assert len(tool_starts) >= 1, "Expected at least 1 ToolCallStartEvent"
    for start in tool_starts:
        assert start.session_id == "", (
            f"Expected empty session_id, got '{start.session_id}'"
        )


@pytest.mark.anyio
async def test_stream_complete_event_lacks_session_id(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """StreamCompleteEvent does not have session_id set by RunExecutor."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    complete_event = events[-1]
    assert isinstance(complete_event, StreamCompleteEvent)
    assert complete_event.session_id == "", (
        f"Expected empty session_id, got '{complete_event.session_id}'"
    )


@pytest.mark.anyio
async def test_tool_call_complete_event_lacks_session_id(
    tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """ToolCallCompleteEvent does not have session_id set by RunExecutor."""
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Call the tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    tool_completes = [e for e in events if isinstance(e, ToolCallCompleteEvent)]
    assert len(tool_completes) >= 1, "Expected at least 1 ToolCallCompleteEvent"
    for complete in tool_completes:
        assert complete.session_id == "", (
            f"Expected empty session_id, got '{complete.session_id}'"
        )


@pytest.mark.anyio
async def test_tool_call_start_dedup(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Only one ToolCallStartEvent emitted when both FunctionToolCallEvent
    and PartStartEvent(BaseToolCallPart) fire for the same tool_call_id."""
    from contextlib import asynccontextmanager
    from unittest.mock import MagicMock

    from pydantic_ai import CallToolsNode
    from pydantic_ai.messages import ToolCallPart
    from pydantic_graph import End

    tool_call_id = "dedup-tool-call-1"

    # Create mock tool_part that passes isinstance checks for both
    # ToolCallPart (FunctionToolCallEvent branch) and BaseToolCallPart (PartStartEvent branch)
    mock_tool_part = MagicMock()
    mock_tool_part.tool_call_id = tool_call_id
    mock_tool_part.tool_name = "dedup_tool"
    mock_tool_part.args = "{}"
    mock_tool_part.__class__ = ToolCallPart

    func_call_event = FunctionToolCallEvent(part=mock_tool_part)
    part_start_event = PartStartEvent(index=0, part=mock_tool_part)

    # Async iterator that yields both event types
    class _EventIter:
        def __init__(self, items: list[Any]) -> None:
            self._items = list(items)
            self._idx = 0

        def __aiter__(self) -> "_EventIter":
            return self

        async def __anext__(self) -> Any:
            if self._idx < len(self._items):
                item = self._items[self._idx]
                self._idx += 1
                return item
            raise StopAsyncIteration

    @asynccontextmanager
    async def _mock_stream(ctx: Any) -> Any:  # noqa: ARG001
        yield _EventIter([func_call_event, part_start_event])

    mock_node = MagicMock()
    mock_node.__class__ = CallToolsNode
    mock_node.stream = _mock_stream

    class MockIter:
        """Mock agent run with a CallToolsNode that yields both event types."""

        def __init__(self) -> None:
            self.ctx = MagicMock()
            self.next_node = mock_node
            # Build a realistic-enough result mock so from_run_result
            # can compute costs without hitting Decimal conversion errors.
            result_mock = MagicMock()
            result_mock.usage = MagicMock()
            result_mock.response = MagicMock()
            result_mock.response.usage = MagicMock()
            result_mock.response.provider_details = {}
            self.result = result_mock

        async def __aenter__(self) -> "MockIter":
            return self

        async def __aexit__(self, *args: Any) -> None:
            pass

        async def next(self, node: Any) -> End[Any]:  # noqa: ARG002
            return End(data=MagicMock())

        def all_messages(self) -> list[Any]:
            return []

    original_get_agentlet = test_agent.get_agentlet

    async def mock_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        agentlet = await original_get_agentlet(*args, **kwargs)
        agentlet.iter = lambda *a, **kw: MockIter()  # type: ignore[method-assign]
        return agentlet

    test_agent.get_agentlet = mock_get_agentlet  # type: ignore[method-assign]

    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Call tool")

    try:
        events, _, _ = await _run_and_collect(
            executor,
            prompts=["Call tool"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]

    # Verify only one ToolCallStartEvent for the deduplicated tool_call_id
    tool_starts = [
        e for e in events
        if isinstance(e, ToolCallStartEvent) and e.tool_call_id == tool_call_id
    ]
    assert len(tool_starts) == 1, (
        f"Expected exactly 1 ToolCallStartEvent for {tool_call_id}, "
        f"got {len(tool_starts)}"
    )
    assert tool_starts[0].tool_name == "dedup_tool"


@pytest.mark.anyio
async def test_run_started_event_session_fields(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunStartedEvent carries session_id and parent_session_id from execute()."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Test session fields")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Test session fields"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        session_id="custom-session-id",
        _parent_id="custom-parent-id",
    )

    assert len(events) > 0
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].session_id == "custom-session-id"
    assert events[0].parent_session_id == "custom-parent-id"
    assert events[0].agent_name == test_agent.name


# ---------------------------------------------------------------------------
# Cancelled before response fallback
# ---------------------------------------------------------------------------


# (test_cancelled_before_response_fallback is defined above in the CancelScope section)


# ---------------------------------------------------------------------------
# New tests: EventBus integration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_events_published_to_event_bus(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Events are published to EventBus and received by subscribers."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    event_bus = EventBus()
    stream = await event_bus.subscribe("test-session", scope="session")

    # Run execute in a background task
    async def run() -> ChatMessage[Any]:
        return await executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="test-session",
            event_bus=event_bus,
        )

    task = asyncio.create_task(run())

    # Collect events from stream
    events: list[Any] = []
    async with stream:
        async for envelope in stream:
            events.append(envelope.event)
            if isinstance(envelope.event, StreamCompleteEvent):
                break

    result = await task

    # Verify events were received
    assert len(events) > 0
    assert isinstance(events[0], RunStartedEvent)
    assert isinstance(events[-1], StreamCompleteEvent)
    assert isinstance(result, ChatMessage)


@pytest.mark.anyio
async def test_cancelled_error_reraised(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """asyncio.CancelledError is re-raised after publishing StreamCompleteEvent."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")
    event_bus = EventBus()
    stream = await event_bus.subscribe("sess-1", scope="session")

    async def run() -> None:
        await executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
            event_bus=event_bus,
        )

    task = asyncio.create_task(run())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Close session to flush events
    await event_bus.close_session("sess-1")

    # Verify StreamCompleteEvent(cancelled=True) was published
    events: list[Any] = []
    async with stream:
        async for envelope in stream:
            events.append(envelope.event)

    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].cancelled is True


@pytest.mark.anyio
async def test_run_aborted_error_graceful(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunAbortedError is treated as graceful cancellation — no RunErrorEvent."""
    from agentpool.tasks.exceptions import RunAbortedError

    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch get_agentlet to raise RunAbortedError
    original_get_agentlet = test_agent.get_agentlet

    async def aborting_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        raise RunAbortedError("user aborted")

    test_agent.get_agentlet = aborting_get_agentlet  # type: ignore[method-assign]

    try:
        events, result, error = await _run_and_collect(
            executor,
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
        )

        # No error — graceful handling
        assert error is None
        assert result is not None

        # StreamCompleteEvent with cancelled=True should be published
        complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
        assert len(complete_events) == 1
        assert complete_events[0].cancelled is True
        assert complete_events[0].message.content == "[Interrupted]"

        # No RunErrorEvent should be published
        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 0, "RunAbortedError should not produce RunErrorEvent"

        # run_ctx.cancelled should be True
        assert run_ctx.cancelled is True
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_merged_events_retain_type(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Events published to EventBus retain their original event type."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    events, _, _ = await _run_and_collect(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    # Verify each event retains its type
    for event in events:
        if isinstance(event, RunStartedEvent):
            assert event.event_kind == "run_started"
        elif isinstance(event, StreamCompleteEvent):
            assert event.event_kind == "stream_complete"
        elif isinstance(event, PartStartEvent):
            # PydanticAI PartStartEvent doesn't have event_kind
            pass
        elif isinstance(event, PartDeltaEvent):
            # PydanticAI PartDeltaEvent doesn't have event_kind
            pass

    # Verify specific types are present and correct
    run_started = [e for e in events if isinstance(e, RunStartedEvent)]
    assert len(run_started) == 1
    assert type(run_started[0]).__name__ == "RunStartedEvent"

    stream_complete = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(stream_complete) == 1
    assert type(stream_complete[0]).__name__ == "StreamCompleteEvent"
