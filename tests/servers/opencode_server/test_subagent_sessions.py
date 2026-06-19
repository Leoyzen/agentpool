"""Integration tests for subagent session handling."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from fastapi import FastAPI
import pytest

from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.dependencies import get_state
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
