"""TDD tests for subagent capability advertisement (T10).

Tests that AgentPoolACPAgent advertises subagent capabilities during
initialization and available subagents during session lifecycle.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import (
    ForkSessionRequest,
    InitializeRequest,
    LoadSessionRequest,
    NewSessionRequest,
    ResumeSessionRequest,
    SubagentCapabilities,
    SubagentInfo,
)
from acp.schema.capabilities import AgentCapabilities
from acp.schema.agent_responses import InitializeResponse
from agentpool import Agent
from agentpool.delegation import AgentPool
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


@pytest.fixture
def mock_connection():
    """Create a mock ACP connection."""
    return AsyncMock()


@pytest.fixture
def mock_agent_pool_with_multiple_agents():
    """Create a mock agent pool with multiple test agents."""

    def callback_a(message: str) -> str:
        return f"Agent A: {message}"

    def callback_b(message: str) -> str:
        return f"Agent B: {message}"

    pool = AgentPool()
    agent_a = Agent.from_callback(
        name="agent_a",
        callback=callback_a,
        agent_pool=pool,
        system_prompt="You are agent A",
    )
    agent_b = Agent.from_callback(
        name="agent_b",
        callback=callback_b,
        agent_pool=pool,
        system_prompt="You are agent B",
    )
    pool.register("agent_a", agent_a)
    pool.register("agent_b", agent_b)
    return pool, agent_a, agent_b


@pytest.fixture
def default_test_agent(mock_agent_pool_with_multiple_agents):
    """Get the first test agent from the mock pool."""
    return mock_agent_pool_with_multiple_agents[1]


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
    session.agent.get_modes = AsyncMock(return_value=[])
    session.agent.get_available_models = AsyncMock(return_value=[])
    session.agent.model_name = "test-model"

    session.notifications = MagicMock()
    session.notifications.replay = AsyncMock()

    session.send_available_commands_update = AsyncMock()
    session._register_prompt_hub_commands = AsyncMock()
    session.init_client_skills = AsyncMock()

    return session


# =============================================================================
# Schema-level tests
# =============================================================================


@pytest.mark.unit
def test_agent_capabilities_create_accepts_subagents() -> None:
    """AgentCapabilities.create() should accept subagents parameter."""
    subagents = SubagentCapabilities(
        streaming=True,
        tools=True,
        prompt_delegation=False,
        background=False,
    )
    caps = AgentCapabilities.create(subagents=subagents)
    assert caps.subagents is not None
    assert caps.subagents.streaming is True
    assert caps.subagents.tools is True
    assert caps.subagents.prompt_delegation is False
    assert caps.subagents.background is False


@pytest.mark.unit
def test_agent_capabilities_create_subagents_defaults_to_none() -> None:
    """AgentCapabilities.create() should default subagents to None."""
    caps = AgentCapabilities.create()
    assert caps.subagents is None


@pytest.mark.unit
def test_initialize_response_create_accepts_subagents() -> None:
    """InitializeResponse.create() should pass subagents through to capabilities."""
    subagents = SubagentCapabilities(
        streaming=True,
        prompt_delegation=False,
        background=False,
    )
    resp = InitializeResponse.create(
        name="test",
        title="Test",
        version="1.0",
        protocol_version=1,
        subagents=subagents,
    )
    assert resp.agent_capabilities is not None
    assert resp.agent_capabilities.subagents is not None
    assert resp.agent_capabilities.subagents.streaming is True
    assert resp.agent_capabilities.subagents.prompt_delegation is False
    assert resp.agent_capabilities.subagents.background is False


# =============================================================================
# AgentPoolACPAgent.initialize() tests
# =============================================================================


@pytest.mark.unit
async def test_initialize_includes_subagent_capabilities(mock_acp_agent) -> None:
    """initialize() should include subagent capabilities in response."""
    mock_acp_agent._initialized = False

    request = InitializeRequest(protocol_version=1)
    response = await mock_acp_agent.initialize(request)

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.subagents is not None
    assert response.agent_capabilities.subagents.prompt_delegation is True
    assert response.agent_capabilities.subagents.background is True


@pytest.mark.unit
async def test_initialize_subagents_phase_two_enabled(mock_acp_agent) -> None:
    """Phase 2: prompt_delegation and background must be True."""
    mock_acp_agent._initialized = False

    request = InitializeRequest(protocol_version=1)
    response = await mock_acp_agent.initialize(request)

    assert response.agent_capabilities is not None
    assert response.agent_capabilities.subagents is not None
    assert response.agent_capabilities.subagents.prompt_delegation is True
    assert response.agent_capabilities.subagents.background is True


# =============================================================================
# AgentPoolACPAgent.new_session() tests
# =============================================================================


@pytest.mark.unit
async def test_new_session_includes_available_subagents(mock_acp_agent, mock_session) -> None:
    """new_session() should include available_subagents in response."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.create_session = AsyncMock(return_value="test-session-id")
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)

    request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    response = await mock_acp_agent.new_session(request)

    assert response.available_subagents is not None
    assert len(response.available_subagents) == 2

    # Check that pool agents are reflected
    ids = {s.subagent_id for s in response.available_subagents}
    assert "agent_a" in ids
    assert "agent_b" in ids


