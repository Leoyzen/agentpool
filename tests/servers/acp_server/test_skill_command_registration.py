"""Test to verify skill command registration in ACP session."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from pathlib import PurePosixPath

from agentpool import Agent, AgentPool
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.session import ACPSession
from agentpool_server.acp_server.session_manager import ACPSessionManager


@pytest.fixture
def agent_pool_with_skill() -> AgentPool:
    """Create an agent pool with a skill command registered."""
    pool = AgentPool()

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    pool.register("test_agent", agent)

    skill = Skill(
        name="test-skill",
        description="A test skill",
        skill_path=PurePosixPath("/tmp/test-skill"),
        instructions="Test skill instructions",
    )
    cmd = SkillCommand(
        name="test-skill",
        description="A test skill",
        skill=skill,
        input_hint="test args",
    )

    registry = SkillCommandRegistry()
    registry.register("test-skill", cmd)
    pool._skill_commands = registry  # type: ignore[reportPrivateUsage]

    return pool


def _make_mock_acp_agent():
    """Create a mock ACP agent with synchronous task execution."""
    mock = Mock()
    mock.tasks = Mock()
    mock.tasks.create_task = lambda coro: coro
    return mock


async def test_skill_commands_registered_in_session(agent_pool_with_skill: AgentPool):
    """Verify skill commands are registered in ACPSession's command_store."""
    agent = agent_pool_with_skill.get_agent("test_agent")
    mock_client = AsyncMock()
    mock_acp_agent = _make_mock_acp_agent()

    session = ACPSession(
        session_id="test-session",
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
    )

    # Check that the skill command is in the command_store
    cmd = session.command_store.get_command("test-skill")
    assert cmd is not None, "Skill command should be registered in command_store"

    # Check available commands list
    commands = list(session.command_store.list_commands())
    command_names = [c.name for c in commands]
    assert "test-skill" in command_names, f"test-skill not in {command_names}"


async def test_available_commands_update_sent_after_create_session(
    agent_pool_with_skill: AgentPool,
):
    """Verify skill commands are registered and send_available_commands_update works.

    In the real ACP flow, send_available_commands_update is called by
    new_session/load_session/resume_session (acp_agent.py) AFTER
    session_manager.create_session() returns — not during session.initialize().
    This test verifies the end-to-end behavior: after create_session, the
    session's command_store contains skill commands and can send them.
    """
    agent = agent_pool_with_skill.get_agent("test_agent")
    mock_client = AsyncMock()
    mock_acp_agent = _make_mock_acp_agent()

    # Mock pool storage to avoid DB dependency in create_session
    agent_pool_with_skill.storage = Mock()
    agent_pool_with_skill.storage.generate_session_id = Mock(return_value="test-session-001")
    agent_pool_with_skill._session_store = None

    # Create session manager
    manager = ACPSessionManager(pool=agent_pool_with_skill)

    # Create session via the real create_session path (used by new_session/load_session/resume_session)
    session_id = await manager.create_session(
        agent=agent,
        cwd="/tmp",
        client=mock_client,
        acp_agent=mock_acp_agent,
    )

    session = manager.get_session(session_id)
    assert session is not None, "Session should be created"

    # Verify skill command is registered in command_store
    cmd = session.command_store.get_command("test-skill")
    assert cmd is not None, "Skill command should be registered after create_session"

    # Verify send_available_commands_update sends the notification
    await session.send_available_commands_update()
    mock_client.session_update.assert_called()
    notification = mock_client.session_update.call_args[0][0]
    assert notification.session_id == session_id
    update = notification.update
    assert update.session_update == "available_commands_update"
    commands = update.available_commands
    command_names = [c.name for c in commands]
    assert "test-skill" in command_names, f"test-skill not in {command_names}"
