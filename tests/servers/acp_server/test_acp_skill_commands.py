"""TDD tests for ACP server skill commands exposure.

These tests verify that skills are properly exposed as slash commands
via the session/update notification (available_commands_update),
per the ACP protocol specification.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, Mock

import pytest

from acp.schema.client_requests import InitializeRequest
from agentpool import Agent, AgentPool
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.session import ACPSession


@pytest.fixture
def agent_pool_with_skill() -> AgentPool:
    """Create an agent pool with a skill command registered."""
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})

    pool = AgentPool(manifest)

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    # pool.register() removed; agent created from callback/config above
    # Create and register a skill command
    skill = Skill(
        name="test-skill",
        description="A test skill for TDD",
        skill_path=PurePosixPath("/tmp/test-skill"),
    )
    cmd = SkillCommand(
        name="test-skill",
        description="A test skill for TDD",
        skill=skill,
        input_hint="test args",
    )

    registry = SkillCommandRegistry()
    registry.register("test-skill", cmd)
    pool._skill_commands = registry  # type: ignore[reportPrivateUsage]

    return pool


@pytest.fixture
def mock_acp_agent_with_skills(agent_pool_with_skill: AgentPool) -> AgentPoolACPAgent:
    """Create an ACP agent with skills configured."""
    mock_connection = Mock()
    agent = agent_pool_with_skill.manifest.agents["test_agent"].get_agent(
        pool=agent_pool_with_skill
    )
    return AgentPoolACPAgent(client=mock_connection, default_agent=agent)


async def test_initialize_does_not_expose_skill_commands(
    mock_acp_agent_with_skills: AgentPoolACPAgent,
):
    """Test that initialize response does NOT include skill commands.

    Per RFC-0032, slash commands must be advertised via session/update
    (available_commands_update) after session creation, not in the
    initialize response.
    """
    request = InitializeRequest.create(title="Test", name="test", version="1.0.0")
    response = await mock_acp_agent_with_skills.initialize(request)

    assert response.agent_capabilities is not None
    assert not hasattr(response.agent_capabilities, "slash_commands"), (
        "initialize response should NOT expose slash_commands per ACP spec"
    )


async def test_initialize_without_skills_no_commands():
    """Test that initialize response has no slash_commands field."""
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})

    pool = AgentPool(manifest)

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    # pool.register() removed; agent created from callback/config above
    mock_connection = Mock()
    acp_agent = AgentPoolACPAgent(client=mock_connection, default_agent=agent)

    request = InitializeRequest.create(title="Test", name="test", version="1.0.0")
    response = await acp_agent.initialize(request)

    assert response.agent_capabilities is not None
    assert not hasattr(response.agent_capabilities, "slash_commands"), (
        "initialize response should NOT have slash_commands field"
    )


async def test_session_update_exposes_skill_commands(
    agent_pool_with_skill: AgentPool,
):
    """Test that skill commands are advertised via session/update after creation.

    Per RFC-0032, skill commands must be sent via available_commands_update
    session notification, not in the initialize response.
    """
    agent = agent_pool_with_skill.manifest.agents["test_agent"].get_agent(
        pool=agent_pool_with_skill
    )
    mock_client = AsyncMock()
    mock_acp_agent = Mock()
    mock_acp_agent.tasks = Mock()
    mock_acp_agent.tasks.create_task = lambda coro: coro

    session = ACPSession(
        session_id="test-session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
    )

    # Mock update_commands to capture what was sent
    session.notifications.update_commands = AsyncMock()  # type: ignore[method-assign]

    # Call send_available_commands_update and capture what was sent
    await session.send_available_commands_update()

    # Verify update_commands was called
    assert session.notifications.update_commands.called, (
        "send_available_commands_update should call update_commands"
    )

    # Extract the commands from the call
    calls = session.notifications.update_commands.call_args_list
    assert len(calls) > 0, "update_commands should have been called"

    sent_commands = calls[0][0][0] if calls[0][0] else calls[0][1].get("commands", [])
    command_names = [cmd.name for cmd in sent_commands]

    assert "test-skill" in command_names, (
        f"Skill command 'test-skill' should be in available_commands_update. "
        f"Got commands: {command_names}"
    )
