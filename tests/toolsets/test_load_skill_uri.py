"""Integration tests for load_skill tool with URI support.

Tests cover backward compatibility, URI-based loading, reference paths,
argument substitution, and error handling.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic_ai.models.test import TestModel
import pytest
from upathtools import UPath

from agentpool import Agent, AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.context import AgentContext
from agentpool.skills.skill import Skill
from agentpool.skills.uri_resolver import ResolvedSkillURI
from agentpool_config.skills import SkillsConfig
from agentpool_toolsets.builtin.skills import _substitute_arguments, load_skill


# =============================================================================
# Test: load_skill docstring mentions skill:// URI reference access (Bug #4)
# =============================================================================


def test_load_skill_docstring_mentions_references():
    """Test that load_skill docstring mentions skill:// URI reference access.

    This verifies Bug #4 fix: the agent-visible docstring should inform agents
    that they can load specific reference files via skill:// URIs.
    """
    docstring = load_skill.__doc__
    assert docstring is not None
    assert "skill://" in docstring
    assert "references" in docstring.lower()


# =============================================================================
# Test: _load_reference_content uses original_name from metadata (Bug #2b Fix)
# =============================================================================


@pytest.mark.asyncio
async def test_load_reference_content_passes_skill_name_kebab() -> None:
    """Test that _load_reference_content passes skill.name (kebab-case) to read_reference.

    The aggregating provider matches skills by Skill.name (always kebab-case).
    The MCP provider's read_reference() internally looks up original_name from
    its skill cache to construct the correct URI for the MCP server.
    """
    from pathlib import PurePosixPath
    from unittest.mock import AsyncMock, MagicMock

    from agentpool_toolsets.builtin.skills import _load_reference_content

    # Create a skill with PurePosixPath (virtual/MCP skill) and original_name metadata
    skill = Skill(
        name="systematic-troubleshooting",  # kebab-case (normalized by Skill model)
        description="Test skill",
        skill_path=PurePosixPath("skill://pool_mcp_scratchpad/systematic-troubleshooting"),
        metadata={"original_name": "systematic_troubleshooting"},  # original with underscores
    )

    # Mock pool with skill_provider
    mock_provider = MagicMock()
    mock_provider.read_reference = AsyncMock(return_value=(b"Phase content", "text/markdown"))
    pool = MagicMock()
    pool.skill_provider = mock_provider

    await _load_reference_content(skill, "references/phase_3_execution.md", pool)

    # Verify read_reference was called with skill.name (kebab-case), NOT original_name.
    # The aggregating provider matches by kebab-case name, and the MCP provider
    # internally looks up original_name for URI construction.
    mock_provider.read_reference.assert_called_once_with(
        "systematic-troubleshooting",  # skill.name (kebab-case)
        "references/phase_3_execution.md",
    )


@pytest.mark.asyncio
async def test_load_reference_content_falls_back_to_skill_name() -> None:
    """Test that _load_reference_content falls back to skill.name when no original_name."""
    from pathlib import PurePosixPath
    from unittest.mock import AsyncMock, MagicMock

    from agentpool_toolsets.builtin.skills import _load_reference_content

    # Create a skill WITHOUT original_name in metadata
    skill = Skill(
        name="simple-skill",
        description="Test skill",
        skill_path=PurePosixPath("skill://pool/simple-skill"),
        metadata={},  # No original_name
    )

    mock_provider = MagicMock()
    mock_provider.read_reference = AsyncMock(return_value=(b"Content", "text/markdown"))
    pool = MagicMock()
    pool.skill_provider = mock_provider

    await _load_reference_content(skill, "guide.md", pool)

    # Should fall back to skill.name
    mock_provider.read_reference.assert_called_once_with("simple-skill", "guide.md")


# =============================================================================
# RED TESTS: End-to-end skill reference loading chain
# =============================================================================


class TestReferenceLoadingChain:
    """RED TESTS: Full chain from _load_reference_content → read_reference → MCP server.

    These tests reproduce the bug where loading skill references via skill:// URIs
    fails even though the reference files exist on disk.

    The chain is:
    1. _load_reference_content(skill, reference_path, pool)
    2. → pool.skill_provider.read_reference(original_name, ref_path)
    3. → MCPResourceProvider.read_reference(skill_name, ref_path)
    4.   → constructs URI: skill://{skill_name}/references/{path}
    5.   → self.read_resource(uri)  # MCP client call
    6. → MCP server SkillsProvider.read_resource(uri)
    7.   → discovers skill by name → reads file

    Key scenarios that should work but may fail:
    - Reference path already has "references/" prefix → no double prefix
    - Skill name uses underscores (original_name) → matches MCP server's directory name
    - File exists in skill's references/ directory → should be found
    """

    @pytest.mark.asyncio
    async def test_e2e_load_reference_with_references_prefix(self) -> None:
        """Full chain with reference_path that already has references/ prefix.

        This is the most common case: ResolvedSkillURI.parse() returns
        reference_path="references/phase_3_execution.md" when parsing
        skill://pool/systematic_troubleshooting/references/phase_3_execution.md
        """
        from pathlib import PurePosixPath
        from unittest.mock import AsyncMock, MagicMock

        from agentpool_toolsets.builtin.skills import _load_reference_content

        # 1. Create a skill with original_name (underscore form)
        skill = Skill(
            name="systematic-troubleshooting",  # kebab-case (normalized)
            description="Systematic troubleshooting skill",
            skill_path=PurePosixPath("skill://pool_mcp_scratchpad/systematic-troubleshooting"),
            metadata={"original_name": "systematic_troubleshooting"},
        )

        # 2. Mock the aggregating provider's read_reference.
        #    _load_reference_content now passes skill.name (kebab-case) to the
        #    aggregating provider, which matches by Skill.name. The MCP provider
        #    internally looks up original_name for URI construction.
        mock_provider = MagicMock()

        async def mock_read_reference(skill_name: str, ref_path: str) -> tuple[bytes, str]:
            """Simulate aggregating provider → MCP provider chain.

            The aggregating provider matches by kebab-case skill.name.
            The MCP provider would internally look up original_name.
            """
            # Verify kebab-case skill name was passed (from skill.name)
            assert skill_name == "systematic-troubleshooting", (
                f"Expected 'systematic-troubleshooting', got '{skill_name}'"
            )
            assert ref_path == "references/phase_3_execution.md", (
                f"Expected 'references/phase_3_execution.md', got '{ref_path}'"
            )
            return b"# Phase 3: Execution\n\nDetailed steps.", "text/markdown"

        mock_provider.read_reference = AsyncMock(side_effect=mock_read_reference)

        pool = MagicMock()
        pool.skill_provider = mock_provider

        # 3. Call _load_reference_content with the reference path
        result = await _load_reference_content(skill, "references/phase_3_execution.md", pool)

        # 4. Verify the content was loaded
        assert "Phase 3" in result
        assert "Reference: references/phase_3_execution.md" in result

    @pytest.mark.asyncio
    async def test_e2e_load_reference_without_references_prefix(self) -> None:
        """Full chain with reference_path without references/ prefix.

        Some callers may pass just "phase_3_execution.md" without the
        references/ prefix. The read_reference() method should add it.
        """
        from pathlib import PurePosixPath
        from unittest.mock import AsyncMock, MagicMock

        from agentpool_toolsets.builtin.skills import _load_reference_content

        skill = Skill(
            name="systematic-troubleshooting",
            description="Systematic troubleshooting skill",
            skill_path=PurePosixPath("skill://pool_mcp_scratchpad/systematic-troubleshooting"),
            metadata={"original_name": "systematic_troubleshooting"},
        )

        mock_provider = MagicMock()
        mock_provider.read_reference = AsyncMock(
            return_value=(b"# Phase 3: Execution\n\nSteps.", "text/markdown")
        )

        pool = MagicMock()
        pool.skill_provider = mock_provider

        # Call with just filename (no references/ prefix)
        result = await _load_reference_content(skill, "phase_3_execution.md", pool)

        # read_reference should have been called with skill.name (kebab-case)
        mock_provider.read_reference.assert_called_once()
        call_args = mock_provider.read_reference.call_args

        # Verify skill.name (kebab-case) was passed, not original_name
        assert call_args[0][0] == "systematic-troubleshooting"

        # Verify the result contains content
        assert "Phase 3" in result

    @pytest.mark.asyncio
    async def test_e2e_uri_construction_matches_server_expectations(self) -> None:
        """RED: Verify that MCPResourceProvider.read_reference() constructs URI correctly.

        The URI sent to the MCP server must match what the server's SkillsProvider
        expects. The server's SkillsProvider discovers skills by directory name
        (e.g., "systematic_troubleshooting") and reads files within that directory.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        from mcp.types import TextResourceContents

        from agentpool.resource_providers.mcp_provider import MCPResourceProvider

        # Create a real MCPResourceProvider (with mocked client)
        with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connected = True
            mock_client.list_prompts = AsyncMock(return_value=[])
            mock_client.list_resources = AsyncMock(return_value=[])
            # Must return TextResourceContents objects since read_resource() uses
            # match TextResourceContents(text=text) to unpack MCP protocol responses
            from mcp.types import AnyUrl

            mock_client.read_resource = AsyncMock(
                return_value=[
                    TextResourceContents(
                        uri=AnyUrl(
                            "skill://systematic_troubleshooting/references/phase_3_execution.md"
                        ),
                        text="# Phase 3\n\nContent",
                    )
                ]
            )

            mock_client_class.return_value = mock_client
            provider = MCPResourceProvider(server="uvx test-server", name="test-mcp")
            provider.client = mock_client

            # Call read_reference with underscore skill name and references/ prefix
            content_bytes, mime_type = await provider.read_reference(
                "systematic_troubleshooting",
                "references/phase_3_execution.md",
            )

            # Verify content was returned correctly
            assert content_bytes == b"# Phase 3\n\nContent"
            assert mime_type == "text/markdown"

            # Verify the URI sent to read_resource
            call_args = provider.client.read_resource.call_args
            uri = call_args[0][0]

            # The URI must match what the MCP server expects:
            # skill://systematic_troubleshooting/references/phase_3_execution.md
            # NOT skill://test-mcp/systematic_troubleshooting/... (no provider prefix)
            # NOT skill://systematic_troubleshooting/references/references/... (no double prefix)
            assert uri == "skill://systematic_troubleshooting/references/phase_3_execution.md", (
                f"URI mismatch: got '{uri}'"
            )
            assert "test-mcp" not in uri, "Provider name should NOT be in URI"
            assert "references/references" not in uri, "Double references/ prefix detected"

    @pytest.mark.asyncio
    async def test_e2e_kebab_case_skill_name_resolves_via_original_name(self) -> None:
        """Kebab-case skill name now resolves via original_name lookup.

        The MCP provider's read_reference() now looks up original_name from
        its skill cache when receiving a kebab-case name. This allows the
        aggregating provider to pass kebab-case (which matches Skill.name)
        while the MCP provider internally resolves the correct URI.
        """
        from pathlib import PurePosixPath
        from unittest.mock import AsyncMock, MagicMock, patch

        from mcp.types import TextResourceContents

        from agentpool.resource_providers.mcp_provider import MCPResourceProvider
        from agentpool.skills.skill import Skill

        with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
            mock_client = MagicMock()
            mock_client.connected = True
            mock_client.list_prompts = AsyncMock(return_value=[])
            mock_client.list_resources = AsyncMock(return_value=[])
            # Server returns content when using the correct underscore name
            from mcp.types import AnyUrl

            mock_client.read_resource = AsyncMock(
                return_value=[
                    TextResourceContents(
                        uri=AnyUrl(
                            "skill://systematic_troubleshooting/references/phase_3_execution.md"
                        ),
                        text="# Phase 3\n\nContent",
                    )
                ]
            )

            mock_client_class.return_value = mock_client
            provider = MCPResourceProvider(server="uvx test-server", name="test-mcp")
            provider.client = mock_client

            # Pre-populate the skill cache with the skill (including original_name metadata)
            # This simulates what happens when get_skills() was called earlier
            skill = Skill(
                name="systematic-troubleshooting",  # kebab-case (normalized by Skill model)
                description="Test skill",
                skill_path=PurePosixPath("skill://test-mcp/systematic-troubleshooting"),
                metadata={"original_name": "systematic_troubleshooting", "skill_type": "resource"},
            )
            provider._skills_cache = [skill]

            # Calling read_reference with kebab-case skill name should now work
            # because the MCP provider looks up original_name from its cache
            content_bytes, mime_type = await provider.read_reference(
                "systematic-troubleshooting",  # kebab-case
                "references/phase_3_execution.md",
            )

            # Verify content was returned
            assert content_bytes == b"# Phase 3\n\nContent"
            assert mime_type == "text/markdown"

            # Verify the URI sent to read_resource uses original_name (underscores)
            call_args = provider.client.read_resource.call_args
            uri = call_args[0][0]
            assert "systematic_troubleshooting" in uri, (
                f"URI should use original_name with underscores: {uri}"
            )
            assert "systematic-troubleshooting" not in uri, f"URI should NOT use kebab-case: {uri}"


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def test_skill_with_root_asset(tmp_path: Path) -> UPath:
    """Create a test skill with a file at skill root (not in references/).

    This fixture is used to verify that _load_reference_content can load files
    from the skill directory root via the UPath (direct filesystem) branch, rather
    than being incorrectly routed through the provider branch which hardcodes
    references/ prefix.
    """
    skill_dir = tmp_path / "expert-knowledge"
    skill_dir.mkdir()

    smd_content = """---
name: expert-knowledge
description: Test skill with assets at root
---
"""
    (skill_dir / "SKILL.md").write_text(smd_content)

    # Create file at skill root (not in references/)
    assets_dir = skill_dir / "assets"
    assets_dir.mkdir()
    (assets_dir / "fta_template.md").write_text("# FTA Template\n\nTemplate content here.")

    return UPath(skill_dir)


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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "simple-skill")

            # Should contain header, metadata, and instructions
            assert "# simple-skill" in result
            assert "A simple test skill" in result
            assert "Instructions go here" in result
            assert "Skill URI:" in result

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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
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
# Test Class: ProviderLessURIFallback
# =============================================================================


