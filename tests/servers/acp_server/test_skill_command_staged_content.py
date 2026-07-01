"""TDD tests for ACP skill slash command staged_content injection.

These tests verify that skill commands properly inject instructions into
staged_content so that the agent runs instead of returning end_turn.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from agentpool import Agent, AgentPool
from agentpool.agents.context import AgentContext
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.session import ACPSession
from agentpool_server.opencode_server.skill_bridge import create_skill_command


@pytest.fixture
def agent_pool_with_skill() -> AgentPool:
    """Create an agent pool with a skill command registered."""
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})

    pool = AgentPool(manifest)

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    # pool.register() removed; agent created from callback/config above
    # Provide a mock SessionPool so process_prompt can route through it
    mock_session_pool = MagicMock()
    mock_session_pool.sessions = MagicMock()
    mock_session_pool.event_bus = MagicMock()
    from tests._helpers.mock_stream import EmptyReceiveStream

    mock_session_pool.event_bus.subscribe = AsyncMock(return_value=EmptyReceiveStream())
    mock_session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=agent)

    # run_stream must return an async iterable
    async def _empty_stream(*args: Any, **kwargs: Any) -> Any:
        return
        yield  # pragma: no cover

    mock_session_pool.run_stream = _empty_stream
    pool._session_pool = mock_session_pool  # type: ignore[reportPrivateUsage]

    # Create and register a skill command
    skill = Skill(
        name="test-skill",
        description="A test skill for TDD",
        skill_path=PurePosixPath("/tmp/test-skill"),
        instructions="Test skill instructions",
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


async def test_skill_command_injects_into_staged_content(agent_pool_with_skill: AgentPool):
    """Test that executing a skill command injects instructions into staged_content.

    When a user sends a slash command like /test-skill, the instructions
    should be staged so the agent can process them.
    """
    agent = agent_pool_with_skill.manifest.agents["test_agent"].get_agent(
        pool=agent_pool_with_skill
    )

    # Create a skill command
    skill_cmd = agent_pool_with_skill._skill_commands.get("test-skill")  # type: ignore[reportPrivateUsage]
    assert skill_cmd is not None

    # Create the slashed command
    slashed_cmd = create_skill_command(skill_cmd)

    # Create an AgentContext with the agent as node
    ctx_data = AgentContext(node=agent)

    # Create a CommandContext
    cmd_ctx = Mock()
    cmd_ctx.data = ctx_data
    cmd_ctx.print = AsyncMock()

    # Execute the skill command
    await slashed_cmd.execute(cmd_ctx, [], {})

    # Verify staged_content has the instructions
    assert len(agent.staged_content) > 0, (
        "Skill command should inject instructions into staged_content"
    )

    # Consume staged content and verify it contains instructions
    staged_text = await agent.staged_content.consume_as_text()
    assert staged_text is not None
    assert "Test skill instructions" in staged_text, (
        f"Staged content should contain skill instructions. Got: {staged_text}"
    )


async def test_skill_command_with_staged_content_triggers_agent_run(
    agent_pool_with_skill: AgentPool,
):
    """Test that process_prompt runs agent when only commands but staged_content exists.

    When a user sends only a slash command (no other text), and the command
    injects content into staged_content, the agent should run rather than
    returning end_turn immediately.
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

    # Register the skill command in the session's command store
    skill_cmd = agent_pool_with_skill._skill_commands.get("test-skill")  # type: ignore[reportPrivateUsage]
    assert skill_cmd is not None

    slashed_cmd = create_skill_command(skill_cmd)
    session.command_store.register_command(slashed_cmd, replace=True)

    # Create a content block that is just the slash command
    from acp.schema import TextContentBlock

    content_block = TextContentBlock(text="/test-skill")

    # Track whether session_pool.run_stream was called
    run_stream_called = False
    session_pool = agent_pool_with_skill._session_pool  # type: ignore[reportPrivateUsage]

    def tracked_run_stream(*args: Any, **kwargs: Any) -> Any:
        nonlocal run_stream_called
        run_stream_called = True

        async def _empty() -> Any:
            return
            yield  # pragma: no cover

        return _empty()

    original_run_stream = session_pool.run_stream
    session_pool.run_stream = tracked_run_stream  # type: ignore[method-assign]

    try:
        await session.process_prompt([content_block])
    finally:
        session_pool.run_stream = original_run_stream  # type: ignore[method-assign]

    assert run_stream_called, (
        "agent.run_stream should be called when skill command injects content into staged_content"
    )


async def test_skill_command_no_instructions_returns_end_turn():
    """Test that process_prompt returns end_turn when skill has no instructions.

    When a skill command executes but finds no instructions, nothing is
    staged, so end_turn is appropriate.
    """
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})

    pool = AgentPool(manifest)

    def simple_callback(message: str) -> str:
        return f"Test response: {message}"

    agent = Agent.from_callback(name="test_agent", callback=simple_callback, agent_pool=pool)
    # pool.register() removed; agent created from callback/config above
    # Create a skill with NO instructions
    skill = Skill(
        name="empty-skill",
        description="A skill with no instructions",
        skill_path=PurePosixPath("/tmp/empty-skill"),
        instructions="",
    )
    cmd = SkillCommand(
        name="empty-skill",
        description="A skill with no instructions",
        skill=skill,
        input_hint="test args",
    )

    registry = SkillCommandRegistry()
    registry.register("empty-skill", cmd)
    pool._skill_commands = registry  # type: ignore[reportPrivateUsage]

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

    slashed_cmd = create_skill_command(cmd)
    session.command_store.register_command(slashed_cmd, replace=True)

    from acp.schema import TextContentBlock

    content_block = TextContentBlock(text="/empty-skill")

    result = await session.process_prompt([content_block])

    assert result == "end_turn", (
        "process_prompt should return end_turn when skill has no instructions"
    )
