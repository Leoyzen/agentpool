"""Integration tests for skill providers.

Tests cover AggregatingResourceProvider with Local and MCP providers,
signal propagation, skill name collision resolution, and provider lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest
from upathtools import UPath

from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.base import ResourceChangeEvent, ResourceProvider
from agentpool.skills.skill import Skill


if TYPE_CHECKING:
    from pydantic_ai.capabilities import AbstractCapability
    from collections.abc import Sequence
    from types import TracebackType

    from agentpool.prompts.prompts import BasePrompt
    from agentpool.resource_providers.resource_info import ResourceInfo
    from agentpool.tools.base import Tool


# =============================================================================
# Mock Providers for Testing
# =============================================================================


class MockLocalResourceProvider(ResourceProvider):
    """Mock provider simulating LocalResourceProvider behavior."""

    kind = "custom"

    def __init__(
        self,
        name: str = "mock_local",
        skills: list[Skill] | None = None,
        tools: list[Tool] | None = None,
        prompts: list[BasePrompt] | None = None,
        resources: list[ResourceInfo] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._skills = skills or []
        self._tools = tools or []
        self._prompts = prompts or []
        self._resources = resources or []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> MockLocalResourceProvider:
        """Async context entry."""
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup."""
        self.exited = True

    async def get_skills(self) -> list[Skill]:
        """Get mock skills."""
        return self._skills

    async def get_tools(self) -> Sequence[Tool]:
        """Get mock tools."""
        return self._tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get mock prompts."""
        return self._prompts

    async def get_resources(self) -> list[ResourceInfo]:
        """Get mock resources."""
        return self._resources

    async def emit_skills_changed(self) -> None:
        """Emit skills changed signal for testing."""
        await self.skills_changed.emit(self.create_change_event("skills"))

    async def emit_tools_changed(self) -> None:
        """Emit tools changed signal for testing."""
        await self.tools_changed.emit(self.create_change_event("tools"))

    def as_capability(self) -> AbstractCapability | None:
        """Return a pydantic-ai capability for this provider.

        Returns:
            A pydantic-ai AbstractCapability instance, or None.
        """
        return None


class MockMCPResourceProvider(ResourceProvider):
    """Mock provider simulating MCPResourceProvider behavior."""

    kind = "mcp"

    def __init__(
        self,
        name: str = "mock_mcp",
        skills: list[Skill] | None = None,
        tools: list[Tool] | None = None,
        prompts: list[BasePrompt] | None = None,
        resources: list[ResourceInfo] | None = None,
    ) -> None:
        super().__init__(name=name)
        self._skills = skills or []
        self._tools = tools or []
        self._prompts = prompts or []
        self._resources = resources or []
        self.entered = False
        self.exited = False

    async def __aenter__(self) -> MockMCPResourceProvider:
        """Async context entry."""
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup."""
        self.exited = True

    async def get_skills(self) -> list[Skill]:
        """Get mock skills."""
        return self._skills

    async def get_tools(self) -> Sequence[Tool]:
        """Get mock tools."""
        return self._tools

    async def get_prompts(self) -> list[BasePrompt]:
        """Get mock prompts."""
        return self._prompts

    async def get_resources(self) -> list[ResourceInfo]:
        """Get mock resources."""
        return self._resources

    async def emit_skills_changed(self) -> None:
        """Emit skills changed signal for testing."""
        await self.skills_changed.emit(self.create_change_event("skills"))

    async def emit_prompts_changed(self) -> None:
        """Emit prompts changed signal for testing."""
        await self.prompts_changed.emit(self.create_change_event("prompts"))

    async def emit_tools_changed(self) -> None:
        """Emit tools changed signal for testing."""
        await self.tools_changed.emit(self.create_change_event("tools"))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_skill_local() -> Skill:
    """Create a mock skill from local provider."""
    return Skill(
        name="local-skill",
        description="A skill from local provider",
        skill_path=UPath("/tmp/local-skill"),
        metadata={"source": "local"},
    )


