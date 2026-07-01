"""Tests for LocalResourceProvider.

This module provides comprehensive tests for:
- LocalResourceProvider initialization
- get_skills() with caching
- get_skill() by name
- get_skill_instructions()
- get_references() listing files
- read_reference() with path traversal protection
- Cache invalidation on skill changes
- SecurityError on path traversal
- ReferenceNotFoundError for missing refs
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentpool.resource_providers.local import LocalResourceProvider
from agentpool.skills.exceptions import ReferenceNotFoundError, SecurityError, SkillNotFoundError
from agentpool.skills.skill import Skill


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def skill_directory(tmp_path):
    """Create a temporary skill directory with test skills."""
    # Create test-skill directory with SKILL.md
    test_skill_dir = tmp_path / "test-skill"
    test_skill_dir.mkdir()

    skill_md = test_skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-skill
description: A test skill for unit testing
---

# Test Skill Instructions

These are the test skill instructions.
""")

    # Create another skill
    another_skill_dir = tmp_path / "another-skill"
    another_skill_dir.mkdir()

    another_skill_md = another_skill_dir / "SKILL.md"
    another_skill_md.write_text("""---
name: another-skill
description: Another test skill
---

# Another Skill

More instructions here.
""")

    return tmp_path


@pytest.fixture
def skill_with_references(tmp_path):
    """Create a skill with references directory."""
    skill_dir = tmp_path / "ref-skill"
    skill_dir.mkdir()

    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: ref-skill
description: Skill with references
---

# Ref Skill

Instructions.
""")

    # Create references directory with files
    refs_dir = skill_dir / "references"
    refs_dir.mkdir()

    (refs_dir / "guide.md").write_text("# Guide\n\nGuide content.")
    (refs_dir / "examples.py").write_text("# Examples\nprint('hello')")
    (refs_dir / "config.json").write_text('{"key": "value"}')

    # Create subdirectory with file
    subdir = refs_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Nested content")

    return tmp_path


@pytest.fixture
async def local_provider(skill_directory):
    """Create and enter a LocalResourceProvider context."""
    provider = LocalResourceProvider(
        name="local-test",
        skills_dirs=[skill_directory],
        cache_ttl=60.0,
    )
    async with provider:
        yield provider


# =============================================================================
# Initialization Tests
# =============================================================================


@pytest.mark.asyncio
async def test_local_provider_initialization(skill_directory):
    """Test LocalResourceProvider initialization."""
    provider = LocalResourceProvider(
        name="test-provider",
        skills_dirs=[skill_directory],
        owner="test-owner",
        cache_ttl=120.0,
    )

    assert provider.name == "test-provider"
    assert provider.owner == "test-owner"
    assert provider.cache_ttl == 120.0
    assert provider.kind == "custom"


@pytest.mark.asyncio
async def test_local_provider_context_manager(skill_directory):
    """Test async context manager entry and exit."""
    provider = LocalResourceProvider(
        name="test-provider",
        skills_dirs=[skill_directory],
    )

    async with provider:
        assert provider._cache_valid is True
        # Skills should be discovered
        skills = await provider.get_skills()
        assert len(skills) == 2

    # After exit, cache should be cleared
    assert provider._cache_valid is False


@pytest.mark.asyncio
async def test_local_provider_multiple_directories(tmp_path):
    """Test LocalResourceProvider with multiple skill directories."""
    # Create two separate skill directories
    dir1 = tmp_path / "dir1"
    dir1.mkdir()
    skill1_dir = dir1 / "skill-one"
    skill1_dir.mkdir()
    (skill1_dir / "SKILL.md").write_text("""---
name: skill-one
description: First skill
---

Content.
""")

    dir2 = tmp_path / "dir2"
    dir2.mkdir()
    skill2_dir = dir2 / "skill-two"
    skill2_dir.mkdir()
    (skill2_dir / "SKILL.md").write_text("""---
name: skill-two
description: Second skill
---