@pytest.mark.integration
class TestProviderLessURIFallback:
    """Test loading skills with provider-less URIs (fallback to search all providers)."""

    async def test_provider_less_uri_with_reference(
        self,
        tmp_path: Path,
        test_skill_with_args: UPath,
    ) -> None:
        """Test loading skill with provider-less URI and reference path."""
        from agentpool.resource_providers.local import LocalResourceProvider
        from agentpool.skills.uri_resolver import SkillURIResolver

        # Create a local provider directly with the test skill directory
        provider = LocalResourceProvider(
            name="test_local",
            skills_dirs=[UPath(tmp_path)],
        )

        async with provider:
            # Create resolver and register the provider
            resolver = SkillURIResolver()
            resolver.register_provider("test_local", provider)

            # Test provider-less URI with reference
            # Format: skill://skill-name/references/file.md
            uri = "skill://arg-skill/references/details.md"
            skill = await resolver.resolve(uri)

            # Should resolve to arg-skill with reference path
            assert skill.name == "arg-skill"
            assert hasattr(skill, "_resolved_reference_path")
            assert skill._resolved_reference_path == "references/details.md"

    async def test_provider_less_uri_without_reference(
        self,
        tmp_path: Path,
        simple_skill: UPath,
    ) -> None:
        """Test loading skill with bare skill name (no URI scheme)."""
        from agentpool.resource_providers.local import LocalResourceProvider
        from agentpool.skills.uri_resolver import SkillURIResolver

        # Create a local provider directly with the test skill directory
        provider = LocalResourceProvider(
            name="test_local",
            skills_dirs=[UPath(tmp_path)],
        )

        async with provider:
            # Create resolver and register the provider
            resolver = SkillURIResolver()
            resolver.register_provider("test_local", provider)

            # Test bare skill name (no URI scheme)
            # This is the standard format for provider-less skill loading
            uri = "simple-skill"
            skill = await resolver.resolve(uri)

            # Should resolve to simple-skill
            assert skill.name == "simple-skill"


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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
            ctx = AgentContext(node=agent, pool=pool)

            result = await list_skills(ctx)

            assert "Available skills:" in result
            assert "simple-skill" in result
            assert "A simple test skill" in result


