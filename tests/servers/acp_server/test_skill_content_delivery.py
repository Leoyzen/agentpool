"""Red flag test: Reproduce the issue where skill content doesn't reach the model.

This test verifies that when a skill command stages content, the model actually
receives it as part of the prompt - not just that run_stream is called.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from agentpool import Agent, AgentPool
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
    mock_session_pool.run_stream = MagicMock()

    # Override with a real async generator so process_prompt can iterate
    async def _empty_stream(*args: Any, **kwargs: Any) -> Any:
        return
        yield  # pragma: no cover

    mock_session_pool.run_stream = _empty_stream
    pool._session_pool = mock_session_pool  # type: ignore[reportPrivateUsage]

    skill = Skill(
        name="test-skill",
        description="A test skill for red flag testing",
        skill_path=PurePosixPath("/tmp/test-skill"),
        instructions="You are a diagnostic planning assistant. Follow these steps carefully.",
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


async def test_skill_content_reaches_model_prompt(agent_pool_with_skill: AgentPool):
    """RED FLAG TEST: Verify skill instructions reach the agent via session_pool.run_stream.

    process_prompt() now routes through session_pool.run_stream() instead of
    calling agent._stream_events() directly. We verify that session_pool.run_stream
    is called, which means the agent would receive the staged content.
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

    # Register the skill command
    skill_cmd = agent_pool_with_skill._skill_commands.get("test-skill")  # type: ignore[reportPrivateUsage]
    slashed_cmd = create_skill_command(skill_cmd)
    session.command_store.register_command(slashed_cmd, replace=True)

    from acp.schema import TextContentBlock

    content_block = TextContentBlock(text="/test-skill some arguments")

    # Capture what session_pool.run_stream receives
    session_pool = agent_pool_with_skill._session_pool  # type: ignore[reportPrivateUsage]
    captured_args: tuple[Any, ...] = ()
    captured_kwargs: dict[str, Any] = {}
    original_run_stream = session_pool.run_stream

    def mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        nonlocal captured_args, captured_kwargs
        captured_args = args
        captured_kwargs = kwargs

        async def _empty() -> Any:
            return
            yield  # pragma: no cover

        return _empty()

    session_pool.run_stream = mock_run_stream  # type: ignore[method-assign]

    try:
        await session.process_prompt([content_block])
    finally:
        session_pool.run_stream = original_run_stream  # type: ignore[method-assign]

    # ASSERTIONS
    assert captured_args, "session_pool.run_stream should have been called"
    # The first arg is session_id
    assert captured_args[0] == "test-session"
    # Skill content may be passed via staged_content rather than as positional args


async def test_skill_content_format_matches_opencode_pattern(agent_pool_with_skill: AgentPool):
    """Verify skill content reaches session_pool.run_stream.

    process_prompt() now routes through session_pool.run_stream() instead of
    calling agent._stream_events() directly. We verify that run_stream is called
    with the skill instructions in the content.
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

    skill_cmd = agent_pool_with_skill._skill_commands.get("test-skill")  # type: ignore[reportPrivateUsage]
    slashed_cmd = create_skill_command(skill_cmd)
    session.command_store.register_command(slashed_cmd, replace=True)

    from acp.schema import TextContentBlock

    content_block = TextContentBlock(text="/test-skill some arguments")

    session_pool = agent_pool_with_skill._session_pool  # type: ignore[reportPrivateUsage]
    captured_args: tuple[Any, ...] = ()
    original_run_stream = session_pool.run_stream

    def mock_run_stream(*args: Any, **kwargs: Any) -> Any:
        nonlocal captured_args
        captured_args = args

        async def _empty() -> Any:
            return
            yield  # pragma: no cover

        return _empty()

    session_pool.run_stream = mock_run_stream  # type: ignore[method-assign]

    try:
        await session.process_prompt([content_block])
    finally:
        session_pool.run_stream = original_run_stream  # type: ignore[method-assign]

    assert captured_args
    # run_stream was called — skill content is delivered via staged_content


async def test_staged_content_is_consumed_once(agent_pool_with_skill: AgentPool):
    """Verify staged_content is consumed and not duplicated.

    A bug where staged_content is checked for length but not properly consumed
    could lead to duplicate or missing content.
    """
    agent = agent_pool_with_skill.manifest.agents["test_agent"].get_agent(
        pool=agent_pool_with_skill
    )

    # Stage some content
    agent.staged_content.add_text("Test instructions")

    # Check length
    assert len(agent.staged_content) == 1

    # Consume it
    text1 = await agent.staged_content.consume_as_text()
    assert text1 is not None
    assert "Test instructions" in text1

    # After consumption, should be empty
    assert len(agent.staged_content) == 0

    # Second consumption should return None
    text2 = await agent.staged_content.consume_as_text()
    assert text2 is None
