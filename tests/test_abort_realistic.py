#!/usr/bin/env python3
"""
Realistic test for Ctrl+C/Esc cancellation in OpenCode server.

This test simulates the ACTUAL OpenCode server flow:
1. POST /message route (synchronous HTTP request, holds agent_lock)
2. POST /abort route (separate HTTP request)
3. Verify that abort_session() actually interrupts the running stream

Key differences from previous test:
- Uses actual ServerState (not simplified)
- Simulates HTTP request flow more accurately
- Checks if agent.interrupt() is called correctly
"""

import asyncio
import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool_server.opencode_server.models import (
    MessageWithParts,
    MessageRequest,
    Session,
    SessionStatus,
    SessionStatusEvent,
    SessionIdleEvent,
    UserMessage,
    TimeCreated,
    PartUpdatedEvent,
    TextPartInput,
)
from agentpool_server.opencode_server.state import ServerState
from agentpool.agents.events import StreamCompleteEvent
from agentpool_server.opencode_server.routes.message_routes import (
    _process_message_locked,
)
from agentpool_server.opencode_server.routes.session_routes import abort_session


class SlowTestModel(TestModel):
    """TestModel that simulates a long-running LLM call (3 seconds)."""

    async def request_stream(self, messages, model_settings, model_request_parameters):
        """Simulate a slow LLM call by sleeping for 3 seconds."""
        print("[SlowTestModel] Simulating slow LLM call, sleeping for 3s...")
        await asyncio.sleep(3.0)  # Simulate slow LLM call
        yield "done"


@pytest.mark.asyncio
async def test_abort_session_interrupts_actual_flow():
    """Test that abort_session() correctly interrupts a running stream in actual server flow."""
    # Create Agent with SlowTestModel
    agent = Agent(
        name="test-agent",
        model=SlowTestModel(),
    )

    # Create ServerState with working_dir and agent
    state = ServerState(working_dir="/tmp/test", agent=agent)

    # Create session
    session_id = "test-session"
    from agentpool_server.opencode_server.models import TimeCreatedUpdated
    import time

    now = int(time.time() * 1000)
    state.sessions[session_id] = Session(
        id=session_id,
        title="Test Session",
        time=TimeCreatedUpdated(created=now, updated=now),
        project_id="test-project",
        directory="/tmp/test",
    )
    state.session_status[session_id] = SessionStatus(type="idle")
    state.messages[session_id] = []  # Initialize messages list

    # Track events
    events_received = []
    original_broadcast = state.broadcast_event

    async def track_broadcast(event):
        events_received.append(type(event).__name__)
        await original_broadcast(event)

    state.broadcast_event = track_broadcast

    # Simulate POST /message route (holds agent_lock)
    async def simulate_message_route():
        """Simulate POST /message processing."""
        print("[TEST] Starting POST /message route...")

        # Create user message (like message_routes.py:264-296)
        user_msg_id = "user-1"
        user_message = UserMessage(
            id=user_msg_id,
            session_id=session_id,
            time=TimeCreated(created=now),
            agent="default",
            model=None,
        )

        # Create user_msg_with_parts (like message_routes.py:272-296)
        user_msg_with_parts = MessageWithParts(info=user_message)
        text_part = user_msg_with_parts.add_text_part("Hello")
        await state.broadcast_event(PartUpdatedEvent.create(text_part))
        state.messages[session_id].append(user_msg_with_parts)

        # Create a simple MessageRequest to avoid None.parts error
        from agentpool_server.opencode_server.models import MessageRequest
        request = MessageRequest(
            message_id=user_msg_id,
            parts=[
                TextPartInput(text="Hello"),
            ],
        )

        print("[TEST] About to call _process_message_locked()...")
        print(f"[TEST] agent_lock locked: {state.agent_lock.locked()}")

        # This should block until abort_session() interrupts
        assistant_msg = await _process_message_locked(
            state=state,
            session_id=session_id,
            request=request,  # Use proper request
            user_msg_id=user_msg_id,
            user_msg_with_parts=user_msg_with_parts,
            mark_busy=True,
            mark_idle=True,
        )

        print(f"[TEST] Message route completed, result: {assistant_msg}")
        return assistant_msg

    # Start message route in background
    message_task = asyncio.create_task(simulate_message_route())

    # Wait a bit to ensure stream started
    await asyncio.sleep(0.1)

    # Verify session is busy
    assert state.session_status[session_id].type == "busy", "Session should be busy"
    print("[TEST] ✓ Session is busy")

    # Now simulate POST /abort (user presses Ctrl+C)
    print("[TEST] User presses Ctrl+C, calling POST /abort...")

    # THE BUG: abort_session() will BLOCK on state.agent_lock!
    # This is because get_or_load_session() acquires agent_lock at line 517
    # but message_route() holds it inside _process_message_locked() at line 392
    try:
        result = await asyncio.wait_for(abort_session(session_id, state), timeout=2.0)
        print(f"[TEST] abort_session() returned: {result}")
    except asyncio.TimeoutError:
        print("[TEST] ✗ BUG CONFIRMED: abort_session() BLOCKED waiting for agent_lock!")
        print("[TEST] This is the root cause: abort_session() cannot interrupt while")
        print("[TEST] message_route() holds agent_lock")
        # Cleanup
        message_task.cancel()
        try:
            await message_task
        except asyncio.CancelledError:
            pass
        raise AssertionError("BUG CONFIRMED: abort_session() blocked on agent_lock")
    except Exception as e:
        print(f"[TEST] abort_session() raised exception: {e}")
        raise

    # Wait for message task to complete (should be quick if interrupt worked)
    try:
        await asyncio.wait_for(message_task, timeout=2.0)
        print("[TEST] ✓ Message task completed quickly (interrupt worked)")
    except asyncio.TimeoutError:
        print("[TEST] ✗ Message task DID NOT complete quickly (interrupt FAILED)")
        # Try to cancel the task
        message_task.cancel()
        try:
            await message_task
        except asyncio.CancelledError:
            pass
        raise AssertionError("abort_session() failed to interrupt the running stream")

    # Verify events
    print(f"[TEST] Events received: {events_received}")
    assert "SessionIdleEvent" in events_received, "Should broadcast SessionIdleEvent"
    print("[TEST] ✓ SessionIdleEvent was broadcast")


if __name__ == "__main__":
    asyncio.run(test_abort_session_interrupts_actual_flow())
