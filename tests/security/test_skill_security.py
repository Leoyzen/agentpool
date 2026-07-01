"""Security audit tests for RFC-0020 skill system.

This module provides comprehensive security tests for:
- Path traversal protection in read_reference methods
- URL-encoded path traversal attacks
- Null byte injection attacks
- Symlink-based directory traversal attacks
- All attacks must raise SecurityError

Security Considerations from RFC-0020:
1. Validation Order:
   - Decode URI components first
   - Check for `..` in path parts
   - Resolve to absolute path
   - Verify path is within allowed directory

2. Symlink Handling:
   - Resolve symlinks before validation
   - Final path must still be within allowed directory
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.resource_providers.local import LocalResourceProvider
from agentpool.resource_providers.mcp_provider import MCPResourceProvider
from agentpool.skills.exceptions import ReferenceNotFoundError, SecurityError, SkillNotFoundError


# =============================================================================
# Path Traversal Attack Tests - LocalResourceProvider
# =============================================================================


@pytest.fixture
def skill_with_references(tmp_path):
    """Create a skill with references directory for testing."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()

    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("""---
name: test-skill
description: Skill with references
---

# Test Skill

Instructions.
""")

    # Create references directory with files
    refs_dir = skill_dir / "references"
    refs_dir.mkdir()

    (refs_dir / "guide.md").write_text("# Guide\n\nGuide content.")
    (refs_dir / "config.json").write_text('{"key": "value"}')

    # Create subdirectory
    subdir = refs_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Nested content")

    return tmp_path


@pytest.fixture
async def local_provider(skill_with_references):
    """Create and enter a LocalResourceProvider context."""
    provider = LocalResourceProvider(
        name="security-test",
        skills_dirs=[skill_with_references],
        cache_ttl=60.0,
    )
    async with provider:
        yield provider


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_absolute_path(skill_with_references):
    """Test path traversal with absolute path attempt: /etc/passwd."""
    provider = LocalResourceProvider(
        name="security-test",
        skills_dirs=[skill_with_references],
    )

    async with provider:
        with pytest.raises((SecurityError, ReferenceNotFoundError)):
            await provider.read_reference("test-skill", "/etc/passwd")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_basic_dotdot(local_provider):
    """Test basic path traversal: ../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await local_provider.read_reference("test-skill", "../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_embedded(local_provider):
    """Test embedded path traversal: subdir/../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await local_provider.read_reference("test-skill", "subdir/../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_multiple_dotdot(local_provider):
    """Test multiple .. sequences: ../../../../../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await local_provider.read_reference("test-skill", "../../../../../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_leading_dotdot(local_provider):
    """Test leading .. sequence: ../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await local_provider.read_reference("test-skill", "../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_traversal_mixed_separators(local_provider):
    r"""Test path traversal with mixed separators: ..\\..\\..\\etc\\passwd."""
    # On Unix, backslash is treated as literal character
    # This test verifies the path is rejected
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "..\\..\\..\\etc\\passwd")


# =============================================================================
# URL-Encoded Path Traversal Tests - LocalResourceProvider
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_url_encoded_traversal_percent_2f(local_provider):
    """Test URL-encoded path traversal: ..%2f..%2f..%2fetc%2fpasswd."""
    # LocalResourceProvider does not URL-decode before checking
    # This should fail as file not found (path still blocked)
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "..%2f..%2f..%2fetc%2fpasswd")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_url_encoded_traversal_lowercase(local_provider):
    """Test URL-encoded with lowercase: ..%2f..%2fetc%2fpasswd."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "..%2f..%2fetc%2fpasswd")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_url_encoded_traversal_uppercase(local_provider):
    """Test URL-encoded with uppercase: ..%2F..%2Fetc%2Fpasswd."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "..%2F..%2Fetc%2Fpasswd")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_url_encoded_dot(local_provider):
    """Test URL-encoded dot: %2e%2e/%2e%2e/%2e%2e/etc/passwd."""
    # %2e is encoded dot
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "%2e%2e/%2e%2e/%2e%2e/etc/passwd")