Content.
""")

    provider = LocalResourceProvider(
        name="multi-dir",
        skills_dirs=[dir1, dir2],
    )

    async with provider:
        skills = await provider.get_skills()
        skill_names = {s.name for s in skills}
        assert skill_names == {"skill-one", "skill-two"}


# =============================================================================
# get_skills() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_returns_all_skills(local_provider):
    """Test that get_skills() returns all discovered skills."""
    skills = await local_provider.get_skills()

    assert len(skills) == 2
    skill_names = {s.name for s in skills}
    assert skill_names == {"test-skill", "another-skill"}


@pytest.mark.asyncio
async def test_get_skills_returns_skill_objects(local_provider):
    """Test that get_skills() returns Skill objects."""
    skills = await local_provider.get_skills()

    for skill in skills:
        assert isinstance(skill, Skill)
        assert skill.name
        assert skill.description


@pytest.mark.asyncio
async def test_get_skills_empty_directory(tmp_path):
    """Test get_skills() with empty directory."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()

    provider = LocalResourceProvider(
        name="empty-test",
        skills_dirs=[empty_dir],
    )

    async with provider:
        skills = await provider.get_skills()
        assert skills == []


@pytest.mark.asyncio
async def test_get_skills_caching(local_provider):
    """Test that get_skills() uses cache."""
    # First call - should populate cache
    skills1 = await local_provider.get_skills()

    # Second call - should use cache
    skills2 = await local_provider.get_skills()

    assert skills1 == skills2
    assert local_provider._cache_valid is True


@pytest.mark.asyncio
async def test_get_skills_cache_invalidation_on_change(skill_directory):
    """Test cache invalidation when skills change."""
    provider = LocalResourceProvider(
        name="cache-test",
        skills_dirs=[skill_directory],
    )

    async with provider:
        # Populate cache
        skills1 = await provider.get_skills()
        assert len(skills1) == 2
        assert provider._cache_valid is True

        # Invalidate cache
        provider._invalidate_cache()
        assert provider._cache_valid is False

        # Next call should refresh from registry
        skills2 = await provider.get_skills()
        assert len(skills2) == 2
        assert provider._cache_valid is True


# =============================================================================
# get_skill() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skill_by_name(local_provider):
    """Test getting a specific skill by name."""
    skill = await local_provider.get_skill("test-skill")

    assert isinstance(skill, Skill)
    assert skill.name == "test-skill"
    assert "test skill" in skill.description.lower()


@pytest.mark.asyncio
async def test_get_skill_not_found(local_provider):
    """Test that SkillNotFoundError is raised for non-existent skill."""
    with pytest.raises(SkillNotFoundError) as exc_info:
        await local_provider.get_skill("non-existent-skill")

    assert "non-existent-skill" in str(exc_info.value)
    # Should include available skills
    assert "test-skill" in str(exc_info.value)
    assert "another-skill" in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_skill_uses_cache(local_provider):
    """Test that get_skill() uses the cache."""
    # First call - should populate cache
    skill1 = await local_provider.get_skill("test-skill")

    # Second call - should use cache
    skill2 = await local_provider.get_skill("test-skill")

    assert skill1 is skill2


# =============================================================================
# get_skill_instructions() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skill_instructions(local_provider):
    """Test getting full instructions for a skill."""
    instructions = await local_provider.get_skill_instructions("test-skill")

    assert "Test Skill Instructions" in instructions
    # The instructions are the content after frontmatter (body)
    assert "test skill instructions" in instructions.lower()


@pytest.mark.asyncio
async def test_get_skill_instructions_not_found(local_provider):
    """Test that SkillNotFoundError is raised for non-existent skill."""
    with pytest.raises(SkillNotFoundError):
        await local_provider.get_skill_instructions("non-existent")