@pytest.fixture
def mock_skill_mcp() -> Skill:
    """Create a mock skill from MCP provider."""
    return Skill(
        name="mcp-skill",
        description="A skill from MCP provider",
        skill_path=UPath("mcp://test/mcp-skill"),
        metadata={"source": "mcp"},
    )


@pytest.fixture
def mock_skill_collision_local() -> Skill:
    """Create a skill for collision testing from local provider."""
    return Skill(
        name="shared-skill",
        description="Local version of shared skill",
        skill_path=UPath("/tmp/shared-skill"),
        metadata={"source": "local", "priority": "high"},
    )


@pytest.fixture
def mock_skill_collision_mcp() -> Skill:
    """Create a skill for collision testing from MCP provider."""
    return Skill(
        name="shared-skill",
        description="MCP version of shared skill",
        skill_path=UPath("mcp://test/shared-skill"),
        metadata={"source": "mcp", "priority": "low"},
    )


@pytest.fixture
def mock_tool_local() -> MagicMock:
    """Create a mock tool from local provider."""
    return MagicMock(name="local_tool")


@pytest.fixture
def mock_tool_mcp() -> MagicMock:
    """Create a mock tool from MCP provider."""
    return MagicMock(name="mcp_tool")


# =============================================================================
# Test Class: AggregatingProviderBasics
# =============================================================================


@pytest.mark.integration
class TestAggregatingProviderBasics:
    """Test basic AggregatingResourceProvider functionality."""

    async def test_empty_provider_list(self) -> None:
        """Test aggregating provider with no child providers."""
        provider = AggregatingResourceProvider(providers=[], name="empty")

        skills = await provider.get_skills()
        tools = await provider.get_tools()
        prompts = await provider.get_prompts()
        resources = await provider.get_resources()

        assert skills == []
        assert tools == []
        assert prompts == []
        assert resources == []

    async def test_single_provider_aggregation(self, mock_skill_local: Skill) -> None:
        """Test aggregating provider with single child provider."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        skills = await aggregator.get_skills()

        assert len(skills) == 1
        assert skills[0].name == "local-skill"

    async def test_multiple_provider_aggregation(
        self, mock_skill_local: Skill, mock_skill_mcp: Skill
    ) -> None:
        """Test aggregating provider combines resources from multiple providers."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        mcp_provider = MockMCPResourceProvider(name="mcp", skills=[mock_skill_mcp])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        skills = await aggregator.get_skills()
        skill_names = {s.name for s in skills}

        assert len(skills) == 2
        assert "local-skill" in skill_names
        assert "mcp-skill" in skill_names


# =============================================================================
# Test Class: SkillNameCollisionResolution
# =============================================================================


@pytest.mark.integration
class TestSkillNameCollisionResolution:
    """Test skill name collision resolution by provider priority."""

    async def test_first_provider_wins_collision(
        self,
        mock_skill_collision_local: Skill,
        mock_skill_collision_mcp: Skill,
    ) -> None:
        """Test that when skills have same name, first provider's skill wins.

        AggregatingResourceProvider deduplicates by name with first-wins priority.
        Local provider is first, so local skill is kept and MCP is dropped.
        """
        local_provider = MockLocalResourceProvider(
            name="local", skills=[mock_skill_collision_local]
        )
        mcp_provider = MockMCPResourceProvider(name="mcp", skills=[mock_skill_collision_mcp])
        # Local provider is first, so its skill should win
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        skills = await aggregator.get_skills()

        # Only one skill should be present (deduplicated by name)
        assert len(skills) == 1
        # The surviving skill should be from local provider (first in order)
        assert skills[0].name == "shared-skill"
        assert skills[0].metadata["source"] == "local"

    async def test_provider_order_determines_priority(
        self,
        mock_skill_collision_local: Skill,
        mock_skill_collision_mcp: Skill,
    ) -> None:
        """Test that reversing provider order changes which skill wins.

        With MCP provider first, the MCP skill wins in name collision.
        """
        local_provider = MockLocalResourceProvider(
            name="local", skills=[mock_skill_collision_local]
        )
        mcp_provider = MockMCPResourceProvider(name="mcp", skills=[mock_skill_collision_mcp])
        # MCP provider is first this time
        aggregator = AggregatingResourceProvider(providers=[mcp_provider, local_provider])

        skills = await aggregator.get_skills()

        # Only one skill should be present (deduplicated by name)
        assert len(skills) == 1
        # The surviving skill should be from MCP provider (first in reversed order)
        assert skills[0].name == "shared-skill"
        assert skills[0].metadata["source"] == "mcp"


