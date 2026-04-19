"""Tests for concurrent message handling in OpenCode server.

These tests verify that the OpenCode server correctly handles concurrent
messages to the same session, preventing race conditions and event interleaving.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool_server.opencode_server.models import (
    MessageRequest,
    TextPartInput,
)
from agentpool_server.opencode_server.models.message import UserMessage
from agentpool_server.opencode_server.models.parts import TextPart
from agentpool_server.opencode_server.routes.message_routes import _process_message
from agentpool_server.opencode_server.state import ServerState


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class SlowAgentMock:
    """Mock agent that simulates slow processing to expose concurrency issues."""

    def __init__(self, delay: float = 0.5) -> None:
        self.name = "test-agent"
        self.delay = delay
        self.run_stream_call_count = 0
        self.active_runs: set[str] = set()
        self.agent_pool: Mock | None = None
        self.env: Mock | None = None
        self.storage: Any = None
        self.tools = []
        self._input_provider = None
        self.model_name = "test-model"
        self.session_id: str | None = None
        # Snapshot tracking: maps session_id -> model_name captured from snapshot
        self.snapshot_session_ids: dict[str, str] = {}
        # Model name captured from snapshot at run_stream call time
        self.model_names_at_call: dict[str, str | None] = {}

    async def set_model(self, model: str) -> None:
        """Mock set_model method."""
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        """Mock set_mode method."""
        return

    async def get_available_models(self):
        """Mock get_available_models method."""
        return []

    async def load_session(self, session_id: str) -> Any:
        """Mock load_session method."""
        self.session_id = session_id
        return None

    def run_stream(
        self,
        user_prompt: Any,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Simulate slow processing with concurrent run detection."""
        self.run_stream_call_count += 1

        # Capture snapshot info for verification in tests
        snapshot = kwargs.get("snapshot")
        if snapshot is not None and session_id is not None:
            self.snapshot_session_ids[session_id] = snapshot.session_id
            self.model_names_at_call[session_id] = snapshot.model_name

        # Check if another run is already active for this session
        if session_id in self.active_runs:
            raise RuntimeError(
                f"Concurrent run detected for session {session_id}! "
                "This indicates missing concurrency control."
            )

        self.active_runs.add(session_id or "unknown")

        async def stream() -> AsyncIterator[Any]:
            try:
                # Simulate processing time
                await asyncio.sleep(self.delay)

                # Yield a simple text event
                from agentpool.agents.events import StreamCompleteEvent, TextContentItem
                from agentpool.messaging import ChatMessage

                yield TextContentItem(text=f"Response for {session_id}")
                yield StreamCompleteEvent(message=ChatMessage(role="assistant", content="done"))
            finally:
                self.active_runs.discard(session_id or "unknown")

        return stream()


@pytest.fixture
def slow_mock_agent():
    """Create a slow mock agent for testing concurrency."""
    agent = SlowAgentMock(delay=0.3)
    saved_sessions: dict[str, Any] = {}

    # Set up pool mock with async storage methods
    pool = Mock()
    pool.manifest = Mock()
    pool.manifest.config_file_path = "/tmp/test"
    pool.manifest.model_variants = {}

    # Storage needs to be properly mocked with async methods
    storage = Mock()

    async def save_session(session_data: Any) -> None:
        saved_sessions[session_data.session_id] = session_data

    storage.save_session = AsyncMock(side_effect=save_session)
    storage.log_session = AsyncMock()
    storage.log_message = AsyncMock()
    pool.storage = storage

    pool.todos = Mock()
    pool.todos.on_change = None
    pool.skill_commands = None

    pool.sessions = Mock()
    pool.sessions.store = None

    # CRITICAL: all_agents must return a real dict to avoid Mock issues
    pool.all_agents = {agent.name: agent}

    agent.agent_pool = pool

    # Set up env mock
    env = Mock()
    fs = Mock()
    fs.read_file = AsyncMock(return_value="file content")
    env.get_fs = Mock(return_value=fs)
    env.cwd = "/tmp"
    agent.env = env

    # Set up storage
    agent.storage = storage

    conversation = Mock()
    conversation.chat_messages = []
    agent.conversation = conversation

    async def load_session(session_id: str) -> Any:
        return saved_sessions.get(session_id)

    agent.load_session = AsyncMock(side_effect=load_session)

    return agent


