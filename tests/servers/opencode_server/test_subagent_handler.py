"""Tests for subagent event handling in OpenCodeStreamAdapter."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from agentpool.agents.events import StreamCompleteEvent, SubAgentEvent
from agentpool.messaging import ChatMessage
from agentpool_server.opencode_server.models import MessageWithParts
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


@pytest.mark.asyncio
async def test_subagent_event_triggers_ensure_session(server_state: ServerState) -> None:
    """Test that SubAgentEvent with child_session_id triggers ensure_session."""
    # Setup
    session_id = "parent-session"
    child_session_id = "child-session"

    # Create valid AssistantMessage via factory
    from agentpool_server.opencode_server.models import MessagePath, MessageTime

    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id=session_id,
        time=MessageTime(created=1000),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="user-msg-1",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )

    # Mock ensure_session
    server_state.ensure_session = AsyncMock()  # type: ignore

    adapter = OpenCodeStreamAdapter(
        state=server_state,
        session_id=session_id,
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        working_dir="/tmp",
    )

    # Create a stream with a SubAgentEvent
    async def event_stream():
        # Inner event to wrap
        inner_event = StreamCompleteEvent(message=ChatMessage(role="assistant", content="Done"))

        yield SubAgentEvent(
            source_name="subagent",
            source_type="agent",
            event=inner_event,
            child_session_id=child_session_id,
            parent_session_id=session_id,
        )

    # Run process_stream
    async for _ in adapter.process_stream(event_stream()):
        pass

    # Verify ensure_session was called
    server_state.ensure_session.assert_awaited_once_with(  # type: ignore
        child_session_id,
        parent_id=session_id,
    )


@pytest.mark.asyncio
async def test_subagent_event_without_child_session_id(server_state: ServerState) -> None:
    """Test that SubAgentEvent without child_session_id works and doesn't trigger ensure_session."""
    # Setup
    session_id = "parent-session"

    from agentpool_server.opencode_server.models import MessagePath, MessageTime

    assistant_msg = MessageWithParts.assistant(
        message_id="msg-1",
        session_id=session_id,
        time=MessageTime(created=1000),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="user-msg-1",
        provider_id="test-provider",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )

    # Mock ensure_session
    server_state.ensure_session = AsyncMock()  # type: ignore

    adapter = OpenCodeStreamAdapter(
        state=server_state,
        session_id=session_id,
        assistant_msg_id="msg-1",
        assistant_msg=assistant_msg,
        working_dir="/tmp",
    )

    # Create a stream with a SubAgentEvent
    async def event_stream():
        inner_event = StreamCompleteEvent(message=ChatMessage(role="assistant", content="Done"))

        yield SubAgentEvent(
            source_name="subagent",
            source_type="agent",
            event=inner_event,
            child_session_id=None,  # No child session ID
        )

    # Run process_stream
    async for _ in adapter.process_stream(event_stream()):
        pass

    # Verify ensure_session was NOT called
    server_state.ensure_session.assert_not_called()  # type: ignore
