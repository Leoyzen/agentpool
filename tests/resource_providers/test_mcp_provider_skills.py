"""Tests for MCPResourceProvider skill methods.

This module provides comprehensive tests for:
- get_skills() returns combined skills
- _get_prompt_skills() maps MCP prompts
- _get_resource_skills() detects skill:// resources
- get_skill_instructions() for both types
- get_references() for skills
- read_reference() with path traversal protection
- _on_skills_changed() callback
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool.skills.exceptions import SecurityError, SkillNotFoundError
from agentpool.skills.skill import Skill
from upathtools import UPath


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_mcp_client():
    """Create a mock MCPClient for testing."""
    client = MagicMock()
    client.connected = True
    # Set up async methods
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
        # Set the client directly to avoid context manager
        provider.client = mock_mcp_client
        yield provider


# =============================================================================
# get_skills() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_returns_combined_skills(mcp_provider):
    """Test that get_skills() returns both prompt-based and resource-based skills."""
    # Create mock prompt skills
    prompt_skill1 = Skill(
        name="test-prompt",
        description="A test prompt skill",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "prompt", "provider": "test-mcp"},
    )
    prompt_skill2 = Skill(
        name="dynamic-prompt",
        description="A dynamic prompt",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "prompt", "provider": "test-mcp"},
    )

    # Create mock resource skills
    resource_skill1 = Skill(
        name="test-skill",
        description="Test skill from resources",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "resource", "provider": "test-mcp"},
    )
    resource_skill2 = Skill(
        name="another-skill",
        description="Another skill",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "resource", "provider": "test-mcp"},
    )

    # Mock the internal methods
    mcp_provider._get_prompt_skills = AsyncMock(return_value=[prompt_skill1, prompt_skill2])
    mcp_provider._get_resource_skills = AsyncMock(return_value=[resource_skill1, resource_skill2])

    skills = await mcp_provider.get_skills()

    # Should have skills from both sources
    assert len(skills) == 4

    # Check prompt-based skills
    prompt_skills = [s for s in skills if s.metadata.get("skill_type") == "prompt"]
    assert len(prompt_skills) == 2

    # Check resource-based skills
    resource_skills = [s for s in skills if s.metadata.get("skill_type") == "resource"]
    assert len(resource_skills) == 2


@pytest.mark.asyncio
async def test_get_skills_caching(mcp_provider):
    """Test that get_skills() caches results."""
    skill = Skill(
        name="cached-skill",
        description="Cached",
        skill_path=UPath("/tmp/test-skill"),
        metadata={},
    )

    mcp_provider._get_prompt_skills = AsyncMock(return_value=[skill])
    mcp_provider._get_resource_skills = AsyncMock(return_value=[])

    # First call
    skills1 = await mcp_provider.get_skills()

    # Second call - should use cache
    skills2 = await mcp_provider.get_skills()

    assert skills1 == skills2
    # Internal methods should only be called once due to caching
    assert mcp_provider._get_prompt_skills.call_count == 1


@pytest.mark.asyncio
async def test_get_skills_deduplication(mcp_provider):
    """Test that resource-based skills take precedence over prompt-based with same name."""
    # Create skills with same name
    prompt_skill = Skill(
        name="duplicate-skill",
        description="From prompt",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "prompt"},
    )
    resource_skill = Skill(
        name="duplicate-skill",
        description="From resource",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "resource"},
    )

    mcp_provider._get_prompt_skills = AsyncMock(return_value=[prompt_skill])
    mcp_provider._get_resource_skills = AsyncMock(return_value=[resource_skill])

    skills = await mcp_provider.get_skills()

    # Should only have one skill (resource takes precedence)
    assert len(skills) == 1
    assert skills[0].metadata["skill_type"] == "resource"


@pytest.mark.asyncio
async def test_get_skills_empty_server(mcp_provider):
    """Test get_skills() with empty server (no prompts or resources)."""
    mcp_provider._get_prompt_skills = AsyncMock(return_value=[])
    mcp_provider._get_resource_skills = AsyncMock(return_value=[])

    skills = await mcp_provider.get_skills()
    assert skills == []


# =============================================================================
# _get_prompt_skills() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_prompt_skills_maps_prompts(mcp_provider):
    """Test that _get_prompt_skills() correctly maps MCP prompts to skills."""
    # Mock prompts with proper attributes
    mock_prompt1 = MagicMock()
    mock_prompt1.name = "test-prompt"
    mock_prompt1.description = "A test prompt"
    mock_prompt1.arguments = []

    mock_prompt2 = MagicMock()
    mock_prompt2.name = "dynamic-prompt"
    mock_prompt2.description = "A dynamic prompt with args"
    mock_arg = MagicMock()
    mock_arg.name = "name"
    mock_arg.description = "The name argument"
    mock_arg.required = True
    mock_prompt2.arguments = [mock_arg]

    # Mock get_prompts to return our mock prompts
    mcp_provider.get_prompts = AsyncMock(return_value=[mock_prompt1, mock_prompt2])

    prompt_skills = await mcp_provider._get_prompt_skills()

    assert len(prompt_skills) == 2

    # Check first prompt skill
    skill1 = next(s for s in prompt_skills if s.name == "test-prompt")
    assert skill1.description == "A test prompt"
    assert skill1.metadata.get("skill_type") == "prompt"
    assert skill1.metadata.get("provider") == "test-mcp"


@pytest.mark.asyncio
async def test_get_prompt_skills_with_arguments(mcp_provider):
    """Test that prompts with arguments have proper argument_schema."""
    mock_prompt = MagicMock()
    mock_prompt.name = "dynamic-prompt"
    mock_prompt.description = "A dynamic prompt"
    mock_arg = MagicMock()
    mock_arg.name = "name"
    mock_arg.description = "The name"
    mock_arg.required = True
    mock_prompt.arguments = [mock_arg]

    mcp_provider.get_prompts = AsyncMock(return_value=[mock_prompt])

    prompt_skills = await mcp_provider._get_prompt_skills()

    dynamic_skill = prompt_skills[0]
    schema_str = dynamic_skill.metadata.get("argument_schema")
    assert schema_str is not None
    import json

    schema = json.loads(schema_str)
    assert schema["type"] == "object"
    assert "name" in schema["properties"]
    assert "name" in schema["required"]


@pytest.mark.asyncio
async def test_get_prompt_skills_handles_errors(mcp_provider):
    """Test that _get_prompt_skills() handles errors gracefully."""
    mcp_provider.get_prompts = AsyncMock(side_effect=Exception("Server error"))

    prompt_skills = await mcp_provider._get_prompt_skills()

    # Should return empty list on error, not crash
    assert prompt_skills == []


@pytest.mark.asyncio
async def test_get_prompt_skills_dict_arguments(mcp_provider):
    """Test prompts with dict-style arguments (some MCP servers use this format)."""
    mock_prompt = MagicMock()
    mock_prompt.name = "dict-args-prompt"
    mock_prompt.description = "Prompt with dict args"
    # Dict-style arguments
    mock_prompt.arguments = [{"name": "arg1", "description": "First arg", "required": True}]

    mcp_provider.get_prompts = AsyncMock(return_value=[mock_prompt])

    prompt_skills = await mcp_provider._get_prompt_skills()

    assert len(prompt_skills) == 1
    skill = prompt_skills[0]
    assert skill.name == "dict-args-prompt"


# =============================================================================
# _get_resource_skills() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_resource_skills_detects_skill_resources(mcp_provider):
    """Test that _get_resource_skills() detects skill:// resources."""
    # Create mock resources
    mock_resource1 = MagicMock()
    mock_resource1.name = "regular-resource"
    mock_resource1.uri = "file:///some/path"
    mock_resource1.description = "A regular resource"

    mock_resource2 = MagicMock()
    mock_resource2.name = "test-skill-skillmd"
    mock_resource2.uri = "skill://test-skill/SKILL.md"
    mock_resource2.description = "Test skill main file"

    mock_resource3 = MagicMock()
    mock_resource3.name = "another-skill-skillmd"
    mock_resource3.uri = "skill://another-skill/SKILL.md"
    mock_resource3.description = "Another skill"

    mcp_provider.get_resources = AsyncMock(
        return_value=[mock_resource1, mock_resource2, mock_resource3]
    )
    mcp_provider._get_skill_manifest = AsyncMock(return_value=None)

    resource_skills = await mcp_provider._get_resource_skills()

    # Should find test-skill and another-skill (not regular-resource)
    skill_names = {s.name for s in resource_skills}
    assert "test-skill" in skill_names
    assert "another-skill" in skill_names
    assert "regular-resource" not in skill_names


