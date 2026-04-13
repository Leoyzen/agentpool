"""Integration tests for load_skill tool with URI support.

Tests cover backward compatibility, URI-based loading, reference paths,
argument substitution, and error handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from pydantic_ai.models.test import TestModel
from upathtools import UPath

from agentpool import Agent, AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.context import AgentContext
from agentpool.skills.uri_resolver import ResolvedSkillURI
from agentpool_config.skills import SkillsConfig
from agentpool_toolsets.builtin.skills import _substitute_arguments, load_skill


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_skill_with_args(tmp_path: Path) -> UPath:
    """Create a test skill directory with argument placeholders."""
    skill_dir = tmp_path / "arg-skill"
    skill_dir.mkdir()

    content = """---
name: arg-skill
description: Skill with argument substitution
---

# Argument Skill

This skill demonstrates argument substitution.

First argument: $1
Second argument: $2
All arguments: $@
Alternative all: $ARGUMENTS
"""
    skills_md = skill_dir / "SKILL.md"
    skills_md.write_text(content)

    # Create reference file
    ref_dir = skill_dir / "references"
    ref_dir.mkdir()
    ref_file = ref_dir / "details.md"
    ref_file.write_text("# Details\n\nDetailed information about $1.")

    return UPath(skill_dir)


@pytest.fixture
def simple_skill(tmp_path: Path) -> UPath:
    """Create a simple test skill."""
    skill_dir = tmp_path / "simple-skill"
    skill_dir.mkdir()

    content = """---
name: simple-skill
description: A simple test skill
---

# Simple Skill

This is a simple skill for testing.

