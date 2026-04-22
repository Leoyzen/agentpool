#!/usr/bin/env python3
"""Test to reproduce the Ctrl+C/Esc cancellation issue in OpenCode 1.4.4+.

This test simulates the OpenCode TUI flow:
1. User sends a message (synchronous POST /{session_id}/message)
2. User presses Ctrl+C/Esc (POST /{session_id}/abort)
3. abort_session() calls agent.interrupt() without run_ctx
4. Expected: stream should exit promptly
5. Bug: stream continues running
"""

import asyncio
import pytest
from pydantic_ai.models.test import TestModel, TestStreamedResponse

from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool_server.opencode_server.routes.message_routes import (
    _process_message_locked,
)
from agentpool_server.opencode_server.server import ServerState


class SlowTestModel(TestModel):
    """TestModel that inserts a delay to simulate long-running LLM call."""

    @classmethod
    def _request(cls, messages, model_settings, model_request_parameters):
        """Override to create a response."""
        # Sleep before yielding response to create a window for interrupt
        # This simulates a long-running LLM API call
        return "Slow response that takes time"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_abort_session_interrupts_synchronous_message():
    """Test that abort_session can interrupt a synchronous message processing flow.

    This reproduces the bug where:
    1. Synchronous POST /message is processing (holds agent_lock)
    2. abort_session() is called (POST /abort)
    3. abort_session() calls agent.interrupt() without run_ctx
    4. Stream should exit promptly but doesn't
    """
    # Setup: create a slow agent
    model = SlowTestModel()
    agent = Agent(name="test-agent", model=model)

    # Start the stream in a task (simulates POST /message route)
    stream_started = asyncio.Event()
    events_received = []

    async def run_stream():
        """Simulate the synchronous message processing flow."""
        async for event in agent.run_stream("Test prompt"):
            stream_started.set()
            events_received.append(event)
            if isinstance(event, StreamCompleteEvent):
                break

    stream_task = asyncio.create_task(run_stream())

    # Wait for stream to start
    await asyncio.wait_for(stream_started.wait(), timeout=2.0)

    # Simulate abort_session: call interrupt with NO run_ctx
    # This is what OpenCode abort_session() does
    print("Calling interrupt()...")
    await agent.interrupt()

    # Give cancellation a moment to propagate (abort_session sleeps 0.1s)
    await asyncio.sleep(0.1)

    # Check if stream stopped
    if stream_task.done():
        print("Stream stopped successfully after interrupt()")
        print(f"Events received: {len(events_received)}")
    else:
        print("BUG: Stream still running after interrupt()!")
        print(f"Events received so far: {len(events_received)}")

        # Clean up by cancelling the task
        stream_task.cancel()
        try:
            await stream_task
        except asyncio.CancelledError:
            pass

    # Verify interrupt state
    assert agent._cancelled is True, "Agent should be marked as cancelled"

    # Verify that stream actually stopped (this will fail if bug exists)
    # Timeout of 3.0 seconds should be enough for a cancelled stream
    try:
        await asyncio.wait_for(stream_task, timeout=3.0)
        print("Test PASSED: Stream stopped after interrupt()")
    except asyncio.TimeoutError:
        pytest.fail("BUG REPRODUCED: Stream did not stop after interrupt() call")


if __name__ == "__main__":
    asyncio.run(test_abort_session_interrupts_synchronous_message())