# =============================================================================
# Test Class: SignalPropagation
# =============================================================================


@pytest.mark.integration
class TestSignalPropagation:
    """Test change signal propagation through provider chain."""

    async def test_skills_changed_signal_propagation(self, mock_skill_local: Skill) -> None:
        """Test that skills_changed signals propagate from child to aggregate."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        # Track signals received by aggregator
        received_events: list[ResourceChangeEvent] = []

        async def on_skills_changed(event: ResourceChangeEvent) -> None:
            received_events.append(event)

        aggregator.skills_changed.connect(on_skills_changed)

        # Emit signal from child provider
        await local_provider.emit_skills_changed()

        # Verify signal was propagated
        assert len(received_events) == 1
        assert received_events[0].provider_name == "local"
        assert received_events[0].resource_type == "skills"

    async def test_tools_changed_signal_propagation(self, mock_tool_local: Tool) -> None:
        """Test that tools_changed signals propagate from child to aggregate."""
        local_provider = MockLocalResourceProvider(name="local", tools=[mock_tool_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        received_events: list[ResourceChangeEvent] = []

        async def on_tools_changed(event: ResourceChangeEvent) -> None:
            received_events.append(event)

        aggregator.tools_changed.connect(on_tools_changed)

        await local_provider.emit_tools_changed()

        assert len(received_events) == 1
        assert received_events[0].provider_name == "local"
        assert received_events[0].resource_type == "tools"

    async def test_prompts_changed_signal_propagation(self) -> None:
        """Test that prompts_changed signals propagate from child to aggregate."""
        mcp_provider = MockMCPResourceProvider(name="mcp")
        aggregator = AggregatingResourceProvider(providers=[mcp_provider])

        received_events: list[ResourceChangeEvent] = []

        async def on_prompts_changed(event: ResourceChangeEvent) -> None:
            received_events.append(event)

        aggregator.prompts_changed.connect(on_prompts_changed)

        await mcp_provider.emit_prompts_changed()

        assert len(received_events) == 1
        assert received_events[0].provider_name == "mcp"
        assert received_events[0].resource_type == "prompts"

    async def test_multiple_provider_signal_propagation(
        self,
        mock_skill_local: Skill,
        mock_skill_mcp: Skill,
    ) -> None:
        """Test signals from multiple providers all propagate to aggregate."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        mcp_provider = MockMCPResourceProvider(name="mcp", skills=[mock_skill_mcp])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        received_events: list[ResourceChangeEvent] = []

        async def on_skills_changed(event: ResourceChangeEvent) -> None:
            received_events.append(event)

        aggregator.skills_changed.connect(on_skills_changed)

        # Emit from both providers
        await local_provider.emit_skills_changed()
        await mcp_provider.emit_skills_changed()

        assert len(received_events) == 2
        provider_names = {e.provider_name for e in received_events}
        assert provider_names == {"local", "mcp"}

    async def test_signal_forwarded_to_external_listener(self, mock_skill_local: Skill) -> None:
        """Test that aggregate signals reach external listeners."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        # External listener tracking
        external_events: list[ResourceChangeEvent] = []

        async def external_listener(event: ResourceChangeEvent) -> None:
            external_events.append(event)

        # Connect to aggregate provider (simulating agent pool listener)
        aggregator.skills_changed.connect(external_listener)

        # Trigger from child
        await local_provider.emit_skills_changed()

        # Verify external listener received it
        assert len(external_events) == 1
        assert external_events[0].provider_kind == "custom"


# =============================================================================
# Test Class: AsyncContextManagerHandling
# =============================================================================


@pytest.mark.integration
class TestAsyncContextManagerHandling:
    """Test async context manager handling for providers."""

    async def test_local_provider_context_manager(self) -> None:
        """Test LocalResourceProvider-style context manager entry/exit."""
        provider = MockLocalResourceProvider(name="local")

        assert not provider.entered
        assert not provider.exited

        async with provider:
            assert provider.entered
            assert not provider.exited

        assert provider.entered
        assert provider.exited

    async def test_mcp_provider_context_manager(self) -> None:
        """Test MCPResourceProvider-style context manager entry/exit."""
        provider = MockMCPResourceProvider(name="mcp")

        assert not provider.entered
        assert not provider.exited

        async with provider:
            assert provider.entered
            assert not provider.exited

        assert provider.entered
        assert provider.exited

    async def test_aggregating_provider_context_manager(self) -> None:
        """Test AggregatingResourceProvider context manager delegates to children."""
        local_provider = MockLocalResourceProvider(name="local")
        mcp_provider = MockMCPResourceProvider(name="mcp")
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        # Aggregator doesn't require context manager, but children might
        # This tests that aggregator works with child providers that need cleanup

        # Before entering
        assert not local_provider.entered
        assert not mcp_provider.entered

        # Aggregator itself doesn't track enter/exit for children automatically
        # The lifecycle is managed by whoever creates the providers

        async with local_provider:
            async with mcp_provider:
                assert local_provider.entered
                assert mcp_provider.entered

        assert local_provider.exited
        assert mcp_provider.exited


# =============================================================================
# Test Class: ProviderLifecycle
# =============================================================================


@pytest.mark.integration
class TestProviderLifecycle:
    """Test provider lifecycle including enter/exit and signal cleanup."""

    async def test_signal_disconnection_on_provider_replace(self) -> None:
        """Test that old provider signals are disconnected when replaced."""
        old_provider = MockLocalResourceProvider(name="old")
        new_provider = MockLocalResourceProvider(name="new")

        aggregator = AggregatingResourceProvider(providers=[old_provider])

        received_events: list[ResourceChangeEvent] = []

        async def on_skills_changed(event: ResourceChangeEvent) -> None:
            received_events.append(event)

        aggregator.skills_changed.connect(on_skills_changed)

        # Verify old provider is connected
        await old_provider.emit_skills_changed()
        assert len(received_events) == 1

        # Replace providers
        aggregator.providers = [new_provider]

        # Old provider should no longer propagate
        await old_provider.emit_skills_changed()
        # Should still be 1, not 2
        assert len(received_events) == 1

        # New provider should propagate
        await new_provider.emit_skills_changed()
        assert len(received_events) == 2

    async def test_multiple_signal_types_isolation(self) -> None:
        """Test that different signal types don't interfere."""
        provider = MockLocalResourceProvider(name="test")
        aggregator = AggregatingResourceProvider(providers=[provider])

        skills_events: list[ResourceChangeEvent] = []
        tools_events: list[ResourceChangeEvent] = []

        async def on_skills(event: ResourceChangeEvent) -> None:
            skills_events.append(event)

        async def on_tools(event: ResourceChangeEvent) -> None:
            tools_events.append(event)

        aggregator.skills_changed.connect(on_skills)
        aggregator.tools_changed.connect(on_tools)

        # Emit only skills changed
        await provider.emit_skills_changed()

        assert len(skills_events) == 1
        assert len(tools_events) == 0

        # Emit only tools changed
        await provider.emit_tools_changed()

        assert len(skills_events) == 1
        assert len(tools_events) == 1


