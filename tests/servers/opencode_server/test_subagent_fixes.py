"""Tests for subagent fixes (RFC-0012)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool_toolsets.builtin.subagent_tools import SubagentTools


@pytest.mark.asyncio
async def test_get_session_children(async_client, server_state):
    """Test GET /session/{session_id}/children endpoint."""
    # Create parent session
    parent_resp = await async_client.post("/session", json={"title": "Parent"})
    assert parent_resp.status_code == 200
    parent_id = parent_resp.json()["id"]

    # Create child sessions
    child1_resp = await async_client.post(
        "/session", json={"title": "Child 1", "parent_id": parent_id}
    )
    assert child1_resp.status_code == 200
    child1_id = child1_resp.json()["id"]

    child2_resp = await async_client.post(
        "/session", json={"title": "Child 2", "parent_id": parent_id}
    )
    assert child2_resp.status_code == 200
    child2_id = child2_resp.json()["id"]

    # Create unrelated session
    other_resp = await async_client.post("/session", json={"title": "Other"})
    assert other_resp.status_code == 200
    other_id = other_resp.json()["id"]

    # Get children of parent
    resp = await async_client.get(f"/session/{parent_id}/children")
    assert resp.status_code == 200
    children = resp.json()

    assert len(children) == 2
    child_ids = [c["id"] for c in children]
    assert child1_id in child_ids
    assert child2_id in child_ids
    assert other_id not in child_ids

    # Verify parent_id in response
    for child in children:
        assert child["parentID"] == parent_id

    # Get children of unrelated session (should be empty)
    resp = await async_client.get(f"/session/{other_id}/children")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


@pytest.mark.asyncio
async def test_task_tool_return_format():
    """Test that task tool returns structured data with metadata."""
    tools = SubagentTools()

    # Mock context
    ctx = MagicMock()

    # Mock node (agent) using a class to satisfy runtime_checkable Protocol
    class MockStreamingAgent:
        def __init__(self):
            self.run_stream = MagicMock()

    mock_agent = MockStreamingAgent()

    # Mock run_stream to yield events
    async def mock_stream(*args, **kwargs):
        yield StreamCompleteEvent(message=ChatMessage(role="assistant", content="Task result"))

    mock_agent.run_stream.side_effect = mock_stream
    ctx.pool.nodes = {"child_agent": mock_agent}
    ctx.node.session_id = "parent_session"
    ctx.events.emit_event = AsyncMock()

    # Execute task
    result = await tools.task(
        ctx=ctx, agent_or_team="child_agent", prompt="Do work", description="Work", async_mode=False
    )

    # Verify result format
    assert isinstance(result, dict)
    assert "output" in result
    assert result["output"] == "Task result"
    assert "metadata" in result
    assert "sessionId" in result["metadata"]
    assert result["metadata"]["sessionId"] is not None
    assert isinstance(result["metadata"]["sessionId"], str)


@pytest.mark.asyncio
async def test_task_tool_async_mode_return_format():
    """Test that task tool in async mode returns structured data."""
    tools = SubagentTools()

    # Mock context
    ctx = MagicMock()
    ctx.node.session_id = "parent_session"

    # Mock node
    class MockStreamingAgent:
        def __init__(self):
            self.run_stream = MagicMock()

    mock_agent = MockStreamingAgent()
    ctx.pool.nodes = {"child_agent": mock_agent}

    # Mock internal_fs
    ctx.internal_fs.mkdirs = MagicMock()

    # Mock events.emit_event (needed for SpawnSessionStart emission)
    ctx.events.emit_event = AsyncMock()

    # Execute task in async mode
    result = await tools.task(
        ctx=ctx, agent_or_team="child_agent", prompt="Do work", description="Work", async_mode=True
    )

    # Verify result format
    assert isinstance(result, dict)
    assert "output" in result
    assert "Task started in background" in result["output"]
    assert "metadata" in result
    assert "taskId" in result["metadata"]
    assert "sessionId" in result["metadata"]
    assert "outputFile" in result["metadata"]
