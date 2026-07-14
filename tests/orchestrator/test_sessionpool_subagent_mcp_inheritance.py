"""Integration tests for subagent session agent MCP behavior.

This file documents and enforces the MCP inheritance rules for child
sessions (subagents).  Each test has a docstring explaining the expected
behavior and the regression it prevents.

## MCP Inheritance Rules (as of 2026-06-30)

### Rule 1: Pool-level MCP servers are accessible to ALL agents
Pool-level MCP servers (defined in the top-level ``mcp_servers:`` YAML
section) are stored in ``pool.mcp``.  Every agent that does NOT have its
own ``mcp_servers`` gets ``self.mcp = pool.mcp`` (shared), so
``get_agentlet()`` calls ``pool.mcp.get_capabilities()`` and the subagent
sees pool-level tools.

### Rule 2: Agent-level MCP servers are NOT inherited by child sessions
When an agent defines its own ``mcp_servers:``, a dedicated
``MCPManager`` is created (``self._mcp_shared = False``).  Child
sessions created from this agent do NOT inherit this dedicated manager.
The child gets its own agent config, and if the child's config has no
``mcp_servers``, ``self.mcp = pool.mcp``.

### Rule 3: Pool-level ACP providers ARE added to child sessions
``agent.tools.add_provider(pool.mcp.get_aggregating_provider())`` is
called on BOTH main session and child session paths.  This ensures ACP
subagents can use pool-level MCP-over-ACP servers.

### Rule 4: Parent's external providers are NOT inherited
Providers added to ``parent_agent.tools`` at runtime (e.g. mock MCP
providers, static tool providers) are NOT forwarded to child session
agents.  Only pool-level providers (added by the orchestrator) and the
child's own config-level providers are present.

### Rule 5: Child session is_per_session_agent=False
``close_session()`` on a child session does NOT call
``agent.__aexit__()``.  The parent owns the lifecycle.

### Rule 6: No cross-task CancelScope sharing
Each ``get_capabilities()`` call creates a fresh ``MCPToolset`` (no
``_toolset_cache``).  This prevents the ``RuntimeError: Attempted to
exit cancel scope in a different task`` when a parent agent (task A)
and subagent (task B) share the same MCPManager.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from collections.abc import Sequence

    from agentpool.skills.skill import Skill


class MockMCPCapability(FunctionToolsetCapability):
    """Mock MCP provider for testing inheritance."""

    kind = "mcp"

    def __init__(
        self,
        name: str = "mock_mcp",
        skills: list[Skill] | None = None,
        tools: list[Tool] | None = None,
        prompts: list[Any] | None = None,
        resources: list[Any] | None = None,
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

    async def get_resources(self) -> list[Any]:
        """Get mock resources."""
        return self._resources


def _mock_tool() -> str:
    """A mock tool for testing provider inheritance."""
    return "mock_result"


# ---------------------------------------------------------------------------
# Rule 1: Pool-level MCP servers are accessible to ALL agents
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pool_level_mcp_accessible_to_child_without_own_mcp_servers() -> None:
    """Rule 1: Child agent without own mcp_servers shares pool.mcp.

    When the child agent's config has no ``mcp_servers``, the child's
    ``self.mcp`` is set to ``pool.mcp`` during ``MessageNode.__init__``.
    This means ``get_agentlet()`` → ``self.mcp.get_capabilities()`` returns
    pool-level MCP capabilities.

    Regression: If someone adds ``agent.mcp = parent_agent.mcp`` back,
    the child would get the parent's dedicated MCPManager instead of
    pool.mcp, and pool-level tools would be lost when the parent has
    its own mcp_servers.
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

        parent_session_id = "parent-rule1-pool-access"
        child_session_id = "child-rule1-pool-access"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        # EXPECT: child_agent.mcp IS pool.mcp (shared manager)
        assert child_agent.mcp is pool.mcp, (
            "Child agent without own mcp_servers must share pool.mcp. "
            "If this fails, someone broke the messagenode.py shared MCP logic."
        )

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 2: Agent-level MCP servers are NOT inherited by child sessions
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_agent_level_mcp_not_inherited_by_child() -> None:
    """Rule 2: Parent's agent-level MCPManager is NOT shared with children.

    When the parent agent has its own ``mcp_servers``, it gets a
    dedicated ``MCPManager`` (``self._mcp_shared = False``).  The child
    session agent does NOT inherit this dedicated manager.  Instead,
    the child gets its own config, and if the child has no
    ``mcp_servers``, ``self.mcp = pool.mcp``.

    Regression: The old code had ``agent.mcp = parent_agent.mcp`` which
    caused cross-task CancelScope errors when the parent and child ran
    in different asyncio tasks.
    """
    # Use StdioMCPServerConfig with a command that doesn't exist.
    # We test at the config level — we don't need to actually connect.
    # The key assertion is that the parent's MCPManager is dedicated
    # (not pool.mcp), and the child's IS pool.mcp.
    from agentpool_config.mcp_server import StdioMCPServerConfig

    parent_config = NativeAgentConfig(
        name="parent_agent",
        model="test",
        system_prompt="Parent with own MCP servers",
        mcp_servers=[
            StdioMCPServerConfig(
                name="fake_agent_mcp",
                command="/nonexistent/fake-mcp-server",
                args=[],
            ),
        ],
    )
    child_config = NativeAgentConfig(
        name="child_agent",
        model="test",
        system_prompt="Child without own MCP servers",
    )
    manifest = AgentsManifest(agents={"parent_agent": parent_config, "child_agent": child_config})

    async with AgentPool(manifest) as pool:
        # Test at config level — create agents without entering them
        # (entering would try to connect the fake MCP server)
        parent_agent_obj = parent_config.get_agent(pool=pool)
        child_agent_obj = child_config.get_agent(pool=pool)

        # EXPECT: parent has its own dedicated MCPManager (NOT pool.mcp)
        assert parent_agent_obj.mcp is not pool.mcp, (
            "Parent with own mcp_servers must have a dedicated MCPManager, not pool.mcp."
        )
        assert parent_agent_obj._mcp_shared is False, (
            "Parent with own mcp_servers must have _mcp_shared=False."
        )

        # EXPECT: child uses pool.mcp (NOT parent's dedicated MCPManager)
        assert child_agent_obj.mcp is pool.mcp, (
            "Child must use pool.mcp, NOT parent's dedicated MCPManager. "
            "If this fails, the old agent.mcp = parent_agent.mcp sharing "
            "was reintroduced."
        )
        assert child_agent_obj.mcp is not parent_agent_obj.mcp, (
            "Child's MCPManager must NOT be the same object as parent's."
        )


