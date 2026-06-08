"""End-to-end test for ACP subagent delegation flow (T20).

Tests the complete lifecycle:
1. Initialize ACP server with AgentPool
2. Create session
3. Send prompt that triggers subagent delegation
4. Verify full protocol flow including subagent capabilities,
   available_subagents, ToolCallStart/Progress emission,
   and session hierarchy fields.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any
from unittest.mock import AsyncMock

import anyio
import pytest

from acp.schema import (
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    SessionNotification,
    SubagentCapabilities,
    SubagentInfo,
    ToolCallProgress,
    ToolCallStart,
)
from acp.schema.content_blocks import TextContentBlock
from agentpool import AgentPool, AgentsManifest
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


@pytest.fixture
def manifest_yaml() -> str:
    """Manifest with orchestrator that delegates to worker via subagent tool."""
    return """
default_agent: orchestrator

agents:
  worker:
    model:
      type: test
      custom_output_text: "Done"
    system_prompt: "You are a worker agent."

  orchestrator:
    model:
      type: test
      call_tools: ["task"]
      tool_args:
        task:
          agent_or_team: worker
          prompt: "Do some work"
          description: "E2E subagent test"
    tools:
      - type: subagent
    system_prompt: "You are an orchestrator. Delegate to worker."

storage:
  providers:
    - type: memory