# =============================================================================
# Test Class: EndToEndSkillResolution
# =============================================================================


@pytest.mark.integration
class TestEndToEndSkillResolution:
    """Test end-to-end skill resolution with multiple providers."""

    async def test_skills_from_local_and_mcp_combined(
        self,
        mock_skill_local: Skill,
        mock_skill_mcp: Skill,
    ) -> None:
        """Test that skills from both Local and MCP providers are available."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        mcp_provider = MockMCPResourceProvider(name="mcp", skills=[mock_skill_mcp])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        skills = await aggregator.get_skills()
        skill_map = {s.name: s for s in skills}

        assert "local-skill" in skill_map
        assert "mcp-skill" in skill_map
        assert skill_map["local-skill"].metadata["source"] == "local"
        assert skill_map["mcp-skill"].metadata["source"] == "mcp"

    async def test_tools_from_multiple_providers(
        self, mock_tool_local: Tool, mock_tool_mcp: Tool
    ) -> None:
        """Test that tools from multiple providers are aggregated."""
        local_provider = MockLocalResourceProvider(name="local", tools=[mock_tool_local])
        mcp_provider = MockMCPResourceProvider(name="mcp", tools=[mock_tool_mcp])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        tools = await aggregator.get_tools()

        assert len(tools) == 2

    async def test_complete_provider_chain_integration(
        self,
        mock_skill_local: Skill,
        mock_skill_mcp: Skill,
        mock_tool_local: Tool,
        mock_tool_mcp: Tool,
    ) -> None:
        """Test complete integration: Local + MCP -> Aggregating -> Signals."""
        local_provider = MockLocalResourceProvider(
            name="local", skills=[mock_skill_local], tools=[mock_tool_local]
        )
        mcp_provider = MockMCPResourceProvider(
            name="mcp", skills=[mock_skill_mcp], tools=[mock_tool_mcp]
        )
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        # Track all signal types
        all_events: list[ResourceChangeEvent] = []

        async def track_all(event: ResourceChangeEvent) -> None:
            all_events.append(event)

        aggregator.skills_changed.connect(track_all)
        aggregator.tools_changed.connect(track_all)

        # Verify initial state
        skills = await aggregator.get_skills()
        tools = await aggregator.get_tools()

        assert len(skills) == 2
        assert len(tools) == 2

        # Verify signal propagation works
        await local_provider.emit_skills_changed()
        await mcp_provider.emit_tools_changed()

        assert len(all_events) == 2
        event_types = {e.resource_type for e in all_events}
        assert event_types == {"skills", "tools"}

    async def test_signal_chain_child_to_aggregate_to_listener(
        self, mock_skill_local: Skill
    ) -> None:
        """Test full signal chain: child -> aggregate -> external listener."""
        local_provider = MockLocalResourceProvider(name="local", skills=[mock_skill_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        # External listener (e.g., agent pool or UI component)
        external_received: list[dict[str, Any]] = []

        async def external_listener(event: ResourceChangeEvent) -> None:
            external_received.append({
                "provider": event.provider_name,
                "kind": event.provider_kind,
                "type": event.resource_type,
            })

        aggregator.skills_changed.connect(external_listener)

        # Trigger from the bottom
        await local_provider.emit_skills_changed()

        # Verify it reached the top
        assert len(external_received) == 1
        assert external_received[0]["provider"] == "local"
        assert external_received[0]["kind"] == "custom"
        assert external_received[0]["type"] == "skills"


# =============================================================================
# Test Class: ProviderPropertyManagement
# =============================================================================


@pytest.mark.integration
class TestProviderPropertyManagement:
    """Test the providers property setter and signal management."""

    async def test_providers_property_getter(self) -> None:
        """Test that providers property returns the list of providers."""
        local = MockLocalResourceProvider(name="local")
        mcp = MockMCPResourceProvider(name="mcp")
        aggregator = AggregatingResourceProvider(providers=[local, mcp])

        providers = aggregator.providers

        assert len(providers) == 2
        assert providers[0] is local
        assert providers[1] is mcp

    async def test_providers_property_setter_replaces_providers(self) -> None:
        """Test that setting providers replaces the entire list."""
        old_provider = MockLocalResourceProvider(name="old")
        new_provider = MockLocalResourceProvider(name="new")

        aggregator = AggregatingResourceProvider(providers=[old_provider])
        assert len(aggregator.providers) == 1

        aggregator.providers = [new_provider]
        assert len(aggregator.providers) == 1
        assert aggregator.providers[0] is new_provider

    async def test_signal_reconnection_on_provider_change(self) -> None:
        """Test that signals are properly reconnected when providers change."""
        provider1 = MockLocalResourceProvider(name="p1")
        provider2 = MockLocalResourceProvider(name="p2")

        aggregator = AggregatingResourceProvider(providers=[provider1])

        events: list[str] = []

        async def on_change(event: ResourceChangeEvent) -> None:
            events.append(event.provider_name)

        aggregator.skills_changed.connect(on_change)

        # First provider works
        await provider1.emit_skills_changed()
        assert events == ["p1"]

        # Switch providers
        aggregator.providers = [provider2]

        # Old provider no longer triggers
        await provider1.emit_skills_changed()
        assert events == ["p1"]  # Still just one

        # New provider triggers
        await provider2.emit_skills_changed()
        assert events == ["p1", "p2"]


# =============================================================================
# Test Class: ToolModeCodemode
# =============================================================================


@pytest.mark.integration
class TestToolModeCodemode:
    """Test tool_mode="codemode" behavior in AggregatingResourceProvider."""

    async def test_tool_mode_default_none(self, mock_tool_local: MagicMock) -> None:
        """Test that default tool_mode is None (no codemode wrapping)."""
        local_provider = MockLocalResourceProvider(name="local", tools=[mock_tool_local])
        aggregator = AggregatingResourceProvider(providers=[local_provider])

        tools = await aggregator.get_tools()

        # Should return tools directly without codemode wrapping
        assert len(tools) == 1
        assert tools[0] is mock_tool_local

    async def test_codemode_provider_configured(self) -> None:
        """Test that codemode tool_mode is properly stored."""
        local_provider = MockLocalResourceProvider(name="local")
        aggregator = AggregatingResourceProvider(providers=[local_provider], tool_mode="codemode")

        # Verify tool_mode is stored
        assert aggregator.tool_mode == "codemode"
        # Codemode provider should be None initially (lazy creation)
        assert aggregator._codemode_provider is None


# =============================================================================
# Test Class: ErrorHandling
# =============================================================================


@pytest.mark.integration
class TestErrorHandling:
    """Test error handling in provider aggregation."""

    async def test_get_request_parts_not_found(self) -> None:
        """Test that KeyError is raised when prompt not found in any provider."""
        local_provider = MockLocalResourceProvider(name="local")
        mcp_provider = MockMCPResourceProvider(name="mcp")
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        with pytest.raises(KeyError, match="Prompt 'nonexistent' not found"):
            await aggregator.get_request_parts("nonexistent")

    async def test_empty_providers_signal_still_works(self) -> None:
        """Test that signals work even with no child providers."""
        aggregator = AggregatingResourceProvider(providers=[], name="empty")

        # Should be able to emit without error
        event = aggregator.create_change_event("skills")
        await aggregator.skills_changed.emit(event)

        # No assertion needed - just verifying no exception


# =============================================================================
# Test Class: ResourceAggregation
# =============================================================================


@pytest.mark.integration
class TestResourceAggregation:
    """Test resource aggregation from multiple providers."""

    async def test_prompts_aggregated_from_all_providers(self) -> None:
        """Test that prompts are combined from all providers."""
        mock_prompt1 = MagicMock(name="prompt1")
        mock_prompt2 = MagicMock(name="prompt2")

        local_provider = MockLocalResourceProvider(name="local", prompts=[mock_prompt1])
        mcp_provider = MockMCPResourceProvider(name="mcp", prompts=[mock_prompt2])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        prompts = await aggregator.get_prompts()

        assert len(prompts) == 2

    async def test_resources_aggregated_from_all_providers(self) -> None:
        """Test that resources are combined from all providers."""
        mock_resource1 = MagicMock(name="resource1")
        mock_resource2 = MagicMock(name="resource2")

        local_provider = MockLocalResourceProvider(name="local", resources=[mock_resource1])
        mcp_provider = MockMCPResourceProvider(name="mcp", resources=[mock_resource2])
        aggregator = AggregatingResourceProvider(providers=[local_provider, mcp_provider])

        resources = await aggregator.get_resources()

        assert len(resources) == 2

    def as_capability(self) -> AbstractCapability | None:
        """Return a pydantic-ai capability for this provider.

        Returns:
            A pydantic-ai AbstractCapability instance, or None.
        """
        return None