@pytest.mark.asyncio
async def test_get_resource_skills_ignores_non_skill_resources(mcp_provider):
    """Test that non-skill:// resources are ignored."""
    mock_resource = MagicMock()
    mock_resource.name = "regular-resource"
    mock_resource.uri = "file:///some/path"
    mock_resource.description = "A regular resource"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])

    resource_skills = await mcp_provider._get_resource_skills()

    # Should not include the regular resource
    assert len(resource_skills) == 0


@pytest.mark.asyncio
async def test_get_resource_skills_reads_manifest(mcp_provider):
    """Test that _get_resource_skills() uses resource.description, not SKILL.md content.

    Since discovery no longer reads SKILL.md content (to avoid N network round-trips),
    the description comes from the MCP resource's built-in description field.
    Full descriptions are loaded lazily via _get_skill_description() only when
    get_skill_instructions() is called.
    """
    mock_resource = MagicMock()
    mock_resource.name = "test-skill-manifest"
    mock_resource.uri = "skill://test-skill/_manifest"
    mock_resource.description = "Test skill"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])

    resource_skills = await mcp_provider._get_resource_skills()

    assert len(resource_skills) == 1
    # Uses resource.description directly — no longer calls _get_skill_description()
    assert resource_skills[0].description == "Test skill"


