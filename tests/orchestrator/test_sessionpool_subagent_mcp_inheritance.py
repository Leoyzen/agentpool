"""Integration test for subagent MCP tool provider inheritance.

This test verifies that when a child session agent is created,
it inherits the parent's external MCP tool providers.

REGRESSION TEST: Previously, child sessions did not inherit parent session
agent tool providers, causing subagents to lose access to MCP tools that
were added to the parent session dynamically (e.g., via ACP session_mcp_providers).

Fix: In get_or_create_session_agent(), after creating a per-session agent for a
child session, parent session agent's MCPResourceProvider instances are copied
to the child (but not other provider types like lead-agent-specific tools).
"""

from __future__ import annotations

from typing import Any, Sequence

import pytest
from pydantic_ai.models.test import TestModel

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
async def test_child_session_inherits_parent_mcp_providers() -> None:
    """Child session agent inherits parent's MCP tool providers.

    Steps:
    1. Create AgentPool with a NativeAgentConfig agent using TestModel.
    2. Create a parent session and get its per-session agent.
    3. Add a MockMCPResourceProvider with a mock tool to the parent agent's
       tools.external_providers.
    4. Create a child session with parent_session_id set.
    5. Get the child session's per-session agent.
    6. Assert the child agent's tools.external_providers contains the MCP provider.
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

        # Step 1: Create parent session and get its per-session agent
        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Step 2: Add a mock MCP provider to the parent agent's external_providers
        mock_tool = Tool.from_callable(_mock_tool, name_override="mock_tool")
        mock_provider = MockMCPResourceProvider(
            name="mock_mcp_provider",
            tools=[mock_tool],
        )
        parent_agent.tools.add_provider(mock_provider)

        # Verify parent has the provider (among pool-level providers)
        assert mock_provider in parent_agent.tools.external_providers, (
            "Parent agent's external_providers should contain the mock MCP provider"
        )

        # Step 3: Create child session with parent_session_id
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )

        # Step 4: Get the child session's per-session agent
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Step 5: Assert child inherited parent's MCP providers
        assert mock_provider in child_agent.tools.external_providers, (
            "Child agent's external_providers should contain the parent's MCP provider"
        )

        # Verify non-MCP providers are NOT inherited
        non_mcp_names = [p.name for p in child_agent.tools.external_providers]
        assert "non_mcp_provider_should_not_be_inherited" not in non_mcp_names, (
            "Non-MCP providers should not be inherited by child sessions"
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_does_not_inherit_non_mcp_providers() -> None:
    """Child session agent should NOT inherit non-MCP providers from parent.

    Only MCPResourceProvider instances should be inherited, not other provider
    types like StaticResourceProvider (which may contain lead-agent-specific
    tools like task, background_cancel, etc.).
    """
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

        # Create parent session and get its per-session agent
        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Add a non-MCP provider (e.g., lead-agent-specific tools)
        non_mcp_tool = Tool.from_callable(_mock_tool, name_override="lead_agent_tool")
        non_mcp_provider = StaticResourceProvider(
            name="lead_agent_tools",
            tools=[non_mcp_tool],
        )
        parent_agent.tools.add_provider(non_mcp_provider)

        # Add an MCP provider
        mcp_tool = Tool.from_callable(_mock_tool, name_override="mcp_tool")
        mcp_provider = MockMCPResourceProvider(
            name="mock_mcp_provider",
            tools=[mcp_tool],
        )
        parent_agent.tools.add_provider(mcp_provider)

        # Verify parent has both new providers (in addition to pool-level ones)
        assert non_mcp_provider in parent_agent.tools.external_providers, (
            "Parent should have the non-MCP provider"
        )
        assert mcp_provider in parent_agent.tools.external_providers, (
            "Parent should have the MCP provider"
        )

        # Create child session
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )

        # Get child agent
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child should inherit ONLY the MCP provider, not the non-MCP one
        assert mcp_provider in child_agent.tools.external_providers, (
            "Child should inherit MCP provider"
        )
        assert non_mcp_provider not in child_agent.tools.external_providers, (
            "Child should NOT inherit non-MCP (lead-agent-specific) providers"
        )
        # Child should have the MCP provider among its providers
        assert any(
            getattr(p, "kind", None) == "mcp" and p.name == "mock_mcp_provider"
            for p in child_agent.tools.external_providers
        ), (
            "Child should have the inherited mock MCP provider"
        )

        await session_pool.shutdown()
