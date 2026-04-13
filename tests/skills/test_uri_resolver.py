"""Tests for URI resolver and skill URI parsing.

This module provides comprehensive tests for:
- ResolvedSkillURI.parse() with various URI formats
- Path traversal detection and security checks
- Provider name validation
- Skill name validation
- URL decoding
- SkillURIResolver provider registration and resolution
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.resource_providers.base import ResourceProvider
from agentpool.skills.exceptions import SecurityError, SkillNotFoundError
from agentpool.skills.uri_resolver import (
    ResolvedSkillURI,
    SkillURIResolver,
    _is_valid_provider_name,
    _validate_provider_name,
    _validate_skill_name,
)

if TYPE_CHECKING:
    from agentpool.skills.skill import Skill


# =============================================================================
# ResolvedSkillURI.parse() - Basic URI Parsing
# =============================================================================


def test_parse_basic_uri() -> None:
    """Test parsing basic skill://provider/skill-name URI."""
    uri = "skill://local/python-expert"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider == "local"
    assert result.skill_name == "python-expert"
    assert result.reference_path is None


def test_parse_uri_with_reference_path() -> None:
    """Test parsing URI with reference path."""
    uri = "skill://local/python-expert/references/guide.md"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider == "local"
    assert result.skill_name == "python-expert"
    assert result.reference_path == "references/guide.md"


def test_parse_uri_with_deep_reference_path() -> None:
    """Test parsing URI with deeply nested reference path."""
    uri = "skill://local/my-skill/a/b/c/d/file.md"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider == "local"
    assert result.skill_name == "my-skill"
    assert result.reference_path == "a/b/c/d/file.md"


def test_parse_bare_skill_name() -> None:
    """Test parsing bare skill name without scheme."""
    uri = "my-skill"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider is None
    assert result.skill_name == "my-skill"
    assert result.reference_path is None


def test_parse_bare_skill_name_with_hyphens() -> None:
    """Test parsing bare skill name with multiple hyphens."""
    uri = "my-test-skill-name"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider is None
    assert result.skill_name == "my-test-skill-name"
    assert result.reference_path is None


# =============================================================================
# ResolvedSkillURI.parse() - URL Decoding
# =============================================================================


def test_parse_uri_with_encoded_characters() -> None:
    """Test parsing URI with URL-encoded characters (hyphen decoded)."""
    uri = "skill://local/my%2Dskill"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider == "local"
    assert result.skill_name == "my-skill"


def test_parse_uri_with_encoded_hyphen() -> None:
    """Test parsing URI with encoded hyphen."""
    uri = "skill://local/my%2Dskill"
    result = ResolvedSkillURI.parse(uri)

    assert result.skill_name == "my-skill"


def test_parse_uri_with_multiple_encoded_chars() -> None:
    """Test parsing URI with multiple encoded characters."""
    uri = "skill://provider-name/skill%2Dname"
    result = ResolvedSkillURI.parse(uri)

    assert result.provider == "provider-name"
    assert result.skill_name == "skill-name"


# =============================================================================
# ResolvedSkillURI.parse() - Path Traversal Detection
# =============================================================================


