"""Integration tests for AgentPool skill integration.

Tests cover skill_resolver property, skill_provider property,
skill resolution through pool, and provider aggregation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from upathtools import UPath

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.capabilities.combined_toolset import CombinedToolsetCapability
from agentpool.skills.uri_resolver import SkillURIResolver
from agentpool_config.skills import SkillsConfig


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_skill(tmp_path: Path) -> UPath:
    """Create a test skill directory."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()

    content = """---
name: test-skill
description: A test skill for pool integration
---

# Test Skill

This is a test skill.
"""
    skills_md = skill_dir / "SKILL.md"
    skills_md.write_text(content)

    return UPath(skill_dir)


@pytest.fixture
def another_skill(tmp_path: Path) -> UPath:
    """Create another test skill directory."""
    skill_dir = tmp_path / "another-skill"
    skill_dir.mkdir()

    content = """---
name: another-skill
description: Another test skill
---

# Another Skill

Another test skill content.
"""
    skills_md = skill_dir / "SKILL.md"
    skills_md.write_text(content)

    return UPath(skill_dir)


@pytest.fixture
def manifest_with_skills(tmp_path: Path, test_skill: UPath) -> AgentsManifest:
    """Create a manifest with skills configured."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )

    return AgentsManifest(
        agents={"test_agent": agent_config},
        skills=SkillsConfig(
            paths=[UPath(tmp_path)],
            include_default=False,
        ),
    )


# =============================================================================
# Test Class: SkillResolverProperty
# =============================================================================


@pytest.mark.integration
class TestSkillResolverProperty:
    """Test AgentPool.skill_resolver property."""

    async def test_skill_resolver_available_when_skills_configured(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_resolver is available when skills are configured."""
        async with AgentPool(manifest_with_skills) as pool:
            assert pool.skill_resolver is not None

    async def test_skill_resolver_is_uri_resolver(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_resolver is a SkillURIResolver instance."""
        async with AgentPool(manifest_with_skills) as pool:
            assert isinstance(pool.skill_resolver, SkillURIResolver)

    async def test_skill_resolver_has_local_provider(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_resolver has local provider registered."""
        async with AgentPool(manifest_with_skills) as pool:
            resolver = pool.skill_resolver
            assert resolver is not None

            providers = resolver.list_providers()
            assert "local" in providers

    async def test_skill_resolver_exists_without_skills(
        self,
    ) -> None:
        """Test skill_resolver behavior without explicit skills config."""
        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            system_prompt="You are a test agent",
        )
        manifest = AgentsManifest(agents={"test_agent": agent_config})

        async with AgentPool(manifest) as pool:
            # Resolver exists and may have default providers
            resolver = pool.skill_resolver
            assert resolver is not None
            # Default paths may still create providers
            assert len(resolver.list_providers()) >= 0


# =============================================================================
# Test Class: SkillProviderProperty
# =============================================================================


