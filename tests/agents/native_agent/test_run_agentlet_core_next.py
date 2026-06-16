"""TDD tests for _run_agentlet_core() next() loop migration.

Validates that _run_agentlet_core() fires after_node_run hooks so
PendingMessageDrainCapability can drain when_idle messages.

The fix replaces `async for node in agent_run:` with an explicit
`while True: node = await agent_run.next(node)` loop, mirroring
RunExecutor.execute().
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.models.test import TestModel
from pydantic_graph import End

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage, MessageHistory

# Import at module level for type annotation resolution in test functions
try:
    from pydantic_ai import RunContext as PydanticRunContext
except ImportError:
    PydanticRunContext = None  # type: ignore[assignment]


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


def _make_mock_stream() -> MagicMock:
    """Create a mock stream that yields no events (empty async iter)."""
    mock_stream = MagicMock()
    mock_stream.__aenter__ = AsyncMock(return_value=mock_stream)
    mock_stream.__aiter__ = MagicMock(return_value=_AsyncListIterator([]))
    return mock_stream


# ---------------------------------------------------------------------------
# GREEN: _run_agentlet_core() uses next() and drains when_idle messages
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_agentlet_core_uses_next_loop() -> None:
    """GREEN: _run_agentlet_core() uses next() to drive agent_run.

    After the fix, _run_agentlet_core() should call agent_run.next(node)
    instead of using bare `async for`. This test verifies the next() calls
    by mocking agent_run and checking that next() is invoked.
    """
    from pydantic_ai._agent_graph import ModelRequestNode

    agent = Agent(
        name="test-next-loop",
        model=TestModel(custom_output_text="hello world"),
    )

    mock_agent_run = AsyncMock()
    mock_agent_run.__aenter__ = AsyncMock(return_value=mock_agent_run)
    mock_agent_run.__aexit__ = AsyncMock(return_value=None)

    # Simulate nodes for the next() loop
    user_prompt_node = MagicMock()
    model_request_node = MagicMock(spec=ModelRequestNode)
    model_request_node.stream = MagicMock(return_value=_make_mock_stream())
    end_node = End(data="final_result")

    # next_node property returns the first node
    type(mock_agent_run).next_node = user_prompt_node

    # next() call sequence: model_request_node, then End
    mock_agent_run.next = AsyncMock(side_effect=[model_request_node, end_node])

    # result property
    mock_result = MagicMock()
    mock_result.output = "final_result"
    mock_result.response = MagicMock()
    mock_result.response.model_name = "test-model"
    mock_result.response.finish_reason = "stop"
    mock_result.response.provider_name = "test"
    mock_result.response.provider_details = {}
    mock_result.response.usage = MagicMock()
    mock_result.response.usage.request_tokens = 0
    mock_result.response.usage.response_tokens = 0
    mock_result.usage = MagicMock()
    mock_result.usage.requests = 1
    mock_result.usage.request_tokens = 10
    mock_result.usage.response_tokens = 5
    mock_result.usage.total_tokens = 15
    mock_result.new_messages = MagicMock(return_value=[])
    type(mock_agent_run).result = mock_result
    mock_agent_run.ctx = MagicMock()

    mock_agentlet = MagicMock()
    mock_agentlet.iter = MagicMock(return_value=mock_agent_run)

    run_ctx = AgentRunContext(session_id="test-session")
    user_msg = ChatMessage.user_prompt(message="test prompt")
    message_history = MessageHistory()
    event_queue: asyncio.Queue[Any] = asyncio.Queue()

    with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
        response_msg = await agent._run_agentlet_core(
            prompts=["test"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=message_history,
            message_id="msg-1",
            session_id="test-session",
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=0.0,
        )

    assert response_msg is not None
    assert response_msg.content == "final_result"

    # GREEN: next() should have been called (twice: model_request_node, then End)
    assert mock_agent_run.next.call_count == 2, (
        f"Expected next() to be called twice, got {mock_agent_run.next.call_count}. "
        "The fix should use explicit next() loop instead of async for."
    )

    # Verify next() was called with the correct nodes
    mock_agent_run.next.assert_any_call(user_prompt_node)
    mock_agent_run.next.assert_any_call(model_request_node)


# ---------------------------------------------------------------------------
# Verify the fix doesn't break basic streaming (regression guard)
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_basic_streaming_still_works() -> None:
    """Basic streaming with no tools should still work after the fix."""
    agent = Agent(
        name="test-basic-stream",
        model=TestModel(custom_output_text="simple response"),
    )

    events: list[Any] = []
    async for event in agent.run_stream("hello"):
        events.append(event)

    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1
    assert "simple response" in str(complete_events[0].message.content)


# ---------------------------------------------------------------------------
# GREEN: After the fix, when_idle messages should be drained
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_next_loop_drains_when_idle_messages() -> None:
    """GREEN: after the fix, when_idle messages are drained via next().

    This test validates the expected behavior using pydantic-ai
    Agent directly (which uses next() correctly). After the fix,
    _run_agentlet_core() should exhibit the same behavior.
    """
    from pydantic_ai import Agent as PydanticAIAgent
    from pydantic_ai.tools import Tool as PydanticTool

    enqueue_called: list[bool] = []

    async def enqueue_when_idle(ctx: PydanticRunContext[None]) -> str:
        """Tool that enqueues a when_idle message."""
        ctx.enqueue("WHEN_IDLE_FOLLOWUP", priority="when_idle")
        enqueue_called.append(True)
        return "tool_done"

    pydantic_agent = PydanticAIAgent(
        model=TestModel(
            call_tools=["enqueue_when_idle"],
            custom_output_text="hello from pydantic-ai",
        ),
        tools=[PydanticTool(enqueue_when_idle)],
    )

    # Drive via next() — this fires after_node_run hooks
    async with pydantic_agent.iter("call the tool") as run:
        node = run.next_node
        while True:
            if hasattr(node, "stream"):
                async with node.stream(run.ctx) as stream:
                    async for _event in stream:
                        pass
            node = await run.next(node)
            if isinstance(node, End):
                break

    assert enqueue_called, "Tool should have been called"

    # Verify the when_idle message was drained and appears in history
    all_messages_text = "\n".join(str(m) for m in run.all_messages())
    assert "WHEN_IDLE_FOLLOWUP" in all_messages_text, (
        "GREEN: when_idle message should appear in message history — "
        "after_node_run hooks fire via next() and drain the message."
    )
