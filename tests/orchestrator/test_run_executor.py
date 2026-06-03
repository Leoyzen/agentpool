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
