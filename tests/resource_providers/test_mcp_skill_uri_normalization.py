"""Tests for MCP skill original_name preservation and resolver fuzzy matching.

When an MCP server (e.g., FastMCP-based scratchpad) publishes skill
resources with underscored URIs (e.g. ``skill://systematic_troubleshooting/SKILL.md``),
the provider stores the original name in ``metadata["original_name"]`` for
constructing ``read_resource`` URIs that the MCP server recognizes.

The ``Skill`` model's field_validator normalizes ``_`` → ``-``, so
``skill.name`` is always kebab-case. The ``ResolvedSkillURI.parse()`` also
normalizes, so both sides of the resolver comparison use kebab-case and
match exactly. Fuzzy matching in ``_name_alternatives()`` provides an
additional safety net for edge cases.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from upathtools import UPath

from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool.skills.exceptions import SkillNotFoundError
from agentpool.skills.skill import Skill
from agentpool.skills.uri_resolver import SkillURIResolver, _name_alternatives


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCPClient for testing."""
    client = MagicMock()
    client.connected = True
    client.list_prompts = AsyncMock(return_value=[])
    client.list_resources = AsyncMock(return_value=[])
    client.read_resource = AsyncMock(return_value=[])
    return client


@pytest.fixture
def mcp_provider(mock_mcp_client):
    """Create an MCPResourceProvider with mocked client."""
    with patch("agentpool.mcp_server.MCPClient") as mock_client_class:
        mock_client_class.return_value = mock_mcp_client
        provider = MCPResourceProvider(server="uvx test-server", name="test-mcp")
        provider.client = mock_mcp_client
        yield provider


# =============================================================================
# _name_alternatives() – helper function tests
# =============================================================================


def test_name_alternatives_underscore_to_hyphen() -> None:
    """Names with underscores generate hyphenated alternatives."""
    assert _name_alternatives("systematic_troubleshooting") == ["systematic-troubleshooting"]


def test_name_alternatives_hyphen_to_underscore() -> None:
    """Names with hyphens generate underscored alternatives."""
    assert _name_alternatives("code-review") == ["code_review"]


def test_name_alternatives_no_separators() -> None:
    """Names without separators have no alternatives."""
    assert _name_alternatives("simple") == []


def test_name_alternatives_mixed_prefers_underscore() -> None:
    """Names with underscores generate hyphenated alternatives (underscore takes priority)."""
    assert _name_alternatives("my_cool-skill") == ["my-cool-skill"]


# =============================================================================
# _get_resource_skills() – original_name preservation tests
# =============================================================================


@pytest.mark.asyncio
async def test_resource_skills_stores_original_name_in_metadata(
    mcp_provider: MCPResourceProvider,
) -> None:
    """Original name with underscores is preserved in metadata["original_name"].

    The provider passes the original name (with underscores) to Skill(),
    but the Skill model's field_validator normalizes it to kebab-case.
    The original name is preserved in metadata["original_name"] for
    constructing read_resource URIs that the MCP server recognizes.
    """
    mock_resource = MagicMock()
    mock_resource.name = "systematic_troubleshooting-skillmd"
    mock_resource.uri = "skill://systematic_troubleshooting/SKILL.md"
    mock_resource.description = "Systematic troubleshooting skill"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])
    mcp_provider._get_skill_manifest = AsyncMock(return_value=None)

    skills = await mcp_provider._get_resource_skills()

    assert len(skills) == 1
    # Skill model normalizes name to kebab-case
    assert skills[0].name == "systematic-troubleshooting"
    # Original name with underscores is preserved in metadata
    assert skills[0].metadata["original_name"] == "systematic_troubleshooting"


@pytest.mark.asyncio
async def test_resource_skills_hyphenated_name_original_name_matches(
    mcp_provider: MCPResourceProvider,
) -> None:
    """For already kebab-case names, original_name equals the normalized name."""
    mock_resource = MagicMock()
    mock_resource.name = "code-review-skillmd"
    mock_resource.uri = "skill://code-review/SKILL.md"
    mock_resource.description = "Code review skill"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])
    mcp_provider._get_skill_manifest = AsyncMock(return_value=None)

    skills = await mcp_provider._get_resource_skills()

    assert len(skills) == 1
    assert skills[0].name == "code-review"
    assert skills[0].metadata["original_name"] == "code-review"


# =============================================================================
# _get_resource_skill_instructions() – URI construction tests
# =============================================================================