@pytest.mark.asyncio
async def test_get_resource_skills_only_processes_skillmd_and_manifest(mcp_provider):
    """Test that only SKILL.md and _manifest resources trigger skill creation."""
    mock_resource = MagicMock()
    mock_resource.name = "other-file"
    mock_resource.uri = "skill://test-skill/other.txt"
    mock_resource.description = "Other file"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])

    resource_skills = await mcp_provider._get_resource_skills()

    # Should not create a skill for other.txt
    assert len(resource_skills) == 0


@pytest.mark.asyncio
async def test_get_resource_skills_handles_errors(mcp_provider):
    """Test that _get_resource_skills() handles errors gracefully."""
    mcp_provider.get_resources = AsyncMock(side_effect=Exception("Server error"))

    resource_skills = await mcp_provider._get_resource_skills()

    # Should return empty list on error, not crash
    assert resource_skills == []


# =============================================================================
# get_skill_instructions() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skill_instructions_for_prompt_skill(mcp_provider):
    """Test getting instructions for a prompt-based skill."""
    # Create mock skill
    skill = Skill(
        name="test-prompt",
        description="Test prompt",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "prompt"},
    )

    # Create mock prompt
    mock_prompt = MagicMock()
    mock_prompt.name = "test-prompt"

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.get_prompts = AsyncMock(return_value=[mock_prompt])
    mcp_provider._get_prompt_skill_instructions = AsyncMock(return_value="Prompt content here")

    instructions = await mcp_provider.get_skill_instructions("test-prompt")

    assert "Prompt content here" in instructions


@pytest.mark.asyncio
async def test_get_skill_instructions_for_resource_skill(mcp_provider):
    """Test getting instructions for a resource-based skill."""
    skill = Skill(
        name="test-skill",
        description="Test skill",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "resource"},
    )

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider._get_resource_skill_instructions = AsyncMock(
        return_value="# Test Skill\n\nContent"
    )

    instructions = await mcp_provider.get_skill_instructions("test-skill")

    assert "Test Skill" in instructions


