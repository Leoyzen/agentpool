"""Integration tests for subagent session agent behavior.

This verifies that child sessions get a lightweight per-session agent
that inherits the parent's session-level MCP providers without sharing
chat history, agent state, or mutating the shared pool-level base_agent.

The child agent shares base_agent.mcp to avoid duplicate MCP subprocess
spawning.  is_per_session_agent is set to False so close_session() on
the child does NOT call agent.__aexit__().
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.resource_providers import ResourceProvider
from agentpool.resource_providers.resource_info import ResourceInfo
from agentpool.skills.skill import Skill
from agentpool.tools.base import Tool


class MockMCPResourceProvider(ResourceProvider):
    """Mock MCP provider for testing inheritance."""

    kind = "mcp"

    def __init__(
        self,
        name: str = "mock_mcp",
        skills: list[Skill] | None = None,
        tools: list[Tool] | None = None,
        prompts: list[Any] | None = None,
        resources: list[ResourceInfo] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._skills = skills or []
        self._tools = tools or []
        self._prompts = prompts or []
        self._resources = resources or []

    async def get_skills(self) -> list[Skill]:
        """Get mock skills."""
        return self._skills

    async def get_tools(self) -> Sequence[Tool]:
        """Get mock tools."""
        return self._tools

    async def get_prompts(self) -> list[Any]:
        """Get mock prompts."""
        return self._prompts

    async def get_resources(self) -> list[ResourceInfo]:
        """Get mock resources."""
        return self._resources


def _mock_tool() -> str:
    """A mock tool for testing provider inheritance."""
    return "mock_result"


@pytest.mark.integration
async def test_child_session_agent_inherits_parent_mcp_providers() -> None:
    """Child session gets a per-session agent with parent's MCP providers.

    The child agent is a NEW object (not the parent's agent), so chat
    history and agent state are NOT shared.  Only kind=='mcp' providers
    are inherited.
    """
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        parent_session_id = "parent-mcp-inherit-test"
        child_session_id = "child-mcp-inherit-test"

        base_agent = pool.get_agent("test_agent")

        # Create parent session and get its per-session agent
        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Add a mock MCP provider ONLY to the parent agent
        mock_tool = Tool.from_callable(_mock_tool, name_override="mock_tool")
        mock_provider = MockMCPResourceProvider(
            name="mock_mcp_provider",
            tools=[mock_tool],
        )
        parent_agent.tools.add_provider(mock_provider)

        # Verify parent has the provider
        assert mock_provider in parent_agent.tools.external_providers

        # Verify base_agent does NOT have the provider
        assert mock_provider not in base_agent.tools.external_providers

        # Create child session
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )

        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child is a NEW agent object (not parent's agent)
        assert child_agent is not parent_agent, (
            "Child must have its own per-session agent, not share parent's"
        )

        # Child inherits parent's MCP provider
        assert mock_provider in child_agent.tools.external_providers, (
            "Child agent should inherit parent's kind=='mcp' providers"
        )

        # base_agent is NOT mutated
        assert mock_provider not in base_agent.tools.external_providers, (
            "base_agent must NOT be mutated"
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_agent_does_not_inherit_non_mcp_providers() -> None:
    """Child session inherits only kind=='mcp' providers, not non-MCP ones."""
    from agentpool.resource_providers import StaticResourceProvider

    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        parent_session_id = "parent-non-mcp-test"
        child_session_id = "child-non-mcp-test"

        base_agent = pool.get_agent("test_agent")

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Add non-MCP provider to parent
        non_mcp_tool = Tool.from_callable(_mock_tool, name_override="lead_agent_tool")
        non_mcp_provider = StaticResourceProvider(
            name="lead_agent_tools",
            tools=[non_mcp_tool],
        )
        parent_agent.tools.add_provider(non_mcp_provider)

        # Add MCP provider to parent
        mcp_tool = Tool.from_callable(_mock_tool, name_override="mcp_tool")
        mcp_provider = MockMCPResourceProvider(
            name="mock_mcp_provider",
            tools=[mcp_tool],
        )
        parent_agent.tools.add_provider(mcp_provider)

        # Create child
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child inherits MCP provider
        assert mcp_provider in child_agent.tools.external_providers, (
            "Child must inherit kind=='mcp' providers"
        )
        # Child does NOT inherit non-MCP provider
        assert non_mcp_provider not in child_agent.tools.external_providers, (
            "Child must NOT inherit non-MCP providers"
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_agent_shares_base_agent_mcp() -> None:
    """Child session agent shares base_agent.mcp to avoid duplicate processes."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        parent_session_id = "parent-mcp-share-test"
        child_session_id = "child-mcp-share-test"

        base_agent = pool.get_agent("test_agent")

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        # Create child
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child shares base_agent's MCP manager
        assert child_agent.mcp is base_agent.mcp, (
            "Child agent must share base_agent.mcp to avoid duplicate MCP processes"
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_is_not_per_session_agent() -> None:
    """Child session has is_per_session_agent=False for cleanup safety."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        await session_pool.start()

        parent_session_id = "parent-cleanup-test"
        child_session_id = "child-cleanup-test"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        await session_pool.sessions.get_or_create_session_agent(child_session_id)

        child_state = session_pool.sessions._sessions.get(child_session_id)
        assert child_state is not None
        assert child_state.is_per_session_agent is False, (
            "Child session must have is_per_session_agent=False "
            "so close_session() does not call agent.__aexit__()"
        )

        # Close child — should not close parent's MCP
        await session_pool.close_session(child_session_id)

        parent_state = session_pool.sessions._sessions.get(parent_session_id)
        assert parent_state is not None, (
            "Parent session should still exist after child close"
        )

        await session_pool.shutdown()