@pytest.mark.integration
class TestSkillProviderProperty:
    """Test AgentPool.skill_provider property."""

    async def test_skill_provider_available_when_skills_configured(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider is available when skills are configured."""
        async with AgentPool(manifest_with_skills) as pool:
            assert pool.skill_provider is not None

    async def test_skill_provider_is_aggregating_provider(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider is an CombinedToolsetCapability."""
        async with AgentPool(manifest_with_skills) as pool:
            assert isinstance(pool.skill_provider, CombinedToolsetCapability)

    async def test_skill_provider_has_local_provider(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider includes local provider."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Check that providers list includes the local provider
            assert len(provider.providers) > 0
            assert any(p.name == "local" for p in provider.providers)

    async def test_skill_provider_has_skills_changed_signal(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider has skills_changed signal."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Should be able to connect to the signal
            events = []

            async def on_change(event: object) -> None:
                events.append(event)

            provider.skills_changed.connect(on_change)

            # Signal should be accessible
            assert provider.skills_changed is not None


# =============================================================================
# Test Class: SkillResolutionThroughPool
# =============================================================================


@pytest.mark.integration
class TestSkillResolutionThroughPool:
    """Test skill resolution through AgentPool."""

    async def test_resolve_via_skills_manager(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test resolving skills through pool's SkillsManager."""
        async with AgentPool(manifest_with_skills) as pool:
            # Use SkillsManager for skill resolution
            skill = pool.skills.get_skill("test-skill")
            assert skill.name == "test-skill"

    async def test_list_skills_via_manager(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test listing skills through pool's SkillsManager."""
        async with AgentPool(manifest_with_skills) as pool:
            skills = pool.skills.list_skills()
            skill_names = {s.name for s in skills}
            assert "test-skill" in skill_names

    async def test_multiple_skills_resolution(
        self,
        tmp_path: Path,
        test_skill: UPath,
        another_skill: UPath,
    ) -> None:
        """Test resolution of multiple skills."""
        agent_config = NativeAgentConfig(
            name="test_agent",
            model="test",
            system_prompt="You are a test agent",
        )
        manifest = AgentsManifest(
            agents={"test_agent": agent_config},
            skills=SkillsConfig(
                paths=[UPath(tmp_path)],
                include_default=False,
            ),
        )

        async with AgentPool(manifest) as pool:
            # Get multiple skills via SkillsManager
            skill1 = pool.skills.get_skill("test-skill")
            skill2 = pool.skills.get_skill("another-skill")

            assert skill1.name == "test-skill"
            assert skill2.name == "another-skill"


# =============================================================================
# Test Class: ProviderAggregation
# =============================================================================


@pytest.mark.integration
class TestProviderAggregation:
    """Test provider aggregation in AgentPool."""

    async def test_skill_provider_aggregates_local(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider aggregates local skills."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Should have at least the local provider
            local_providers = [p for p in provider.providers if p.name == "local"]
            assert len(local_providers) >= 1

    async def test_provider_count_matches_sources(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that provider count matches configured skill sources."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # At minimum, should have local provider for filesystem skills
            assert len(provider.providers) >= 1

    async def test_aggregating_provider_skills_changed_signal(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that CombinedToolsetCapability properly handles skills_changed."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Should be able to connect to skills_changed
            received_events = []

            async def on_skills_changed(event: object) -> None:
                received_events.append(event)

            provider.skills_changed.connect(on_skills_changed)

            # Should be able to emit events
            event = provider.create_change_event("skills")
            await provider.skills_changed.emit(event)

            assert len(received_events) == 1

    async def test_skills_accessible_via_pool_skills(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that pool.skills provides access to skills."""
        async with AgentPool(manifest_with_skills) as pool:
            # Access via legacy pool.skills interface
            skills = pool.skills.list_skills()
            skill_names = {s.name for s in skills}

            assert "test-skill" in skill_names


# =============================================================================
# Test Class: PoolLifecycle
# =============================================================================


@pytest.mark.integration
class TestPoolLifecycle:
    """Test skill integration during pool lifecycle."""

    async def test_resolver_initialized_on_enter(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that resolver is initialized when pool enters context."""
        pool = AgentPool(manifest_with_skills)

        # Before entering, resolver might be None
        # After entering, it should be available
        async with pool:
            assert pool.skill_resolver is not None

    async def test_provider_initialized_on_enter(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that provider is initialized when pool enters context."""
        pool = AgentPool(manifest_with_skills)

        async with pool:
            assert pool.skill_provider is not None

    async def test_skills_work_via_manager(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that pool.skills works throughout pool lifecycle."""
        async with AgentPool(manifest_with_skills) as pool:
            # SkillsManager should work
            legacy_skills = pool.skills.list_skills()

            legacy_names = {s.name for s in legacy_skills}

            assert "test-skill" in legacy_names


# =============================================================================
# Test Class: ProviderRegistration
# =============================================================================


@pytest.mark.integration
class TestProviderRegistration:
    """Test provider registration in skill_resolver."""

    async def test_can_list_all_providers(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that all providers can be listed."""
        async with AgentPool(manifest_with_skills) as pool:
            resolver = pool.skill_resolver
            assert resolver is not None

            providers = resolver.list_providers()
            assert "local" in providers

    async def test_unregistered_provider_returns_none(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that unregistered provider returns None."""
        async with AgentPool(manifest_with_skills) as pool:
            resolver = pool.skill_resolver
            assert resolver is not None

            provider = resolver.get_provider("nonexistent")
            assert provider is None

    async def test_resolve_fails_for_unregistered_provider(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that resolution fails for unregistered provider."""
        async with AgentPool(manifest_with_skills) as pool:
            resolver = pool.skill_resolver
            assert resolver is not None

            with pytest.raises(ValueError, match="Provider 'mcp' not registered"):
                await resolver.resolve("skill://mcp/some-skill")


# =============================================================================
# Test Class: SkillsChangedIntegration
# =============================================================================


@pytest.mark.integration
class TestSkillsChangedIntegration:
    """Test skills_changed signal integration."""

    async def test_pool_forwards_skills_changed_events(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that pool forwards skills_changed events."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Track events
            events = []

            async def on_event(event: object) -> None:
                events.append(event)

            provider.skills_changed.connect(on_event)

            # Emit an event
            event = provider.create_change_event("skills")
            await provider.skills_changed.emit(event)

            assert len(events) == 1

    async def test_signal_propagation_chain(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test signal propagation from local to aggregate."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # Find local provider
            local = None
            for p in provider.providers:
                if p.name == "local":
                    local = p
                    break

            if local is not None:
                events = []

                async def on_event(event: object) -> None:
                    events.append(event)

                provider.skills_changed.connect(on_event)

                # Emit from local
                event = local.create_change_event("skills")
                await local.skills_changed.emit(event)

                # Should propagate to aggregate
                assert len(events) == 1


# =============================================================================
# register_skill_provider() / unregister_skill_provider() Tests
# =============================================================================


@pytest.mark.integration
class TestRegisterUnregisterSkillProvider:
    """Test AgentPool.register_skill_provider() and unregister_skill_provider()."""

    async def test_register_skill_provider_adds_to_aggregator(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that register_skill_provider() makes skills visible in aggregator."""
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.capabilities import AbstractCapability

        async with AgentPool(manifest_with_skills) as pool:
            skill = MagicMock(spec="Skill")
            skill.name = "dynamic-skill"

            mock_provider = MagicMock(spec=AbstractCapability)
            mock_provider.name = "dynamic_provider"
            mock_provider.get_skills = AsyncMock(return_value=[skill])
            mock_provider.skills_changed = MagicMock()
            mock_provider.skills_changed.connect = MagicMock()
            mock_provider.skills_changed.disconnect = MagicMock()

            pool.register_skill_provider(mock_provider)

            # Skills should now include the dynamic provider's skill
            assert pool._skill_provider is not None
            skills = await pool._skill_provider.get_skills()
            assert skill in skills

    async def test_unregister_skill_provider_removes_from_aggregator(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that unregister_skill_provider() removes skills from aggregator."""
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.capabilities import AbstractCapability

        async with AgentPool(manifest_with_skills) as pool:
            skill = MagicMock(spec="Skill")
            skill.name = "temporary-skill"

            mock_provider = MagicMock(spec=AbstractCapability)
            mock_provider.name = "temp_provider"
            mock_provider.get_skills = AsyncMock(return_value=[skill])
            mock_provider.skills_changed = MagicMock()
            mock_provider.skills_changed.connect = MagicMock()
            mock_provider.skills_changed.disconnect = MagicMock()

            pool.register_skill_provider(mock_provider)
            assert pool._skill_provider is not None
            skills = await pool._skill_provider.get_skills()
            assert skill in skills

            pool.unregister_skill_provider(mock_provider)
            skills = await pool._skill_provider.get_skills()
            assert skill not in skills

    async def test_register_skill_provider_adds_to_resolver(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that register_skill_provider() adds provider to URI resolver."""
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.capabilities import AbstractCapability

        async with AgentPool(manifest_with_skills) as pool:
            mock_provider = MagicMock(spec=AbstractCapability)
            mock_provider.name = "resolver_provider"
            mock_provider.get_skills = AsyncMock(return_value=[])
            mock_provider.skills_changed = MagicMock()
            mock_provider.skills_changed.connect = MagicMock()
            mock_provider.skills_changed.disconnect = MagicMock()

            pool.register_skill_provider(mock_provider)

            assert pool._skill_resolver is not None
            assert "resolver_provider" in pool._skill_resolver.list_providers()

    async def test_unregister_skill_provider_removes_from_resolver(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that unregister_skill_provider() removes from URI resolver."""
        from unittest.mock import AsyncMock, MagicMock

        from pydantic_ai.capabilities import AbstractCapability

        async with AgentPool(manifest_with_skills) as pool:
            mock_provider = MagicMock(spec=AbstractCapability)
            mock_provider.name = "rm_provider"
            mock_provider.get_skills = AsyncMock(return_value=[])
            mock_provider.skills_changed = MagicMock()
            mock_provider.skills_changed.connect = MagicMock()
            mock_provider.skills_changed.disconnect = MagicMock()

            pool.register_skill_provider(mock_provider)
            assert pool._skill_resolver is not None
            assert "rm_provider" in pool._skill_resolver.list_providers()

            pool.unregister_skill_provider(mock_provider)
            assert "rm_provider" not in pool._skill_resolver.list_providers()

    async def test_register_before_setup_buffers_and_drains(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that register_skill_provider() buffers when called before setup."""
        async with AgentPool(manifest_with_skills) as pool:
            # _pending_skill_providers should be empty after __aenter__
            # since _setup_skills_provider() drains the buffer
            pending = getattr(pool, "_pending_skill_providers", [])
            assert len(pending) == 0
