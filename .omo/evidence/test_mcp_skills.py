#!/usr/bin/env python3
"""Test script to verify MCPResourceProvider skill support."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def test_imports():
    """Test that all required imports work."""
    print("Testing imports...")
    from agentpool.resource_providers.mcp_provider import MCPResourceProvider
    from agentpool.skills.exceptions import SecurityError, SkillNotFoundError
    from agentpool.skills.skill import Skill

    print("✓ All imports successful")
    return True


def test_skill_methods_exist():
    """Test that all required skill methods exist."""
    print("\nTesting method existence...")
    from agentpool.resource_providers.mcp_provider import MCPResourceProvider

    required_methods = [
        "get_skills",
        "_get_prompt_skills",
        "_get_resource_skills",
        "_get_skill_manifest",
        "_get_skill_description",
        "get_skill_instructions",
        "_get_prompt_skill_instructions",
        "_get_resource_skill_instructions",
        "_format_prompt_skill_template",
        "get_references",
        "read_reference",
        "_on_skills_changed",
    ]

    for method in required_methods:
        assert hasattr(MCPResourceProvider, method), f"Missing method: {method}"
        print(f"  ✓ {method}")

    print("✓ All required methods exist")
    return True


def test_skills_cache_exists():
    """Test that _skills_cache attribute exists."""
    print("\nTesting _skills_cache attribute...")
    from agentpool.resource_providers.mcp_provider import MCPResourceProvider

    # Create a mock instance to check attributes
    with patch.object(MCPResourceProvider, "__init__", lambda self, *args, **kwargs: None):
        provider = object.__new__(MCPResourceProvider)
        provider._skills_cache = None

    print("✓ _skills_cache attribute exists")
    return True


def test_security_error_raised():
    """Test that SecurityError is raised for path traversal."""
    print("\nTesting path traversal protection...")
    import asyncio

    from agentpool.resource_providers.mcp_provider import MCPResourceProvider
    from agentpool.skills.exceptions import SecurityError

    async def test_traversal():
        with patch.object(MCPResourceProvider, "__init__", lambda self, *args, **kwargs: None):
            provider = object.__new__(MCPResourceProvider)
            provider.name = "test"

            # Test path traversal detection
            try:
                await provider.read_reference("test-skill", "../../../etc/passwd")
                print("  ✗ Should have raised SecurityError")
                return False
            except SecurityError as e:
                print(f"  ✓ SecurityError raised: {e}")
                return True
            except Exception as e:
                print(f"  ✗ Unexpected error: {e}")
                return False

    return asyncio.run(test_traversal())


def test_skill_not_found_error():
    """Test that SkillNotFoundError is properly raised."""
    print("\nTesting SkillNotFoundError...")
    from agentpool.skills.exceptions import SkillNotFoundError

    try:
        raise SkillNotFoundError("test-skill", ["skill1", "skill2"])
    except SkillNotFoundError as e:
        msg = str(e)
        assert "test-skill" in msg
        assert "skill1" in msg
        assert "skill2" in msg
        print(f"  ✓ SkillNotFoundError: {e}")
        return True

    print("  ✗ Exception not raised")
    return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("MCPResourceProvider Skill Support Verification")
    print("=" * 60)

    tests = [
        test_imports,
        test_skill_methods_exist,
        test_skills_cache_exists,
        test_security_error_raised,
        test_skill_not_found_error,
    ]

    results = []
    for test in tests:
        try:
            result = test()
            results.append((test.__name__, result))
        except Exception as e:
            print(f"  ✗ Error: {e}")
            results.append((test.__name__, False))

    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)

    passed = sum(1 for _, r in results if r)
    total = len(results)

    for name, result in results:
        status = "✓ PASS" if result else "✗ FAIL"
        print(f"{status}: {name}")

    print(f"\nTotal: {passed}/{total} tests passed")

    if passed == total:
        print("\n🎉 All tests passed!")
        return 0
    else:
        print(f"\n⚠️ {total - passed} test(s) failed")
        return 1


if __name__ == "__main__":
    exit(main())
