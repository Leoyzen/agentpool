"""Integration tests for ACPSkillBridge with AgentPoolACPAgent.

These tests verify that the ACPSkillBridge is properly wired up to
AgentPoolACPAgent and receives skill commands from the SkillCommandRegistry.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from acp.schema.slash_commands import AvailableCommand
from agentpool.skills import SkillCommand, SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge


@pytest.fixture
def mock_base_agent() -> MagicMock:
    """Create a mock BaseAgent with agent_pool reference."""
    agent = MagicMock()
    agent.name = "test_agent"
    agent.agent_pool = None  # Will be set by tests
    agent.host_context = None  # Will be set by tests
    return agent


@pytest.fixture
def mock_client() -> MagicMock:
    """Create a mock ACP Client."""
    return MagicMock()


@pytest.fixture
def mock_pool() -> MagicMock:
    """Create a mock AgentPool."""
    pool = MagicMock()
    pool.skill_commands = None  # Will be set by tests that need it
    pool.storage.metadata_generated.connect = MagicMock()
    return pool


@pytest.fixture
def skill_registry() -> SkillCommandRegistry:
    """Create a SkillCommandRegistry for testing."""
    skills_registry = SkillsRegistry()
    return SkillCommandRegistry(skills_registry=skills_registry)


@pytest.fixture
def mock_skill() -> MagicMock:
    """Create a mock Skill for testing."""
    skill = MagicMock()
    skill.name = "test_skill"
    skill.description = "A test skill"
    return skill


@pytest.fixture
def sample_skill_command(mock_skill: MagicMock) -> SkillCommand:
    """Create a sample SkillCommand for testing."""
    return SkillCommand(
        name="test_skill",
        description="A test skill",
        skill=mock_skill,
    )


def test_bridge_created_when_pool_has_skill_commands(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
    skill_registry: SkillCommandRegistry,
    sample_skill_command: SkillCommand,
) -> None:
    """Test that bridge is created when pool has skill_commands with commands."""
    # Setup: Add a command to the registry
    skill_registry.register("test_skill", sample_skill_command)
    mock_pool.skill_commands = skill_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Assert: Bridge should be created
    assert acp_agent._skill_bridge is not None
    assert isinstance(acp_agent._skill_bridge, ACPSkillBridge)


def test_bridge_created_with_empty_skill_registry(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Test that bridge is created even when skill registry is empty.

    The bridge should be wired up whenever pool.skill_commands is not None,
    even if it has no commands initially. The bridge will still receive
    notifications when commands are added later.
    """
    # Setup: Empty skill registry
    skills_registry = SkillsRegistry()
    empty_registry = SkillCommandRegistry(skills_registry=skills_registry)
    mock_pool.skill_commands = empty_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Assert: Bridge should be created even with empty registry
    assert acp_agent._skill_bridge is not None
    assert isinstance(acp_agent._skill_bridge, ACPSkillBridge)
    # Bridge should have no commands initially
    assert len(acp_agent._skill_bridge.get_available_commands()) == 0


def test_bridge_not_created_when_no_skill_commands_attr(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Test graceful handling when pool has no skill_commands attribute."""
    # Setup: Pool without skill_commands attribute
    mock_pool.skill_commands = None
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent - should not raise
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Assert: Bridge should not be created
    assert acp_agent._skill_bridge is None


def test_bridge_receives_commands_from_registry(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
    skill_registry: SkillCommandRegistry,
    sample_skill_command: SkillCommand,
) -> None:
    """Test that bridge receives commands added to the registry."""
    # Setup: Pre-populate registry before creating agent
    skill_registry.register("test_skill", sample_skill_command)
    mock_pool.skill_commands = skill_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Assert: Bridge should have the registered command
    assert acp_agent._skill_bridge is not None
    commands = acp_agent._skill_bridge.get_available_commands()
    assert len(commands) == 1
    assert commands[0].name == "test_skill"


def test_bridge_receives_command_updates(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
    skill_registry: SkillCommandRegistry,
    sample_skill_command: SkillCommand,
) -> None:
    """Test that bridge receives updates when commands are added/removed."""
    # Setup: Pre-populate registry
    skill_registry.register("test_skill", sample_skill_command)
    mock_pool.skill_commands = skill_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Add another command after bridge setup
    second_skill_mock = MagicMock()
    second_skill_mock.name = "second_skill"
    second_skill_mock.description = "Second test skill"
    second_command = SkillCommand(
        name="second_skill",
        description="Second test skill",
        skill=second_skill_mock,
    )
    skill_registry.register("second_skill", second_command)

    # Assert: Bridge should have both commands
    assert acp_agent._skill_bridge is not None
    commands = acp_agent._skill_bridge.get_available_commands()
    command_names = {cmd.name for cmd in commands}
    assert command_names == {"test_skill", "second_skill"}


def test_get_skill_commands_returns_bridge_commands(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
    skill_registry: SkillCommandRegistry,
    sample_skill_command: SkillCommand,
) -> None:
    """Test that get_skill_commands returns commands from the bridge."""
    # Setup
    skill_registry.register("test_skill", sample_skill_command)
    mock_pool.skill_commands = skill_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Get commands via the public method
    commands = acp_agent.get_skill_commands()

    # Assert: Should return AvailableCommand objects
    assert commands is not None
    assert len(commands) == 1
    assert isinstance(commands[0], AvailableCommand)
    assert commands[0].name == "test_skill"


def test_get_skill_commands_returns_none_when_no_bridge(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Test that get_skill_commands returns None when no bridge is configured."""
    # Setup: No skill_commands on pool
    mock_pool.skill_commands = None
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Get commands via the public method
    commands = acp_agent.get_skill_commands()

    # Assert: Should return None when no bridge
    assert commands is None


def test_bridge_handles_command_removal(
    mock_base_agent: MagicMock,
    mock_client: MagicMock,
    mock_pool: MagicMock,
    skill_registry: SkillCommandRegistry,
    sample_skill_command: SkillCommand,
) -> None:
    """Test that bridge handles removal of commands from registry."""
    # Setup: Add then remove command
    skill_registry.register("test_skill", sample_skill_command)
    mock_pool.skill_commands = skill_registry
    mock_base_agent.host_context = MagicMock(pool=mock_pool)

    # Create the ACP agent
    acp_agent = AgentPoolACPAgent(
        client=mock_client,
        default_agent=mock_base_agent,
        debug_commands=False,
        load_skills=True,
    )

    # Verify command exists (bridge is not None due to test setup)
    assert acp_agent._skill_bridge is not None
    assert len(acp_agent._skill_bridge.get_available_commands()) == 1

    # Remove command
    del skill_registry["test_skill"]

    # Assert: Bridge should have no commands
    assert acp_agent._skill_bridge is not None
    commands = acp_agent._skill_bridge.get_available_commands()
    assert len(commands) == 0