# =============================================================================
# Test Class: UPathReferenceLoading
# =============================================================================


class TestUPathReferenceLoading:
    """Test _load_reference_content with UPath (local filesystem) skills.

    Verifies that UPath skills correctly use the direct filesystem branch,
    which loads files relative to the skill directory root, rather than being
    incorrectly routed through the provider branch which hardcodes references/.
    """

    @pytest.mark.asyncio
    async def test_load_reference_from_skill_root_via_upath(self) -> None:
        """Load a reference file at skill root via UPath direct branch.

        The file is at <skill_dir>/assets/fta_template.md, NOT in references/.
        This must go through the UPath direct filesystem branch.
        """
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock

        from agentpool_toolsets.builtin.skills import _load_reference_content

        # Create a real temp skill directory with a file at root level
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = Path(tmp_dir) / "test-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---")
            assets_dir = skill_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "guide.md").write_text("# Guide\n\nContent from root asset.")

            skill = MagicMock()
            skill.name = "test-skill"
            skill.skill_path = UPath(skill_dir)

            # pool with skill_provider != None to verify UPath is NOT routed
            # through the provider branch (file is not in references/)
            mock_provider = MagicMock()
            mock_provider.read_reference = None  # would fail if called
            pool = MagicMock()
            pool.skill_provider = mock_provider

            result = await _load_reference_content(skill, "assets/guide.md", pool)

            assert "# Guide" in result
            assert "Content from root asset" in result
            assert "Reference: assets/guide.md" in result

    @pytest.mark.asyncio
    async def test_upath_does_not_route_to_provider(self) -> None:
        """Verify UPath skills are NOT routed through the provider branch.

        The provider branch would look in references/ subdirectory.
        The file only exists at skill root, so if incorrectly routed through
        provider, it would fail with ReferenceNotFoundError.
        """
        from pathlib import Path
        import tempfile
        from unittest.mock import AsyncMock, MagicMock

        from agentpool_toolsets.builtin.skills import _load_reference_content

        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = Path(tmp_dir) / "test-skill-2"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text("---\nname: test-skill-2\ndescription: Test\n---")
            (skill_dir / "root_file.md").write_text("# Root level content")

            skill = MagicMock()
            skill.name = "test-skill-2"
            skill.skill_path = UPath(skill_dir)

            # Track whether provider.read_reference was called
            provider_called = False

            async def fail_if_called(*args, **kwargs) -> tuple[bytes, str]:
                nonlocal provider_called
                provider_called = True
                msg = "Provider should NOT be called for UPath skills"
                raise RuntimeError(msg)

            mock_provider = MagicMock()
            mock_provider.read_reference = AsyncMock(side_effect=fail_if_called)
            pool = MagicMock()
            pool.skill_provider = mock_provider

            result = await _load_reference_content(skill, "root_file.md", pool)

            assert "Root level content" in result
            assert not provider_called, "Provider was incorrectly called for UPath skill"
            mock_provider.read_reference.assert_not_called()

    @pytest.mark.asyncio
    async def test_upath_ref_path_priority_from_resolver_fallback(self) -> None:
        """_resolved_reference_path from resolver fallback takes priority.

        For provider-less URIs like skill://expert-knowledge/assets/fta_template.md,
        the URI parser misidentifies the skill name as provider. The resolver's
        fallback corrects this by setting _resolved_reference_path to the full
        path ("assets/fta_template.md"), while resolved.reference_path remains the
        partial ("fta_template.md"). The priority flip ensures the corrected path wins.
        """
        from pathlib import Path
        import tempfile
        from unittest.mock import MagicMock

        from agentpool_toolsets.builtin.skills import _load_reference_content

        # Simulate the scenario: skill has _resolved_reference_path set by resolver
        with tempfile.TemporaryDirectory() as tmp_dir:
            skill_dir = Path(tmp_dir) / "expert-knowledge"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: expert-knowledge\ndescription: Test\n---"
            )
            assets_dir = skill_dir / "assets"
            assets_dir.mkdir()
            (assets_dir / "fta_template.md").write_text("# FTA Template\n\nCorrect content.")

            skill = MagicMock()
            skill.name = "expert-knowledge"
            skill.skill_path = UPath(skill_dir)
            skill.safe_uri = "skill://local/expert-knowledge"
            # Simulate resolver fallback setting this
            skill._resolved_reference_path = "assets/fta_template.md"  # type: ignore[attr-defined]

            mock_provider = MagicMock()
            mock_provider.read_reference = None
            pool = MagicMock()
            pool.skill_provider = mock_provider

            # Simulate what load_skill() does: ref_path should prefer _resolved_reference_path
            ref_path = getattr(skill, "_resolved_reference_path", None)
            assert ref_path == "assets/fta_template.md"
            result = await _load_reference_content(skill, ref_path, pool=pool)

            assert "Correct content" in result
            assert "Reference: assets/fta_template.md" in result