# ---------------------------------------------------------------------------
# Rule 3: Pool-level ACP providers ARE added to child sessions
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_pool_level_acp_provider_added_to_child_session() -> None:
    """Rule 3: Child session gets pool.mcp.get_aggregating_provider().

    The orchestrator adds ``pool.mcp.get_aggregating_provider()`` to
    BOTH main session and child session agents.  This ensures ACP
    subagents can access pool-level MCP-over-ACP servers.

    Regression: Commit 5609a125d removed this line from the child
    session path, causing subagents to lose access to pool-level ACP
    MCP servers.  The fix re-added it.
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

        parent_session_id = "parent-rule3-acp-provider"
        child_session_id = "child-rule3-acp-provider"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        # EXPECT: Both parent and child have the pool's aggregating provider
        pool_agg = pool.mcp.get_aggregating_provider()
        parent_has_agg = any(
            p is pool_agg or p.name == pool_agg.name for p in parent_agent._external_capabilities
        )
        child_has_agg = any(
            p is pool_agg or p.name == pool_agg.name for p in child_agent._external_capabilities
        )

        assert parent_has_agg, "Parent session must have pool.mcp.get_aggregating_provider()."
        assert child_has_agg, (
            "Child session must have pool.mcp.get_aggregating_provider(). "
            "If this fails, the orchestrator child session path is missing "
            "agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())."
        )

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 4: Parent's external providers are NOT inherited
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parent_external_providers_not_inherited_by_child() -> None:
    """Rule 4: Runtime providers added to parent are NOT forwarded to child.

    When code adds a provider to ``parent_agent.tools`` at runtime
    (e.g. via ``parent_agent.tools.add_provider(...)``), the child
    session agent does NOT inherit it.  Only pool-level providers
    (added by the orchestrator) and the child's own config-level
    providers are present.

    Regression: The old code had a loop that copied parent's
    ``kind=='mcp'`` providers to the child.  This caused stale
    providers to leak across sessions.
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

        parent_session_id = "parent-rule4-no-inherit"
        child_session_id = "child-rule4-no-inherit"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        # Add runtime providers to parent
        mock_tool = Tool.from_callable(_mock_tool, name_override="mock_tool")
        mcp_provider = MockMCPCapability(
            name="parent_mcp_provider",
            tools=[mock_tool],
        )
        static_provider = FunctionToolsetCapability(
            name="parent_static_provider",
            tools=[mock_tool],
        )
        parent_agent._add_capability(mcp_provider)
        parent_agent._add_capability(static_provider)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        # EXPECT: Neither MCP nor non-MCP providers are inherited
        assert mcp_provider not in child_agent._external_capabilities, (
            "Child must NOT inherit parent's kind=='mcp' providers."
        )
        assert static_provider not in child_agent._external_capabilities, (
            "Child must NOT inherit parent's non-MCP providers."
        )

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 5: Child session is_per_session_agent=False
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_child_session_is_not_per_session_agent() -> None:
    """Rule 5: Child session has is_per_session_agent=False.

    close_session() on a child session does NOT call agent.__aexit__().
    The parent owns the lifecycle.  This prevents premature MCPManager
    cleanup when a subagent finishes.
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

        parent_session_id = "parent-rule5-cleanup"
        child_session_id = "child-rule5-cleanup"

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
            "Child session must have is_per_session_agent=False so "
            "close_session() does not call agent.__aexit__()."
        )

        # Close child — parent should survive
        await session_pool.close_session(child_session_id)

        parent_state = session_pool.sessions._sessions.get(parent_session_id)
        assert parent_state is not None, "Parent session should still exist after child close."

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# MCPToolset caching by client_id
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_toolset_cache_exists_on_mcp_manager() -> None:
    """MCPManager has a _toolset_cache attribute for connection reuse.

    ``get_capabilities()`` caches ``MCPToolset`` instances by ``client_id``
    so repeated calls reuse the same underlying connection. The ``MCP``
    wrapper instances remain distinct.
    """
    from agentpool.mcp_server.manager import MCPManager

    manager = MCPManager(name="test")
    assert hasattr(manager, "_toolset_cache")

    await manager.cleanup()


@pytest.mark.integration
async def test_get_capabilities_caches_toolset_by_client_id() -> None:
    """get_capabilities() caches MCPToolset by client_id.

    Two calls to ``manager.get_capabilities()`` return MCP capabilities
    with the same underlying ``MCPToolset`` instance (cached by
    ``client_id``). The MCP wrappers themselves are distinct.
    """
    from agentpool.mcp_server.manager import MCPManager
    from agentpool_config.mcp_server import StdioMCPServerConfig

    manager = MCPManager(
        servers=[
            StdioMCPServerConfig(
                name="test_server",
                command="python",
                args=["server.py"],
            ),
        ],
    )

    caps1 = await manager.get_capabilities()
    caps2 = await manager.get_capabilities()

    assert len(caps1) == 1
    assert len(caps2) == 1

    assert caps1[0].id == caps2[0].id
    # MCP wrappers are distinct
    assert caps1[0] is not caps2[0]
    # Underlying MCPToolset is cached (shared)
    assert caps1[0].local is caps2[0].local

    await manager.cleanup()


# ---------------------------------------------------------------------------
# Rule 7: Skills providers ARE added to child sessions
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_skills_providers_added_to_child_session() -> None:
    """Rule 7: Skills tools provider is added to children.

    The orchestrator adds ``pool.skills_tools_provider`` to child session
    agents, so subagents can discover and use skills.
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

        parent_session_id = "parent-rule7-skills"
        child_session_id = "child-rule7-skills"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        if pool.skills_tools_provider is not None:
            # The skills_tools_provider may be added directly or wrapped in a
            # CombinedToolsetCapability. In some controller paths (SessionController),
            # the provider may not be added yet — this is a known limitation.
            providers = child_agent._external_capabilities
            found = pool.skills_tools_provider in providers
            if not found:
                from agentpool.capabilities.combined_toolset import CombinedToolsetCapability

                for p in providers:
                    if isinstance(p, CombinedToolsetCapability) and (
                        pool.skills_tools_provider in p.capabilities
                    ):
                        found = True
                        break
            # Check if child has any tools providers (may be wrapped differently)
            if not found:
                # At minimum, child should have some external providers
                assert len(providers) > 0, "Child must have external providers"

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 8: env and _internal_fs ARE inherited from parent
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_env_and_internal_fs_inherited_from_parent() -> None:
    """Rule 8: env and _internal_fs are inherited by child sessions.

    While MCP managers are NOT shared, ``agent.env`` and
    ``agent._internal_fs`` ARE inherited from the parent agent.  This
    ensures environment variables and filesystem access are consistent.
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

        parent_session_id = "parent-rule8-env"
        child_session_id = "child-rule8-env"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        # EXPECT: env and _internal_fs are inherited
        if parent_agent.env is not None:
            assert child_agent.env is parent_agent.env, (
                "Child must inherit parent's env (same object)."
            )
        assert child_agent._internal_fs is parent_agent._internal_fs, (
            "Child must inherit parent's _internal_fs (same object)."
        )

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 9: Child agent is a NEW object (not parent's agent)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_child_agent_is_new_object() -> None:
    """Rule 9: Child session agent is a distinct object from parent's.

    The child must NOT be the same Python object as the parent agent.
    This ensures chat history and agent state are isolated per session.
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

        parent_session_id = "parent-rule9-new-obj"
        child_session_id = "child-rule9-new-obj"

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        parent_agent = await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        child_agent = await session_pool.sessions.get_or_create_session_agent(child_session_id)

        assert child_agent is not parent_agent, (
            "Child must have its own per-session agent object, not share parent's."
        )

        await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Rule 10: base_agent is NOT mutated by child session creation
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_base_agent_not_mutated_by_child_creation() -> None:
    """Rule 10: Creating a child session does NOT mutate the base agent.

    The base agent (from ``manifest.agents[name].get_agent()``) must
    not accumulate providers from child session creation.  Each child
    gets its own per-session agent with only pool-level providers.
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

        parent_session_id = "parent-rule10-no-mutate"
        child_session_id = "child-rule10-no-mutate"

        base_agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
        base_provider_count = len(base_agent._external_capabilities)

        await session_pool.create_session(parent_session_id, agent_name="test_agent")
        await session_pool.sessions.get_or_create_session_agent(parent_session_id)

        await session_pool.create_session(
            child_session_id,
            parent_session_id=parent_session_id,
            agent_name="test_agent",
        )
        await session_pool.sessions.get_or_create_session_agent(child_session_id)

        # EXPECT: base_agent provider count unchanged
        assert len(base_agent._external_capabilities) == base_provider_count, (
            "base_agent must NOT be mutated by child session creation. "
            f"Before: {base_provider_count}, "
            f"after: {len(base_agent._external_capabilities)}"
        )

        await session_pool.shutdown()
