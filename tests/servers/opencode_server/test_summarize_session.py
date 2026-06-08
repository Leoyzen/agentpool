"""Tests for summarize_session endpoint with SessionPool migration."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, Mock, patch

import pytest

from agentpool_config.session_pool import OpenCodeConfig


if TYPE_CHECKING:
    from unittest.mock import MagicMock

    from httpx import AsyncClient

    from agentpool_server.opencode_server.state import ServerState


pytestmark = pytest.mark.asyncio


async def test_summarize_uses_session_pool_when_flag_enabled(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
    mock_pool: Mock,
):
    """When use_session_pool_for_summarize is True, endpoint uses SessionPool.run_stream."""
    from pydantic_ai import RequestUsage, TextPart, TextPartDelta

    from agentpool.agents.events import PartDeltaEvent, PartStartEvent, StreamCompleteEvent
    from agentpool.messaging.messages import ChatMessage
    from agentpool_server.opencode_server.models import (
        AssistantMessage,
        MessagePath,
        MessageTime,
        MessageWithParts,
        TextPart as OpenCodeTextPart,
    )

    # Create session and add a message
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Pre-populate messages so summarize doesn't 400
    user_msg = AssistantMessage(
        id="m1",
        session_id=session_id,
        parent_id="",
        model_id="default",
        provider_id="agentpool",
        mode="ask",
        agent="test-agent",
        path=MessagePath(cwd=server_state.working_dir, root=server_state.working_dir),
        time=MessageTime(created=0),
    )
    server_state.messages[session_id] = [
        MessageWithParts(
            info=user_msg,
            parts=[OpenCodeTextPart(id="p1", message_id="m1", session_id=session_id, text="hello")],
        )
    ]

    # Enable the feature flag
    mock_pool.manifest.opencode = OpenCodeConfig(
        use_session_pool=True,
        use_session_pool_for_summarize=True,
    )

    # Track whether session_pool.run_stream was called
    run_stream_called = False

    async def mock_run_stream(*args: object, **kwargs: object):
        nonlocal run_stream_called
        run_stream_called = True
        yield PartStartEvent(index=0, part=TextPart(content="Summary"))
        yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" text"))
        yield StreamCompleteEvent(
            message=ChatMessage(
                content="Summary text",
                role="assistant",
                usage=RequestUsage(input_tokens=10, output_tokens=5),
            )
        )

    mock_pool.session_pool.run_stream = mock_run_stream

    # Mock compact_conversation to avoid real compaction logic
    with patch(
        "agentpool.messaging.compaction.compact_conversation",
        new=AsyncMock(),
    ):
        response = await async_client.post(f"/session/{session_id}/summarize")

    assert response.status_code == 200
    assert run_stream_called is True

    result = response.json()
    assert "info" in result
    assert "parts" in result
    # Should have step_start, text_part, step_finish
    assert len(result["parts"]) >= 2


async def test_summarize_uses_direct_agent_when_flag_disabled(
    async_client: AsyncClient,
    server_state: ServerState,
    mock_agent: Mock,
    mock_pool: Mock,
):
    """When use_session_pool_for_summarize is False, endpoint uses agent.run_stream directly."""
    from pydantic_ai import RequestUsage, TextPart, TextPartDelta

    from agentpool.agents.events import PartDeltaEvent, PartStartEvent, StreamCompleteEvent
    from agentpool.messaging.messages import ChatMessage
    from agentpool_server.opencode_server.models import (
        AssistantMessage,
        MessagePath,
        MessageTime,
        MessageWithParts,
        TextPart as OpenCodeTextPart,
    )

    # Create session and add a message
    response = await async_client.post("/session", json={"title": "Test Session"})
    assert response.status_code == 200
    session_id = response.json()["id"]

    # Pre-populate messages
    user_msg = AssistantMessage(
        id="m1",
        session_id=session_id,
        parent_id="",
        model_id="default",
        provider_id="agentpool",
        mode="ask",
        agent="test-agent",
        path=MessagePath(cwd=server_state.working_dir, root=server_state.working_dir),
        time=MessageTime(created=0),
    )
    server_state.messages[session_id] = [
        MessageWithParts(
            info=user_msg,
            parts=[OpenCodeTextPart(id="p1", message_id="m1", session_id=session_id, text="hello")],
        )
    ]

    # Ensure flag is disabled
    mock_pool.manifest.opencode = OpenCodeConfig(
        use_session_pool=True,
        use_session_pool_for_summarize=False,
    )

    # Mock agent.run_stream
    async def mock_agent_stream(*args: object, **kwargs: object):
        yield PartStartEvent(index=0, part=TextPart(content="Direct summary"))
        yield PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" done"))
        yield StreamCompleteEvent(
            message=ChatMessage(
                content="Direct summary done",
                role="assistant",
                usage=RequestUsage(input_tokens=5, output_tokens=3),
            )
        )

    mock_agent.run_stream = mock_agent_stream

    # Mock compact_conversation
    with patch(
        "agentpool.messaging.compaction.compact_conversation",
        new=AsyncMock(),
    ):
        response = await async_client.post(f"/session/{session_id}/summarize")

    assert response.status_code == 200

    # Verify session_pool.run_stream was NOT called
    assert not hasattr(mock_pool.session_pool.run_stream, "call_count") or mock_pool.session_pool.run_stream.call_count == 0

    result = response.json()
    assert "info" in result
    assert "parts" in result