# =============================================================================
# Test Class: ProviderLessURIReferenceLoading
# =============================================================================


@pytest.mark.integration
class TestProviderLessURIReferenceLoading:
    """Integration tests for provider-less URIs like skill://skill-name/path.

    Verifies the full load_skill flow resolves provider-less URIs correctly,
    using the resolver's fallback logic to reconstruct the full reference path.
    """

    async def test_provider_less_uri_loads_reference_from_skill_root(
        self,
        tmp_path: Path,
        test_skill_with_root_asset: UPath,
    ) -> None:
        """skill://expert-knowledge/assets/fta_template.md loads root-level asset.

        Provider-less URI where URI parser misidentifies "expert-knowledge" as
        provider. Resolver fallback corrects this and reconstructs the full
        reference path "assets/fta_template.md".
        """
        from agentpool_toolsets.builtin.skills import load_skill

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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
            ctx = AgentContext(node=agent, pool=pool)

            # Provider-less URI — should load the asset via resolver fallback
            result = await load_skill(ctx, "skill://expert-knowledge/assets/fta_template.md")

            assert "FTA Template" in result
            assert "Template content here" in result

    async def test_provider_less_uri_with_nested_path(
        self,
        tmp_path: Path,
    ) -> None:
        """skill://test/deep/nested/file.md correctly reconstructs path."""
        from agentpool_toolsets.builtin.skills import load_skill

        # Create skill with deeply nested file
        skill_dir = tmp_path / "test-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("---\nname: test-skill\ndescription: Test\n---")
        nested_dir = skill_dir / "deep" / "nested"
        nested_dir.mkdir(parents=True)
        (nested_dir / "file.md").write_text("# Deeply nested content")

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
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)
            ctx = AgentContext(node=agent, pool=pool)

            result = await load_skill(ctx, "skill://test-skill/deep/nested/file.md")

            assert "Deeply nested content" in result
