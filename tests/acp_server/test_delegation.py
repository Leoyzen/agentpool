"""TDD tests for delegation handler with auto/disable/prefer/require policies (T12).

Tests that ACPSession.process_prompt correctly handles PromptDelegation
based on the advertised prompt_delegation capability.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import TextContentBlock
from acp.schema.requests import PromptDelegation
from agentpool import Agent
from agentpool.agents.events import StreamCompleteEvent
from agentpool.delegation import AgentPool
from agentpool.messaging import ChatMessage
from agentpool.tool_impls.read.tool import ReadTool
from agentpool_toolsets.builtin.subagent_tools import SubagentTools
from agentpool_server.acp_server.session import ACPSession


def _make_stream_complete_event() -> StreamCompleteEvent:
    """Create a simple StreamCompleteEvent for mocking run_stream."""
    return StreamCompleteEvent(message=ChatMessage(content="test", role="assistant"))


async def _mock_run_stream(*_args: object, **_kwargs: object) -> AsyncIterator[StreamCompleteEvent]:
    """Async generator that yields a single StreamCompleteEvent."""
    yield _make_stream_complete_event()


@pytest.fixture
def agent_pool_with_agents() -> tuple[AgentPool, Agent, Agent]:
    """Create a pool with a main agent and a subagent."""
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
    )
    pool.register("main_agent", main_agent)
    pool.register("subagent_a", subagent)
    return pool, main_agent, subagent


@pytest.fixture
def acp_session(agent_pool_with_agents: tuple[AgentPool, Agent, Agent]) -> ACPSession:
    """Create an ACPSession with mocked dependencies for unit testing."""
    _pool, main_agent, _subagent = agent_pool_with_agents
    mock_client = MagicMock()
    mock_acp_agent = MagicMock()
    mock_acp_agent.prompt_delegation_enabled = True

    session = ACPSession(
        session_id="test-session",
        agent=main_agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
    )

    # Mock acp_env to avoid real cleanup errors in close()
    session.acp_env = MagicMock()
    session.acp_env.__aexit__ = AsyncMock()

    # Mock notifications
    session.notifications = MagicMock()
    session.notifications.send_update = AsyncMock()

    return session


@pytest.fixture
def text_content_block() -> TextContentBlock:
    """Create a simple text content block."""
    return TextContentBlock(text="hello")


# =============================================================================
# auto policy tests
# =============================================================================


@pytest.mark.unit
async def test_auto_policy_runs_normal_agent_flow(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """auto policy should call agent.run_stream normally, not route to subagent."""
    session = acp_session
    delegation = PromptDelegation(policy="auto")

    with patch.object(session.agent, "run_stream", side_effect=_mock_run_stream) as mock_run:
        with patch.object(session, "_run_subagent_directly", new_callable=AsyncMock) as mock_direct:
            stop_reason = await session.process_prompt([text_content_block], delegation=delegation)

    assert stop_reason == "end_turn"
    mock_run.assert_called_once()
    mock_direct.assert_not_awaited()


# =============================================================================
# disable policy tests
# =============================================================================


@pytest.mark.unit
async def test_disable_policy_filters_subagent_tools(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """disable policy should disable subagent tools before run and re-enable after."""
    session = acp_session
    delegation = PromptDelegation(policy="disable")

    with patch.object(session.agent, "run_stream", side_effect=_mock_run_stream):
        with patch.object(
            session.agent.tools, "disable_tool", new_callable=AsyncMock
        ) as mock_disable:
            with patch.object(
                session.agent.tools, "enable_tool", new_callable=AsyncMock
            ) as mock_enable:
                stop_reason = await session.process_prompt(
                    [text_content_block], delegation=delegation
                )

    assert stop_reason == "end_turn"

    # Should disable subagent tools
    mock_disable.assert_awaited()
    disabled_names = {call.args[0] for call in mock_disable.await_args_list}
    # SubagentTools creates "list_available_nodes" and "task"
    assert "list_available_nodes" in disabled_names
    assert "task" in disabled_names

    # Should re-enable them after
    mock_enable.assert_awaited()
    enabled_names = {call.args[0] for call in mock_enable.await_args_list}
    assert "list_available_nodes" in enabled_names
    assert "task" in enabled_names


@pytest.mark.unit
async def test_disable_policy_leaves_non_subagent_tools_enabled(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """disable policy should not disable tools that are not subagent tools."""
    session = acp_session

    # Add a non-subagent tool to the agent
    read_tool = ReadTool(name="read")
    session.agent.tools.builtin_provider.add_tool(read_tool)

    delegation = PromptDelegation(policy="disable")

    with patch.object(session.agent, "run_stream", side_effect=_mock_run_stream):
        with patch.object(
            session.agent.tools, "disable_tool", new_callable=AsyncMock
        ) as mock_disable:
            with patch.object(
                session.agent.tools, "enable_tool", new_callable=AsyncMock
            ) as mock_enable:
                await session.process_prompt([text_content_block], delegation=delegation)

    disabled_names = {call.args[0] for call in mock_disable.await_args_list}
    assert "read" not in disabled_names

    enabled_names = {call.args[0] for call in mock_enable.await_args_list}
    assert "read" not in enabled_names


# =============================================================================
# prefer policy tests
# =============================================================================


@pytest.mark.unit
async def test_prefer_policy_routes_to_subagent_when_available(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """prefer policy should route to subagent when subagent_id exists and supports streaming."""
    session = acp_session
    delegation = PromptDelegation(policy="prefer", subagent_id="subagent_a")

    with patch.object(
        session, "_run_subagent_directly", new_callable=AsyncMock, return_value="end_turn"
    ) as mock_direct:
        stop_reason = await session.process_prompt([text_content_block], delegation=delegation)

    assert stop_reason == "end_turn"
    mock_direct.assert_awaited_once()


@pytest.mark.unit
async def test_prefer_policy_falls_back_to_normal_when_subagent_missing(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """prefer policy should fall back to normal flow when subagent is not available."""
    session = acp_session
    delegation = PromptDelegation(policy="prefer", subagent_id="nonexistent")

    with patch.object(session.agent, "run_stream", side_effect=_mock_run_stream) as mock_run:
        with patch.object(session, "_run_subagent_directly", new_callable=AsyncMock) as mock_direct:
            stop_reason = await session.process_prompt([text_content_block], delegation=delegation)

    assert stop_reason == "end_turn"
    mock_direct.assert_not_awaited()
    mock_run.assert_called_once()


# =============================================================================
# require policy tests
# =============================================================================


@pytest.mark.unit
async def test_require_policy_routes_to_subagent_when_available(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """require policy should route to subagent when available."""
    session = acp_session
    delegation = PromptDelegation(policy="require", subagent_id="subagent_a")

    with patch.object(
        session, "_run_subagent_directly", new_callable=AsyncMock, return_value="end_turn"
    ) as mock_direct:
        stop_reason = await session.process_prompt([text_content_block], delegation=delegation)

    assert stop_reason == "end_turn"
    mock_direct.assert_awaited_once()


@pytest.mark.unit
async def test_require_policy_errors_when_subagent_unavailable(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """require policy should raise RequestError when subagent is not available."""
    session = acp_session
    delegation = PromptDelegation(policy="require", subagent_id="nonexistent")

    from acp.exceptions import RequestError

    with pytest.raises(RequestError, match="Subagent 'nonexistent' not available"):
        await session.process_prompt([text_content_block], delegation=delegation)


# =============================================================================
# capability advertisement tests
# =============================================================================


@pytest.mark.unit
async def test_delegation_ignored_when_capability_not_advertised(
    acp_session: ACPSession,
    text_content_block: TextContentBlock,
) -> None:
    """Delegation should be ignored when prompt_delegation capability is False."""
    session = acp_session
    session.acp_agent.prompt_delegation_enabled = False  # type: ignore[reportAttributeAccessIssue]

    delegation = PromptDelegation(policy="require", subagent_id="subagent_a")

    with patch.object(session.agent, "run_stream", side_effect=_mock_run_stream) as mock_run:
        with patch.object(session, "_run_subagent_directly", new_callable=AsyncMock) as mock_direct:
            stop_reason = await session.process_prompt([text_content_block], delegation=delegation)

    assert stop_reason == "end_turn"
    mock_direct.assert_not_awaited()
    mock_run.assert_called_once()
