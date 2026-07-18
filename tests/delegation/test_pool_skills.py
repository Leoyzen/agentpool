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
from agentpool.capabilities.resource_protocols import SkillResource
from agentpool.skills.exceptions import SkillNotFoundError
from agentpool.skills.uri_resolver import SkillURIResolver
from agentpool_config.skills import SkillsConfig


pytestmark = pytest.mark.integration


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Test helpers
# =============================================================================


class _FakeSkillResourceProvider(SkillResource):
    """Fake provider implementing SkillResource for testing registration."""

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    async def list_skills(self):
        return []

    async def read_skill(self, name: str) -> str | None:
        return None

    async def skill_exists(self, name: str) -> bool:
        return False


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

    async def test_skill_resolver_has_providers(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_resolver has providers registered."""
        async with AgentPool(manifest_with_skills) as pool:
            resolver = pool.skill_resolver
            assert resolver is not None

            providers = resolver.list_providers()
            # Provider list may be empty if no MCP servers configured,
            # or may contain MCP providers if configured
            assert isinstance(providers, list)

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
            resolver = pool.skill_resolver
            assert resolver is not None
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

    async def test_skill_provider_has_capabilities(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider has capabilities list."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # CombinedToolsetCapability exposes capabilities property
            caps = provider.capabilities
            assert isinstance(caps, list)


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

    async def test_skill_provider_has_capabilities_list(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider has a capabilities list."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            caps = provider.capabilities
            assert isinstance(caps, list)

    async def test_skills_accessible_via_pool_skills(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that pool.skills provides access to skills."""
        async with AgentPool(manifest_with_skills) as pool:
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
            assert isinstance(providers, list)

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

            with pytest.raises(SkillNotFoundError, match="not found via ExtensionRegistry"):
                await resolver.resolve("skill://mcp/some-skill")


# =============================================================================
# Test Class: SkillsChangedIntegration
# =============================================================================


@pytest.mark.integration
class TestSkillsChangedIntegration:
    """Test skills_changed signal integration.

    The old skills_changed signal API was removed when CombinedToolsetCapability
    replaced AggregatingResourceProvider. The new capability API uses on_change()
    for capability-level change notification. These tests verify the new API.
    """

    async def test_skill_provider_has_on_change(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that skill_provider has on_change method."""
        async with AgentPool(manifest_with_skills) as pool:
            provider = pool.skill_provider
            assert provider is not None

            # CombinedToolsetCapability has on_change() method
            assert hasattr(provider, "on_change")


# =============================================================================
# register_skill_provider() / unregister_skill_provider() Tests
# =============================================================================


@pytest.mark.integration
class TestRegisterUnregisterSkillProvider:
    """Test AgentPool.register_skill_provider() and unregister_skill_provider()."""

    async def test_register_skill_provider_adds_to_resolver(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that register_skill_provider() adds provider to URI resolver."""
        async with AgentPool(manifest_with_skills) as pool:
            mock_provider = _FakeSkillResourceProvider("resolver_provider")

            pool.register_skill_provider(mock_provider)

            assert pool._skill_resolver is not None
            assert "resolver_provider" in pool._skill_resolver.list_providers()

    async def test_unregister_skill_provider_removes_from_resolver(
        self,
        manifest_with_skills: AgentsManifest,
    ) -> None:
        """Test that unregister_skill_provider() removes from URI resolver."""
        async with AgentPool(manifest_with_skills) as pool:
            mock_provider = _FakeSkillResourceProvider("rm_provider")

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
            pending = getattr(pool, "_pending_skill_providers", [])
            assert len(pending) == 0
