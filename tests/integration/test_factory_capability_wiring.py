"""Integration tests for AgentFactory capability wiring and hot-swap.

Tests cover (M3 Todo 11):
1. Factory produces agents with native capabilities
2. AggregatedResourceSource constructed from compiled capabilities
3. on_change() triggers hot-swap (mock capability emits ChangeEvent, verify listener)
4. Native capability wiring (no adapter fallback needed)
5. Mixed capabilities function correctly together
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.capabilities.resource_source import (
    AggregatedResourceSource,
)
from agentpool.capabilities.subagent_capability import SubagentCapability
from agentpool.host.factory import AgentFactory, _inject_pool_providers
from agentpool.tools.base import Tool


if TYPE_CHECKING:
    from agentpool.models.manifest import AnyAgentConfig


# =============================================================================
# Helpers
# =============================================================================


def _make_test_tool(name: str = "test_tool") -> Tool[Any]:
    """Create a minimal Tool for testing."""

    def dummy_fn(x: int) -> int:
        """Return x doubled.

        Args:
            x: Input value
        """
        return x * 2

    return Tool.from_callable(dummy_fn, name_override=name)


def _make_mock_host_context(
    *,
    skills_tools_provider: Any | None = None,
    pool: Any | None = None,
) -> MagicMock:
    """Create a mock HostContext with the given providers."""
    ctx = MagicMock()
    ctx.skills_tools_provider = skills_tools_provider
    ctx.pool = pool
    ctx.mcp = MagicMock()
    ctx.mcp.get_aggregating_provider.return_value = MagicMock()
    ctx.config_file_path = None
    return ctx


def _make_mock_manifest(
    agents: dict[str, AnyAgentConfig] | None = None,
) -> MagicMock:
    """Create a mock AgentsManifest with the given agents."""
    manifest = MagicMock()
    manifest.agents = agents or {}
    return manifest


# =============================================================================
# Test 1: Factory produces agents with native capabilities
# =============================================================================


def test_compile_produces_capability_registry(
    agents_manifest_with_subagent: AnyAgentConfig,
) -> None:
    """Test compile produces capability registry with subagent.

    Given: manifest with an agent that has a subagent toolset.
    When: factory.compile() is called.
    Then: capability_registry maps agent name to list with SubagentCapability.
    """
    cfg = agents_manifest_with_subagent
    manifest = _make_mock_manifest({"agent1": cfg})
    host_context = _make_mock_host_context(
        skills_tools_provider=MagicMock(),
    )

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    factory.compile(manifest, host_context)

    caps = factory.capability_registry.get("agent1", [])
    assert len(caps) >= 1
    # Should have at least one SubagentCapability
    subagent_caps = [c for c in caps if isinstance(c, SubagentCapability)]
    assert len(subagent_caps) == 1


def test_compile_registry_empty_when_no_tools(
    simple_agent_config: AnyAgentConfig,
) -> None:
    """Test compile registry is empty when no tools configured.

    Given: manifest with an agent that has no special tools.
    When: factory.compile() is called.
    Then: capability_registry maps agent name to a list (may be empty).
    """
    manifest = _make_mock_manifest({"simple": simple_agent_config})
    host_context = _make_mock_host_context(skills_tools_provider=None)

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    factory.compile(manifest, host_context)

    caps = factory.capability_registry.get("simple", [])
    # No skills_tools_provider, no subagent, no tool providers → empty
    assert len(caps) == 0


# =============================================================================
# Test 2: AggregatedResourceSource constructed from compiled capabilities
# =============================================================================


def test_resource_source_collection_from_capabilities() -> None:
    """Test resource source collection from capabilities.

    Given: capabilities list with some implementing ResourceSource.
    When: _collect_resource_sources() is called.
    Then: AggregatedResourceSource is constructed with those sources.
    """
    from agentpool.capabilities.mcp_capability import MCPCapability

    # MCPCapability implements ResourceSource
    mock_client = MagicMock()
    mock_client.config.name = "test_mcp"
    mcp_cap = MCPCapability(mock_client)

    caps: list[Any] = [mcp_cap]

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    result = factory._collect_resource_sources(caps)

    assert result is not None
    assert isinstance(result, AggregatedResourceSource)
    assert len(result.sources) == 1


def test_resource_source_collection_returns_none_when_no_sources() -> None:
    """Test resource source collection returns None when no sources.

    Given: capabilities list with no ResourceSource implementations.
    When: _collect_resource_sources() is called.
    Then: Returns None.
    """
    caps: list[Any] = [SubagentCapability()]

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    result = factory._collect_resource_sources(caps)

    assert result is None


# =============================================================================
# Test 3: on_change() triggers hot-swap
# =============================================================================


@pytest.mark.asyncio
async def test_hot_swap_listener_starts_for_change_capable() -> None:
    """Test hot-swap listener starts for change-capable capabilities.

    Given: a capability with non-None on_change().
    When: _start_hot_swap_listeners() is called.
    Then: a background task is created and tracked.
    """
    import asyncio

    from agentpool.capabilities.combined_toolset import _OnChangeCapable

    # Create a mock capability that is _OnChangeCapable
    change_queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()

    class MockChangeCap:
        @property
        def name(self) -> str:
            return "mock_change_cap"

        def on_change(self):
            async def _gen():
                while True:
                    event = await change_queue.get()
                    yield event

            return _gen()

    cap = MockChangeCap()
    assert isinstance(cap, _OnChangeCapable)

    mock_agent = MagicMock()
    mock_agent._extra_capabilities = []

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])

    assert len(factory._hot_swap_tasks) == 1
    assert not factory._hot_swap_tasks[0].done()

    # Emit a change event
    await change_queue.put(ChangeEvent(capability_name="mock_change_cap"))

    # Give the task time to process
    await asyncio.sleep(0.05)

    # Task should still be running (listening for more events)
    assert not factory._hot_swap_tasks[0].done()

    # Clean up
    await factory.stop_hot_swap_listeners()


@pytest.mark.asyncio
async def test_hot_swap_listener_skips_static_capabilities() -> None:
    """Test hot-swap listener skips static capabilities.

    Given: a capability with None on_change().
    When: _start_hot_swap_listeners() is called.
    Then: no background task is created.
    """
    # SubagentCapability has no on_change() → isinstance check fails
    cap = SubagentCapability()

    mock_agent = MagicMock()
    mock_agent._extra_capabilities = []

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])

    assert len(factory._hot_swap_tasks) == 0


@pytest.mark.asyncio
async def test_stop_hot_swap_listeners_cancels_tasks() -> None:
    """Test stop hot-swap listeners cancels all tasks.

    Given: factory with running hot-swap tasks.
    When: stop_hot_swap_listeners() is called.
    Then: all tasks are cancelled and cleared.
    """
    import asyncio

    change_queue: asyncio.Queue[ChangeEvent] = asyncio.Queue()

    class MockChangeCap:
        @property
        def name(self) -> str:
            return "mock_change_cap"

        def on_change(self):
            async def _gen():
                while True:
                    event = await change_queue.get()
                    yield event

            return _gen()

    cap = MockChangeCap()
    mock_agent = MagicMock()

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    await factory._start_hot_swap_listeners("test_agent", mock_agent, [cap])
    assert len(factory._hot_swap_tasks) == 1

    await factory.stop_hot_swap_listeners()
    assert len(factory._hot_swap_tasks) == 0


# =============================================================================
# Test 4: Native capability wiring (no adapter needed)
# =============================================================================


def test_native_capability_wiring_for_tool_providers() -> None:
    """Test native capability wiring for tool providers.

    Given: a FunctionToolsetCapability (native capability).
    When: _compile_agent_capabilities() processes it.
    Then: the capability is added directly (no adapter wrapping).
    """
    from agentpool import NativeAgentConfig

    tool = _make_test_tool("native_tool")
    provider = FunctionToolsetCapability(name="static_tools", tools=[tool])

    cfg = NativeAgentConfig(
        name="test_agent",
        model="openai:gpt-4o-mini",
        system_prompt="You are a test agent.",
        tools=[],
    )

    host_context = _make_mock_host_context(skills_tools_provider=None)

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    with patch.object(NativeAgentConfig, "get_tool_providers", return_value=[provider]):
        caps = factory._compile_agent_capabilities("test", cfg, host_context)

    # The provider should be added directly as a native capability
    assert provider in caps


def test_inject_pool_providers_adds_nothing_for_main_agent() -> None:
    """Test inject pool providers adds nothing for main agent.

    Given: host_context with pool and include_aggregating=False.
    When: _inject_pool_providers() is called.
    Then: nothing is added to agent.tools.
    """
    host_context = _make_mock_host_context(
        pool=MagicMock(),
    )
    mock_agent = MagicMock()

    _inject_pool_providers(mock_agent, host_context, include_aggregating=False)

    mock_agent.tools.add_provider.assert_not_called()


def test_inject_pool_providers_adds_aggregating_for_child() -> None:
    """Test inject pool providers adds aggregating for child sessions.

    Given: host_context with pool and include_aggregating=True.
    When: _inject_pool_providers() is called.
    Then: MCP aggregating provider is added to agent._external_capabilities.
    """
    mock_aggregating = MagicMock()
    host_context = _make_mock_host_context(
        pool=MagicMock(),
    )
    host_context.mcp.get_aggregating_provider.return_value = mock_aggregating
    mock_agent = MagicMock()
    mock_agent._external_capabilities = []

    _inject_pool_providers(mock_agent, host_context, include_aggregating=True)

    # Should be in external_capabilities: aggregating provider only
    assert mock_aggregating in mock_agent._external_capabilities


def test_inject_pool_providers_skips_when_no_pool() -> None:
    """Test inject pool providers skips when no pool.

    Given: host_context with pool=None.
    When: _inject_pool_providers() is called.
    Then: nothing is added to agent.tools.
    """
    host_context = _make_mock_host_context(pool=None)
    mock_agent = MagicMock()

    _inject_pool_providers(mock_agent, host_context, include_aggregating=True)

    mock_agent.tools.add_provider.assert_not_called()


# =============================================================================
# Test 5: Mixed capabilities together
# =============================================================================


def test_mixed_capabilities_native_and_subagent() -> None:
    """Test mixed capabilities native and subagent together.

    Given: agent config with both subagent toolset and inline tools.
    When: _compile_agent_capabilities() is called.
    Then: capabilities list contains both SubagentCapability and
          FunctionToolsetCapability.
    """
    from agentpool import NativeAgentConfig
    from agentpool_config.toolsets import SubagentToolsetConfig

    tool = _make_test_tool("inline_tool")
    provider = FunctionToolsetCapability(name="inline_provider", tools=[tool])

    cfg = NativeAgentConfig(
        name="mixed",
        model="openai:gpt-4o-mini",
        system_prompt="You are a mixed agent.",
        tools=[SubagentToolsetConfig()],
    )

    mock_skills_provider = MagicMock()

    host_context = _make_mock_host_context(
        skills_tools_provider=mock_skills_provider,
    )

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    with patch.object(NativeAgentConfig, "get_tool_providers", return_value=[provider]):
        caps = factory._compile_agent_capabilities("mixed", cfg, host_context)

    # Should have: 1 skills_tools_provider + 1 SubagentCapability
    # + 1 inline provider = 3
    subagent_caps = [c for c in caps if isinstance(c, SubagentCapability)]

    assert len(subagent_caps) == 1
    assert provider in caps
    assert mock_skills_provider in caps
    assert len(caps) == 3


def test_compile_populates_resource_sources_per_agent(
    agents_manifest_with_subagent: AnyAgentConfig,
) -> None:
    """Test compile populates resource sources per agent.

    Given: manifest with agents.
    When: compile() is called.
    Then: resource_sources dict is populated with one entry per agent.
    """
    manifest = _make_mock_manifest({"agent1": agents_manifest_with_subagent})
    host_context = _make_mock_host_context(skills_tools_provider=None)

    factory = AgentFactory.__new__(AgentFactory)
    factory._pool = MagicMock()
    factory._capability_registry = {}
    factory._resource_sources = {}
    factory._hot_swap_tasks = []

    factory.compile(manifest, host_context)

    assert "agent1" in factory.resource_sources
    # SubagentCapability doesn't implement ResourceSource
    assert factory.resource_sources["agent1"] is None


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def agents_manifest_with_subagent() -> AnyAgentConfig:
    """Return an agent config with a subagent toolset."""
    from agentpool import NativeAgentConfig
    from agentpool_config.toolsets import SubagentToolsetConfig

    return NativeAgentConfig(
        name="agent1",
        model="openai:gpt-4o-mini",
        system_prompt="You are a test agent.",
        tools=[SubagentToolsetConfig()],
    )


@pytest.fixture
def simple_agent_config() -> AnyAgentConfig:
    """Return a simple agent config with no special tools."""
    from agentpool import NativeAgentConfig

    return NativeAgentConfig(
        name="simple",
        model="openai:gpt-4o-mini",
        system_prompt="You are a simple agent.",
        tools=[],
    )