@pytest.mark.asyncio
async def test_resource_skill_instructions_uses_original_name_for_uri(
    mcp_provider: MCPResourceProvider,
) -> None:
    """read_resource URI uses original_name (with underscores) from metadata."""
    skill_content = "# Systematic Troubleshooting\n\nStep-by-step debugging guide."

    # Skill with underscored original_name
    skill = Skill(
        name="systematic_troubleshooting",
        description="Systematic troubleshooting",
        skill_path=UPath("/tmp/test-skill"),
        metadata={
            "skill_type": "resource",
            "provider": "test-mcp",
            "original_name": "systematic_troubleshooting",
        },
    )

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.read_resource = AsyncMock(return_value=[skill_content])

    instructions = await mcp_provider.get_skill_instructions("systematic-troubleshooting")

    assert "Systematic Troubleshooting" in instructions
    # The provider MUST call read_resource with the original URI (underscores)
    mcp_provider.read_resource.assert_called_once_with(
        "skill://systematic_troubleshooting/SKILL.md"
    )


@pytest.mark.asyncio
async def test_resource_skill_instructions_hyphenated_name(
    mcp_provider: MCPResourceProvider,
) -> None:
    """Hyphenated skill names produce hyphenated URIs (original_name matches)."""
    skill_content = "# Code Review\n\nReview guidelines."

    skill = Skill(
        name="code-review",
        description="Code review",
        skill_path=UPath("/tmp/test-skill"),
        metadata={
            "skill_type": "resource",
            "provider": "test-mcp",
            "original_name": "code-review",
        },
    )

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.read_resource = AsyncMock(return_value=[skill_content])

    instructions = await mcp_provider.get_skill_instructions("code-review")

    assert "Code Review" in instructions
    mcp_provider.read_resource.assert_called_once_with("skill://code-review/SKILL.md")


@pytest.mark.asyncio
async def test_resource_skill_instructions_not_found(
    mcp_provider: MCPResourceProvider,
) -> None:
    """SkillNotFoundError raised when read_resource fails."""
    skill = Skill(
        name="missing-skill",
        description="Missing",
        skill_path=UPath("/tmp/test-skill"),
        metadata={
            "skill_type": "resource",
            "provider": "test-mcp",
            "original_name": "missing_skill",
        },
    )

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.read_resource = AsyncMock(side_effect=ValueError("Resource not found"))

    with pytest.raises(SkillNotFoundError):
        await mcp_provider.get_skill_instructions("missing-skill")


# =============================================================================
# SkillURIResolver – fuzzy matching tests
# =============================================================================


@pytest.mark.asyncio
async def test_resolver_exact_match_when_both_normalized() -> None:
    """Both Skill.name and resolved.skill_name are kebab-case → exact match."""
    skill = Skill(
        name="systematic_troubleshooting",  # model normalizes to "systematic-troubleshooting"
        description="Test",
        skill_path=PurePosixPath("skill://mcp/systematic-troubleshooting"),
        metadata={"skill_type": "resource", "original_name": "systematic_troubleshooting"},
    )
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[skill])

    resolver = SkillURIResolver()
    resolver.register_provider("mcp", mock_provider)

    # parse() normalizes to "systematic-troubleshooting" → matches skill.name
    result = await resolver.resolve("skill://mcp/systematic-troubleshooting")
    assert result.name == "systematic-troubleshooting"
    assert result.metadata["original_name"] == "systematic_troubleshooting"


@pytest.mark.asyncio
async def test_resolver_exact_match_for_already_kebab_skill() -> None:
    """When provider skill is already kebab-case, exact match works."""
    skill = Skill(
        name="code-review",
        description="Test",
        skill_path=PurePosixPath("skill://mcp/code-review"),
        metadata={"skill_type": "resource"},
    )
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[skill])

    resolver = SkillURIResolver()
    resolver.register_provider("mcp", mock_provider)

    result = await resolver.resolve("skill://mcp/code-review")
    assert result.name == "code-review"


@pytest.mark.asyncio
async def test_resolver_fuzzy_match_bare_skill_name() -> None:
    """Fuzzy matching works for bare skill names (no skill:// prefix)."""
    skill = Skill(
        name="systematic_troubleshooting",  # normalized to "systematic-troubleshooting"
        description="Test",
        skill_path=PurePosixPath("skill://mcp/systematic-troubleshooting"),
        metadata={"skill_type": "resource", "original_name": "systematic_troubleshooting"},
    )
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[skill])

    resolver = SkillURIResolver()
    resolver.register_provider("mcp", mock_provider)

    # Bare name: _validate_skill_name normalizes to "systematic-troubleshooting"
    # → exact match with skill.name (also normalized)
    result = await resolver.resolve("systematic-troubleshooting")
    assert result.name == "systematic-troubleshooting"
    assert result.metadata["original_name"] == "systematic_troubleshooting"


