"""Integration tests for subagent session agent behavior.

This verifies that child sessions reuse the shared pool-level agent
(base_agent) and do NOT inherit parent session's dynamically-added
MCP providers.

Pool-level MCP providers (from YAML ``mcp_servers``) are already present
on the shared ``base_agent``, so child sessions have access to those.

Session-level MCP providers (added dynamically via ACP mcp-over-acp,
e.g. ``workspace-fs``) are per-session: child sessions created via ACP
``session/new`` receive their own independent MCP connections.
Inheriting the parent's session-level providers would register duplicate
``FunctionToolset`` instances on the shared ``base_agent``, causing
pydantic-ai ``CombinedToolset`` to raise a ``UserError`` for conflicting
tool names.
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
async def test_child_session_does_not_inherit_parent_session_mcp_providers() -> None:
    """Child session agent does NOT inherit parent's session-level MCP providers.

    Child sessions reuse the shared pool-level agent (base_agent), which
    already has pool-level MCP providers. Session-level MCP providers
    (added dynamically to the parent via ACP mcp-over-acp) are NOT
    inherited — child sessions get their own via ACP session/new.

    Steps:
    1. Create AgentPool with a NativeAgentConfig agent using TestModel.
    2. Create a parent session and get its per-session agent.
    3. Add a MockMCPResourceProvider ONLY to the parent agent.
    4. Verify the shared agent does NOT have the provider.
    5. Create a child session with parent_session_id set.
    6. Get the child session's agent — should NOT have the parent-only
       MCP provider (child uses shared base_agent, no inheritance).
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

        # Get the shared pool-level agent
        base_agent = pool.get_agent("test_agent")

        # Step 1: Create parent session and get its per-session agent
        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Step 2: Add a mock MCP provider ONLY to the parent agent
        mock_tool = Tool.from_callable(_mock_tool, name_override="mock_tool")
        mock_provider = MockMCPResourceProvider(
            name="mock_mcp_provider",
            tools=[mock_tool],
        )
        parent_agent.tools.add_provider(mock_provider)

        # Verify parent has the provider
        assert mock_provider in parent_agent.tools.external_providers, (
            "Parent agent's external_providers should contain the mock MCP provider"
        )

        # Verify shared agent does NOT have the provider
        assert mock_provider not in base_agent.tools.external_providers, (
            "Shared agent must NOT have the parent-only MCP provider"
        )

        # Step 3: Create child session with parent_session_id
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )

        # Step 4: Get the child session's agent
        # Child reuses shared base_agent — no MCP inheritance
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Step 5: Assert child does NOT have the parent-only MCP provider
        assert mock_provider not in child_agent.tools.external_providers, (
            "Child agent must NOT inherit parent-only MCP providers. "
            "Pool-level providers are already on base_agent; session-level "
            "providers are per-session and child gets its own via ACP."
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_does_not_inherit_any_session_providers() -> None:
    """Child session agent inherits NEITHER MCP nor non-MCP providers from parent.

    Both MCP and non-MCP providers added dynamically to the parent
    session are per-session and should not leak to child sessions.
    Pool-level providers are already on the shared base_agent.
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

        # Get the shared pool-level agent
        base_agent = pool.get_agent("test_agent")

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

        # Add an MCP provider to parent only
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

        # Get child agent (returns shared pool-level agent)
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child should inherit NEITHER provider type
        assert mcp_provider not in child_agent.tools.external_providers, (
            "Child should NOT inherit session-level MCP providers"
        )
        assert non_mcp_provider not in child_agent.tools.external_providers, (
            "Child should NOT inherit non-MCP (lead-agent-specific) providers"
        )

        await session_pool.shutdown()


@pytest.mark.integration
async def test_child_session_uses_shared_agent_without_inheritance() -> None:
    """Child session uses shared base_agent without inheriting parent providers.

    Child sessions reuse the shared pool-level agent (base_agent).
    Session-level MCP providers added dynamically to the parent
    (e.g., via ACP session_mcp_providers) are NOT copied to the child.
    This is intentional: child sessions get their own MCP connections
    via ACP session/new, and inheriting would cause duplicate tool
    registrations on the shared base_agent.
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

        parent_session_id = "parent-no-inherit-test"
        child_session_id = "child-no-inherit-test"

        # Get the shared pool-level agent — we do NOT add providers to it
        base_agent = pool.get_agent("test_agent")

        # Create parent session and get its per-session agent
        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(
            parent_session_id
        )

        # Add a mock MCP provider ONLY to the parent agent (NOT shared agent)
        mock_tool = Tool.from_callable(_mock_tool, name_override="noinherit_mcp_tool")
        mock_provider = MockMCPResourceProvider(
            name="noinherit_mcp_provider",
            tools=[mock_tool],
        )
        parent_agent.tools.add_provider(mock_provider)

        # Verify parent has the provider
        assert mock_provider in parent_agent.tools.external_providers, (
            "Parent agent should have the mock MCP provider"
        )

        # CRITICAL: Verify shared agent does NOT have this provider
        assert mock_provider not in base_agent.tools.external_providers, (
            "Shared agent must NOT have the parent-only MCP provider"
        )

        # Create child session with parent_session_id
        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )

        # Get child session agent — reuses shared base_agent, no inheritance
        child_agent = await session_pool.sessions.get_or_create_session_agent(
            child_session_id
        )

        # Child should NOT inherit parent-only MCP providers.
        # Pool-level providers are already on base_agent; session-level
        # providers are per-session and child gets its own via ACP.
        assert mock_provider not in child_agent.tools.external_providers, (
            "Child session must NOT inherit parent-only MCP providers. "
            "Child reuses shared base_agent; session-level providers are "
            "per-session and would cause duplicate tool registrations."
        )

        await session_pool.shutdown()