@pytest.mark.asyncio
async def test_get_skill_instructions_not_found(mcp_provider):
    """Test that SkillNotFoundError is raised for non-existent skill."""
    mcp_provider.get_skills = AsyncMock(return_value=[])

    with pytest.raises(SkillNotFoundError) as exc_info:
        await mcp_provider.get_skill_instructions("non-existent-skill")

    assert "non-existent-skill" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_skill_instructions_missing_args_returns_template(mcp_provider):
    """Test that missing args returns a template for prompt skills."""
    skill = Skill(
        name="arg-prompt",
        description="Prompt needing args",
        skill_path=UPath("/tmp/test-skill"),
        metadata={"skill_type": "prompt"},
    )

    # Create mock prompt
    mock_prompt = MagicMock()
    mock_prompt.name = "arg-prompt"

    mcp_provider.get_skills = AsyncMock(return_value=[skill])
    mcp_provider.get_prompts = AsyncMock(return_value=[mock_prompt])
    mcp_provider._get_prompt_skill_instructions = AsyncMock(
        return_value="# arg-prompt\n\n## Arguments\n- **required_arg** (required): A required argument"
    )

    instructions = await mcp_provider.get_skill_instructions("arg-prompt")

    # Should return a template
    assert "arg-prompt" in instructions
    assert "Arguments" in instructions


# =============================================================================
# _get_prompt_skill_instructions() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_prompt_skill_instructions_with_components(mcp_provider):
    """Test rendering prompt-based skill with components."""
    mock_component1 = MagicMock()
    mock_component1.content = "Part 1"
    mock_component2 = MagicMock()
    mock_component2.content = "Part 2"

    mock_prompt = MagicMock()
    mock_prompt.name = "multi-part"
    mock_prompt.get_components = AsyncMock(return_value=[mock_component1, mock_component2])

    instructions = await mcp_provider._get_prompt_skill_instructions(mock_prompt, {})

    assert "Part 1" in instructions
    assert "Part 2" in instructions


# =============================================================================
# _get_resource_skill_instructions() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_resource_skill_instructions(mcp_provider):
    """Test reading SKILL.md content for resource-based skill."""
    # Mock the provider's read_resource method directly
    mcp_provider.read_resource = AsyncMock(return_value=["# Test Skill\n\nContent"])

    instructions = await mcp_provider._get_resource_skill_instructions("test-skill")

    assert "Test Skill" in instructions


@pytest.mark.asyncio
async def test_get_resource_skill_instructions_not_found(mcp_provider):
    """Test error when SKILL.md is not found."""
    mcp_provider.read_resource = AsyncMock(return_value=[])

    with pytest.raises(SkillNotFoundError):
        await mcp_provider._get_resource_skill_instructions("missing-skill")


# =============================================================================
# get_references() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_references(mcp_provider):
    """Test listing references for a skill."""
    # Create mock resources including references
    mock_resource1 = MagicMock()
    mock_resource1.name = "guide"
    mock_resource1.uri = "skill://test-skill/references/guide.md"
    mock_resource1.description = "Guide"

    mock_resource2 = MagicMock()
    mock_resource2.name = "examples"
    mock_resource2.uri = "skill://test-skill/references/examples.py"
    mock_resource2.description = "Examples"

    mock_resource3 = MagicMock()
    mock_resource3.name = "main"
    mock_resource3.uri = "skill://test-skill/SKILL.md"
    mock_resource3.description = "Main"

    mcp_provider.get_resources = AsyncMock(
        return_value=[mock_resource1, mock_resource2, mock_resource3]
    )

    refs = await mcp_provider.get_references("test-skill")

    # Should only include references, not main SKILL.md
    assert len(refs) == 2
    ref_paths = {r["path"] for r in refs}
    assert "guide.md" in ref_paths
    assert "examples.py" in ref_paths


