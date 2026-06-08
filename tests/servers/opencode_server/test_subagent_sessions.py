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
from agentpool_server.opencode_server.models import MessageWithParts
from agentpool_server.opencode_server.routes import file_router, message_router, session_router
from agentpool_server.opencode_server.session_pool_integration import ensure_session, get_messages_for_session


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
        server_state,
        event_capture,
    ):
        """Test the complete flow of subagent session creation and event propagation.

        Flow:
        1. Create parent session
        2. Directly process SubAgentEvent through EventProcessor
        3. Verify child session is created with correct parent_id
        4. Verify session.created event is emitted for child session
        """
        # 1. Create parent session
        parent_id = "ses_parent"
        child_id = "ses_child_123"
        await ensure_session(server_state, parent_id)

        # 2. Set up EventProcessor context and process SubAgentEvent
        from agentpool_server.opencode_server.event_processor import EventProcessor
        from agentpool_server.opencode_server.event_processor_context import EventProcessorContext
        from agentpool_server.opencode_server.models import MessagePath, MessageTime

        processor = EventProcessor()
        parent_assistant_msg = MessageWithParts.assistant(
            message_id="parent-msg-1",
            session_id=parent_id,
            time=MessageTime(created=0),
            agent_name="parent-agent",
            model_id="test-model",
            parent_id="parent-user-1",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
        )
        parent_ctx = EventProcessorContext(
            session_id=parent_id,
            assistant_msg_id="parent-msg-1",
            assistant_msg=parent_assistant_msg,
            state=server_state,
            working_dir="/tmp",
        )

        inner_event = StreamCompleteEvent(
            message=ChatMessage(role="assistant", content="Subagent done")
        )
        subagent_event = SubAgentEvent(
            source_name="subagent",
            source_type="agent",
            event=inner_event,
            child_session_id=child_id,
            parent_session_id=parent_id,
        )

        events = []
        async for e in processor.process(subagent_event, parent_ctx):
            events.append(e)

        # 3. Verify child session exists and has correct parent
        assert child_id in server_state.sessions
        assert server_state.sessions[child_id].parent_id == parent_id

        # 4. Verify SSE events
        created_events = event_capture.get_events_by_type("session.created")
        child_events = [e for e in created_events if e.properties.info.id == child_id]

        assert len(child_events) >= 1
        event = child_events[0]
        assert event.properties.info.parent_id == parent_id
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
        await ensure_session(server_state, parent_id)

        # Create child with parent reference
        child_session = await ensure_session(server_state, child_id, parent_id=parent_id)

        assert child_session.id == child_id
        assert child_session.parent_id == parent_id

        # Verify persistence
        stored_session = server_state.sessions[child_id]
        assert stored_session.parent_id == parent_id

    @pytest.mark.asyncio
    async def test_sse_events_include_session_id(
        self,
        server_state,
        event_capture,
    ):
        """Verify that SSE events generated during subagent execution include session IDs."""
        # Setup parent and child IDs
        parent_id = "ses_parent_sse"
        child_id = "ses_child_sse"

        # Create parent session
        await ensure_session(server_state, parent_id)

        # Directly process SubAgentEvent through EventProcessor
        from agentpool_server.opencode_server.event_processor import EventProcessor
        from agentpool_server.opencode_server.event_processor_context import EventProcessorContext
        from agentpool_server.opencode_server.models import MessagePath, MessageTime

        processor = EventProcessor()
        parent_assistant_msg = MessageWithParts.assistant(
            message_id="parent-msg-1",
            session_id=parent_id,
            time=MessageTime(created=0),
            agent_name="parent-agent",
            model_id="test-model",
            parent_id="parent-user-1",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
        )
        parent_ctx = EventProcessorContext(
            session_id=parent_id,
            assistant_msg_id="parent-msg-1",
            assistant_msg=parent_assistant_msg,
            state=server_state,
            working_dir="/tmp",
        )

        inner_event = StreamCompleteEvent(message=ChatMessage(role="assistant", content="Done"))
        subagent_event = SubAgentEvent(
            source_name="subagent",
            source_type="agent",
            event=inner_event,
            child_session_id=child_id,
            parent_session_id=parent_id,
        )

        async for _ in processor.process(subagent_event, parent_ctx):
            pass

        # Check captured events
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
        session_messages = await get_messages_for_session(server_state, session_id)
        assert len(session_messages) > 0
