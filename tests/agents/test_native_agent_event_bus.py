"""Tests for the event_bus branch in _run_agentlet_core().

These tests verify that when run_ctx.event_bus is set, tool completion events
are published directly to the event_bus (session pool mode). When event_bus is
None, tool completion events flow through the local event_queue (standalone mode).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from pydantic_ai import BaseToolCallPart, FunctionToolCallEvent, FunctionToolResultEvent
from pydantic_ai.models.test import TestModel
from pydantic_ai.messages import ToolCallPart, ToolReturnPart
import pytest

from agentpool import Agent, ChatMessage
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent
from agentpool.agents.native_agent.helpers import process_tool_event
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

        # After fix: ToolCallCompleteEvent flows through local queue -> stream -> EventBus
        # Local queue should contain ToolCallCompleteEvent (enqueued by _run_agentlet_core)
        local_tool_complete = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(local_tool_complete) >= 1, (
            f"ToolCallCompleteEvent should be in local queue (gets forwarded to EventBus via stream), "
            f"got {len(local_tool_complete)}"
        )
        # Verify the one from _run_agentlet_core has our message_id
        our_events = [e for e in local_tool_complete if e.message_id == "msg-1"]
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

        # After fix: ToolCallCompleteEvent flows through local queue -> stream -> EventBus
        local_tool_complete = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]
        assert len(local_tool_complete) >= 1, (
            f"ToolCallCompleteEvent should be in local queue (gets forwarded to EventBus via stream), "
            f"got {len(local_tool_complete)}"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_redflag_event_bus_branch_missing_tool_call_start_event() -> None:
    """RED FLAG: SessionPool mode lacks ToolCallStartEvent mapping.

    In standalone mode (run_executor.py), FunctionToolCallEvent is mapped to
    ToolCallStartEvent before being placed in the event queue. This gives the
    event_processor a rich start event with title and structured input.

    In SessionPool mode (_run_agentlet_core event_bus branch), this mapping is
    missing. Only raw FunctionToolCallEvent flows through the local queue, and
    ToolCallCompleteEvent is published directly to the EventBus. The result is
    that the opencode event_processor may see ToolCallCompleteEvent BEFORE
    FunctionToolCallEvent (race condition), causing the completion to be dropped.

    REGRESSION TEST:
      After the fix, _run_agentlet_core should produce ToolCallStartEvent
      (either by mapping FunctionToolCallEvent or by placing the raw event
      into run_ctx.event_queue for uniform publishing).
    """
    from pydantic_ai import FunctionToolCallEvent

    model = TestModel(call_tools="all")

    # Standalone mode: process_tool_event returns ToolCallCompleteEvent to local queue
    async with Agent(name="standalone-agent", model=model, tools=[greet]) as agent:
        run_ctx_standalone = AgentRunContext(event_bus=None, session_id="sess-standalone")
        user_msg = ChatMessage.user_prompt("Greet someone")
        queue_standalone: asyncio.Queue[Any] = asyncio.Queue()

        await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx_standalone,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-standalone",
            session_id="sess-standalone",
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=queue_standalone,
            start_time=time.perf_counter(),
        )

        standalone_events = _drain_queue(queue_standalone)
        standalone_has_func_call = any(
            isinstance(e, FunctionToolCallEvent) for e in standalone_events
        )

    # SessionPool mode: process_tool_event publishes ToolCallCompleteEvent directly to EventBus
    async with Agent(name="sessionpool-agent", model=model, tools=[greet]) as agent:
        event_bus = EventBus()
        session_id = "sess-pool"
        bus_queue = await event_bus.subscribe(session_id)

        run_ctx_pool = AgentRunContext(event_bus=event_bus, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        queue_pool: asyncio.Queue[Any] = asyncio.Queue()

        await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx_pool,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-pool",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=queue_pool,
            start_time=time.perf_counter(),
        )

        pool_local_events = _drain_queue(queue_pool)
        pool_bus_events = _drain_queue(bus_queue)

        pool_local_has_func_call = any(
            isinstance(e, FunctionToolCallEvent) for e in pool_local_events
        )
        pool_bus_has_tool_complete = any(
            isinstance(e, ToolCallCompleteEvent) for e in pool_bus_events
        )

    # Both modes should have the raw FunctionToolCallEvent somewhere
    assert standalone_has_func_call, "Standalone mode should have FunctionToolCallEvent"
    assert pool_local_has_func_call, (
        "SessionPool mode local queue should have FunctionToolCallEvent"
    )
    # After fix: ToolCallCompleteEvent flows through local queue -> stream -> EventBus
    # (TurnRunner forwards local queue to EventBus, but this test calls _run_agentlet_core directly)
    pool_local_tool_complete = any(
        isinstance(e, ToolCallCompleteEvent) for e in pool_local_events
    )
    assert pool_local_tool_complete, (
        "SessionPool mode local queue should have ToolCallCompleteEvent (from process_tool_event)"
    )

    # After fix: ToolCallStartEvent is mapped from FunctionToolCallEvent in _run_agentlet_core
    pool_local_has_tool_start = any(
        isinstance(e, ToolCallStartEvent) for e in pool_local_events
    )
    assert pool_local_has_tool_start, (
        "SessionPool mode local queue should have ToolCallStartEvent (mapped from FunctionToolCallEvent)"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_process_tool_event_never_publishes_to_event_bus() -> None:
    """process_tool_event() should never publish directly to EventBus.

    After the fix, process_tool_event() always returns the combined event
    and never publishes directly, regardless of run_ctx.event_bus state.
    """
    event_bus = EventBus()
    session_id = "test-session-process-tool"
    bus_queue = await event_bus.subscribe(session_id)

    run_ctx = AgentRunContext(event_bus=event_bus, session_id=session_id)
    pending_tcs: dict[str, BaseToolCallPart] = {}

    # Simulate a tool call start
    tool_part = ToolCallPart(tool_name="greet", args={"name": "test"}, tool_call_id="tc-001")
    start_event = FunctionToolCallEvent(part=tool_part)

    result = await process_tool_event(
        agent_name="test-agent",
        event=start_event,
        pending_tool_calls=pending_tcs,
        message_id="msg-1",
        run_ctx=run_ctx,
    )
    assert result is None, "process_tool_event should return None for start events"

    # Simulate a tool call result
    return_part = ToolReturnPart(tool_name="greet", tool_call_id="tc-001", content="Hello, test!")
    result_event = FunctionToolResultEvent(result=return_part)

    combined = await process_tool_event(
        agent_name="test-agent",
        event=result_event,
        pending_tool_calls=pending_tcs,
        message_id="msg-1",
        run_ctx=run_ctx,
    )
    assert combined is not None, "process_tool_event should return ToolCallCompleteEvent"
    assert combined.tool_name == "greet"

    # Verify NO events were published to EventBus
    with pytest.raises(asyncio.QueueEmpty):
        bus_queue.get_nowait()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_tool_event_fifo_ordering() -> None:
    """ToolCallStartEvent is enqueued before ToolCallCompleteEvent in SessionPool mode."""
    model = TestModel(call_tools="all")
    async with Agent(name="fifo-test-agent", model=model, tools=[greet]) as agent:
        event_bus = EventBus()
        session_id = "test-session-fifo"
        run_ctx = AgentRunContext(event_bus=event_bus, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-fifo",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=time.perf_counter(),
        )

        local_events = _drain_queue(event_queue)

        # Find the indices of ToolCallStartEvent and ToolCallCompleteEvent
        start_idx = None
        complete_idx = None
        for i, e in enumerate(local_events):
            if isinstance(e, ToolCallStartEvent) and start_idx is None:
                start_idx = i
            if isinstance(e, ToolCallCompleteEvent) and complete_idx is None:
                complete_idx = i

        assert start_idx is not None, "ToolCallStartEvent should be in local queue"
        assert complete_idx is not None, "ToolCallCompleteEvent should be in local queue"
        assert start_idx < complete_idx, (
            f"ToolCallStartEvent (index {start_idx}) should come before "
            f"ToolCallCompleteEvent (index {complete_idx})"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_duplicate_tool_events_in_sessionpool_mode() -> None:
    """Exactly one ToolCallStartEvent and one ToolCallCompleteEvent per tool call.

    With EventBusHooksAdapter tool events disabled, there should be no duplicates.
    """
    model = TestModel(call_tools="all")
    async with Agent(name="dup-test-agent", model=model, tools=[greet]) as agent:
        event_bus = EventBus()
        session_id = "test-session-dup"
        run_ctx = AgentRunContext(event_bus=event_bus, session_id=session_id)
        user_msg = ChatMessage.user_prompt("Greet someone")
        event_queue: asyncio.Queue[Any] = asyncio.Queue()

        await agent._run_agentlet_core(
            prompts=["Greet someone"],
            run_ctx=run_ctx,
            user_msg=user_msg,
            message_history=agent.conversation,
            message_id="msg-dup",
            session_id=session_id,
            parent_id=None,
            input_provider=None,
            deps=None,
            event_queue=event_queue,
            start_time=time.perf_counter(),
        )

        local_events = _drain_queue(event_queue)
        start_events = [e for e in local_events if isinstance(e, ToolCallStartEvent)]
        complete_events = [e for e in local_events if isinstance(e, ToolCallCompleteEvent)]

        assert len(start_events) == 1, (
            f"Expected exactly 1 ToolCallStartEvent, got {len(start_events)}"
        )
        assert len(complete_events) == 1, (
            f"Expected exactly 1 ToolCallCompleteEvent, got {len(complete_events)}"
        )
