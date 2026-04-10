"""Tests for MCP-based skills integration.

This module tests that MCP-based skills are properly exposed through:
- GET /command endpoint (for OpenCode)
- load_skill tool
- list_skills tool
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.skills.skill import Skill
from agentpool_toolsets.builtin.skills import load_skill, list_skills


@pytest.fixture
def mock_agent_context():
    """Create a mock agent context with pool that has MCP-based skills."""
    ctx = MagicMock()
    ctx.pool = MagicMock()

    # Mock local skills (empty - simulating no local skills)
    ctx.pool.skills.list_skills.return_value = []
    ctx.pool.skills.get_skill_instructions.return_value = ""

    # Mock MCP-based skills (test both hyphen and underscore formats)
    mcp_skill_hyphen = Skill(
        name="systematic-troubleshooting",
        description="Systematic troubleshooting guide",
        skill_path=PurePosixPath("skill://mcp_provider/systematic-troubleshooting"),
        instructions="# Troubleshooting Guide\n\nFollow these steps...",
        metadata={"skill_type": "resource", "provider": "mcp_provider"},
    )
    mcp_skill_underscore = Skill(
        name="systematic_troubleshooting",
        description="Systematic troubleshooting guide (underscore)",
        skill_path=PurePosixPath("skill://mcp_provider/systematic_troubleshooting"),
        instructions="# Troubleshooting Guide\n\nFollow these steps...",
        metadata={"skill_type": "resource", "provider": "mcp_provider"},
    )

    # Mock skill_provider
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[mcp_skill_hyphen, mcp_skill_underscore])
    ctx.pool.skill_provider = mock_provider

    # Mock skill_resolver
    mock_resolver = MagicMock()
    mock_resolver.list_providers.return_value = ["mcp_provider"]
    mock_provider_from_resolver = MagicMock()
    mock_provider_from_resolver.get_skills = AsyncMock(
        return_value=[mcp_skill_hyphen, mcp_skill_underscore]
    )
    mock_resolver.get_provider.return_value = mock_provider_from_resolver
    ctx.pool.skill_resolver = mock_resolver

    return ctx, mcp_skill_hyphen, mcp_skill_underscore


@pytest.mark.asyncio
async def test_list_skills_includes_mcp_skills(mock_agent_context):
    """Test that list_skills includes MCP-based skills."""
    ctx, mcp_skill_hyphen, mcp_skill_underscore = mock_agent_context

    result = await list_skills(ctx)

    # Should include both MCP-based skills
    assert "systematic-troubleshooting" in result
    assert "systematic_troubleshooting" in result
    print(f"list_skills output:\n{result}")


@pytest.mark.asyncio
async def test_load_skill_finds_mcp_skills_with_hyphen(mock_agent_context):
    """Test that load_skill can find MCP-based skills with hyphen names."""
    ctx, mcp_skill_hyphen, _ = mock_agent_context

    result = await load_skill(ctx, "systematic-troubleshooting")

    # Should successfully load the skill
    assert "systematic-troubleshooting" in result
    assert "Troubleshooting Guide" in result
    print(f"load_skill (hyphen) output:\n{result}")


@pytest.mark.asyncio
async def test_load_skill_finds_mcp_skills_with_underscore(mock_agent_context):
    """Test that load_skill can find MCP-based skills with underscore names."""
    ctx, _, mcp_skill_underscore = mock_agent_context

    result = await load_skill(ctx, "systematic_troubleshooting")

    # Should successfully load the skill with underscore
    assert "systematic_troubleshooting" in result
    assert "Troubleshooting Guide" in result
    print(f"load_skill (underscore) output:\n{result}")


@pytest.mark.asyncio
async def test_load_skill_returns_error_for_missing_skill(mock_agent_context):
    """Test that load_skill returns error for non-existent skill."""
    ctx, _, _ = mock_agent_context

    result = await load_skill(ctx, "nonexistent-skill")

    # Should return error message
    assert "not found" in result.lower() or "No skills available" in result
    print(f"load_skill error output:\n{result}")


@pytest.mark.asyncio
async def test_list_skills_shows_empty_when_no_skills():
    """Test that list_skills shows 'No skills available' when pool has no skills."""
    ctx = MagicMock()
    ctx.pool = MagicMock()
    ctx.pool.skills.list_skills.return_value = []
    ctx.pool.skill_provider = None

    result = await list_skills(ctx)

    assert "No skills available" in result


@pytest.mark.asyncio
async def test_load_skill_with_uri(mock_agent_context):
    """Test that load_skill works with skill:// URI."""
    ctx, mcp_skill_hyphen, _ = mock_agent_context

    # Mock the resolver to return the skill
    ctx.pool.skill_resolver.resolve = AsyncMock(return_value=mcp_skill_hyphen)

    result = await load_skill(ctx, "skill://mcp_provider/systematic-troubleshooting")

    # Should successfully load via URI
    assert "systematic-troubleshooting" in result
    print(f"load_skill with URI output:\n{result}")