@pytest.mark.asyncio
async def test_get_references_returns_dict_list(mcp_provider):
    """Test that get_references() returns list of dicts with expected keys."""
    mock_resource = MagicMock()
    mock_resource.name = "guide"
    mock_resource.uri = "skill://test-skill/references/guide.md"
    mock_resource.description = "Guide reference"

    mcp_provider.get_resources = AsyncMock(return_value=[mock_resource])

    refs = await mcp_provider.get_references("test-skill")

    for ref in refs:
        assert "name" in ref
        assert "path" in ref
        assert "uri" in ref
        assert "description" in ref


@pytest.mark.asyncio
async def test_get_references_empty_for_skill_without_refs(mcp_provider):
    """Test that empty list is returned for skill without references."""
    mcp_provider.get_resources = AsyncMock(return_value=[])

    refs = await mcp_provider.get_references("no-refs-skill")

    assert refs == []


@pytest.mark.asyncio
async def test_get_references_handles_errors(mcp_provider):
    """Test that errors in get_references() are handled gracefully."""
    mcp_provider.get_resources = AsyncMock(side_effect=Exception("Server error"))

    refs = await mcp_provider.get_references("test-skill")

    # Should return empty list on error
    assert refs == []


# =============================================================================
# read_reference() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_read_reference(mcp_provider):
    """Test reading a reference file."""
    mcp_provider.read_resource = AsyncMock(return_value=["# Guide\n\nGuide content"])

    content_bytes, mime_type = await mcp_provider.read_reference("test-skill", "guide.md")

    assert b"Guide" in content_bytes
    assert mime_type == "text/markdown"


@pytest.mark.asyncio
async def test_read_reference_path_traversal_dotdot(mcp_provider):
    """Test path traversal protection with .. sequences."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_reference_path_traversal_embedded(mcp_provider):
    """Test path traversal protection with embedded .. in path."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "refs/../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_reference_null_bytes(mcp_provider):
    """Test that null bytes in path are rejected."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "file\x00.txt")

    assert "Null bytes" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_reference_url_encoded_traversal(mcp_provider):
    """Test path traversal protection with URL-encoded .. sequences."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "..%2f..%2fetc%2fpasswd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_reference_not_found(mcp_provider):
    """Test error when reference is not found."""
    mcp_provider.read_resource = AsyncMock(return_value=[])

    with pytest.raises(SkillNotFoundError) as exc_info:
        await mcp_provider.read_reference("test-skill", "nonexistent.md")

    assert "nonexistent.md" in str(exc_info.value)


# =============================================================================
# _on_skills_changed() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_on_skills_changed_invalidates_cache(mcp_provider):
    """Test that _on_skills_changed() invalidates the skills cache."""
    # Populate cache
    skill = Skill(
        name="test",
        description="Test",
        skill_path=UPath("/tmp/test-skill"),
        metadata={},
    )
    mcp_provider._skills_cache = [skill]

    # Call change handler
    await mcp_provider._on_skills_changed()

    # Cache should be invalidated
    assert mcp_provider._skills_cache is None


@pytest.mark.asyncio
async def test_on_skills_changed_emits_signal(mcp_provider):
    """Test that _on_skills_changed() emits the skills_changed signal."""
    # Track emitted events
    emitted_events = []
    mcp_provider.skills_changed.connect(lambda event: emitted_events.append(event))

    # Call change handler
    await mcp_provider._on_skills_changed()

    # Event should be emitted
    assert len(emitted_events) == 1
    assert emitted_events[0].resource_type == "skills"


# =============================================================================
# _format_prompt_skill_template() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_format_prompt_skill_template(mcp_provider):
    """Test template formatting for prompts with required args."""
    mock_arg = MagicMock()
    mock_arg.name = "name"
    mock_arg.description = "Your name"
    mock_arg.required = True

    mock_prompt = MagicMock()
    mock_prompt.name = "greet"
    mock_prompt.description = "Greet someone"
    mock_prompt.arguments = [mock_arg]

    template = mcp_provider._format_prompt_skill_template(mock_prompt, {})

    assert "greet" in template
    assert "Arguments" in template
    assert "name" in template
    assert "required" in template