@pytest.mark.unit
async def test_new_session_subagent_info_structure(mock_acp_agent, mock_session) -> None:
    """available_subagents entries should have correct structure."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.create_session = AsyncMock(return_value="test-session-id")
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)

    request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    response = await mock_acp_agent.new_session(request)

    assert response.available_subagents is not None
    agent_a_info = next(
        (s for s in response.available_subagents if s.subagent_id == "agent_a"), None
    )
    assert agent_a_info is not None
    assert agent_a_info.name == "agent_a"
    # description should come from system_prompt (truncated)
    assert agent_a_info.description is not None
    assert "You are agent A" in agent_a_info.description


# =============================================================================
# AgentPoolACPAgent.load_session() tests
# =============================================================================


@pytest.mark.unit
async def test_load_session_includes_available_subagents(mock_acp_agent, mock_session) -> None:
    """load_session() should include available_subagents in response."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)

    request = LoadSessionRequest(session_id="test-session-id", cwd="/tmp", mcp_servers=[])
    response = await mock_acp_agent.load_session(request)

    assert response.available_subagents is not None
    assert len(response.available_subagents) == 2
    ids = {s.subagent_id for s in response.available_subagents}
    assert "agent_a" in ids
    assert "agent_b" in ids


# =============================================================================
# AgentPoolACPAgent.fork_session() tests
# =============================================================================


@pytest.mark.unit
async def test_fork_session_includes_available_subagents(mock_acp_agent, mock_session) -> None:
    """fork_session() should include available_subagents in response."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.create_session = AsyncMock(return_value="forked-session-id")

    request = ForkSessionRequest(session_id="test-session-id", cwd="/tmp")
    response = await mock_acp_agent.fork_session(request)

    assert response.available_subagents is not None
    assert len(response.available_subagents) == 2
    ids = {s.subagent_id for s in response.available_subagents}
    assert "agent_a" in ids
    assert "agent_b" in ids


# =============================================================================
# AgentPoolACPAgent.resume_session() tests
# =============================================================================


@pytest.mark.unit
async def test_resume_session_includes_available_subagents(mock_acp_agent, mock_session) -> None:
    """resume_session() should include available_subagents in response."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)

    request = ResumeSessionRequest(session_id="test-session-id", cwd="/tmp")
    response = await mock_acp_agent.resume_session(request)

    assert response.available_subagents is not None
    assert len(response.available_subagents) == 2
    ids = {s.subagent_id for s in response.available_subagents}
    assert "agent_a" in ids
    assert "agent_b" in ids


# =============================================================================
# available_subagents reflects pool agents tests
# =============================================================================


@pytest.mark.unit
async def test_available_subagents_reflects_pool_agents(mock_acp_agent, mock_session) -> None:
    """available_subagents must reflect agents registered in the pool."""
    mock_acp_agent._initialized = True
    mock_acp_agent.session_manager.create_session = AsyncMock(return_value="test-session-id")
    mock_acp_agent.session_manager.get_session = MagicMock(return_value=mock_session)

    request = NewSessionRequest(mcp_servers=[], cwd="/tmp")
    response = await mock_acp_agent.new_session(request)

    # Should have exactly the two pool agents
    assert response.available_subagents is not None
    assert len(response.available_subagents) == 2

    # Verify structure
    for info in response.available_subagents:
        assert isinstance(info, SubagentInfo)
        assert info.subagent_id
        assert info.name