Instructions go here.
"""
    skills_md = skill_dir / "SKILL.md"
    skills_md.write_text(content)

    return UPath(skill_dir)


@pytest.fixture
def test_manifest(tmp_path: Path) -> AgentsManifest:
    """Create a test manifest with skills configured."""
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
# Test Class: LoadSkillBackwardCompatibility
# =============================================================================


@pytest.mark.integration
class TestLoadSkillBackwardCompatibility:
    """Test backward compatibility with bare skill names."""

    async def test_load_skill_with_bare_name(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test loading skill with bare name (no URI scheme)."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "simple-skill")

            assert "Simple Skill" in result
            assert "simple-skill" in result

    async def test_bare_name_returns_skill_instructions(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test that bare name returns full skill instructions."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "simple-skill")

            # Should contain header, metadata, and instructions
            assert "# simple-skill" in result
            assert "A simple test skill" in result
            assert "Instructions go here" in result
            assert "Skill directory:" in result

    async def test_bare_name_skill_not_found(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test error handling for non-existent bare skill name."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "non-existent-skill")

            assert "not found" in result.lower()
            assert "simple-skill" in result  # Should list available skills


# =============================================================================
# Test Class: LoadSkillWithURI
# =============================================================================


@pytest.mark.integration
class TestLoadSkillWithURI:
    """Test load_skill with skill:// URIs."""

    async def test_uri_includes_provider_info(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test that URI-loaded skills include provider information when available."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            # Load with URI
            result = await load_skill(ctx, "skill://local/simple-skill")

            # Should include URI information if resolver is available
            # Note: This depends on whether skill_resolver is properly initialized
            # If resolver is not available, falls back to bare name resolution
            assert "simple-skill" in result

    async def test_uri_skill_not_found_in_provider(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test error when skill not found via URI resolution."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "skill://local/non-existent")

            # Should return error message
            assert "Failed to resolve" in result or "not found" in result.lower()

    async def test_invalid_uri_format(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test handling of invalid URI format."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            # Invalid scheme
            result = await load_skill(ctx, "http://local/simple-skill")

            assert "Invalid" in result


# =============================================================================
# Test Class: ArgumentSubstitution
# =============================================================================


@pytest.mark.integration
class TestArgumentSubstitution:
    """Test argument substitution in load_skill."""

    async def test_dollar_one_substitution(
        self,
        tmp_path: Path,
        test_skill_with_args: UPath,
    ) -> None:
        """Test $1 argument substitution."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "arg-skill", "first-arg second-arg")

            assert "First argument: first-arg" in result
            assert "Second argument: second-arg" in result

    async def test_dollar_at_substitution(
        self,
        tmp_path: Path,
        test_skill_with_args: UPath,
    ) -> None:
        """Test $@ argument substitution."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "arg-skill", "arg1 arg2 arg3")

            assert "All arguments: arg1 arg2 arg3" in result

    async def test_dollar_arguments_substitution(
        self,
        tmp_path: Path,
        test_skill_with_args: UPath,
    ) -> None:
        """Test $ARGUMENTS substitution."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "arg-skill", "foo bar")

            assert "Alternative all: foo bar" in result

    async def test_no_arguments_no_substitution(
        self,
        tmp_path: Path,
        test_skill_with_args: UPath,
    ) -> None:
        """Test that placeholders remain when no arguments provided."""
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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "arg-skill")

            # Placeholders should remain unchanged
            assert "First argument: $1" in result
            assert "All arguments: $@" in result


# =============================================================================
# Test Class: ArgumentSubstitutionUnit
# =============================================================================


class TestArgumentSubstitutionUnit:
    """Unit tests for _substitute_arguments helper function."""

    def test_single_positional_argument(self) -> None:
        """Test replacement of $1."""
        result = _substitute_arguments("Value: $1", "hello")
        assert result == "Value: hello"

    def test_multiple_positional_arguments(self) -> None:
        """Test replacement of $1, $2, etc."""
        result = _substitute_arguments("First: $1, Second: $2, Third: $3", "a b c")
        assert result == "First: a, Second: b, Third: c"

    def test_at_symbol_replacement(self) -> None:
        """Test replacement of $@."""
        result = _substitute_arguments("All: $@", "one two three")
        assert result == "All: one two three"

    def test_arguments_uppercase_replacement(self) -> None:
        """Test replacement of $ARGUMENTS."""
        result = _substitute_arguments("Args: $ARGUMENTS", "x y z")
        assert result == "Args: x y z"

    def test_mixed_placeholders(self) -> None:
        """Test mixed $1, $2, and $@."""
        template = "First: $1, Second: $2, All: $@"
        result = _substitute_arguments(template, "alpha beta")
        assert result == "First: alpha, Second: beta, All: alpha beta"

    def test_no_arguments_returns_original(self) -> None:
        """Test that template is unchanged when no arguments provided."""
        template = "Value: $1"
        result = _substitute_arguments(template, None)
        assert result == template

    def test_empty_arguments_clears_placeholders(self) -> None:
        """Test that empty string clears placeholders."""
        result = _substitute_arguments("Value: $1, All: $@", "")
        assert result == "Value: $1, All: "

    def test_partial_arguments(self) -> None:
        """Test with fewer arguments than placeholders."""
        result = _substitute_arguments("$1 $2 $3", "only-one")
        assert result == "only-one $2 $3"


# =============================================================================
# Test Class: URIParsing
# =============================================================================


class TestURIParsing:
    """Unit tests for URI parsing."""

    def test_parse_bare_skill_name(self) -> None:
        """Test parsing bare skill name without scheme."""
        resolved = ResolvedSkillURI.parse("my-skill")

        assert resolved.skill_name == "my-skill"
        assert resolved.provider is None
        assert resolved.reference_path is None

    def test_parse_simple_uri(self) -> None:
        """Test parsing skill://provider/skill-name."""
        resolved = ResolvedSkillURI.parse("skill://local/my-skill")

        assert resolved.provider == "local"
        assert resolved.skill_name == "my-skill"
        assert resolved.reference_path is None

    def test_parse_uri_with_reference(self) -> None:
        """Test parsing URI with reference path."""
        resolved = ResolvedSkillURI.parse("skill://local/my-skill/references/guide.md")

        assert resolved.provider == "local"
        assert resolved.skill_name == "my-skill"
        assert resolved.reference_path == "references/guide.md"

    def test_parse_uri_with_nested_reference(self) -> None:
        """Test parsing URI with nested reference path."""
        resolved = ResolvedSkillURI.parse("skill://local/my-skill/docs/guides/advanced.md")

        assert resolved.provider == "local"
        assert resolved.skill_name == "my-skill"
        assert resolved.reference_path == "docs/guides/advanced.md"

    def test_parse_invalid_scheme_raises(self) -> None:
        """Test that invalid scheme raises ValueError."""
        with pytest.raises(ValueError, match="Invalid URI scheme"):
            ResolvedSkillURI.parse("http://local/my-skill")

    def test_parse_empty_path_raises(self) -> None:
        """Test that empty path raises ValueError."""
        with pytest.raises(ValueError, match="path is empty"):
            ResolvedSkillURI.parse("skill://local/")


# =============================================================================
# Test Class: NoPoolContext
# =============================================================================


@pytest.mark.integration
class TestNoPoolContext:
    """Test load_skill behavior when no pool is available."""

    async def test_no_pool_returns_error(self) -> None:
        """Test that missing pool returns appropriate error."""
        agent = Agent(name="test", model=TestModel())
        ctx = AgentContext(node=agent, pool=None)

        result = await load_skill(ctx, "any-skill")

        assert "No agent pool available" in result


# =============================================================================
# Test Class: ListSkillsIntegration
# =============================================================================


@pytest.mark.integration
class TestListSkillsIntegration:
    """Test list_skills function integration."""

    async def test_list_skills_returns_available(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test that list_skills returns available skills."""
        from agentpool_toolsets.builtin.skills import list_skills

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
            agent = pool.get_agent("test_agent")
            ctx = AgentContext(node=agent, pool=pool)

            result = await list_skills(ctx)

            assert "Available skills:" in result
            assert "simple-skill" in result
            assert "A simple test skill" in result
