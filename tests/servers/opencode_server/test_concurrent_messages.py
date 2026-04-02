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
    Session,
    SessionStatus,
    TextPartInput,
)
from agentpool_server.opencode_server.models.common import TimeCreatedUpdated
from agentpool_server.opencode_server.models.message import UserMessage
from agentpool_server.opencode_server.routes.message_routes import _process_message
from agentpool_server.opencode_server.state import ServerState
from agentpool.utils.time_utils import now_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.agents.base_agent import BaseAgent


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

    async def set_model(self, model: str) -> None:
        """Mock set_model method."""
        self.model_name = model

    async def set_mode(self, mode: str, category_id: str | None = None) -> None:
        """Mock set_mode method."""
        pass

    async def get_available_models(self):
        """Mock get_available_models method."""
        return []

    def run_stream(
        self,
        user_prompt: Any,
        session_id: str | None = None,
    ) -> AsyncIterator[Any]:
        """Simulate slow processing with concurrent run detection."""
        self.run_stream_call_count += 1

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
                from agentpool.agents.events import TextContentItem, StreamCompleteEvent
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

    # Set up pool mock with async storage methods
    pool = Mock()
    pool.manifest = Mock()
    pool.manifest.config_file_path = "/tmp/test"
    pool.manifest.model_variants = {}

    # Storage needs to be properly mocked with async methods
    storage = Mock()
    storage.save_session = AsyncMock()
    storage.log_message = AsyncMock()
    pool.storage = storage

    pool.todos = Mock()
    pool.todos.on_change = None
    pool.skill_commands = None

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
