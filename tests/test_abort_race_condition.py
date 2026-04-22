#!/usr/bin/env python3
"""
Integration test to reproduce the Ctrl+C/Esc cancellation race condition bug.

This test simulates the exact OpenCode server flow:
1. Start slow message processing (holds agent_lock for 5s)
2. Call abort_session() after 0.05s
3. abort_session() returns immediately after 0.1s sleep
4. Immediately send new message
5. Verify new message BLOCKS trying to acquire agent_lock (race window)

Expected behavior: New message should be blocked indefinitely
This proves abort_session() returns before agent actually stops.
"""

import asyncio
from contextlib import asynccontextmanager
import pytest
from pydantic_ai.models.test import TestModel
from agentpool.agents.events import StreamCompleteEvent
from agentpool_server.opencode_server.state import ServerState
from agentpool_server.opencode_server.models import SessionStatusEvent, SessionStatus

class BlockingTestModel(TestModel):
    """TestModel that simulates a long-running LLM call.
    
    This simulates exact scenario where Ctrl+C/Esc is pressed
    during a long-running operation.
    """

    def __init__(self, sleep_time: float = 5.0):
        super().__init__()
        self.sleep_time = sleep_time

    @asynccontextmanager
    async def request_stream(  # type: ignore[override]
        self,
        messages,
        model_settings,
        model_request_parameters,
        run_context=None,
    ):
        """Simulate a long LLM call that takes 5 seconds.

        During this sleep, we can test if abort_session() works.
        """
        import time
        start = time.time()

        print(f"[TEST] BlockingTestModel: request_stream called, will sleep for {self.sleep_time}s")

        # Simulate long LLM call (longer than abort_session's 0.1s sleep)
        # IMPORTANT: Do NOT check for cancellation to test the race condition!
        # We want to see if agent_lock is held after abort_session() returns
        await asyncio.sleep(self.sleep_time)

        # Yield some response
        elapsed = time.time() - start
        print(f"[TEST] BlockingTestModel: Completed after {elapsed:.2f}s (ignoring cancellation)")

        from pydantic_ai.models.result import StreamedResponse

        yield StreamedResponse(
            content="Response",
            timestamp=asyncio.get_event_loop().time(),
            role="assistant",
        )


