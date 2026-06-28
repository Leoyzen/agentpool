"""Integration test: NativeTurn → RunHandle → EventBus → consumer.

Verifies the full event pipeline that xeno-agent's background task
provider depends on:

1. RunHandle.start() creates a NativeTurn via agent.create_turn()
2. NativeTurn.execute() yields events including StreamCompleteEvent
3. RunHandle publishes events to EventBus
4. EventBus consumer (simulating xeno-agent _run_and_stream) receives
   StreamCompleteEvent and terminates

This test was created to reproduce the bug where NativeTurn.execute()
was missing ``yield StreamCompleteEvent(...)`` at the end, causing the
EventBus consumer to hang forever waiting for a event that never
arrived.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

import anyio
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import StreamCompleteEvent
from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_native_turn_events_reach_event_bus_consumer() -> None:
    """Full pipeline: RunHandle + real NativeTurn + EventBus consumer.

    Simulates xeno-agent's _run_and_stream() which:
    1. Subscribes to EventBus BEFORE starting the run
    2. Calls receive_request / start() to kick off the turn
    3. Waits for StreamCompleteEvent on the EventBus queue
    4. Must terminate (not hang) when the turn completes

    If NativeTurn doesn't yield StreamCompleteEvent, this test hangs
    forever (or times out).
    """
    agent = Agent(
        name="test-integration",
        model=TestModel(custom_output_text="integration response"),
    )
    async with agent:
        event_bus = EventBus()

        # Simulate SessionState with a turn_lock
        from agentpool.orchestrator.core import SessionState

        session = SessionState(
            session_id="test-integration-session",
            agent_name="test-integration",
        )

        run_ctx = AgentRunContext(
            session_id="test-integration-session",
            event_bus=event_bus,
        )

        run_handle = RunHandle(
            run_id="test-run-integration",
            session_id="test-integration-session",
            agent_type="test-integration",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Step 1: Subscribe to EventBus BEFORE starting the run
        # (mirrors xeno-agent's _run_and_stream pattern)
        receive_stream = await event_bus.subscribe(
            "test-integration-session",
            scope="session",
        )

        # Step 2: Start the run in a background task
        async def _drive_run() -> None:
            async for _ in run_handle.start("test prompt"):
                pass  # events are published to EventBus inside start()

        drive_task = asyncio.create_task(_drive_run())

        # Step 3: Consume events from EventBus, waiting for StreamCompleteEvent
        received_events: list[RichAgentStreamEvent[Any]] = []
        stream_complete_received = False

        try:
            # Use a timeout to prevent infinite hang (the bug being tested)
            async with asyncio.timeout(10):
                while True:
                    try:
                        envelope = await receive_stream.receive()
                    except anyio.EndOfStream:
                        break

                    event = (
                        envelope.event
                        if hasattr(envelope, "event")
                        else envelope
                    )
                    received_events.append(event)

                    if isinstance(event, StreamCompleteEvent):
                        stream_complete_received = True
                        break
        except TimeoutError:
            pytest.fail(
                "Timed out waiting for StreamCompleteEvent on EventBus. "
                f"Received {len(received_events)} events but none was "
                "StreamCompleteEvent. This confirms the bug: NativeTurn."
                "execute() does not yield StreamCompleteEvent."
            )
        finally:
            drive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await drive_task

        # Assertions
        assert stream_complete_received, (
            "Consumer never received StreamCompleteEvent from EventBus. "
            f"Events received: {[type(e).__name__ for e in received_events]}"
        )

        # Should have received at least RunStartedEvent + StreamCompleteEvent
        event_types = [type(e).__name__ for e in received_events]
        assert "RunStartedEvent" in event_types, (
            f"RunStartedEvent not found in events: {event_types}"
        )
        assert event_types[-1] == "StreamCompleteEvent", (
            f"Last event must be StreamCompleteEvent, got {event_types[-1]}"
        )
