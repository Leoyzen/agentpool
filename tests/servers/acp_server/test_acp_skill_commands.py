"""TDD tests for ACP server skill commands exposure.

These tests verify that skills are properly exposed as slash commands
via the session/update notification (available_commands_update),
per the ACP protocol specification.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from pathlib import PurePosixPath

from acp.schema.client_requests import InitializeRequest
from agentpool import Agent, AgentPool
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


@pytest.fixture
def agent_pool_with_skill() -> AgentPool:
    """Create an agent pool with a skill command registered."""
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)

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
    agent = agent_pool_with_skill.get_agent("test_agent")
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
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)

    mock_connection = Mock()
    acp_agent = AgentPoolACPAgent(client=mock_connection, default_agent=agent)

    request = InitializeRequest.create(title="Test", name="test", version="1.0.0")
    response = await acp_agent.initialize(request)

    assert response.agent_capabilities is not None
    assert not hasattr(response.agent_capabilities, "slash_commands"), (
        "initialize response should NOT have slash_commands field"
    )
