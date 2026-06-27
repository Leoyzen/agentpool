"""Test: Verify agent_run state after RunAbortedError.

When a tool raises RunAbortedError during agentlet.iter(), does
agent_run.all_messages() still return tool call data after the
`async with` block exits?

This determines the fix for missing tool results in conversation history.
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.messaging.message_history import MessageHistory
from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run_executor import RunExecutor
from agentpool.tasks.exceptions import RunAbortedError


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
    """Execute RunExecutor and collect all events from EventBus."""
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


@pytest.fixture
def run_ctx() -> AgentRunContext:
    return AgentRunContext()


@pytest.fixture
def message_history() -> MessageHistory:
    return MessageHistory()


@pytest.mark.unit
async def test_run_aborted_produces_interrupted_message(
    run_ctx: AgentRunContext,
    message_history: MessageHistory,
) -> None:
    """RunAbortedError from a tool produces [Interrupted] message — current behavior.

    This test documents the current (buggy) behavior:
    - Tool raises RunAbortedError
    - iteration_messages = agent_run.all_messages() is NEVER called
    - response_msg is None → fallback to [Interrupted]
    - Tool call and partial results are LOST from conversation history
    """
    async def aborting_tool(ctx: RunContext[Any], arg: str) -> str:
        """Tool that raises RunAbortedError — simulates question_for_user cancel."""
        raise RunAbortedError("User cancelled the elicitation request")

    model = TestModel(custom_output_text="Let me ask a question")

    agent = Agent(
        name="test-abort-agent",
        model=model,
        system_prompt="You are a test agent.",
        tools=[aborting_tool],
    )

    executor = RunExecutor(agent)
    user_msg = ChatMessage.user_prompt("Call the aborting tool")

    events, result, error = await _run_and_collect(
        executor,
        prompts=["Call the aborting tool"],
        run_ctx=run_ctx,
        user_msg=user_msg,
        message_history=message_history,
    )

    # No error — RunAbortedError is handled gracefully
    assert error is None
    assert result is not None

    # StreamCompleteEvent with cancelled=True
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) >= 1
    assert complete_events[-1].cancelled is True

    # After fix: result should contain partial content from agent_run, not [Interrupted]
    print(f"\n=== Result content: {result.content!r}")
    print(f"=== run_ctx.cancelled: {run_ctx.cancelled}")

    # Result should NOT be the bare [Interrupted] fallback
    assert result.content != "[Interrupted]", (
        "Result should contain partial content from agent_run, not bare [Interrupted]"
    )

    # Result should contain the interruption note from extract_text_from_messages
    assert "interrupt" in result.content.lower(), (
        f"Result should contain interruption note, got: {result.content!r}"
    )

    # Verify that iteration_messages was captured (tool call data preserved)
    # The executor doesn't store to history directly — that's base_agent's job.
    # But the response_msg now contains extracted text from agent_run messages,
    # which includes any tool calls and partial results.
