"""TDD tests for ACP server skill commands exposure.

These tests verify that skills are properly exposed as slash commands
in the ACP initialize response and available commands updates.
"""

from __future__ import annotations

from unittest.mock import MagicMock, Mock

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


async def test_initialize_exposes_skill_commands(mock_acp_agent_with_skills: AgentPoolACPAgent):
    """Test that initialize response includes skill commands when skills are configured.

    This is a TDD test: it should fail before the fix and pass after.
    """
    request = InitializeRequest.create(title="Test", name="test", version="1.0.0")
    response = await mock_acp_agent_with_skills.initialize(request)

    assert response.agent_capabilities is not None
    assert len(response.agent_capabilities.slash_commands) > 0, (
        "initialize response should expose skill commands when skills are configured"
    )

    cmd_names = [cmd.name for cmd in response.agent_capabilities.slash_commands]
    assert "test-skill" in cmd_names, (
        f"Expected 'test-skill' in slash commands, got: {cmd_names}"
    )


async def test_initialize_without_skills_has_empty_commands():
    """Test that initialize response has empty slash commands when no skills configured."""
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
    assert response.agent_capabilities.slash_commands == []