"""


@pytest.fixture
async def agent_pool(manifest_yaml: str) -> AsyncGenerator[AgentPool, None]:
    """Create a real AgentPool from the test manifest."""
    manifest = AgentsManifest.from_yaml(manifest_yaml)
    pool = AgentPool(manifest)
    await pool.__aenter__()
    yield pool
    await pool.__aexit__(None, None, None)


@pytest.fixture
def mock_client() -> AsyncMock:
    """Create a mock ACP client that captures all notifications."""
    client = AsyncMock()
    client.session_update = AsyncMock()
    client.send_request = AsyncMock(return_value={"connectionId": "test-conn"})
    return client


@pytest.fixture
def captured_notifications(mock_client: AsyncMock) -> list[SessionNotification]:
    """Access the list of captured session notifications."""
    notifications: list[SessionNotification] = []

    async def capture(notification: SessionNotification) -> None:
        notifications.append(notification)

    mock_client.session_update.side_effect = capture
    return notifications


@pytest.fixture
async def acp_agent(agent_pool: AgentPool, mock_client: AsyncMock) -> AgentPoolACPAgent:
    """Create an initialized AgentPoolACPAgent with the test pool."""
    default_agent = agent_pool.get_agent("orchestrator")
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=default_agent,
    )
    # Initialize
    init_request = InitializeRequest(protocol_version=1)
    await acp_agent.initialize(init_request)
    return acp_agent


class TestE2ESubagentFlow:
    """End-to-end subagent delegation flow tests."""

    @pytest.mark.anyio
    async def test_initialize_response_has_subagent_capabilities(
        self,
        agent_pool: AgentPool,
        mock_client: AsyncMock,
    ) -> None:
        """InitializeResponse must advertise subagent capabilities."""
        default_agent = agent_pool.get_agent("orchestrator")
        acp_agent = AgentPoolACPAgent(client=mock_client, default_agent=default_agent)

        request = InitializeRequest(protocol_version=1)
        response = await acp_agent.initialize(request)

        assert response.agent_capabilities is not None
        assert response.agent_capabilities.subagents is not None
        assert isinstance(response.agent_capabilities.subagents, SubagentCapabilities)
        assert response.agent_capabilities.subagents.prompt_delegation is True
        assert response.agent_capabilities.subagents.background is True

    @pytest.mark.anyio
    async def test_new_session_has_available_subagents(
        self,
        acp_agent: AgentPoolACPAgent,
    ) -> None:
        """NewSessionResponse must include available_subagents from the pool."""
        request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
        response = await acp_agent.new_session(request)

        assert response.session_id
        assert response.available_subagents is not None
        assert len(response.available_subagents) >= 2

        ids = {s.subagent_id for s in response.available_subagents}
        assert "orchestrator" in ids
        assert "worker" in ids

        # Verify structure of subagent info
        for info in response.available_subagents:
            assert isinstance(info, SubagentInfo)
            assert info.subagent_id
            assert info.name

    @pytest.mark.anyio
    async def test_prompt_triggers_subagent_and_emits_protocol_events(
        self,
        acp_agent: AgentPoolACPAgent,
        captured_notifications: list[SessionNotification],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Full prompt flow: subagent delegation emits correct ACP protocol events."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "tool_box")

        # Create a session
        session_request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
        session_response = await acp_agent.new_session(session_request)
        session_id = session_response.session_id

        # Allow background tasks from session setup to complete
        await anyio.sleep(0.1)
        captured_notifications.clear()

        # Send prompt that triggers subagent delegation
        prompt_request = PromptRequest(
            session_id=session_id,
            prompt=[TextContentBlock(text="Delegate to worker")],
            message_id="msg-001",
        )
        prompt_response = await acp_agent.prompt(prompt_request)

        assert prompt_response.stop_reason == "end_turn"
        assert prompt_response.user_message_id == "msg-001"

        # Allow any remaining background notifications to flush
        await anyio.sleep(0.1)

        # Extract all updates from captured notifications
        updates: list[Any] = []
        for notification in captured_notifications:
            if isinstance(notification, SessionNotification):
                updates.append(notification.update)

        # Find ToolCallStart with kind="subagent"
        tool_call_starts = [
            u for u in updates if isinstance(u, ToolCallStart) and u.kind == "subagent"
        ]
        assert len(tool_call_starts) >= 1, (
            f"Expected at least one ToolCallStart(kind='subagent'), "
            f"got {len(tool_call_starts)}. Updates: {[type(u).__name__ for u in updates]}"
        )

        start = tool_call_starts[0]
        assert start.status == "in_progress"
        assert start.subagent is not None
        assert start.subagent.subagent_id == "worker"
        assert start.subagent.name == "worker"
        assert start.subagent.child_session_id is not None
        assert start.subagent.run_mode == "foreground"
        assert start.subagent.depth is not None
        assert start.subagent.depth >= 1

        child_session_id = start.subagent.child_session_id
        tool_call_id = start.tool_call_id

        # Find ToolCallProgress with status="completed" for the same tool call
        completed_progresses = [
            u
            for u in updates
            if isinstance(u, ToolCallProgress)
            and u.status == "completed"
            and u.tool_call_id == tool_call_id
        ]
        assert len(completed_progresses) >= 1, (
            f"Expected at least one ToolCallProgress(status='completed') for {tool_call_id}, "
            f"got {len(completed_progresses)}"
        )

        completed = completed_progresses[0]
        assert completed.subagent is not None
        assert completed.subagent.subagent_id == "worker"
        assert completed.subagent.child_session_id == child_session_id
        assert completed.subagent.status == "completed"

    @pytest.mark.skip(
        reason="SessionPool does not yet support parent_tool_call_id/subagent_id fields on SessionData"
    )
    @pytest.mark.anyio
    async def test_session_hierarchy_fields_correct(
        self,
        agent_pool: AgentPool,
        acp_agent: AgentPoolACPAgent,
        captured_notifications: list[SessionNotification],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Child session created by subagent delegation has correct hierarchy fields."""
        monkeypatch.setenv("ACP_SUBAGENT_DISPLAY_MODE", "tool_box")

        session_request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
        session_response = await acp_agent.new_session(session_request)
        parent_session_id = session_response.session_id

        await anyio.sleep(0.1)
        captured_notifications.clear()

        prompt_request = PromptRequest(
            session_id=parent_session_id,
            prompt=[TextContentBlock(text="Delegate to worker")],
            message_id="msg-002",
        )
        await acp_agent.prompt(prompt_request)
        await anyio.sleep(0.1)

        # Extract child_session_id from the subagent ToolCallStart
        child_session_id: str | None = None
        tool_call_id: str | None = None
        for notification in captured_notifications:
            if isinstance(notification, SessionNotification):
                update = notification.update
                if isinstance(update, ToolCallStart) and update.kind == "subagent":
                    child_session_id = update.subagent.child_session_id if update.subagent else None
                    tool_call_id = update.tool_call_id
                    break

        assert child_session_id is not None, "Child session ID not found in notifications"
        assert tool_call_id is not None, "Tool call ID not found in notifications"

        # Load child session data from the store
        assert agent_pool.session_pool is not None
        assert agent_pool.session_pool.sessions.store is not None
        child_data = await agent_pool.session_pool.sessions.store.load(child_session_id)

        assert child_data is not None, f"Child session {child_session_id} not found in store"
        assert child_data.parent_id == parent_session_id, (
            f"Expected parent_id={parent_session_id}, got {child_data.parent_id}"
        )
        assert child_data.agent_name == "worker", (
            f"Expected agent_name='worker', got {child_data.agent_name}"
        )

        # Verify hierarchy metadata fields (T4)
        assert child_data.parent_tool_call_id == tool_call_id, (
            f"Expected parent_tool_call_id={tool_call_id}, got {child_data.parent_tool_call_id}"
        )
        assert child_data.subagent_id == "worker", (
            f"Expected subagent_id='worker', got {child_data.subagent_id}"
        )