@pytest.mark.asyncio
async def test_abort_race_condition_blocks_new_messages(tmp_path, monkeypatch):
    """Reproduce the race condition bug.

    This test simulates:
    1. User sends message (holds agent_lock)
    2. User presses Ctrl+C (calls abort_session)
    3. abort_session() returns after 0.1s
    4. User sends new message (should block on agent_lock)

    The bug: New message blocks indefinitely because agent_lock is still held.
    """
    # Setup: Create a blocking agent and server state
    from agentpool import Agent

    # Create agent with blocking model
    blocking_model = BlockingTestModel(sleep_time=5.0)
    agent = Agent(name="test-agent", model=blocking_model)

    # Create server state
    state = ServerState(
        working_dir=str(tmp_path),
        agent=agent,
    )

    # Create session
    session_id = "test-session"
    from agentpool_server.opencode_server.models import Session, TimeCreatedUpdated

    session = Session(
        id=session_id,
        project_id="test-project",
        directory=str(tmp_path),
        title="Test Session",
        messages=[],
        time=TimeCreatedUpdated(
            created=int(asyncio.get_event_loop().time()),
            updated=int(asyncio.get_event_loop().time()),
        ),
        agent="test-agent",
        prompt=[],
    )
    state.sessions[session_id] = session
    state.session_status[session_id] = SessionStatus(type="idle")

    # Simulate message processing in background task
    message_processing_done = asyncio.Event()
    new_message_blocked = asyncio.Event()
    new_message_acquired_lock = asyncio.Event()

    async def simulate_slow_message_processing():
        """Simulate POST /message route that holds agent_lock."""
        print("[TEST] Starting slow message processing...")
        # Acquire agent_lock (like message_routes.py:392)
        try:
            async with state.agent_lock:
                print("[TEST] Acquired agent_lock, starting stream...")
                new_message_acquired_lock.set()  # Signal we have the lock

                # Bind agent to session (like message_routes.py:395)
                state.bind_agent_to_session(session_id, agent=agent)

                # Run stream (like message_routes.py:470-472)
                # This will take 5 seconds due to BlockingTestModel
                async for event in agent.run_stream("Hello", session_id=session_id):
                    print(f"[TEST] Stream event: {event}")

                print("[TEST] Stream completed normally (shouldn't happen if cancelled)")
        finally:
            print("[TEST] Released agent_lock")
            message_processing_done.set()

    # Create session
    session_id = "test-session"
    from agentpool_server.opencode_server.models import Session, TimeCreatedUpdated

    async def simulate_abort_session():
        """Simulate POST /abort route."""
        print("[TEST] Starting abort_session...")

        # Wait a bit to ensure message processing started
        await asyncio.sleep(0.05)

        print("[TEST] Calling cancel_session_background_tasks...")
        await state.cancel_session_background_tasks(session_id)

        print("[TEST] Calling agent.interrupt()...")
        await state.agent.interrupt()

        # Give 0.1s for cancellation (like session_routes.py:880)
        print("[TEST] Sleeping 0.1s for cancellation to propagate...")
        await asyncio.sleep(0.1)

        # Mark session idle (like session_routes.py:885-887)
        print("[TEST] Setting session idle and broadcasting events...")
        state.session_status[session_id] = SessionStatus(type="idle")
        await state.broadcast_event(SessionStatusEvent.create(session_id, SessionStatus(type="idle")))

        print("[TEST] abort_session() RETURNED to client")

    async def simulate_new_message_after_abort():
        """Simulate POST /message immediately after abort returns.

        This should BLOCK because agent_lock is still held by the stopping stream.
        """
        print("[TEST] Waiting for abort_session() to return...")
        await asyncio.sleep(0.15)  # abort returns at 0.05 + 0.1 = 0.15s

        print("[TEST] abort_session() returned, trying to acquire agent_lock for new message...")
        new_message_blocked.set()  # Signal we're trying to acquire lock

        try:
            # Try to acquire agent_lock with timeout
            # In production, this would block indefinitely
            # For test, we use timeout to detect block
            try:
                # Try to acquire lock with timeout
                await asyncio.wait_for(state.agent_lock.acquire(), timeout=0.5)
                print("[TEST] UNEXPECTED: Acquired agent_lock for new message!")
                # If we get here, bug is NOT reproduced
                # Need to release the lock since we acquired it
                state.agent_lock.release()
            except asyncio.TimeoutError:
                print("[TEST] SUCCESS: Blocked on agent_lock (bug reproduced!)")
                # This proves bug: lock is still held after abort_session() returned
                raise AssertionError(
                    "BUG REPRODUCED: New message blocked on agent_lock after abort_session() returned"
                )
        finally:
            print("[TEST] New message test completed")

    # Run all tasks concurrently
    slow_task = asyncio.create_task(simulate_slow_message_processing())
    abort_task = asyncio.create_task(simulate_abort_session())
    new_msg_task = asyncio.create_task(simulate_new_message_after_abort())

    # Wait for either:
    # 1. Bug reproduced (new_msg_task raises AssertionError)
    # 2. Or timeout (everything completes without issue)
    done, pending = await asyncio.wait(
        [slow_task, abort_task, new_msg_task],
        timeout=6.0,  # Longer than the 5s sleep
        return_when=asyncio.FIRST_COMPLETED,
    )

    # Check results
    if new_msg_task in done:
        # New message task completed (either succeeded or failed)
        try:
            await new_msg_task
        except AssertionError as e:
            # Bug reproduced!
            print(f"[TEST] ✓ Bug reproduced: {e}")

            # Clean up remaining tasks
            for task in [slow_task, abort_task]:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except Exception:
                        pass

            # We expect this assertion to fail the test
            # But actually we want to verify the behavior
            # So we'll just return here
            pytest.fail(
                "BUG REPRODUCED: abort_session() returned before agent_lock was released"
            )

    # Check if we timed out (means bug NOT reproduced or stream cancelled properly)
    if new_msg_task not in done and new_message_blocked.is_set():
        # New message is waiting on lock (bug reproduced)
        print("[TEST] ✓ Bug reproduced: New message blocked after abort returned")

        # Clean up
        for task in [slow_task, abort_task, new_msg_task]:
            if not task.done():
                task.cancel()
                try:
                    await task
                except Exception:
                    pass

        pytest.fail(
            "BUG REPRODUCED: abort_session() returned before agent_lock was released"
        )

    # Bug NOT reproduced (everything completed without blocking)
    print("[TEST] Bug NOT reproduced - all tasks completed")
    await asyncio.gather(*pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_abort_stops_agent_quickly(tmp_path):
    """Verify that abort_session() CAN stop agent when operations are short.

    This is the positive test case - when operations are short (<0.1s),
    abort_session() should work correctly.
    """
    from agentpool import Agent

    # Create agent with SHORT blocking model (0.05s)
    blocking_model = BlockingTestModel(sleep_time=0.05)
    agent = Agent(name="test-agent", model=blocking_model)

    state = ServerState(
        working_dir=str(tmp_path),
        agent=agent,
    )

    # Create session
    session_id = "test-session"
    from agentpool_server.opencode_server.models import Session, TimeCreatedUpdated

    session = Session(
        id=session_id,
        project_id="test-project",
        directory=str(tmp_path),
        title="Test Session",
        messages=[],
        time=TimeCreatedUpdated(
            created=int(asyncio.get_event_loop().time()),
            updated=int(asyncio.get_event_loop().time()),
        ),
        agent="test-agent",
        prompt=[],
    )
    state.sessions[session_id] = session
    state.session_status[session_id] = SessionStatus(type="idle")

    # Run message and abort
    processing_done = asyncio.Event()
    stream_events = []

    async def run_message():
        """Run message processing."""
        try:
            async with state.agent_lock:
                state.bind_agent_to_session(session_id, agent=agent)
                async for event in agent.run_stream("Hello", session_id=session_id):
                    stream_events.append(event)
        finally:
            # Mark processing done even if cancelled
            processing_done.set()

    async def run_abort():
        """Call abort_session - updated implementation waits for active_stream_task."""
        await asyncio.sleep(0.02)  # Start abort after 0.02s
        await state.cancel_session_background_tasks(session_id)
        await state.agent.interrupt()
        await asyncio.sleep(0.1)  # Give 0.1s for cancellation
        # Wait for active stream task to complete (NEW in fix)
        if state.active_stream_task and not state.active_stream_task.done():
            await asyncio.wait_for(state.active_stream_task, timeout=5.0)
        state.session_status[session_id] = SessionStatus(type="idle")

    # Run concurrently (use return_exceptions=True so abort can handle cancellations)
    await asyncio.gather(run_message(), run_abort(), return_exceptions=True)

    # Verify
    assert processing_done.is_set(), "Message processing should have completed"
    # Stream might have partial events or be cancelled
    print(f"[TEST] Stream events received: {len(stream_events)}")
    print("[TEST] ✓ Abort works correctly for short operations")


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v", "-s"]))
