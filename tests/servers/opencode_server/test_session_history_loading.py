"""Test for session history loading during session switches.

This test verifies that conversation history is correctly loaded when
switching between sessions, preventing cross-session contamination.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import Mock

import pytest

from agentpool.sessions.models import SessionData
from agentpool_server.opencode_server.routes.session_routes import get_or_load_session


if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


class TestSessionHistoryLoading:
    """Tests for conversation history loading during session switches."""

    async def test_session_switch_reloads_history(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        tmp_project_dir: Path,
        event_capture,
    ):
        """When switching sessions, agent should reload conversation history."""
        # Setup: Create session A
        response_a = await async_client.post("/session", json={"title": "Session A"})
        assert response_a.status_code == 200
        session_a_id = response_a.json()["id"]

        # Verify agent has session A loaded
        assert server_state.agent.session_id == session_a_id

        # Setup: Create session B (this switches agent to session B)
        response_b = await async_client.post("/session", json={"title": "Session B"})
        assert response_b.status_code == 200
        session_b_id = response_b.json()["id"]

        # Verify agent now has session B loaded
        assert server_state.agent.session_id == session_b_id

        # Setup: Clear session A from memory cache
        del server_state.sessions[session_a_id]
        del server_state.messages[session_a_id]

        # Prepare session A data
        now = datetime.now(UTC)
        session_a_data = SessionData(
            session_id=session_a_id,
            agent_name="test-agent",
            cwd=str(tmp_project_dir),
            created_at=now,
            last_active=now,
            metadata={"title": "Session A"},
        )

        # Track if load_session was called
        load_session_calls: list[str] = []

        async def mock_load_session(sid: str) -> SessionData | None:
            load_session_calls.append(sid)
            if sid == session_a_id:
                server_state.agent.session_id = session_a_id
                # Set up conversation mock with empty list
                server_state.agent.conversation = Mock()
                server_state.agent.conversation.chat_messages = []
                return session_a_data
            return None

        server_state.agent.load_session = mock_load_session  # type: ignore[method-assign]

        # ACTION: Load session A
        loaded_session = await get_or_load_session(server_state, session_a_id)

        # VERIFY: load_session should have been called
        assert session_a_id in load_session_calls
        assert loaded_session is not None
        assert loaded_session.id == session_a_id
        assert server_state.agent.session_id == session_a_id
        status_events = [
            event
            for event in event_capture.get_events_by_type("session.status")
            if event.properties.session_id == session_a_id
        ]
        idle_events = [
            event
            for event in event_capture.get_events_by_type("session.idle")
            if event.properties.session_id == session_a_id
        ]
        assert status_events
        assert idle_events

    async def test_cached_session_with_wrong_agent_session_gets_reloaded(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        tmp_project_dir: Path,
    ):
        """If agent has different session loaded, cached session should reload history."""
        # Create session A
        response_a = await async_client.post("/session", json={"title": "Session A"})
        session_a_id = response_a.json()["id"]

        # Create session B
        response_b = await async_client.post("/session", json={"title": "Session B"})
        session_b_id = response_b.json()["id"]

        # Verify agent has session B loaded
        assert server_state.agent.session_id == session_b_id

        # Add session A back to cache but agent still has B
        from agentpool_server.opencode_server.models import (
            Session,
            TimeCreatedUpdated,
        )
        from agentpool_storage.opencode_provider import helpers

        now = int(datetime.now(UTC).timestamp() * 1000)
        session_a = Session(
            id=session_a_id,
            project_id=helpers.compute_project_id(str(tmp_project_dir)),
            directory=str(tmp_project_dir),
            title="Session A",
            version="1",
            time=TimeCreatedUpdated(created=now, updated=now),
        )
        server_state.sessions[session_a_id] = session_a
        server_state.messages[session_a_id] = []

        session_a_data = SessionData(
            session_id=session_a_id,
            agent_name="test-agent",
            cwd=str(tmp_project_dir),
            created_at=datetime.now(UTC),
            last_active=datetime.now(UTC),
            metadata={"title": "Session A"},
        )

        load_session_called = False

        async def mock_load_session(sid: str) -> SessionData | None:
            nonlocal load_session_called
            if sid == session_a_id:
                load_session_called = True
                server_state.agent.session_id = session_a_id
                # Set up conversation mock with empty list
                server_state.agent.conversation = Mock()
                server_state.agent.conversation.chat_messages = []
                return session_a_data
            return None

        server_state.agent.load_session = mock_load_session  # type: ignore[method-assign]

        # ACTION: Get session A (cached but agent has B)
        loaded_session = await get_or_load_session(server_state, session_a_id)

        # VERIFY: load_session should have been called
        assert loaded_session is not None
        assert load_session_called
        assert server_state.agent.session_id == session_a_id

    async def test_same_session_no_reload(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
    ):
        """If agent already has the correct session loaded, no reload needed."""
        # Create session A
        response_a = await async_client.post("/session", json={"title": "Session A"})
        session_a_id = response_a.json()["id"]

        # Verify agent has session A loaded
        assert server_state.agent.session_id == session_a_id

        load_session_called = False

        async def mock_load_session(sid: str) -> SessionData | None:
            nonlocal load_session_called
            load_session_called = True
            return None

        server_state.agent.load_session = mock_load_session  # type: ignore[method-assign]

        # ACTION: Get session A (already loaded)
        loaded_session = await get_or_load_session(server_state, session_a_id)

        # VERIFY: load_session should NOT have been called
        assert loaded_session is not None
        assert loaded_session.id == session_a_id
        assert not load_session_called

    async def test_input_provider_set_on_session_switch(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        tmp_project_dir: Path,
    ):
        """When switching sessions, input provider should be updated."""
        # Create session A
        response_a = await async_client.post("/session", json={"title": "Session A"})
        session_a_id = response_a.json()["id"]
        input_provider_a = server_state.input_providers[session_a_id]

        # Create session B
        response_b = await async_client.post("/session", json={"title": "Session B"})
        session_b_id = response_b.json()["id"]
        input_provider_b = server_state.input_providers[session_b_id]

        # Verify agent has session B's input provider
        assert server_state.agent._input_provider is input_provider_b

        # Clear session A from cache
        del server_state.sessions[session_a_id]
        del server_state.messages[session_a_id]

        # Prepare session A data
        now_dt = datetime.now(UTC)
        session_a_data = SessionData(
            session_id=session_a_id,
            agent_name="test-agent",
            cwd=str(tmp_project_dir),
            created_at=now_dt,
            last_active=now_dt,
            metadata={"title": "Session A"},
        )

        async def mock_load_session(sid: str) -> SessionData | None:
            if sid == session_a_id:
                server_state.agent.session_id = session_a_id
                # Set up conversation mock with empty list
                server_state.agent.conversation = Mock()
                server_state.agent.conversation.chat_messages = []
                return session_a_data
            return None

        server_state.agent.load_session = mock_load_session  # type: ignore[method-assign]

        # ACTION: Switch back to session A
        await get_or_load_session(server_state, session_a_id)

        # VERIFY: Agent should now have session A's input provider
        assert server_state.agent._input_provider is input_provider_a


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
