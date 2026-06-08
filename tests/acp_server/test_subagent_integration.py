"""Integration tests for subagent functionality through ACP server (T17).

Tests the full end-to-end subagent flow through AgentPoolACPAgent:
1. Full flow: initialize -> new_session -> prompt with subagent -> verify ToolCallStart emitted
2. Session hierarchy fields returned in session info
3. Capability gating: subagent features only work when advertised
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from acp.schema import (
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    TextContentBlock,
    ToolCallStart,
)
from acp.schema.requests import PromptDelegation
from agentpool import AgentPool, AgentsManifest
from agentpool.sessions import SessionData
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.converters import to_session_info


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def subagent_pool():
    """Create an AgentPool with orchestrator and worker agents."""
    manifest = AgentsManifest.from_yaml("""
default_agent: orchestrator

agents:
  worker:
    model:
      type: test
      custom_output_text: "Worker done"
    system_prompt: You are a worker agent.

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do some work"
          description: "Test task"
    tools:
      - type: subagent
    system_prompt: You are an orchestrator.
""")
    async with AgentPool(manifest) as pool:
        yield pool


@pytest.fixture
def mock_client():
    """Create a mock ACP client that captures session updates and ext notifications."""
    client = AsyncMock()
    updates = []
    ext_notifications = []

    async def capture_session_update(notification):
        updates.append(notification)

    async def capture_ext_notification(method, params=None):
        ext_notifications.append({"method": method, "params": params})

    client.session_update = capture_session_update
    client.ext_notification = capture_ext_notification
    client.updates = updates
    client.ext_notifications = ext_notifications
    return client


@pytest.fixture
async def acp_agent(subagent_pool, mock_client):
    """Create an initialized AgentPoolACPAgent with subagent pool."""
    orchestrator = subagent_pool.get_agent("orchestrator")
    agent = AgentPoolACPAgent(client=mock_client, default_agent=orchestrator)
    agent._initialized = False
    await agent.initialize(InitializeRequest(protocol_version=1))
    return agent


# =============================================================================
# Test 1: Full subagent flow
# =============================================================================


@pytest.mark.anyio
async def test_full_subagent_flow_emits_tool_call_start(acp_agent, mock_client):
    """Full flow through ACP agent emits ToolCallStart(kind='subagent') during subagent delegation."""
    # Create a new session
    new_session_req = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    new_session_resp = await acp_agent.new_session(new_session_req)
    session_id = new_session_resp.session_id
    assert session_id is not None

    # Clear any updates from session creation
    mock_client.updates.clear()

    # Send a prompt that triggers the task tool (orchestrator model is configured to call "task")
    prompt_req = PromptRequest(
        session_id=session_id,
        prompt=[TextContentBlock(text="Please delegate to worker")],
    )

    prompt_resp = await acp_agent.prompt(prompt_req)

    # Verify the prompt was processed successfully
    assert prompt_resp.stop_reason == "end_turn"

    # Collect all ToolCallStart updates from notifications
    tool_starts: list[ToolCallStart] = []
    for notification in mock_client.updates:
        update = getattr(notification, "update", None)
        if isinstance(update, ToolCallStart):
            tool_starts.append(update)

    # Verify at least one ToolCallStart with kind="subagent" was emitted
    subagent_starts = [t for t in tool_starts if t.kind == "subagent"]
    assert len(subagent_starts) >= 1, (
        f"Expected at least one ToolCallStart(kind='subagent'), "
        f"got {len(subagent_starts)} subagent starts out of {len(tool_starts)} total tool starts. "
        f"Updates: {[type(n.update).__name__ for n in mock_client.updates if hasattr(n, 'update')]}"
    )

    # Verify the subagent run info structure
    start = subagent_starts[0]
    assert start.subagent is not None
    assert start.subagent.subagent_id == "worker"
    assert start.status == "in_progress"
    assert start.title is not None
    assert "worker" in start.title


# =============================================================================
# Test 2: Session hierarchy fields returned in session info
# =============================================================================


@pytest.mark.anyio
async def test_session_hierarchy_fields_in_session_info():
    """to_session_info maps SessionData parent_id to SessionInfo.parent_session_id and sets depth."""
    # Create a root session
    root_data = SessionData(
        session_id="root-session",
        agent_name="test_agent",
        cwd="/tmp",
    )

    # Create a child session
    child_data = SessionData(
        session_id="child-session",
        agent_name="subagent",
        cwd="/tmp",
        parent_id="root-session",
    )

    root_info = to_session_info(root_data)
    child_info = to_session_info(child_data)

    # Root should have no parent and depth 0
    assert root_info.parent_session_id is None
    assert root_info.depth == 0

    # Child should have parent_session_id mapped from parent_id and depth 1
    assert child_info.parent_session_id == "root-session"
    assert child_info.depth == 1


@pytest.mark.anyio
async def test_session_hierarchy_fields_via_list_sessions(acp_agent, subagent_pool, mock_client):
    """list_sessions returns SessionInfo with hierarchy fields populated."""
    # Seed the memory storage provider with parent and child sessions
    provider = subagent_pool.storage.providers[0]
    provider.sessions["parent-ses"] = SessionData(
        session_id="parent-ses",
        agent_name="orchestrator",
        cwd="/tmp",
    )
    provider.sessions["child-ses"] = SessionData(
        session_id="child-ses",
        agent_name="worker",
        cwd="/tmp",
        parent_id="parent-ses",
    )

    # Clear cache to force fresh read
    acp_agent._sessions_cache = None
    acp_agent._sessions_cache_time = 0.0

    from acp.schema import ListSessionsRequest

    response = await acp_agent.list_sessions(ListSessionsRequest())

    # Find child session in response
    child_info = next((s for s in response.sessions if s.session_id == "child-ses"), None)
    assert child_info is not None, "Child session not found in list_sessions response"
    assert child_info.parent_session_id == "parent-ses"
    assert child_info.depth == 1

    # Find parent session
    parent_info = next((s for s in response.sessions if s.session_id == "parent-ses"), None)
    assert parent_info is not None
    assert parent_info.parent_session_id is None
    assert parent_info.depth == 0


# =============================================================================
# Test 3: Capability gating
# =============================================================================


@pytest.mark.anyio
async def test_capability_gating_delegation_ignored_when_not_advertised(acp_agent, mock_client):
    """When prompt_delegation is not advertised, delegation policy is ignored."""
    # Create a new session
    new_session_req = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    new_session_resp = await acp_agent.new_session(new_session_req)
    session_id = new_session_resp.session_id

    # Disable prompt delegation capability by patching the property
    with patch.object(type(acp_agent), "prompt_delegation_enabled", new=False):
        mock_client.updates.clear()
        mock_client.ext_notifications.clear()

        # Send a prompt with require delegation policy
        prompt_req = PromptRequest(
            session_id=session_id,
            prompt=[TextContentBlock(text="Hello")],
            delegation=PromptDelegation(policy="require", subagent_id="nonexistent"),
        )

        # Should NOT raise - delegation is ignored, normal flow runs
        prompt_resp = await acp_agent.prompt(prompt_req)
        assert prompt_resp.stop_reason == "end_turn"

    # Verify no error toast was sent (no ext notifications with _agentpool/toast)
    toast_notifications = [
        n for n in mock_client.ext_notifications if n.get("method") == "_agentpool/toast"
    ]
    assert len(toast_notifications) == 0, (
        "Delegation with missing subagent should be ignored when capability not advertised, "
        "but error toasts were sent"
    )


@pytest.mark.anyio
async def test_capability_gating_require_errors_when_advertised_and_missing(acp_agent, mock_client):
    """When prompt_delegation IS advertised, require policy errors on missing subagent."""
    # Create a new session
    new_session_req = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    new_session_resp = await acp_agent.new_session(new_session_req)
    session_id = new_session_resp.session_id

    # Ensure capability is advertised (default is True)
    assert acp_agent.prompt_delegation_enabled is True

    mock_client.updates.clear()
    mock_client.ext_notifications.clear()

    # Send a prompt with require delegation for a nonexistent subagent
    prompt_req = PromptRequest(
        session_id=session_id,
        prompt=[TextContentBlock(text="Hello")],
        delegation=PromptDelegation(policy="require", subagent_id="nonexistent"),
    )

    # Should return end_turn after catching the RequestError internally
    prompt_resp = await acp_agent.prompt(prompt_req)
    assert prompt_resp.stop_reason == "end_turn"

    # Verify an error toast WAS sent because the subagent is missing
    toast_notifications = [
        n for n in mock_client.ext_notifications if n.get("method") == "_agentpool/toast"
    ]
    assert len(toast_notifications) >= 1, (
        "Require delegation with missing subagent should produce error toast "
        "when capability is advertised"
    )
