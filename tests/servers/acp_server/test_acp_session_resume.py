"""Unit tests for AgentPoolACPAgent.resume_session()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import ResumeSessionRequest, ResumeSessionResponse
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return AsyncMock()


@pytest.fixture
def mock_agent_pool_with_agent():
    """Create a mock agent pool with a test agent."""

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    pool = AgentPool()
    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)
    return pool, agent


@pytest.fixture
def default_test_agent(mock_agent_pool_with_agent):
    """Get the default test agent from the mock pool."""
    return mock_agent_pool_with_agent[1]


@pytest.fixture
def mock_acp_agent(mock_connection, default_test_agent):
    """Create a mock ACP agent for testing."""
    return AgentPoolACPAgent(client=mock_connection, default_agent=default_test_agent)


@pytest.fixture
def mock_session():
    """Create a mock ACPSession with all required attributes."""
    session = MagicMock()
    session.session_id = "test-session-id"
    session.cwd = "/tmp"

    session.agent = MagicMock()
    session.agent.conversation = MagicMock()
    session.agent.conversation.chat_messages = []
    session.agent.load_session = AsyncMock(return_value=True)
    session.agent.load_rules = AsyncMock()

    session.notifications = MagicMock()
    session.notifications.replay = AsyncMock()

    session.send_available_commands_update = AsyncMock()

    return session


@pytest.fixture
def resume_session_request():
    """Create a ResumeSessionRequest."""
    return ResumeSessionRequest(session_id="test-session-id", cwd="/tmp")


@pytest.mark.unit
async def test_resume_session_calls_agent_load_session(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test that agent.load_session() is called during resume_session."""
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)
    mock_acp_agent._initialized = True

    await mock_acp_agent.resume_session(resume_session_request)

    mock_session.agent.load_session.assert_awaited_once_with("test-session-id")


@pytest.mark.unit
async def test_resume_session_does_not_call_replay(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test that notifications.replay() is NOT called during resume_session."""
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)
    mock_acp_agent._initialized = True

    await mock_acp_agent.resume_session(resume_session_request)

    mock_session.notifications.replay.assert_not_awaited()


@pytest.mark.unit
async def test_resume_session_schedules_commands_update(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test that send_available_commands_update() is scheduled after resume."""
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)
    mock_acp_agent._initialized = True

    with patch.object(mock_acp_agent.tasks, "create_task") as mock_create_task:
        await mock_acp_agent.resume_session(resume_session_request)

        # Should schedule send_available_commands_update and load_rules
        assert mock_create_task.call_count == 2


@pytest.mark.unit
async def test_resume_session_agent_load_fails(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test resume_session handles agent.load_session() failure gracefully."""
    mock_session.agent.load_session = AsyncMock(return_value=False)
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)
    mock_acp_agent._initialized = True

    response = await mock_acp_agent.resume_session(resume_session_request)

    assert isinstance(response, ResumeSessionResponse)
    # Even when load fails, session wrapper exists so resume succeeds
    mock_session.agent.load_session.assert_awaited_once_with("test-session-id")


@pytest.mark.unit
async def test_resume_session_creates_session_if_not_found(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test that resume_session creates a new session wrapper if session not found."""
    mock_acp_agent.session_manager.get_session = MagicMock(side_effect=[None, mock_session])
    mock_acp_agent.session_manager.create_session = AsyncMock(return_value="test-session-id")
    mock_acp_agent._initialized = True

    await mock_acp_agent.resume_session(resume_session_request)

    mock_acp_agent.session_manager.create_session.assert_awaited_once()


@pytest.mark.unit
async def test_resume_session_exception_returns_empty_response(
    mock_acp_agent, mock_session, resume_session_request
):
    """Test that resume_session returns empty ResumeSessionResponse on exception."""
    mock_session.agent.load_session = AsyncMock(side_effect=RuntimeError("boom"))
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)
    mock_acp_agent._initialized = True

    response = await mock_acp_agent.resume_session(resume_session_request)

    assert isinstance(response, ResumeSessionResponse)
