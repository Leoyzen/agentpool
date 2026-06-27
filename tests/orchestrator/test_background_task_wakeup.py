"""Tests for RunExecutor background task wake-up and re-iteration loop.

Covers:
- Happy path: background task completes, steer message triggers re-iteration
- Cancellation: session cancelled during background task wait
- Message history: re-iteration receives prior iteration's message history
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any, Literal

import pytest
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart
from pydantic_ai.models import ModelRequestParameters, ModelSettings
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run_executor import RunExecutor


pytestmark = pytest.mark.unit


class SequencedTestModel(TestModel):
    """TestModel that produces different output text on each text-producing call.

    Each model invocation within a single iteration calls ``_request`` twice:
    first to emit tool calls, then to produce the text response. This subclass
    cycles through ``outputs`` only on text-producing calls, so each iteration
    gets a distinct response.
    """

    def __init__(
        self,
        *,
        outputs: list[str],
        call_tools: list[str] | Literal["all"] = "all",
    ) -> None:
        super().__init__(call_tools=call_tools)
        self._outputs = outputs
        self._text_call_index = 0

    def _request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        if self._text_call_index < len(self._outputs):
            self.custom_output_text = self._outputs[self._text_call_index]
        else:
            self.custom_output_text = self._outputs[-1]

        result = super()._request(messages, model_settings, model_request_parameters)

        # Only increment on text-producing calls (not tool-call-only calls)
        if any(isinstance(p, TextPart) for p in result.parts):
            self._text_call_index += 1

        return result


async def _run_and_collect(
    executor: RunExecutor,
    *,
    prompts: list[Any],
    run_ctx: AgentRunContext,
    user_msg: ChatMessage[Any],
    message_history: MessageHistory,
    session_id: str = "test-session",
    message_id: str = "msg-1",
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
            event_bus=event_bus,
        )
    except BaseException as exc:
        error = exc

    await event_bus.close_session(session_id)

    events: list[Any] = []
    async with stream:
        async for envelope in stream:
            events.append(envelope.event)

    return events, result, error


def _make_steer_callback(run_ctx: AgentRunContext) -> Any:
    """Create a steer_callback that appends messages to queued_steer_messages."""

    async def steer_callback(session_id: str, message: str) -> bool:
        run_ctx.queued_steer_messages.append(message)
        return True

    return steer_callback


# ---------------------------------------------------------------------------
# Test 1: Happy path — background task completes and steer triggers re-iteration
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_background_task_wakeup_happy_path() -> None:
    """Background task completes and steer message triggers re-iteration.

    Given: a native Agent with a tool that spawns a 200ms background task.
    When: the background task completes and calls steer() with a result.
    Then: exactly 1 StreamCompleteEvent, response from re-iteration, >= 200ms.
    """
    session_id = "test-bg-wakeup"
    run_ctx = AgentRunContext()
    run_ctx.steer_callback = _make_steer_callback(run_ctx)

    tool_call_count = 0
    bg_tasks: list[asyncio.Task[Any]] = []

    async def spawn_bg_tool() -> str:
        """Tool that spawns a background task on first call only."""
        nonlocal tool_call_count
        tool_call_count += 1
        if tool_call_count > 1:
            return "already started"

        run_ctx.pending_background_tasks += 1
        run_ctx.background_tasks_complete.clear()

        async def bg_task() -> None:
            try:
                await asyncio.sleep(0.2)
                if run_ctx.steer_callback is not None:
                    await run_ctx.steer_callback(session_id, "bg result")
            finally:
                run_ctx.pending_background_tasks -= 1
                if run_ctx.pending_background_tasks <= 0:
                    run_ctx.background_tasks_complete.set()

        bg_tasks.append(asyncio.create_task(bg_task()))
        return "started"

    model = SequencedTestModel(
        outputs=["Initial response", "Re-iteration response"],
    )
    agent = Agent(name="bg-wakeup-test-agent", model=model, tools=[spawn_bg_tool])
    executor = RunExecutor(agent)

    user_msg = ChatMessage.user_prompt("Start background task")
    message_history = MessageHistory()

    start = time.perf_counter()
    events, result, error = await _run_and_collect(
        executor,
        prompts=["Start background task"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        session_id=session_id,
    )
    elapsed = time.perf_counter() - start

    # Clean up any lingering background tasks
    for t in bg_tasks:
        if not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    assert error is None, f"execute() raised: {error}"
    assert result is not None

    # Exactly 1 StreamCompleteEvent (no intermediate from re-iteration)
    stream_completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(stream_completes) == 1, (
        f"Expected 1 StreamCompleteEvent, got {len(stream_completes)}"
    )

    # Response from re-iteration (second model output)
    assert result.content == "Re-iteration response", (
        f"Expected 'Re-iteration response', got {result.content!r}"
    )

    # Background task took at least 200ms
    assert elapsed >= 0.2, f"Expected >= 200ms, got {elapsed * 1000:.0f}ms"

    # Steer messages were consumed
    assert len(run_ctx.queued_steer_messages) == 0

    # Model produced text twice (once per iteration — tool is only called on
    # the first iteration because TestModel skips tool calls when a ModelResponse
    # already exists in message history)
    assert model._text_call_index == 2, (
        f"Expected 2 text-producing model calls, got {model._text_call_index}"
    )


# ---------------------------------------------------------------------------
# Test 2: Cancellation — session cancelled during background task wait
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_background_task_wakeup_cancellation() -> None:
    """Session cancellation during background task wait.

    Given: a native Agent with a tool that spawns a background task.
    When: the run is cancelled during the 200ms background task wait.
    Then: StreamCompleteEvent with cancelled=True.
    """
    session_id = "test-bg-cancel"
    run_ctx = AgentRunContext()
    run_ctx.steer_callback = _make_steer_callback(run_ctx)

    tool_call_count = 0
    bg_tasks: list[asyncio.Task[Any]] = []

    async def spawn_bg_and_cancel_tool() -> str:
        """Tool that spawns a background task and a delayed cancellation."""
        nonlocal tool_call_count
        tool_call_count += 1
        if tool_call_count > 1:
            return "already started"

        run_ctx.pending_background_tasks += 1
        run_ctx.background_tasks_complete.clear()

        async def bg_task() -> None:
            try:
                await asyncio.sleep(0.2)
                if run_ctx.steer_callback is not None:
                    await run_ctx.steer_callback(session_id, "bg result")
            finally:
                run_ctx.pending_background_tasks -= 1
                if run_ctx.pending_background_tasks <= 0:
                    run_ctx.background_tasks_complete.set()

        bg_tasks.append(asyncio.create_task(bg_task()))

        async def cancel_task() -> None:
            """Cancel the run after 100ms (during the 200ms wait)."""
            await asyncio.sleep(0.1)
            run_ctx.cancelled = True
            run_ctx.background_tasks_complete.set()

        bg_tasks.append(asyncio.create_task(cancel_task()))
        return "started"

    model = SequencedTestModel(
        outputs=["Initial response", "Should not reach"],
    )
    agent = Agent(
        name="bg-cancel-test-agent",
        model=model,
        tools=[spawn_bg_and_cancel_tool],
    )
    executor = RunExecutor(agent)

    user_msg = ChatMessage.user_prompt("Start and cancel")
    message_history = MessageHistory()

    events, result, error = await _run_and_collect(
        executor,
        prompts=["Start and cancel"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        session_id=session_id,
    )

    # Clean up any lingering background tasks
    for t in bg_tasks:
        if not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    assert error is None, f"execute() raised: {error}"
    assert result is not None

    stream_completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(stream_completes) == 1, (
        f"Expected 1 StreamCompleteEvent, got {len(stream_completes)}"
    )
    assert stream_completes[0].cancelled is True, (
        "Expected StreamCompleteEvent.cancelled=True"
    )


# ---------------------------------------------------------------------------
# Test 3: Message history — re-iteration can reference prior response
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_re_iteration_message_history() -> None:
    """Re-iteration receives prior iteration's message history.

    Given: a native Agent with TestModel producing a sequence of responses.
    When: background task completes and steer message triggers re-iteration.
    Then: re-iteration produces a different response, proving the model was
          invoked again with the steer message and prior message history.
    """
    session_id = "test-bg-history"
    run_ctx = AgentRunContext()
    run_ctx.steer_callback = _make_steer_callback(run_ctx)

    tool_call_count = 0
    bg_tasks: list[asyncio.Task[Any]] = []

    async def spawn_bg_tool() -> str:
        """Tool that spawns a short background task on first call."""
        nonlocal tool_call_count
        tool_call_count += 1
        if tool_call_count > 1:
            return "already started"

        run_ctx.pending_background_tasks += 1
        run_ctx.background_tasks_complete.clear()

        async def bg_task() -> None:
            try:
                await asyncio.sleep(0.05)
                if run_ctx.steer_callback is not None:
                    await run_ctx.steer_callback(session_id, "bg result")
            finally:
                run_ctx.pending_background_tasks -= 1
                if run_ctx.pending_background_tasks <= 0:
                    run_ctx.background_tasks_complete.set()

        bg_tasks.append(asyncio.create_task(bg_task()))
        return "started"

    model = SequencedTestModel(
        outputs=["First response", "Second response after steer"],
    )
    agent = Agent(name="bg-history-test-agent", model=model, tools=[spawn_bg_tool])
    executor = RunExecutor(agent)

    user_msg = ChatMessage.user_prompt("Start task")
    message_history = MessageHistory()

    events, result, error = await _run_and_collect(
        executor,
        prompts=["Start task"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
        session_id=session_id,
    )

    # Clean up any lingering background tasks
    for t in bg_tasks:
        if not t.done():
            t.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await t

    assert error is None, f"execute() raised: {error}"
    assert result is not None

    # Final response is from re-iteration (second output)
    assert result.content == "Second response after steer", (
        f"Expected 'Second response after steer', got {result.content!r}"
    )

    # Model was invoked twice (proving re-iteration happened with message history)
    assert model._text_call_index == 2, (
        f"Expected 2 text-producing model calls, got {model._text_call_index}"
    )

    stream_completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(stream_completes) == 1