# =============================================================================
# get_references() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_references(skill_with_references):
    """Test listing reference files for a skill."""
    provider = LocalResourceProvider(
        name="ref-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        refs = await provider.get_references("ref-skill")

        # Should list direct files in references directory (non-recursive)
        assert "guide.md" in refs
        assert "examples.py" in refs
        assert "config.json" in refs
        # Subdirectories themselves are not listed
        assert "subdir" not in refs


@pytest.mark.asyncio
async def test_get_references_no_references_directory(skill_directory):
    """Test get_references() when references directory doesn't exist."""
    provider = LocalResourceProvider(
        name="no-refs",
        skills_dirs=[skill_directory],
    )

    async with provider:
        refs = await provider.get_references("test-skill")
        assert refs == []


@pytest.mark.asyncio
async def test_get_references_skill_not_found(local_provider):
    """Test that SkillNotFoundError is raised for non-existent skill."""
    with pytest.raises(SkillNotFoundError):
        await local_provider.get_references("non-existent")


@pytest.mark.asyncio
async def test_get_references_sorted(skill_with_references):
    """Test that references are returned sorted."""
    provider = LocalResourceProvider(
        name="sorted-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        refs = await provider.get_references("ref-skill")

        # Should be sorted alphabetically
        assert refs == sorted(refs)


# =============================================================================
# read_reference() Tests
# =============================================================================


@pytest.mark.asyncio
async def test_read_reference(skill_with_references):
    """Test reading a reference file."""
    provider = LocalResourceProvider(
        name="read-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        content, mime_type = await provider.read_reference("ref-skill", "guide.md")

        assert b"Guide content" in content
        assert mime_type == "text/markdown"


@pytest.mark.asyncio
async def test_read_reference_python_file(skill_with_references):
    """Test reading a Python reference file."""
    provider = LocalResourceProvider(
        name="py-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        content, mime_type = await provider.read_reference("ref-skill", "examples.py")

        assert b"print" in content
        assert mime_type in {"text/x-python", "application/octet-stream"}


@pytest.mark.asyncio
async def test_read_reference_nested(skill_with_references):
    """Test reading a nested reference file."""
    provider = LocalResourceProvider(
        name="nested-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        content, _mime_type = await provider.read_reference("ref-skill", "subdir/nested.txt")

        assert b"Nested content" in content


@pytest.mark.asyncio
async def test_read_reference_not_found(skill_with_references):
    """Test ReferenceNotFoundError for missing reference."""
    provider = LocalResourceProvider(
        name="notfound-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        with pytest.raises(ReferenceNotFoundError) as exc_info:
            await provider.read_reference("ref-skill", "nonexistent.md")

        assert "nonexistent.md" in str(exc_info.value)


@pytest.mark.asyncio
async def test_read_reference_skill_not_found(skill_with_references):
    """Test SkillNotFoundError for non-existent skill."""
    provider = LocalResourceProvider(
        name="noskill-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        with pytest.raises(SkillNotFoundError):
            await provider.read_reference("non-existent", "guide.md")


# =============================================================================
# Security Tests - Path Traversal Protection
# =============================================================================


@pytest.mark.asyncio
async def test_read_reference_path_traversal_dotdot(skill_with_references):
    """Test path traversal protection with .. sequences."""
    provider = LocalResourceProvider(
        name="security-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        with pytest.raises(SecurityError) as exc_info:
            await provider.read_reference("ref-skill", "../../../etc/passwd")

        assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_reference_path_traversal_embedded(skill_with_references):
    """Test path traversal protection with embedded .. in path."""
    provider = LocalResourceProvider(
        name="security-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        with pytest.raises(SecurityError) as exc_info:
            await provider.read_reference("ref-skill", "subdir/../../../etc/passwd")

        assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_read_reference_path_traversal_url_encoded(skill_with_references):
    """Test path traversal protection with URL-encoded .. sequences."""
    provider = LocalResourceProvider(
        name="security-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        # The provider checks for literal ".." in path parts before URL decoding
        # URL-encoded %2f (/) makes "..%2f.." not match the ".." check
        # So this fails as file not found rather than security error
        # (The path traversal is still blocked, just via a different error path)
        with pytest.raises(ReferenceNotFoundError):
            await provider.read_reference("ref-skill", "..%2f..%2fetc%2fpasswd")


@pytest.mark.asyncio
async def test_read_reference_null_bytes(skill_with_references):
    """Test that null bytes in path are rejected."""
    provider = LocalResourceProvider(
        name="null-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        # This would be caught by path validation
        with pytest.raises((SecurityError, ReferenceNotFoundError)):
            await provider.read_reference("ref-skill", "file\x00.txt")


# =============================================================================
# Cache Invalidation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_callback_handlers_invalidate_cache(skill_directory):
    """Test that registry callbacks invalidate cache."""
    provider = LocalResourceProvider(
        name="callback-test",
        skills_dirs=[skill_directory],
    )

    async with provider:
        # Populate cache
        await provider.get_skills()
        assert provider._cache_valid is True

        # Simulate skill addition by calling the handler directly
        mock_skill = MagicMock(spec=Skill)
        mock_skill.name = "new-skill"

        # Get the added handler and call it
        if provider._registry._skill_added_handlers:
            handler = provider._registry._skill_added_handlers[0]
            handler("new-skill", mock_skill)

        assert provider._cache_valid is False


@pytest.mark.asyncio
async def test_callback_handlers_on_remove(skill_directory):
    """Test that remove callbacks invalidate cache."""
    provider = LocalResourceProvider(
        name="remove-test",
        skills_dirs=[skill_directory],
    )

    async with provider:
        # Populate cache
        await provider.get_skills()
        assert provider._cache_valid is True

        # Simulate skill removal by calling the handler directly
        if provider._registry._skill_removed_handlers:
            handler = provider._registry._skill_removed_handlers[0]
            handler("test-skill", None)

        assert provider._cache_valid is False


# =============================================================================
# MIME Type Detection Tests
# =============================================================================


@pytest.mark.asyncio
async def test_detect_mime_type():
    """Test MIME type detection for various file types."""
    from upathtools import UPath

    provider = LocalResourceProvider(
        name="mime-test",
        skills_dirs=[],
    )

    # Test various extensions
    test_cases = [
        ("test.md", ["text/markdown"]),
        ("test.py", ["text/x-python", "text/plain"]),
        ("test.json", ["application/json"]),
        ("test.txt", ["text/plain"]),
        ("test.unknown", ["application/octet-stream"]),
    ]

    for filename, expected_types in test_cases:
        path = UPath(f"/tmp/{filename}")
        mime = provider._detect_mime_type(path)
        # MIME types can vary by system, so check it's reasonable
        assert mime in expected_types or mime == "application/octet-stream"


# =============================================================================
# Edge Cases
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_with_empty_skill_directory(tmp_path):
    """Test handling of empty skill directory (no SKILL.md)."""
    empty_skill_dir = tmp_path / "empty-skill"
    empty_skill_dir.mkdir()
    # No SKILL.md file

    provider = LocalResourceProvider(
        name="empty-skill-test",
        skills_dirs=[tmp_path],
    )

    async with provider:
        skills = await provider.get_skills()
        # Should not include empty-skill (no SKILL.md)
        assert all(s.name != "empty-skill" for s in skills)


@pytest.mark.asyncio
async def test_read_reference_directory_traversal(skill_with_references):
    """Test that directories are rejected in read_reference."""
    provider = LocalResourceProvider(
        name="dir-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        # Try to read a directory as a reference
        with pytest.raises(ReferenceNotFoundError):
            await provider.read_reference("ref-skill", "subdir")


@pytest.mark.asyncio
async def test_tilde_expansion(tmp_path):
    """Test that tilde (~) is expanded in paths."""
    # This is more of a smoke test - we can't actually use ~ in tests
    # but we can verify the initialization doesn't fail
    provider = LocalResourceProvider(
        name="tilde-test",
        skills_dirs=["~/.claude/skills"],  # This gets expanded
    )

    assert len(provider.skills_dirs) == 1
    # Should be expanded to absolute path
    assert str(provider.skills_dirs[0]) != "~/.claude/skills"


@pytest.mark.asyncio
async def test_skill_path_validation(skill_directory):
    """Test that skill paths are properly validated."""
    provider = LocalResourceProvider(
        name="path-test",
        skills_dirs=[skill_directory],
    )

    async with provider:
        skill = await provider.get_skill("test-skill")
        # skill_path should be a UPath
        assert skill.skill_path is not None
        assert skill.skill_path.exists()