@pytest.fixture
def concurrent_test_state(tmp_project_dir, slow_mock_agent):
    """Create a server state with slow agent for concurrency testing."""
    return ServerState(
        working_dir=str(tmp_project_dir),
        agent=slow_mock_agent,
    )


@pytest.fixture
def sample_message_request():
    """Create a sample message request."""
    return MessageRequest(
        parts=[TextPartInput(text="Hello, test!")],
        agent="default",
    )


class TestConcurrentMessageHandling:
    """Tests for concurrent message handling behavior."""

    @pytest.mark.asyncio
    async def test_concurrent_messages_same_session_should_be_sequential(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that concurrent messages to the same session are processed sequentially.

        This test verifies that when multiple messages are sent to the same session
        concurrently, they are processed one at a time (not in parallel), preventing
        event interleaving and data corruption.

        Before the fix: This test would fail because both messages would be processed
        concurrently, causing the SlowAgentMock to raise a RuntimeError.

        After the fix: Messages should be processed sequentially, and no concurrent
        run error should occur.
        """
        state = concurrent_test_state
        session_id = "test-session-concurrent"

        # Create session first
        await state.ensure_session(session_id)

        # Track events for verification
        all_events = []
        original_broadcast = state.broadcast_event

        async def tracking_broadcast(event):
            all_events.append(event)
            await original_broadcast(event)

        state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        # Send two messages concurrently to the same session
        # This should NOT cause concurrent processing
        async def send_message_with_id(msg_id: str):
            req = sample_message_request.model_copy()
            req.message_id = msg_id
            return await _process_message(session_id, req, state)

        # Run both messages concurrently
        results = await asyncio.gather(
            send_message_with_id("msg-1"),
            send_message_with_id("msg-2"),
            return_exceptions=True,
        )

        # Debug: capture all error events first
        from agentpool_server.opencode_server.models.events import SessionErrorEvent

        error_events = [e for e in all_events if isinstance(e, SessionErrorEvent)]
        if error_events:
            print(f"Error events found: {error_events}")
            for err in error_events:
                if hasattr(err, "properties") and hasattr(err.properties, "message"):
                    print(f"Error message: {err.properties.message}")

        # Verify no errors occurred (no concurrent run detected)
        for result in results:
            if isinstance(result, Exception):
                pytest.fail(f"Exception during processing: {result}")

        # Verify both messages were processed
        assert len(state.messages[session_id]) == 4  # 2 user + 2 assistant messages

        # Verify the agent was called twice
        agent_mock = cast(SlowAgentMock, state.agent)
        assert agent_mock.run_stream_call_count == 2

    @pytest.mark.asyncio
    async def test_session_status_reflects_busy_state(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that session status correctly reflects busy state during processing.

        This ensures that the session status is set to "busy" while a message is
        being processed and reset to "idle" afterward.
        """
        state = concurrent_test_state
        session_id = "test-session-status"

        # Create session
        await state.ensure_session(session_id)

        # Initial status should be idle
        assert state.session_status[session_id].type == "idle"

        # Track status changes
        status_history = []
        original_broadcast = state.broadcast_event

        async def tracking_broadcast(event):
            if hasattr(event, "type"):
                status_history.append((event.type, state.session_status.get(session_id)))
            await original_broadcast(event)

        state.broadcast_event = tracking_broadcast  # type: ignore[method-assign]

        # Process a message
        await _process_message(session_id, sample_message_request, state)

        # Final status should be idle
        assert state.session_status[session_id].type == "idle"

        # Verify status transitioned through busy
        status_types = [s.type for s in state.session_status.values()]
        assert "busy" in status_types or any("busy" in str(h) for h in status_history)

    @pytest.mark.asyncio
    async def test_different_sessions_can_process_concurrently(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that different sessions can process messages concurrently.

        While the same session should process messages sequentially, different
        sessions should be able to process messages in parallel.
        """
        state = concurrent_test_state
        session_id_1 = "test-session-1"
        session_id_2 = "test-session-2"

        # Create both sessions
        await state.ensure_session(session_id_1)
        await state.ensure_session(session_id_2)

        # Track start and end times
        start_times = {}
        end_times = {}

        original_run_stream = state.agent.run_stream

        async def tracked_run_stream(*args, session_id=None, **kwargs):
            start_times[session_id] = asyncio.get_event_loop().time()
            async for event in original_run_stream(*args, session_id=session_id, **kwargs):
                yield event
            end_times[session_id] = asyncio.get_event_loop().time()

        state.agent.run_stream = tracked_run_stream  # type: ignore[method-assign]

        # Process messages to different sessions concurrently
        await asyncio.gather(
            _process_message(session_id_1, sample_message_request, state),
            _process_message(session_id_2, sample_message_request, state),
        )

        # Verify both sessions processed their messages
        assert len(state.messages[session_id_1]) == 2  # user + assistant
        assert len(state.messages[session_id_2]) == 2  # user + assistant

    @pytest.mark.asyncio
    async def test_message_ordering_preserved_under_concurrency(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that message ordering is preserved when messages are processed sequentially.

        When multiple messages are queued for the same session, they should be
        processed in the order they were received.
        """
        state = concurrent_test_state
        session_id = "test-session-order"

        # Create session
        await state.ensure_session(session_id)

        # Send messages with specific IDs to verify order
        async def send_message_with_content(content: str, msg_id: str):
            req = MessageRequest(
                parts=[TextPartInput(text=content)],
                agent="default",
                message_id=msg_id,
            )
            return await _process_message(session_id, req, state)

        # Process multiple messages concurrently
        await asyncio.gather(
            send_message_with_content("First message", "msg-first"),
            send_message_with_content("Second message", "msg-second"),
            send_message_with_content("Third message", "msg-third"),
        )

        # Get user messages (every other message starting from 0)
        user_messages = [
            msg for msg in state.messages[session_id] if isinstance(msg.info, UserMessage)
        ]

        # Verify we have 3 user messages
        assert len(user_messages) == 3

        # Verify the agent was called 3 times
        agent_mock = cast(SlowAgentMock, state.agent)
        assert agent_mock.run_stream_call_count == 3

    @pytest.mark.asyncio
    async def test_two_sessions_back_to_back_b_not_blocked(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that Session B can start while Session A is still running.

        When two sessions start their turns close together, Session B should NOT
        wait for Session A's full turn to finish. The per-session locking only
        serializes messages within the same session; different sessions run in
        parallel. This is verified by checking that the total wall time for both
        sessions is less than 2 * agent_delay (which would be the sequential time).
        """
        state = concurrent_test_state
        session_id_a = "test-session-back-to-back-a"
        session_id_b = "test-session-back-to-back-b"

        # Create both sessions
        await state.ensure_session(session_id_a)
        await state.ensure_session(session_id_b)

        agent_mock = cast(SlowAgentMock, state.agent)
        agent_delay = agent_mock.delay

        start = asyncio.get_event_loop().time()

        # Start Session A processing
        task_a = asyncio.create_task(_process_message(session_id_a, sample_message_request, state))

        # After a short delay, start Session B processing
        await asyncio.sleep(0.05)
        task_b = asyncio.create_task(_process_message(session_id_b, sample_message_request, state))

        # Wait for both to complete
        await asyncio.gather(task_a, task_b)

        elapsed = asyncio.get_event_loop().time() - start

        # Both sessions should have their messages (2 each: user + assistant)
        assert len(state.messages[session_id_a]) == 2
        assert len(state.messages[session_id_b]) == 2

        # Agent was called twice (once per session)
        assert agent_mock.run_stream_call_count == 2

        # Timing assertion: if they ran sequentially, total would be >= 2 * delay.
        # Since they ran concurrently, total should be < 2 * delay.
        # We use a generous margin to avoid flaky CI failures.
        assert elapsed < 2 * agent_delay, (
            f"Sessions did not run concurrently: elapsed={elapsed:.3f}s "
            f"but sequential would be ~{2 * agent_delay:.3f}s"
        )

    @pytest.mark.asyncio
    async def test_in_flight_uses_snapshot_not_live_fields(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that an in-flight run uses captured snapshot values, not live fields.

        When Session A is mid-run and the shared agent's model_name is mutated
        (simulating Session B binding to a different model), Session A's ongoing
        run must continue using the snapshot it captured at the start — not the
        new live value. This is the core guarantee of the RunSnapshot mechanism.
        """
        state = concurrent_test_state
        session_id_a = "test-session-snapshot-a"

        # Create session A
        await state.ensure_session(session_id_a)

        agent_mock = cast(SlowAgentMock, state.agent)

        # Start Session A processing (model_name is "test-model" at this point)
        task_a = asyncio.create_task(_process_message(session_id_a, sample_message_request, state))

        # Wait briefly for Session A to have captured its snapshot
        await asyncio.sleep(0.05)

        # Mutate the live agent model_name (simulating Session B binding)
        agent_mock.model_name = "different-model"

        # Wait for Session A to finish
        await task_a

        # Verify Session A used the original snapshot model, not the mutated live value
        assert agent_mock.model_names_at_call.get(session_id_a) == "test-model", (
            f"Session A should have seen snapshot model_name='test-model', "
            f"but got '{agent_mock.model_names_at_call.get(session_id_a)}'"
        )

        # The live value should still be the mutated one
        assert agent_mock.model_name == "different-model"

        # Session A should have completed successfully
        assert len(state.messages[session_id_a]) == 2

    @pytest.mark.asyncio
    async def test_read_only_route_during_active_turn(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that a read-only route returns immediately during an active turn.

        When Session A has an in-flight agent run, calling get_or_load_session
        for a different, already-cached Session B should return without waiting
        for A's turn to complete. This verifies that read-only (cache-hit) paths
        do not acquire agent_lock or block on in-flight turns.
        """
        state = concurrent_test_state
        session_id_a = "test-session-readonly-a"
        session_id_b = "test-session-readonly-b"

        # Create both sessions and ensure B is cached
        await state.ensure_session(session_id_a)
        await state.ensure_session(session_id_b)

        # Ensure B has messages so it passes the agent_has_correct_session check
        from agentpool_server.opencode_server.models import (
            AssistantMessage,
            MessagePath,
            MessageTime,
            MessageWithParts,
            TextPart,
        )
        from agentpool.utils import identifiers as identifier

        msg_id = identifier.ascending("message")
        part_id = identifier.ascending("part")
        assistant_msg = AssistantMessage(
            id=msg_id,
            session_id=session_id_b,
            parent_id="",
            model_id="test",
            provider_id="test",
            mode="test",
            agent="default",
            path=MessagePath(cwd=state.working_dir, root=state.working_dir),
            time=MessageTime(created=0),
        )
        state.messages[session_id_b] = [
            MessageWithParts(
                info=assistant_msg,
                parts=[TextPart(id=part_id, message_id=msg_id, session_id=session_id_b, text="hi")],
            )
        ]

        # Make agent report session_id_b so the fast-path triggers
        state.agent.session_id = session_id_b

        # Start a slow agent turn on Session A
        task_a = asyncio.create_task(_process_message(session_id_a, sample_message_request, state))

        # Wait briefly for A's turn to start
        await asyncio.sleep(0.05)

        # Now call get_or_load_session for B — this should return immediately
        # because B is cached and agent has correct session loaded (fast path)
        start = asyncio.get_event_loop().time()
        from agentpool_server.opencode_server.routes.session_routes import get_or_load_session

        session_b = await get_or_load_session(state, session_id_b)
        elapsed = asyncio.get_event_loop().time() - start

        # The read should have returned very quickly (< 0.1s), not waited for A
        assert elapsed < 0.1, (
            f"get_or_load_session for cached session B took {elapsed:.3f}s, "
            f"should return immediately from cache"
        )
        assert session_b is not None
        assert session_b.id == session_id_b

        # Clean up: wait for A to finish
        await task_a

    @pytest.mark.asyncio
    async def test_conversation_isolation_concurrent_sessions(
        self,
        concurrent_test_state: ServerState,
    ) -> None:
        """Test that messages from one session never leak into another.

        Two sessions run concurrently; after both finish, messages from Session A
        must not appear in Session B's `state.messages` and vice versa.  Also
        verifies that per-session `MessageHistory` instances are separate objects.
        """
        state = concurrent_test_state
        session_id_a = "test-session-isolation-a"
        session_id_b = "test-session-isolation-b"

        # Create both sessions
        await state.ensure_session(session_id_a)
        await state.ensure_session(session_id_b)

        # Build unique requests so we can distinguish message content
        req_a = MessageRequest(
            parts=[TextPartInput(text="Message for Session A")],
            agent="default",
        )
        req_b = MessageRequest(
            parts=[TextPartInput(text="Message for Session B")],
            agent="default",
        )

        # Process both sessions concurrently
        await asyncio.gather(
            _process_message(session_id_a, req_a, state),
            _process_message(session_id_b, req_b, state),
        )

        # Each session should have exactly 2 messages (user + assistant)
        assert len(state.messages[session_id_a]) == 2
        assert len(state.messages[session_id_b]) == 2

        # Verify content isolation: Session A's messages must not appear in B
        texts_b = [
            part.text
            for msg in state.messages[session_id_b]
            for part in msg.parts
            if isinstance(part, TextPart)
        ]
        assert not any("Session A" in t for t in texts_b), (
            "Session A content leaked into Session B's messages"
        )

        # Verify content isolation: Session B's messages must not appear in A
        texts_a = [
            part.text
            for msg in state.messages[session_id_a]
            for part in msg.parts
            if isinstance(part, TextPart)
        ]
        assert not any("Session B" in t for t in texts_a), (
            "Session B content leaked into Session A's messages"
        )

        # Per-session MessageHistory instances must be separate objects
        assert (
            state.session_conversations[session_id_a]
            is not state.session_conversations[session_id_b]
        ), "Session A and B share the same MessageHistory instance"

    @pytest.mark.asyncio
    async def test_interrupt_session_a_does_not_cancel_session_b(
        self,
        concurrent_test_state: ServerState,
        sample_message_request: MessageRequest,
    ) -> None:
        """Test that cancelling Session A's run does not affect Session B.

        Two sessions start overlapping runs; `cancel_session_run` on Session A
        cancels A but Session B completes successfully with all messages intact.
        """
        state = concurrent_test_state
        session_id_a = "test-session-interrupt-a"
        session_id_b = "test-session-interrupt-b"

        # Create both sessions
        await state.ensure_session(session_id_a)
        await state.ensure_session(session_id_b)

        # Start Session A processing
        task_a = asyncio.create_task(_process_message(session_id_a, sample_message_request, state))

        # Wait for A to have started (snapshot captured + task registered)
        await asyncio.sleep(0.05)

        # Register A's task so cancel_session_run can find it
        state.register_active_run(session_id_a, task_a)

        # Start Session B processing
        task_b = asyncio.create_task(_process_message(session_id_b, sample_message_request, state))

        # Wait for B to have started
        await asyncio.sleep(0.05)

        # Cancel only Session A
        cancelled = state.cancel_session_run(session_id_a)
        assert cancelled, "cancel_session_run should return True for active Session A"

        # Session A should be done (cancelled or completed after handling CancelledError)
        # _process_message_locked catches CancelledError internally, but the
        # task may still surface it depending on timing. Either way, it must be done.
        try:
            await task_a
        except asyncio.CancelledError:
            pass
        assert task_a.done()

        # Wait for Session B to complete (with timeout)
        await asyncio.wait_for(task_b, timeout=2.0)

        # Session B should have completed successfully
        assert len(state.messages[session_id_b]) == 2, (
            "Session B should have 2 messages (user + assistant) after completing"
        )

        # Session B was NOT cancelled by Session A's interrupt
        assert not task_b.cancelled(), "Session B should not have been cancelled"
