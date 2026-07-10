"""Tests for MCP-based skills integration.

This module tests that MCP-based skills are properly exposed through:
- GET /command endpoint (for OpenCode)
- load_skill tool
- list_skills tool
"""

from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.skills.skill import Skill
from agentpool_toolsets.builtin.skills import list_skills, load_skill


@pytest.fixture
def mock_agent_context():
    """Create a mock agent context with pool that has MCP-based skills."""
    ctx = MagicMock()
    ctx.pool = MagicMock()

    # Mock local skills (empty - simulating no local skills)
    ctx.pool.skills.list_skills.return_value = []
    ctx.pool.skills.get_skill_instructions.return_value = ""

    # Mock MCP-based skills (underscores are normalized to hyphens per Agent Skills Spec)
    mcp_skill_hyphen = Skill(
        name="systematic-troubleshooting",
        description="Systematic troubleshooting guide",
        skill_path=PurePosixPath("skill://mcp_provider/systematic-troubleshooting"),
        instructions="# Troubleshooting Guide\n\nFollow these steps...",
        metadata={"skill_type": "resource", "provider": "mcp_provider"},
    )
    # Underscore names are normalized to hyphens at Skill creation time
    mcp_skill_from_underscore = Skill(
        name="equipment_operation_assistant",
        description="Equipment operation assistant guide",
        skill_path=PurePosixPath("skill://mcp_provider/equipment-operation-assistant"),
        instructions="# Equipment Operation\n\nFollow these procedures...",
        metadata={"skill_type": "resource", "provider": "mcp_provider"},
    )

    # Mock skill_provider
    mock_provider = MagicMock()
    mock_provider.get_skills = AsyncMock(return_value=[mcp_skill_hyphen, mcp_skill_from_underscore])
    mock_provider.get_skill_instructions = AsyncMock(
        return_value="# Troubleshooting Guide\n\nFollow these steps..."
    )
    # Set up capabilities as a list with a child provider that has get_skills
    mock_child_provider = MagicMock()
    mock_child_provider.get_skills = AsyncMock(
        return_value=[mcp_skill_hyphen, mcp_skill_from_underscore]
    )
    mock_child_provider.get_skill_instructions = AsyncMock(
        return_value="# Troubleshooting Guide\n\nFollow these steps..."
    )
    mock_provider.capabilities = [mock_child_provider]
    ctx.pool.skill_provider = mock_provider

    # Mock skill_resolver
    mock_resolver = MagicMock()
    mock_resolver.list_providers.return_value = ["mcp_provider"]
    mock_provider_from_resolver = MagicMock()
    mock_provider_from_resolver.get_skills = AsyncMock(
        return_value=[mcp_skill_hyphen, mcp_skill_from_underscore]
    )
    mock_resolver.get_provider.return_value = mock_provider_from_resolver

    # Mock resolve method to return appropriate skill based on name
    async def mock_resolve(uri: str):
        if "systematic-troubleshooting" in uri:
            return mcp_skill_hyphen
        if "equipment-operation-assistant" in uri:
            return mcp_skill_from_underscore
        raise ValueError(f"Skill not found: {uri}")

    mock_resolver.resolve = mock_resolve
    ctx.pool.skill_resolver = mock_resolver

    return ctx, mcp_skill_hyphen, mcp_skill_from_underscore


@pytest.mark.asyncio
async def test_list_skills_includes_mcp_skills(mock_agent_context):
    """Test that list_skills includes MCP-based skills."""
    ctx, _mcp_skill_hyphen, _mcp_skill_from_underscore = mock_agent_context

    result = await list_skills(ctx)

    # Should include MCP-based skills (all normalized to kebab-case)
    assert "systematic-troubleshooting" in result
    assert "equipment-operation-assistant" in result
    print(f"list_skills output:\n{result}")


@pytest.mark.asyncio
async def test_load_skill_finds_mcp_skills_with_hyphen(mock_agent_context):
    """Test that load_skill can find MCP-based skills with hyphen names."""
    ctx, _mcp_skill_hyphen, _ = mock_agent_context

    result = await load_skill(ctx, "systematic-troubleshooting")

    # Should successfully load the skill
    assert "systematic-troubleshooting" in result
    assert "Troubleshooting Guide" in result
    print(f"load_skill (hyphen) output:\n{result}")


@pytest.mark.asyncio
async def test_load_skill_normalizes_underscore_to_hyphen(mock_agent_context):
    """Test that load_skill normalizes underscore names to hyphens per spec."""
    ctx, _, _mcp_skill_from_underscore = mock_agent_context

    # Calling with underscores should work because normalization converts to hyphens
    result = await load_skill(ctx, "equipment_operation_assistant")

    # The skill name is normalized to kebab-case
    assert "equipment-operation-assistant" in result
    assert "Equipment operation assistant guide" in result
    print(f"load_skill (underscore normalized) output:\n{result}")


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
    ctx.pool.skill_resolver = None

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
