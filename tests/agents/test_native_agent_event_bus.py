"""Tests for the event_bus branch in _run_agentlet_core().

These tests verify that when run_ctx.event_bus is set, tool completion events
are published directly to the event_bus (session pool mode). When event_bus is
None, tool completion events flow through the local event_queue (standalone mode).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent, ChatMessage
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import ToolCallCompleteEvent
from agentpool.orchestrator.core import EventBus


def greet(name: str) -> str:
    """Greet someone."""
    return f"Hello, {name}!"


def _drain_queue(queue: asyncio.Queue[Any]) -> list[Any]:
    """Drain all items from an asyncio queue."""
    items = []
    while True:
        try:
            items.append(queue.get_nowait())
        except asyncio.QueueEmpty:
            break
    return items


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_branch_publishes_tool_complete_to_bus() -> None:
    """When run_ctx.event_bus is set, ToolCallCompleteEvent goes to event_bus."""
    model = TestModel()  # default call_tools='all' triggers tool calls
    async with Agent(name="eventbus-test-agent", model=model, tools=[greet]) as agent:
        event_bus = EventBus()
        session_id = "test-session-bus"

        # Subscribe to event_bus before running
        bus_queue = await event_bus.subscribe(session_id)

        run_ctx = AgentRunContext(event_bus=event_bus, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        response = await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-1",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=time.perf_counter(),
        )

        assert response is not None
        assert isinstance(response.content, str)

        # Collect events from local event_queue
        local_events = _drain_queue(event_queue)

        # Collect events from event_bus
        bus_events = _drain_queue(bus_queue)

        # Local queue should contain stream events but NO ToolCallCompleteEvent
        local_tool_complete = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(local_tool_complete) == 0, (
            f"ToolCallCompleteEvent should NOT be in local queue when event_bus is set, "
            f"got {len(local_tool_complete)}"
        )

        # event_bus should have ToolCallCompleteEvent (may be >1 due to hooks/capabilities)
        bus_tool_complete = [e for e in bus_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(bus_tool_complete) >= 1, (
            f"Expected at least 1 ToolCallCompleteEvent on event_bus, got {len(bus_tool_complete)}"
        )
        # Verify the one from _run_agentlet_core has our message_id
        our_events = [e for e in bus_tool_complete if e.message_id == "msg-1"]
        assert len(our_events) == 1, (
            f"Expected exactly 1 ToolCallCompleteEvent with message_id='msg-1', "
            f"got {len(our_events)}"
        )
        assert our_events[0].tool_name == "greet"
        assert our_events[0].agent_name == "eventbus-test-agent"

        # Local queue should still have raw stream events
        assert len(local_events) > 0, "Expected stream events in local queue"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_event_bus_branch_puts_tool_complete_in_queue() -> None:
    """When run_ctx.event_bus is None, ToolCallCompleteEvent goes to local queue."""
    model = TestModel()  # default call_tools='all' triggers tool calls
    async with Agent(name="no-eventbus-test-agent", model=model, tools=[greet]) as agent:
        session_id = "test-session-no-bus"

        run_ctx = AgentRunContext(event_bus=None, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        response = await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-1",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=time.perf_counter(),
        )

        assert response is not None
        assert isinstance(response.content, str)

        # Collect events from local event_queue
        local_events = _drain_queue(event_queue)

        # Local queue should have ToolCallCompleteEvent
        local_tool_complete = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(local_tool_complete) == 1, (
            f"Expected exactly 1 ToolCallCompleteEvent in local queue, got {len(local_tool_complete)}"
        )
        assert local_tool_complete[0].tool_name == "greet"
        assert local_tool_complete[0].agent_name == "no-eventbus-test-agent"
        assert local_tool_complete[0].message_id == "msg-1"

        # Local queue should also have raw stream events
        assert len(local_events) > 1, "Expected stream events plus ToolCallCompleteEvent"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_event_bus_branch_basic_stream_events_still_flow() -> None:
    """Stream events still reach local queue even when event_bus is active."""
    model = TestModel()  # default call_tools='all' triggers tool calls
    async with Agent(name="stream-test-agent", model=model, tools=[greet]) as agent:
        event_bus = EventBus()
        session_id = "test-session-stream"

        run_ctx = AgentRunContext(event_bus=event_bus, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-1",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=time.perf_counter(),
        )

        local_events = _drain_queue(event_queue)

        # Should have at least some events (stream events from the model/tool calls)
        assert len(local_events) > 0, "Expected stream events in local queue"

        # ToolCallCompleteEvent should NOT be in local queue (goes to event_bus instead)
        local_tool_complete = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(local_tool_complete) == 0, (
            f"ToolCallCompleteEvent should NOT be in local queue when event_bus is set, "
            f"got {len(local_tool_complete)}"
        )