@pytest.mark.asyncio
async def test_resolver_no_match_raises_not_found() -> None:
    """Non-existent skill name raises SkillNotFoundError even after fuzzy matching."""
    skill = Skill(
        name="code-review",
        description="Test",
        skill_path=PurePosixPath("skill://mcp/code-review"),
        metadata={"skill_type": "resource"},
    )
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[skill])

    resolver = SkillURIResolver()
    resolver.register_provider("mcp", mock_provider)

    with pytest.raises(SkillNotFoundError):
        await resolver.resolve("skill://mcp/nonexistent-skill")


# =============================================================================
# Full flow integration test
# =============================================================================


@pytest.mark.asyncio
async def test_full_flow_underscored_uri_discovery_and_load(
    mcp_provider: MCPResourceProvider,
) -> None:
    """End-to-end: discover skill with underscored URI → load instructions with original URI.

    This test exercises the complete path:
    1. MCP server publishes ``skill://systematic_troubleshooting/SKILL.md``
    2. ``_get_resource_skills`` creates Skill with metadata["original_name"]
       preserving the underscored form (Skill model normalizes name to kebab-case)
    3. ``get_skill_instructions("systematic-troubleshooting")`` uses
       metadata["original_name"] to construct ``skill://systematic_troubleshooting/SKILL.md``
    """
    skill_content = "# Systematic Troubleshooting\n\nFollow these steps."

    # Step 1: set up resource discovery
    mock_resource = MagicMock()
    mock_resource.name = "systematic_troubleshooting-skillmd"
    mock_resource.uri = "skill://systematic_troubleshooting/SKILL.md"
    mock_resource.description = "Systematic troubleshooting skill"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])
    mcp_provider._get_skill_manifest = AsyncMock(return_value=None)
    mcp_provider._get_prompt_skills = AsyncMock(return_value=[])

    # Step 2: set up resource reading — only accept the original URI
    async def read_resource_selective(uri: str) -> list[str]:
        if uri == "skill://systematic_troubleshooting/SKILL.md":
            return [skill_content]
        # Any other URI (e.g. with hyphens) would fail on the real server
        msg = f"Unknown resource: {uri}"
        raise ValueError(msg)

    mcp_provider.read_resource = AsyncMock(side_effect=read_resource_selective)

    # Step 3: discover skills (this populates the cache)
    skills = await mcp_provider.get_skills()

    assert len(skills) == 1
    # Skill model normalizes name to kebab-case
    assert skills[0].name == "systematic-troubleshooting"
    # Original name preserved in metadata
    assert skills[0].metadata["original_name"] == "systematic_troubleshooting"

    # Step 4: load instructions using the normalized name
    # get_skill_instructions uses metadata["original_name"] internally
    # to construct the correct URI for the MCP server
    instructions = await mcp_provider.get_skill_instructions("systematic-troubleshooting")

    assert "Systematic Troubleshooting" in instructions


@pytest.mark.asyncio
async def test_full_flow_resolver_then_load(
    mcp_provider: MCPResourceProvider,
) -> None:
    """End-to-end: model uses kebab-case → resolver finds skill → load with original URI.

    This tests the full user-facing scenario:
    1. MCP server publishes ``skill://systematic_troubleshooting/SKILL.md``
    2. list_skills shows the skill with URI ``skill://test-mcp/systematic-troubleshooting``
    3. Model calls load_skill with ``skill://test-mcp/systematic-troubleshooting``
    4. Resolver finds the skill (exact match since both sides are kebab-case)
    5. get_skill_instructions uses metadata["original_name"] for the MCP server URI
    """
    skill_content = "# Systematic Troubleshooting\n\nFollow these steps."

    # Set up provider with a skill whose original_name has underscores
    skill = Skill(
        name="systematic_troubleshooting",  # model normalizes to "systematic-troubleshooting"
        description="Systematic troubleshooting",
        skill_path=PurePosixPath("skill://test-mcp/systematic-troubleshooting"),
        metadata={
            "skill_type": "resource",
            "provider": "test-mcp",
            "original_name": "systematic_troubleshooting",
        },
    )

    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[skill])

    resolver = SkillURIResolver()
    resolver.register_provider("test-mcp", mock_provider)

    # Model calls with kebab-case URI → exact match (both normalized)
    resolved_skill = await resolver.resolve("skill://test-mcp/systematic-troubleshooting")
    assert resolved_skill.name == "systematic-troubleshooting"
    assert resolved_skill.metadata["original_name"] == "systematic_troubleshooting"

    # Now load instructions — get_skill_instructions uses original_name
    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.read_resource = AsyncMock(return_value=[skill_content])

    instructions = await mcp_provider.get_skill_instructions("systematic-troubleshooting")
    assert "Systematic Troubleshooting" in instructions
    # Verify the original URI was used (with underscores from original_name)
    mcp_provider.read_resource.assert_called_once_with(
        "skill://systematic_troubleshooting/SKILL.md"
    )
