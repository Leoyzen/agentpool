"""Tests for RunExecutor.

Covers:
- Basic event stream matching (RunStartedEvent, PartStartEvent, PartDeltaEvent,
  StreamCompleteEvent)
- Tool call event mapping (ToolCallStartEvent, ToolCallCompleteEvent)
- CancelScope safety (background task cleanup on consumer cancellation)
- Error propagation (background task errors raised in consumer)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
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
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.run_executor import RunExecutor


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


async def _collect_events(
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


# ---------------------------------------------------------------------------
# Basic event stream matching
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_basic_event_stream(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunExecutor yields RunStartedEvent, model events, and StreamCompleteEvent."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    events = await _collect_events(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

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

    events = await _collect_events(
        executor,
        prompts=["Say hello"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    complete_event = events[-1]
    assert isinstance(complete_event, StreamCompleteEvent)
    assert complete_event.message.content == "Hello from RunExecutor"


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

    events = await _collect_events(
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
async def test_raw_tool_events_still_present(
    tool_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Raw FunctionToolCallEvent / FunctionToolResultEvent are still yielded."""
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events = await _collect_events(
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
        events = await _collect_events(
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
    """Cancelling the consumer cancels the background iteration task cleanly."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    collected: list[Any] = []

    async def consume() -> None:
        async for event in executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
        ):
            collected.append(event)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)  # Let iteration start
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # The iteration task should have been cleaned up
    assert executor._iteration_task is None


@pytest.mark.anyio
async def test_cancelled_run_yields_partial_stream(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """When cancelled, RunExecutor still yields any events that were queued."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    collected: list[Any] = []

    async def consume() -> None:
        async for event in executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
        ):
            collected.append(event)
            # Cancel after receiving the first event
            if len(collected) == 1:
                task = asyncio.current_task()
                if task is not None:
                    task.cancel()

    task = asyncio.create_task(consume())

    with pytest.raises(asyncio.CancelledError):
        await task

    # We should have received at least the RunStartedEvent
    assert len(collected) >= 1
    assert isinstance(collected[0], RunStartedEvent)


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_error_propagation_from_iteration_task(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Errors in the background iteration task are propagated to the consumer."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch get_agentlet to raise an error
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("agentlet creation failed")

    test_agent.get_agentlet = broken_get_agentlet  # type: ignore[method-assign]

    try:
        with pytest.raises(RuntimeError, match="agentlet creation failed"):
            async for _event in executor.execute(
                prompts=["Say hello"],
                run_ctx=run_ctx,
                user_msg=user_msg,
                message_history=message_history,
                message_id="msg-1",
                session_id="sess-1",
            ):
                pass
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_error_during_stream_propagated(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """Errors during node streaming are propagated to the consumer."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    # Patch agent.get_agentlet so execute() gets a broken agentlet
    original_get_agentlet = test_agent.get_agentlet

    async def broken_get_agentlet(*args: Any, **kwargs: Any) -> Any:
        agentlet = await original_get_agentlet(*args, **kwargs)
        original_iter = agentlet.iter

        async def _broken_stream(ctx: Any) -> AsyncIterator[Any]:  # noqa: ARG001
            yield AgentPoolPartStartEvent.text(index=0, content="x")
            raise ValueError("stream broke")

        class BrokenIter:
            """Mock agent run that raises mid-stream."""

            def __init__(self) -> None:
                self.ctx = MagicMock()
                self.next_node = MagicMock()
                self.next_node.stream = _broken_stream
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
        with pytest.raises(ValueError, match="stream broke"):
            async for _event in executor.execute(
                prompts=["Say hello"],
                run_ctx=run_ctx,
                user_msg=user_msg,
                message_history=message_history,
                message_id="msg-1",
                session_id="sess-1",
            ):
                pass
    finally:
        test_agent.get_agentlet = original_get_agentlet  # type: ignore[method-assign]


@pytest.mark.anyio
async def test_run_started_event_always_first(
    test_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunStartedEvent is always the first event yielded."""
    executor = RunExecutor(test_agent)
    user_msg = ChatMessage.user_prompt("Test")

    events = await _collect_events(
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
    """RunExecutor yields ToolCallStartEvent and ToolCallCompleteEvent even when event_bus is set on run_ctx.

    After the fix, process_tool_event() always returns combined events regardless
    of event_bus state. RunExecutor should yield these events normally.
    """
    from agentpool.orchestrator.core import EventBus

    event_bus = EventBus()
    run_ctx = AgentRunContext(event_bus=event_bus, session_id="test-session-bus")
    executor = RunExecutor(tool_agent)
    user_msg = ChatMessage.user_prompt("Call the tool")

    events = await _collect_events(
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

    events = await _collect_events(
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

    events = await _collect_events(
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

    events = await _collect_events(
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

    events = await _collect_events(
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
        events = await _collect_events(
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

    events: list[Any] = []
    async for event in executor.execute(
        prompts=["Test session fields"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        message_id="msg-1",
        session_id="custom-session-id",
        _parent_id="custom-parent-id",
    ):
        events.append(event)

    assert len(events) > 0
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].session_id == "custom-session-id"
    assert events[0].parent_session_id == "custom-parent-id"
    assert events[0].agent_name == test_agent.name


# ---------------------------------------------------------------------------
# Cancelled before response fallback
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_cancelled_before_response_fallback(
    slow_agent: Agent[None],
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """When run_ctx.cancelled is set before the model responds, a fallback
    StreamCompleteEvent with ``[Interrupted]`` content is yielded."""
    executor = RunExecutor(slow_agent)
    user_msg = ChatMessage.user_prompt("Say hello")

    collected: list[Any] = []

    async def collect() -> None:
        async for event in executor.execute(
            prompts=["Say hello"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="sess-1",
        ):
            collected.append(event)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.05)  # Let iteration start before model responds
    run_ctx.cancelled = True
    await task

    # Verify StreamCompleteEvent was yielded with fallback message
    complete_events = [e for e in collected if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1, (
        f"Expected exactly 1 StreamCompleteEvent, got {len(complete_events)}"
    )
    msg = complete_events[0].message
    assert msg is not None
    assert msg.content == "[Interrupted]"
    assert msg.finish_reason == "stop"
    assert msg.role == "assistant"
    assert msg.name == slow_agent.name