@pytest.mark.asyncio
async def test_format_prompt_skill_template_with_provided_args(mcp_provider):
    """Test template shows provided arguments."""
    mock_arg = MagicMock()
    mock_arg.name = "name"
    mock_arg.description = "Your name"
    mock_arg.required = False

    mock_prompt = MagicMock()
    mock_prompt.name = "greet"
    mock_prompt.description = "Greet someone"
    mock_prompt.arguments = [mock_arg]

    template = mcp_provider._format_prompt_skill_template(mock_prompt, {"name": "Alice"})

    assert "Alice" in template


@pytest.mark.asyncio
async def test_format_prompt_skill_template_no_args(mcp_provider):
    """Test template without arguments."""
    mock_prompt = MagicMock()
    mock_prompt.name = "simple"
    mock_prompt.description = "Simple prompt"
    mock_prompt.arguments = []

    template = mcp_provider._format_prompt_skill_template(mock_prompt, {})

    assert "simple" in template
    assert "Arguments" not in template


# =============================================================================
# _get_skill_manifest() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skill_manifest(mcp_provider):
    """Test reading _manifest resource."""
    # _get_skill_manifest calls read_resource directly
    mcp_provider.read_resource = AsyncMock(return_value=['{"description": "From manifest"}'])

    manifest = await mcp_provider._get_skill_manifest("test-skill")

    assert manifest is not None
    assert manifest["description"] == "From manifest"


@pytest.mark.asyncio
async def test_get_skill_manifest_not_found(mcp_provider):
    """Test that None is returned when manifest not found."""
    mcp_provider.read_resource = AsyncMock(return_value=[])

    manifest = await mcp_provider._get_skill_manifest("no-manifest-skill")

    assert manifest is None


@pytest.mark.asyncio
async def test_get_skill_manifest_invalid_yaml(mcp_provider):
    """Test handling of invalid YAML in manifest."""
    mcp_provider.read_resource = AsyncMock(return_value=["not valid yaml: : :"])

    manifest = await mcp_provider._get_skill_manifest("bad-manifest-skill")

    # Should return None on parse error
    assert manifest is None


# =============================================================================
# _get_skill_description() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skill_description_from_frontmatter(mcp_provider):
    """Test extracting description from SKILL.md frontmatter."""
    content = """---
description: Custom description from frontmatter
---

# Title

Body content.
"""
    mcp_provider.read_resource = AsyncMock(return_value=[content])

    desc = await mcp_provider._get_skill_description("test-skill", "skill://test-skill/SKILL.md")

    assert desc == "Custom description from frontmatter"


@pytest.mark.asyncio
async def test_get_skill_description_from_first_line(mcp_provider):
    """Test extracting description from first non-empty, non-header line."""
    content = """# Title Line

More content here.
"""
    mcp_provider.read_resource = AsyncMock(return_value=[content])

    desc = await mcp_provider._get_skill_description("test-skill", "skill://test-skill/SKILL.md")

    # The implementation skips lines starting with #, so "More content here." is returned
    assert "More content here" in desc


@pytest.mark.asyncio
async def test_get_skill_description_fallback(mcp_provider):
    """Test fallback to default description."""
    mcp_provider.read_resource = AsyncMock(return_value=[])

    desc = await mcp_provider._get_skill_description("my-skill", "skill://my-skill/SKILL.md")

    assert desc == "MCP skill: my-skill"


# =============================================================================
# Integration with signals
# =============================================================================


@pytest.mark.asyncio
async def test_skills_changed_signal_forwarding(mcp_provider):
    """Test that skills changes trigger the signal."""
    events = []
    mcp_provider.skills_changed.connect(lambda e: events.append(e))

    await mcp_provider._on_skills_changed()

    assert len(events) == 1
    assert events[0].provider_name == "test-mcp"


