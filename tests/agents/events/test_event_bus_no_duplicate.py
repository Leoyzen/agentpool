"""Test that StreamEventEmitter._emit() publishes exactly once per event.

Prevents regression of the old dual-consumer issue where events were
published to both EventBus and run_ctx.event_queue simultaneously.
"""

from __future__ import annotations

import asyncio

import pytest

from agentpool import Agent
from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamEventEmitter
from agentpool.orchestrator.core import EventBus


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


@pytest.mark.anyio
async def test_emit_publishes_exactly_once_to_event_bus() -> None:
    """When event_bus is set, _emit() publishes to EventBus exactly once.

    Verifies that:
    1. The EventBus subscriber receives exactly one copy of the event.
    2. The run_ctx.event_queue receives zero events (no dual publish).
    """
    session_id = "test-session-001"
    event_bus = EventBus()
    queue = await event_bus.subscribe(session_id)

    agent = Agent(name="test_agent", model="test")
    agent.session_id = session_id

    run_ctx = AgentRunContext()
    ctx = AgentContext(node=agent, run_ctx=run_ctx)

    emitter = StreamEventEmitter(ctx, event_bus=event_bus)

    event = RunStartedEvent(session_id=session_id, run_id="run-1")
    await emitter.emit_event(event)

    # EventBus subscriber should receive exactly one event
    received = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert received is not None
    assert isinstance(received, RunStartedEvent)
    assert received.run_id == "run-1"

    # No additional events should be on the EventBus queue
    assert queue.empty()

    # run_ctx.event_queue should be empty (no dual-consumer fallback)
    assert run_ctx.event_queue.empty()


@pytest.mark.anyio
async def test_emit_multiple_events_each_published_once() -> None:
    """Multiple events are each published exactly once to EventBus."""
    session_id = "test-session-002"
    event_bus = EventBus()
    queue = await event_bus.subscribe(session_id)

    agent = Agent(name="test_agent", model="test")
    agent.session_id = session_id

    run_ctx = AgentRunContext()
    ctx = AgentContext(node=agent, run_ctx=run_ctx)

    emitter = StreamEventEmitter(ctx, event_bus=event_bus)

    events = [
        RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        for i in range(3)
    ]
    for event in events:
        await emitter.emit_event(event)

    received: list[RunStartedEvent] = []
    while not queue.empty():
        ev = queue.get_nowait()
        if ev is not None:
            received.append(ev)

    assert len(received) == 3
    assert [ev.run_id for ev in received] == ["run-0", "run-1", "run-2"]
    assert run_ctx.event_queue.empty()
