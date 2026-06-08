"""Integration tests for delegation through AgentPoolACPAgent (T18).

Tests the full protocol flow:
1. initialize -> verify delegation capabilities advertised
2. new_session -> verify available_subagents in response
3. prompt with delegation policy -> verify routing behavior
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from acp.schema import (
    InitializeRequest,
    NewSessionRequest,
    PromptRequest,
    TextContentBlock,
)
from acp.schema.requests import PromptDelegation
from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.delegation import AgentPool
from agentpool.messaging import ChatMessage
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_toolsets.builtin.subagent_tools import SubagentTools


def _make_text_block(text: str = "hello") -> TextContentBlock:
    """Create a text content block for prompts."""
    return TextContentBlock(text=text)


async def _mock_empty_stream(
    *_args: object, **_kwargs: object
) -> AsyncIterator[StreamCompleteEvent]:
    """Async generator that yields a single completion event."""
    yield StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


@pytest.fixture
async def delegation_pool():
    """Create a pool with a main agent and a subagent for delegation tests."""
    pool = AgentPool()

    def main_callback(message: str) -> str:
        return f"Main: {message}"

    def subagent_callback(message: str) -> str:
        return f"Sub: {message}"

    main_agent = Agent.from_callback(
        name="main_agent",
        callback=main_callback,
        agent_pool=pool,
        toolsets=[SubagentTools()],
    )
    subagent = Agent.from_callback(
        name="subagent_a",
        callback=subagent_callback,
        agent_pool=pool,
        system_prompt="You are subagent A",
    )
    pool.register("main_agent", main_agent)
    pool.register("subagent_a", subagent)
    return pool, main_agent, subagent


@pytest.fixture
async def acp_agent_with_delegation(delegation_pool):
    """Create an AgentPoolACPAgent wired to a pool with subagents."""
    pool, main_agent, _subagent = delegation_pool
    mock_client = AsyncMock()
    acp_agent = AgentPoolACPAgent(client=mock_client, default_agent=main_agent)
    yield acp_agent, pool, mock_client
    # Cleanup
    await acp_agent.session_manager.close_all_sessions()


# =============================================================================
# Full delegation flow
# =============================================================================


@pytest.mark.integration
async def test_full_delegation_flow_prefer_policy(acp_agent_with_delegation) -> None:
    """Full flow: initialize -> new_session -> prompt with prefer policy routes to subagent."""
    acp_agent, pool, _mock_client = acp_agent_with_delegation

    # Step 1: initialize
    init_req = InitializeRequest(protocol_version=1)
    init_resp = await acp_agent.initialize(init_req)
    assert init_resp.agent_capabilities.subagents is not None
    assert init_resp.agent_capabilities.subagents.prompt_delegation is True
    assert init_resp.agent_capabilities.subagents.background is True

    # Step 2: new_session
    new_sess_req = NewSessionRequest(cwd="/tmp", mcp_servers=[])
    new_sess_resp = await acp_agent.new_session(new_sess_req)
    assert new_sess_resp.available_subagents is not None
    subagent_ids = {s.subagent_id for s in new_sess_resp.available_subagents}
    assert "subagent_a" in subagent_ids
    assert "main_agent" in subagent_ids

    session_id = new_sess_resp.session_id

    # Step 3: prompt with prefer policy
    delegation = PromptDelegation(policy="prefer", subagent_id="subagent_a")
    prompt_req = PromptRequest(
        session_id=session_id,
        prompt=[_make_text_block("Do something")],
        delegation=delegation,
    )

    # Patch the subagent's run_stream to verify it gets called
    subagent = pool.all_agents["subagent_a"]
    with patch.object(subagent, "run_stream", side_effect=_mock_empty_stream) as mock_run:
        prompt_resp = await acp_agent.prompt(prompt_req)

    assert prompt_resp.stop_reason == "end_turn"
    mock_run.assert_called_once()
    # Verify parent_session_id and depth were passed
    call_kwargs = mock_run.call_args.kwargs
    assert call_kwargs.get("parent_session_id") == session_id
    assert call_kwargs.get("depth") == 1


# =============================================================================
# Error case: require policy with missing subagent
# =============================================================================


@pytest.mark.integration
async def test_require_policy_missing_subagent_returns_error_response(
    acp_agent_with_delegation,
) -> None:
    """Require policy with missing subagent should return PromptResponse, not crash."""
    acp_agent, _pool, mock_client = acp_agent_with_delegation

    # Initialize and create session
    await acp_agent.initialize(InitializeRequest(protocol_version=1))
    new_sess_resp = await acp_agent.new_session(NewSessionRequest(cwd="/tmp", mcp_servers=[]))
    session_id = new_sess_resp.session_id

    delegation = PromptDelegation(policy="require", subagent_id="nonexistent")
    prompt_req = PromptRequest(
        session_id=session_id,
        prompt=[_make_text_block("Do something")],
        delegation=delegation,
    )

    prompt_resp = await acp_agent.prompt(prompt_req)

    # Should return a PromptResponse (not raise unhandled exception)
    assert prompt_resp.stop_reason == "end_turn"
    assert prompt_resp.user_message_id == prompt_req.message_id
    # Should have sent an error toast notification via the client
    mock_client.ext_notification.assert_called()
    # Verify the toast contains error info about missing subagent
    call_args = mock_client.ext_notification.call_args
    assert call_args is not None
    params = call_args[0][1] if len(call_args[0]) > 1 else call_args[1].get("params", {})
    assert "nonexistent" in str(params.get("message", ""))


# =============================================================================
# Disable policy: verify subagent tools are filtered out
# =============================================================================


@pytest.mark.integration
async def test_disable_policy_filters_subagent_tools(acp_agent_with_delegation) -> None:
    """Disable policy should prevent subagent tools from being used during the prompt."""
    acp_agent, _pool, _mock_client = acp_agent_with_delegation

    # Initialize and create session
    await acp_agent.initialize(InitializeRequest(protocol_version=1))
    new_sess_resp = await acp_agent.new_session(NewSessionRequest(cwd="/tmp", mcp_servers=[]))
    session_id = new_sess_resp.session_id

    # Get the session to inspect tool states
    session = acp_agent.session_manager.get_session(session_id)
    assert session is not None

    # Verify subagent tools exist and are enabled before the prompt
    all_tools = await session.agent.tools.get_tools()
    subagent_tools = [t for t in all_tools if t.category == "subagent"]
    assert len(subagent_tools) > 0
    for tool in subagent_tools:
        assert tool.enabled is True

    delegation = PromptDelegation(policy="disable")
    prompt_req = PromptRequest(
        session_id=session_id,
        prompt=[_make_text_block("Do something locally")],
        delegation=delegation,
    )

    # Patch run_stream to avoid actual LLM call
    with patch.object(session.agent, "run_stream", side_effect=_mock_empty_stream):
        prompt_resp = await acp_agent.prompt(prompt_req)

    assert prompt_resp.stop_reason == "end_turn"

    # Verify subagent tools are re-enabled after the prompt
    all_tools_after = await session.agent.tools.get_tools()
    for tool in all_tools_after:
        if tool.category == "subagent":
            assert tool.enabled is True