def test_parse_uri_with_path_traversal_in_skill_name() -> None:
    """Test that path traversal in skill name raises SecurityError."""
    uri = "skill://local/../etc/passwd"

    with pytest.raises(SecurityError, match="Path traversal"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_path_traversal_in_reference_path() -> None:
    """Test that path traversal in reference path raises SecurityError."""
    uri = "skill://local/my-skill/../../../etc/passwd"

    with pytest.raises(SecurityError, match="Path traversal"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_encoded_path_traversal() -> None:
    """Test that encoded path traversal raises SecurityError."""
    uri = "skill://local/my-skill/%2e%2e/%2e%2e/secret"

    with pytest.raises(SecurityError, match="Path traversal"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_single_dot_is_allowed() -> None:
    """Test that single dot in path is allowed."""
    uri = "skill://local/my-skill/./file.md"
    result = ResolvedSkillURI.parse(uri)

    assert result.skill_name == "my-skill"
    assert result.reference_path == "./file.md"


# =============================================================================
# ResolvedSkillURI.parse() - Null Byte Detection
# =============================================================================


def test_parse_uri_with_null_byte() -> None:
    """Test that null byte in URI raises SecurityError."""
    uri = "skill://local/my-skill\x00"

    with pytest.raises(SecurityError, match="null bytes"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_encoded_null_byte() -> None:
    """Test that encoded null byte raises SecurityError."""
    uri = "skill://local/my-skill%00"

    with pytest.raises(SecurityError, match="null bytes"):
        ResolvedSkillURI.parse(uri)


# =============================================================================
# ResolvedSkillURI.parse() - Invalid URI Format
# =============================================================================


def test_parse_uri_with_invalid_scheme() -> None:
    """Test that invalid scheme raises ValueError."""
    uri = "http://local/my-skill"

    with pytest.raises(ValueError, match="Invalid URI scheme"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_empty_path() -> None:
    """Test that empty path raises ValueError."""
    uri = "skill://local/"

    with pytest.raises(ValueError, match="path is empty"):
        ResolvedSkillURI.parse(uri)


def test_parse_uri_with_only_scheme() -> None:
    """Test that URI with only scheme raises ValueError."""
    uri = "skill://"

    with pytest.raises(ValueError, match="path is empty"):
        ResolvedSkillURI.parse(uri)


# =============================================================================
# Provider Name Validation
# =============================================================================


def test_is_valid_provider_name_with_valid_names() -> None:
    """Test valid provider names are accepted."""
    valid_names = [
        "local",
        "my-provider",
        "my_provider",
        "provider123",
        "Provider",
        "a",
        "a" * 63,  # Max length
    ]

    for name in valid_names:
        assert _is_valid_provider_name(name) is True, f"{name!r} should be valid"


def test_is_valid_provider_name_with_invalid_names() -> None:
    """Test invalid provider names are rejected."""
    invalid_names = [
        "",
        "a" * 64,  # Too long
        "my.provider",  # Dot not allowed
        "my/provider",  # Slash not allowed
        "my:provider",  # Colon not allowed
        "my provider",  # Space not allowed
    ]

    for name in invalid_names:
        assert _is_valid_provider_name(name) is False, f"{name!r} should be invalid"


def test_validate_provider_name_with_valid_name() -> None:
    """Test that valid provider name is returned unchanged."""
    name = "my-provider"
    result = _validate_provider_name(name)

    assert result == name


def test_validate_provider_name_with_invalid_name_raises() -> None:
    """Test that invalid provider name raises SecurityError."""
    with pytest.raises(SecurityError, match="Invalid provider name"):
        _validate_provider_name("invalid.name")


def test_validate_provider_name_with_empty_name() -> None:
    """Test that empty provider name raises SecurityError."""
    with pytest.raises(SecurityError, match="Invalid provider name"):
        _validate_provider_name("")


def test_validate_provider_name_with_too_long_name() -> None:
    """Test that too long provider name raises SecurityError."""
    with pytest.raises(SecurityError, match="Invalid provider name"):
        _validate_provider_name("a" * 64)


# =============================================================================
# Skill Name Validation
# =============================================================================


def test_validate_skill_name_with_valid_names() -> None:
    """Test valid skill names are accepted."""
    valid_names = [
        "my-skill",
        "skill123",
        "a",
        "abc-def-ghi",
        "python-expert",
    ]

    for name in valid_names:
        result = _validate_skill_name(name)
        assert result == name, f"{name!r} should be valid"


def test_validate_skill_name_converts_to_lowercase() -> None:
    """Test that skill name is normalized to lowercase."""
    # Note: The validator expects already-lowercase input
    # This tests that mixed case is rejected
    with pytest.raises(SecurityError, match="must be lowercase"):
        _validate_skill_name("My-Skill")


def test_validate_skill_name_rejects_uppercase() -> None:
    """Test that uppercase letters are rejected."""
    with pytest.raises(SecurityError, match="must be lowercase"):
        _validate_skill_name("Python-Expert")


def test_validate_skill_name_rejects_starting_hyphen() -> None:
    """Test that skill name starting with hyphen is rejected."""
    with pytest.raises(SecurityError, match="cannot start or end with a hyphen"):
        _validate_skill_name("-my-skill")


def test_validate_skill_name_rejects_ending_hyphen() -> None:
    """Test that skill name ending with hyphen is rejected."""
    with pytest.raises(SecurityError, match="cannot start or end with a hyphen"):
        _validate_skill_name("my-skill-")


def test_validate_skill_name_rejects_consecutive_hyphens() -> None:
    """Test that consecutive hyphens are rejected."""
    with pytest.raises(SecurityError, match="consecutive hyphens"):
        _validate_skill_name("my--skill")


def test_validate_skill_name_rejects_invalid_characters() -> None:
    """Test that invalid characters are rejected."""
    invalid_names = [
        "my_skill",  # Underscore
        "my.skill",  # Dot
        "my/skill",  # Slash
        "my skill",  # Space
    ]

    for name in invalid_names:
        with pytest.raises(SecurityError, match="invalid characters"):
            _validate_skill_name(name)


def test_validate_skill_name_rejects_empty() -> None:
    """Test that empty skill name is rejected."""
    with pytest.raises(SecurityError, match="non-empty"):
        _validate_skill_name("")


def test_validate_skill_name_rejects_whitespace_only() -> None:
    """Test that whitespace-only skill name is rejected."""
    with pytest.raises(SecurityError, match="non-empty"):
        _validate_skill_name("   ")


def test_validate_skill_name_strips_whitespace() -> None:
    """Test that skill name is stripped of whitespace."""
    result = _validate_skill_name("  my-skill  ")
    assert result == "my-skill"


# =============================================================================
# SkillURIResolver - Provider Registration
# =============================================================================


def test_resolver_register_provider() -> None:
    """Test registering a provider."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)

    resolver.register_provider("local", provider)

    assert resolver.get_provider("local") is provider


def test_resolver_register_multiple_providers() -> None:
    """Test registering multiple providers."""
    resolver = SkillURIResolver()
    provider1 = MagicMock(spec=ResourceProvider)
    provider2 = MagicMock(spec=ResourceProvider)

    resolver.register_provider("local", provider1)
    resolver.register_provider("remote", provider2)

    assert resolver.get_provider("local") is provider1
    assert resolver.get_provider("remote") is provider2


def test_resolver_unregister_provider() -> None:
    """Test unregistering a provider."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)

    resolver.register_provider("local", provider)
    assert resolver.get_provider("local") is provider

    resolver.unregister_provider("local")
    assert resolver.get_provider("local") is None


def test_resolver_unregister_nonexistent_provider() -> None:
    """Test unregistering a nonexistent provider does not raise."""
    resolver = SkillURIResolver()

    # Should not raise
    resolver.unregister_provider("nonexistent")


def test_resolver_list_providers() -> None:
    """Test listing registered providers."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)

    resolver.register_provider("local", provider)
    resolver.register_provider("remote", provider)

    providers = resolver.list_providers()
    assert "local" in providers
    assert "remote" in providers
    assert len(providers) == 2


def test_resolver_register_with_invalid_provider_name() -> None:
    """Test that invalid provider name raises SecurityError."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)

    with pytest.raises(SecurityError, match="Invalid provider name"):
        resolver.register_provider("invalid.name", provider)


# =============================================================================
# SkillURIResolver - Skill Resolution
# =============================================================================


@pytest.mark.asyncio
async def test_resolver_resolve_with_explicit_provider() -> None:
    """Test resolving skill with explicit provider."""
    resolver = SkillURIResolver()
    skill = MagicMock(spec="Skill")
    skill.name = "my-skill"
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[skill])

    resolver.register_provider("local", provider)
    result = await resolver.resolve("skill://local/my-skill")

    assert result is skill


@pytest.mark.asyncio
async def test_resolver_resolve_with_bare_skill_name() -> None:
    """Test resolving bare skill name across all providers."""
    resolver = SkillURIResolver()
    skill = MagicMock(spec="Skill")
    skill.name = "my-skill"
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[skill])

    resolver.register_provider("local", provider)
    result = await resolver.resolve("my-skill")

    assert result is skill


@pytest.mark.asyncio
async def test_resolver_resolve_not_found_in_provider() -> None:
    """Test that SkillNotFoundError is raised when skill not in provider."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[])

    resolver.register_provider("local", provider)

    with pytest.raises(SkillNotFoundError, match="not found"):
        await resolver.resolve("skill://local/missing-skill")


@pytest.mark.asyncio
async def test_resolver_resolve_not_found_any_provider() -> None:
    """Test that SkillNotFoundError is raised when skill not in any provider."""
    resolver = SkillURIResolver()
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[])

    resolver.register_provider("local", provider)

    with pytest.raises(SkillNotFoundError, match="not found"):
        await resolver.resolve("missing-skill")


@pytest.mark.asyncio
async def test_resolver_resolve_unregistered_provider() -> None:
    """Test that ValueError is raised for unregistered provider."""
    resolver = SkillURIResolver()

    with pytest.raises(ValueError, match="not registered"):
        await resolver.resolve("skill://unregistered/my-skill")


@pytest.mark.asyncio
async def test_resolver_resolve_searches_multiple_providers() -> None:
    """Test that resolver searches all providers for bare skill name."""
    resolver = SkillURIResolver()

    skill1 = MagicMock(spec="Skill")
    skill1.name = "skill-1"
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[])

    skill2 = MagicMock(spec="Skill")
    skill2.name = "skill-2"
    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill2])

    resolver.register_provider("provider1", provider1)
    resolver.register_provider("provider2", provider2)

    result = await resolver.resolve("skill-2")

    assert result is skill2


@pytest.mark.asyncio
async def test_resolver_resolve_first_match_wins() -> None:
    """Test that first matching skill is returned when duplicates exist."""
    resolver = SkillURIResolver()

    skill1 = MagicMock(spec="Skill")
    skill1.name = "my-skill"
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[skill1])

    skill2 = MagicMock(spec="Skill")
    skill2.name = "my-skill"
    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill2])

    resolver.register_provider("provider1", provider1)
    resolver.register_provider("provider2", provider2)

    result = await resolver.resolve("my-skill")

    # First provider's skill should be returned
    assert result is skill1
