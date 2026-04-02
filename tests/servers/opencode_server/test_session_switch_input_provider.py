"""Test for session switch input_provider issue.

This test verifies the root cause of the "session switch" bug where
switching to an existing session causes messages to not respond.

Hypothesis: When get_or_load_session() loads an existing session,
it does NOT create/set an input_provider for that session, unlike
create_session() which does. This causes the agent to use the wrong
input_provider (or none at all) after switching.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock

import pytest

from agentpool.sessions.models import SessionData
from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
from agentpool_server.opencode_server.routes.session_routes import get_or_load_session


if TYPE_CHECKING:
    from pathlib import Path

    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


class TestSessionSwitchInputProvider:
    """Tests for input_provider handling during session switching."""

    async def test_create_session_sets_input_provider(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
    ):
        """Creating a session should set input_provider for that session.

        This is the baseline - create_session() correctly sets up input_provider.
        """
        # Create a session
        response = await async_client.post("/session", json={"title": "Test Session"})
        assert response.status_code == 200
        session_id = response.json()["id"]

        # Verify input_provider was created for this session
        assert session_id in server_state.input_providers
        input_provider = server_state.input_providers[session_id]
        assert isinstance(input_provider, OpenCodeInputProvider)

        # Verify agent's input_provider points to this session
        assert server_state.agent._input_provider is not None
        agent_provider: OpenCodeInputProvider = server_state.agent._input_provider  # type: ignore[assignment]
        assert agent_provider.session_id == session_id

    async def test_get_or_load_session_does_not_set_input_provider(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        tmp_project_dir: Path,
    ):
        """Loading an existing session via get_or_load_session does NOT set input_provider.

        This is the BUG: get_or_load_session() doesn't create/set input_provider,
        unlike create_session(). After switching sessions, the agent still has
        the old session's input_provider (or none).

        Expected behavior: After get_or_load_session, the agent._input_provider
        should point to the loaded session's input_provider.
        Actual behavior: agent._input_provider is unchanged (still points to old session).
        """
        # Setup: Create session A with input_provider
        response_a = await async_client.post("/session", json={"title": "Session A"})
        assert response_a.status_code == 200
        session_a_id = response_a.json()["id"]

        # Verify session A's input_provider is set
        assert session_a_id in server_state.input_providers
        input_provider_a = server_state.input_providers[session_a_id]
        assert server_state.agent._input_provider is input_provider_a

        # Setup: Create session B (this switches agent._input_provider to B)
        response_b = await async_client.post("/session", json={"title": "Session B"})
        assert response_b.status_code == 200
        session_b_id = response_b.json()["id"]

        # Verify session B's input_provider is now set
        assert session_b_id in server_state.input_providers
        input_provider_b = server_state.input_providers[session_b_id]
        assert server_state.agent._input_provider is input_provider_b

        # Setup: Mock agent.load_session to return session A data
        # This simulates session A existing in storage
        now = datetime.now(UTC)
        session_a_data = SessionData(
            session_id=session_a_id,
            agent_name="test-agent",
            cwd=str(tmp_project_dir),
            created_at=now,
            last_active=now,
            metadata={"title": "Session A"},
        )

        # Clear session A from memory to simulate "switching to existing session"
        del server_state.sessions[session_a_id]
        del server_state.messages[session_a_id]
        # Keep input_providers[session_a_id] to simulate it existing

        # Mock load_session to return the session data
        server_state.agent.load_session = AsyncMock(return_value=session_a_data)  # type: ignore[method-assign]

        # Also need to mock conversation.chat_messages for the conversion
        server_state.agent.conversation = Mock()
        server_state.agent.conversation.chat_messages = []

        # ACTION: Call get_or_load_session to "switch" to session A
        loaded_session = await get_or_load_session(server_state, session_a_id)

        # Verify session was loaded
        assert loaded_session is not None
        assert loaded_session.id == session_a_id

        # THE BUG: agent._input_provider should now point to session A's input_provider
        # But it still points to session B's input_provider!
        print(f"\n=== DEBUG INFO ===")
        print(f"Session A ID: {session_a_id}")
        print(f"Session B ID: {session_b_id}")
        print(f"input_provider_a.session_id: {input_provider_a.session_id}")
        print(f"input_provider_b.session_id: {input_provider_b.session_id}")
        current_agent_provider: OpenCodeInputProvider = server_state.agent._input_provider  # type: ignore[assignment]
        print(f"agent._input_provider.session_id: {current_agent_provider.session_id}")
        print(
            f"agent._input_provider is input_provider_a: {server_state.agent._input_provider is input_provider_a}"
        )
        print(
            f"agent._input_provider is input_provider_b: {server_state.agent._input_provider is input_provider_b}"
        )
        print(f"==================\n")

        # This assertion will FAIL, demonstrating the bug
        # The agent should have session A's input_provider after loading session A
        # But it still has session B's input_provider
        assert server_state.agent._input_provider is input_provider_a, (
            f"BUG: After loading session A, agent._input_provider should be "
            f"input_provider_a (session_id={session_a_id}), but it's "
            f"input_provider_b (session_id={current_agent_provider.session_id})"
        )

    async def test_input_provider_session_id_mismatch_after_switch(
        self,
        async_client: AsyncClient,
        server_state: ServerState,
        tmp_project_dir: Path,
    ):
        """After switching sessions, input_provider.session_id doesn't match loaded session.

        This test demonstrates the practical impact: after switching to session A,
        the agent's input_provider still has session_id of session B. This causes
        permission requests and other input operations to be routed to the wrong session.
        """
        # Create session A
        response_a = await async_client.post("/session", json={"title": "Session A"})
        session_a_id = response_a.json()["id"]

        # Create session B (agent._input_provider now points to B)
        response_b = await async_client.post("/session", json={"title": "Session B"})
        session_b_id = response_b.json()["id"]

        # Clear session A from memory
        del server_state.sessions[session_a_id]
        del server_state.messages[session_a_id]

        # Mock load_session
        now = datetime.now(UTC)
        session_a_data = SessionData(
            session_id=session_a_id,
            agent_name="test-agent",
            cwd=str(tmp_project_dir),
            created_at=now,
            last_active=now,
            metadata={"title": "Session A"},
        )
        server_state.agent.load_session = AsyncMock(return_value=session_a_data)  # type: ignore[method-assign]
        server_state.agent.conversation = Mock()
        server_state.agent.conversation.chat_messages = []

        # Switch to session A
        await get_or_load_session(server_state, session_a_id)

        # The agent's input_provider still has session B's ID!
        # This means any tool confirmations will be sent to session B, not A
        current_input_provider: OpenCodeInputProvider = server_state.agent._input_provider  # type: ignore[assignment]
        assert current_input_provider is not None

        # This assertion demonstrates the bug
        assert current_input_provider.session_id == session_a_id, (
            f"BUG: agent._input_provider.session_id is '{current_input_provider.session_id}' "
            f"but should be '{session_a_id}' after switching to session A. "
            f"Tool confirmations will go to the wrong session!"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
