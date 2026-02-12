"""Integration tests for subagent session handling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from fastapi import FastAPI
import pytest

from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.dependencies import get_state
from agentpool_server.opencode_server.routes import file_router, message_router, session_router


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


class TestSubagentSessions:
    """Integration tests for subagent session handling."""

    @pytest.fixture
    def app(self, server_state: ServerState) -> FastAPI:
        """Create a FastAPI app with message routes."""
        app = FastAPI()
        app.include_router(session_router)
        app.include_router(message_router)
        app.include_router(file_router)
        app.dependency_overrides[get_state] = lambda: server_state
        return app

    @pytest.fixture
    def mock_agent_stream(self, server_state: ServerState):
        """Mock the agent's run_stream method to yield specific events."""
        original_run_stream = server_state.agent.run_stream

        # Create a mock that we can configure per test
        mock = MagicMock()
        server_state.agent.run_stream = mock

        yield mock

        # Restore original
        server_state.agent.run_stream = original_run_stream

    @pytest.mark.asyncio
    async def test_full_subagent_session_flow(
        self,
        async_client,
        server_state,
        mock_agent_stream,
        event_capture,
    ):
        """Test the complete flow of subagent session creation and event propagation.

        Flow:
        1. Create parent session
        2. Trigger agent execution that yields SubAgentEvent
        3. Verify child session is created with correct parent_id
        4. Verify session.created event is emitted for child session
        """
        # 1. Create parent session
        parent_response = await async_client.post("/session", json={"title": "Parent Session"})
        assert parent_response.status_code == 200
        parent_id = parent_response.json()["id"]

        # 2. Configure mock agent to yield SubAgentEvent
        child_id = "ses_child_123"

        async def stream_generator(*args, **kwargs):
            # First yield a normal part
            from agentpool.agents.events import PartDeltaEvent

            yield PartDeltaEvent.text(index=0, content="Starting subagent...")

            # Then yield the subagent event
            inner_event = StreamCompleteEvent(
                message=ChatMessage(role="assistant", content="Subagent done")
            )

            yield SubAgentEvent(
                source_name="subagent",
                source_type="agent",
                event=inner_event,
                child_session_id=child_id,
                parent_session_id=parent_id,
            )

            # Finally complete the stream
            yield StreamCompleteEvent(message=ChatMessage(role="assistant", content="All done"))

        mock_agent_stream.side_effect = stream_generator

        # 3. Send message to parent session to trigger the stream
        response = await async_client.post(
            f"/session/{parent_id}/message",
            json={"parts": [{"type": "text", "text": "Run subagent"}]},
        )
        assert response.status_code == 200

        # Wait for background processing (message stream is handled in background)
        # We can wait for the session created event or check state periodically
        max_retries = 10
        for _ in range(max_retries):
            if child_id in server_state.sessions:
                break
            await asyncio.sleep(0.1)

        # 4. Verify child session exists and has correct parent
        # Check via API
        child_response = await async_client.get(f"/session/{child_id}")
        assert child_response.status_code == 200
        child_data = child_response.json()

        assert child_data["id"] == child_id
        assert child_data["parentID"] == parent_id

        # Check internal state
        assert child_id in server_state.sessions
        assert server_state.sessions[child_id].parent_id == parent_id

        # 5. Verify SSE events
        # We should see a session.created event for the child session
        created_events = event_capture.get_events_by_type("session.created")

        # Filter for our child session
        child_events = [e for e in created_events if e.properties.info.id == child_id]

        assert len(child_events) == 1
        event = child_events[0]
        assert event.properties.info.parent_id == parent_id
        # Session ID is in properties.info.id
        assert event.properties.info.id == child_id

    @pytest.mark.asyncio
    async def test_child_session_has_parent_id(
        self,
        server_state,
    ):
        """Verify ensure_session correctly sets parent_id on the model."""
        parent_id = "ses_parent"
        child_id = "ses_child"

        # Pre-create parent
        await server_state.ensure_session(parent_id)

        # Create child with parent reference
        child_session = await server_state.ensure_session(child_id, parent_id=parent_id)

        assert child_session.id == child_id
        assert child_session.parent_id == parent_id

        # Verify persistence
        stored_session = server_state.sessions[child_id]
        assert stored_session.parent_id == parent_id

    @pytest.mark.asyncio
    async def test_sse_events_include_session_id(
        self,
        async_client,
        mock_agent_stream,
        event_capture,
    ):
        """Verify that SSE events generated during subagent execution include session IDs."""
        # Setup parent and child IDs
        parent_id = "ses_parent_sse"
        child_id = "ses_child_sse"

        # Create parent session
        response = await async_client.post("/session", json={"title": "Parent"})
        assert response.status_code == 200
        parent_id = response.json()["id"]

        # Mock stream with subagent event
        async def stream_generator(*args, **kwargs):
            inner_event = StreamCompleteEvent(message=ChatMessage(role="assistant", content="Done"))
            yield SubAgentEvent(
                source_name="subagent",
                source_type="agent",
                event=inner_event,
                child_session_id=child_id,
                parent_session_id=parent_id,
            )
            yield StreamCompleteEvent(message=ChatMessage(role="assistant", content="Done"))

        mock_agent_stream.side_effect = stream_generator

        # Trigger execution
        await async_client.post(
            f"/session/{parent_id}/message",
            json={"parts": [{"type": "text", "text": "Go"}]},
        )

        # Wait for processing
        await asyncio.sleep(0.5)

        # Check captured events
        # We expect events related to the child session to have child_id
        # Note: The specific events emitted depend on how SubAgentEvent is handled
        # But we specifically want to verify the session.created event for the child

        created_events = event_capture.get_events_by_type("session.created")
        child_created = next((e for e in created_events if e.properties.info.id == child_id), None)

        assert child_created is not None
        assert child_created.properties.info.id == child_id
        assert child_created.properties.info.parent_id == parent_id

    @pytest.mark.asyncio
    async def test_backward_compatibility_non_subagent(
        self,
        async_client,
        mock_agent_stream,
        server_state,
    ):
        """Verify that regular tool usage without subagents still works."""
        # Create session
        response = await async_client.post("/session", json={"title": "Legacy"})
        assert response.status_code == 200
        session_id = response.json()["id"]

        # Mock normal stream without subagent events
        async def stream_generator(*args, **kwargs):
            from agentpool.agents.events import PartDeltaEvent

            yield PartDeltaEvent.text(index=0, content="Normal response")
            yield StreamCompleteEvent(
                message=ChatMessage(role="assistant", content="Normal response")
            )

        mock_agent_stream.side_effect = stream_generator

        # Send message
        response = await async_client.post(
            f"/session/{session_id}/message",
            json={"parts": [{"type": "text", "text": "Hello"}]},
        )
        assert response.status_code == 200

        # Wait for processing
        await asyncio.sleep(0.2)

        # Verify no unexpected sessions were created
        assert len(server_state.sessions) == 1
        assert session_id in server_state.sessions

        # Verify message was appended
        assert len(server_state.messages[session_id]) > 0