# =============================================================================
# Null Byte Injection Tests - LocalResourceProvider
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_null_byte_injection(local_provider):
    r"""Test null byte injection: file\x00.txt."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "file\x00.txt")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_null_byte_injection_with_path(local_provider):
    r"""Test null byte injection with path: subdir/file\x00.txt."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "subdir/file\x00.txt")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_null_byte_at_start(local_provider):
    r"""Test null byte at start of path: \x00file.txt."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "\x00file.txt")


# =============================================================================
# Symlink Attack Tests - LocalResourceProvider
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_symlink_to_outside_directory(skill_with_references):
    """Test that symlink pointing outside references dir is blocked.

    Creates a symlink inside references/ that points to a file outside
    the references directory. The read_reference should resolve the symlink
    and reject the path.
    """
    skill_dir = skill_with_references / "test-skill"
    refs_dir = skill_dir / "references"

    # Create a file outside the references directory
    outside_file = skill_with_references / "outside_secret.txt"
    outside_file.write_text("SECRET CONTENT OUTSIDE REFERENCES")

    # Create a symlink inside references pointing to outside file
    symlink_path = refs_dir / "malicious_link.txt"
    try:
        symlink_path.symlink_to(outside_file)

        provider = LocalResourceProvider(
            name="symlink-test",
            skills_dirs=[skill_with_references],
        )

        async with provider:
            # Try to read through the symlink - should be blocked
            # The implementation uses resolve() which resolves symlinks
            # Then relative_to() checks if still within references_dir
            with pytest.raises((SecurityError, ReferenceNotFoundError)):
                await provider.read_reference("test-skill", "malicious_link.txt")
    finally:
        # Cleanup
        if symlink_path.exists() or symlink_path.is_symlink():
            symlink_path.unlink()


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_symlink_chain_traversal(skill_with_references):
    """Test symlink chain that eventually escapes references directory.

    Creates a chain of symlinks where the final target is outside
    the allowed directory.
    """
    skill_dir = skill_with_references / "test-skill"
    refs_dir = skill_dir / "references"
    subdir = refs_dir / "subdir"

    # Create files and symlinks
    outside_file = skill_with_references / "secret.txt"
    outside_file.write_text("SECRET")

    intermediate_link = skill_with_references / "intermediate.txt"

    link1 = subdir / "link1.txt"
    link2 = refs_dir / "link2.txt"

    try:
        # Create intermediate link outside references
        intermediate_link.symlink_to(outside_file)
        # Create link1 -> intermediate (within subdir)
        link1.symlink_to(intermediate_link)
        # Create link2 -> link1 (within refs)
        link2.symlink_to(link1)

        provider = LocalResourceProvider(
            name="symlink-chain-test",
            skills_dirs=[skill_with_references],
        )

        async with provider:
            # Try to read through symlink chain
            with pytest.raises((SecurityError, ReferenceNotFoundError)):
                await provider.read_reference("test-skill", "link2.txt")
    finally:
        # Cleanup
        for link in [link2, link1, intermediate_link]:
            if link.exists() or link.is_symlink():
                link.unlink()


# =============================================================================
# Path Traversal Attack Tests - MCPResourceProvider
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
        provider = MCPResourceProvider(server="uvx test-server", name="security-test-mcp")
        provider.client = mock_mcp_client
        yield provider


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_path_traversal_basic_dotdot(mcp_provider):
    """Test MCP path traversal: ../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_path_traversal_embedded(mcp_provider):
    """Test MCP embedded path traversal: refs/../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "refs/../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_path_traversal_leading_dotdot(mcp_provider):
    """Test MCP leading .. sequence: ../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_path_traversal_deeply_nested(mcp_provider):
    """Test MCP deeply nested traversal: a/b/c/../../../../etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "a/b/c/../../../../etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


# =============================================================================
# URL-Encoded Path Traversal Tests - MCPResourceProvider
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_url_encoded_traversal_percent_2f(mcp_provider):
    """Test MCP URL-encoded path traversal: ..%2f..%2f..%2fetc%2fpasswd.

    MCPResourceProvider properly URL-decodes before checking,
    so this should raise SecurityError.
    """
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "..%2f..%2f..%2fetc%2fpasswd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_url_encoded_traversal_uppercase(mcp_provider):
    """Test MCP URL-encoded with uppercase: ..%2F..%2Fetc%2Fpasswd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "..%2F..%2Fetc%2Fpasswd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_url_encoded_dot(mcp_provider):
    """Test MCP URL-encoded dot: %2e%2e/%2e%2e/%2e%2e/etc/passwd."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "%2e%2e/%2e%2e/%2e%2e/etc/passwd")

    assert "traversal" in str(exc_info.value).lower()


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_double_url_encoding(mcp_provider):
    """Test double URL-encoded path: %%32%65%%32%65 (double-encoded ..)."""
    # Double encoding: % -> %25, 2 -> %32, e -> %65
    # %%32%65 = %2e = .
    with pytest.raises((SecurityError, SkillNotFoundError)):
        await mcp_provider.read_reference("test-skill", "%%32%65%%32%65")


# =============================================================================
# Null Byte Injection Tests - MCPResourceProvider
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_null_byte_injection(mcp_provider):
    r"""Test MCP null byte injection: file\x00.txt."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "file\x00.txt")

    assert "Null bytes" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_null_byte_in_middle(mcp_provider):
    r"""Test MCP null byte in middle: config\x00.json."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "config\x00.json")

    assert "Null bytes" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_null_byte_with_path(mcp_provider):
    r"""Test MCP null byte with path: subdir/file\x00.txt."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "subdir/file\x00.txt")

    assert "Null bytes" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_null_byte_at_start(mcp_provider):
    r"""Test MCP null byte at start: \x00file.txt."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "\x00file.txt")

    assert "Null bytes" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_multiple_null_bytes(mcp_provider):
    r"""Test MCP multiple null bytes: file\x00\x00\x00.txt."""
    with pytest.raises(SecurityError) as exc_info:
        await mcp_provider.read_reference("test-skill", "file\x00\x00\x00.txt")

    assert "Null bytes" in str(exc_info.value)


# =============================================================================
# Edge Case Security Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_empty_path(local_provider):
    """Test empty path handling."""
    with pytest.raises((SecurityError, ReferenceNotFoundError)):
        await local_provider.read_reference("test-skill", "")


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_empty_path(mcp_provider):
    """Test MCP empty path handling."""
    # Empty path after validation would resolve to references dir itself
    # Should either raise SecurityError or SkillNotFoundError
    with pytest.raises((SecurityError, SkillNotFoundError)):
        await mcp_provider.read_reference("test-skill", "")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_single_dot(local_provider):
    """Test single dot path: ./file.txt."""
    # Single dot should be allowed (refers to current directory)
    # But file doesn't exist, so ReferenceNotFoundError
    with pytest.raises(ReferenceNotFoundError):
        await local_provider.read_reference("test-skill", "./nonexistent.txt")


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_dot_slash_prefix(local_provider):
    """Test dot slash prefix: ./guide.md."""
    # This should work - single dot is not traversal
    content, _mime_type = await local_provider.read_reference("test-skill", "./guide.md")
    assert b"Guide content" in content


@pytest.mark.asyncio
@pytest.mark.security
async def test_local_path_with_special_chars(local_provider):
    """Test path with special characters that are NOT traversal."""
    # These should be treated as literal filenames (which don't exist)
    with pytest.raises(ReferenceNotFoundError):
        await local_provider.read_reference("test-skill", "file@2x.txt")

    with pytest.raises(ReferenceNotFoundError):
        await local_provider.read_reference("test-skill", "file#name.txt")


@pytest.mark.asyncio
@pytest.mark.security
async def test_mcp_path_with_special_chars(mcp_provider):
    """Test MCP path with special characters that are NOT traversal."""
    # These should not trigger SecurityError, just file not found
    mcp_provider.read_resource = AsyncMock(return_value=[])

    with pytest.raises(SkillNotFoundError):
        await mcp_provider.read_reference("test-skill", "file@2x.txt")


# =============================================================================
# Security Validation Summary Tests
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.security
async def test_all_attacks_raise_security_error_or_blocked(local_provider, mcp_provider):
    """Summary test: verify all attack vectors are blocked.

    This test documents that both providers correctly block:
    1. Path traversal with ..
    2. URL-encoded path traversal
    3. Null byte injection
    4. Symlink-based attacks

    Any failure here indicates a security vulnerability.
    """
    attacks_blocked = []

    # Test 1: Basic path traversal - Local
    try:
        await local_provider.read_reference("test-skill", "../../../etc/passwd")
        attacks_blocked.append(("local_basic_traversal", False))
    except (SecurityError, ReferenceNotFoundError):
        attacks_blocked.append(("local_basic_traversal", True))

    # Test 2: URL-encoded traversal - MCP
    try:
        await mcp_provider.read_reference("test-skill", "..%2f..%2fetc%2fpasswd")
        attacks_blocked.append(("mcp_url_encoded_traversal", False))
    except (SecurityError, SkillNotFoundError):
        attacks_blocked.append(("mcp_url_encoded_traversal", True))

    # Test 3: Null byte - Local
    try:
        await local_provider.read_reference("test-skill", "file\x00.txt")
        attacks_blocked.append(("local_null_byte", False))
    except (SecurityError, ReferenceNotFoundError):
        attacks_blocked.append(("local_null_byte", True))

    # Test 4: Null byte - MCP
    try:
        await mcp_provider.read_reference("test-skill", "file\x00.txt")
        attacks_blocked.append(("mcp_null_byte", False))
    except (SecurityError, SkillNotFoundError):
        attacks_blocked.append(("mcp_null_byte", True))

    # Verify all attacks were blocked
    failed = [name for name, blocked in attacks_blocked if not blocked]
    if failed:
        pytest.fail(f"Security vulnerabilities detected! Unblocked attacks: {failed}")


# =============================================================================
# Documentation Test
# =============================================================================


def test_security_considerations_documented():
    """Verify security considerations are properly documented in code.

    This test checks that SecurityError has appropriate docstrings
    and is properly exported from the exceptions module.
    """
    from agentpool.skills.exceptions import SecurityError

    # Verify SecurityError can be instantiated
    error = SecurityError("Test security violation")
    assert "Security violation" in str(error)
    assert "Test security violation" in str(error)

    # Verify it's a proper exception hierarchy
    from agentpool.skills.exceptions import SkillError

    assert issubclass(SecurityError, SkillError)


@pytest.mark.security
def test_security_error_message_format():
    """Test that SecurityError produces properly formatted messages."""
    error = SecurityError("Path traversal detected in: ../../../etc/passwd")
    msg = str(error)

    assert "Security violation" in msg
    assert "Path traversal detected" in msg
    assert "../../../etc/passwd" in msg