# =============================================================================
# Error Handling
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_handles_individual_skill_errors(mcp_provider):
    """Test that errors in individual skill creation don't break the whole list."""
    # Create one valid skill
    valid_skill = Skill(
        name="valid-skill",
        description="Valid",
        skill_path=UPath("/tmp/test-skill"),
        metadata={},
    )

    # Mock to return skills
    mcp_provider._get_prompt_skills = AsyncMock(return_value=[])
    mcp_provider._get_resource_skills = AsyncMock(return_value=[valid_skill])

    skills = await mcp_provider.get_skills()

    # Should still have skills even if one source had issues
    assert len(skills) == 1


@pytest.mark.asyncio
async def test_read_reference_handles_resource_error(mcp_provider):
    """Test error handling when reading resource fails."""
    mcp_provider.read_resource = AsyncMock(side_effect=Exception("Connection failed"))

    with pytest.raises(SkillNotFoundError):
        await mcp_provider.read_reference("test-skill", "guide.md")


# =============================================================================
# read_reference() Double references/ Prefix Tests (Bug #2 Fix)
# =============================================================================


@pytest.mark.asyncio
async def test_read_reference_without_references_prefix(mcp_provider):
    """Test that read_reference constructs URI with references/ prefix when ref_path lacks it."""
    mcp_provider.read_resource = AsyncMock(return_value=["# Guide\n\nGuide content"])

    content_bytes, mime_type = await mcp_provider.read_reference("test-skill", "guide.md")

    # Verify the URI constructed includes references/ prefix
    # URI format: skill://{skill_name}/references/{path} (no provider prefix)
    call_args = mcp_provider.read_resource.call_args
    uri = call_args[0][0]
    assert uri == "skill://test-skill/references/guide.md"
    assert "test-mcp" not in uri  # Provider name should NOT be in URI
    assert b"Guide" in content_bytes


@pytest.mark.asyncio
async def test_read_reference_with_references_prefix(mcp_provider):
    """Test that read_reference avoids double references/ prefix when ref_path already has it."""
    mcp_provider.read_resource = AsyncMock(return_value=["# Phase 3\n\nExecution content"])

    content_bytes, mime_type = await mcp_provider.read_reference(
        "test-skill", "references/phase_3_execution.md"
    )

    # Verify the URI does NOT have double references/
    # URI format: skill://{skill_name}/references/{path} (no provider prefix)
    call_args = mcp_provider.read_resource.call_args
    uri = call_args[0][0]
    assert uri == "skill://test-skill/references/phase_3_execution.md"
    assert "references/references" not in uri
    assert "test-mcp" not in uri  # Provider name should NOT be in URI
    assert b"Phase 3" in content_bytes


@pytest.mark.asyncio
async def test_read_reference_url_encoded_with_references_prefix(mcp_provider):
    """Test that URL-encoded path with references/ prefix avoids double prefix."""
    mcp_provider.read_resource = AsyncMock(return_value=["# Doc\n\nContent"])

    # URL-encoded "references/file.md"
    content_bytes, mime_type = await mcp_provider.read_reference(
        "test-skill", "references%2Ffile.md"
    )

    call_args = mcp_provider.read_resource.call_args
    uri = call_args[0][0]
    assert "references/references" not in uri
    assert "test-mcp" not in uri  # Provider name should NOT be in URI


@pytest.mark.asyncio
async def test_read_reference_underscore_skill_name(mcp_provider):
    """Test that read_reference preserves underscore skill names in URI.

    MCP server skill directories use directory names as-is (e.g., systematic_troubleshooting).
    The URI must use the original name with underscores, NOT the kebab-case normalized form.
    """
    mcp_provider.read_resource = AsyncMock(return_value=["# Diagnostic Phases\n\nPhase content"])

    content_bytes, mime_type = await mcp_provider.read_reference(
        "systematic_troubleshooting", "references/diagnostic-phases.md"
    )

    # Verify the URI uses the original underscore name
    call_args = mcp_provider.read_resource.call_args
    uri = call_args[0][0]
    assert uri == "skill://systematic_troubleshooting/references/diagnostic-phases.md"
    assert "test-mcp" not in uri
    assert b"Diagnostic Phases" in content_bytes
