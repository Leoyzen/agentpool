from __future__ import annotations

from pathlib import PurePosixPath
from unittest.mock import AsyncMock, MagicMock

import pytest
from upathtools import UPath

from agentpool.agents.context import AgentContext
from agentpool.resource_providers.skills_instruction import SkillsInstructionProvider
from agentpool.skills.skill import Skill


@pytest.fixture
def mock_registry():
    registry = MagicMock()
    skill1 = Skill(
        name="skill1",
        description="description1",
        skill_path=UPath("/tmp/skill1"),
        instructions="instructions1",
    )
    skill2 = Skill(
        name="skill2",
        description="description2",
        skill_path=UPath("/tmp/skill2"),
        instructions="instructions2",
    )

    # Mock items() to return a list of tuples
    registry.items.return_value = [("skill1", skill1), ("skill2", skill2)]
    # Mock bool(registry) if needed, but registry is a MagicMock which is True
    return registry


@pytest.fixture
def mock_ctx():
    # Mock that works as both AgentContext and RunContext
    ctx = MagicMock(spec=AgentContext)
    ctx.node = MagicMock()
    ctx.node.tools = MagicMock()
    ctx.node.tools.providers = []
    # For RunContext compatibility
    ctx.deps = ctx
    return ctx


@pytest.mark.asyncio
async def test_skills_instruction_off(mock_registry, mock_ctx):
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="off",
    )
    result = await provider._generate_skills_instruction(mock_ctx)
    assert result == ""


@pytest.mark.asyncio
async def test_skills_instruction_metadata(mock_registry, mock_ctx):
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
    )
    result = await provider._generate_skills_instruction(mock_ctx)
    assert "<available-skills>" in result
    assert "<skill>" in result
    assert "<id>skill1</id>" in result
    assert "<name>skill1</name>" in result
    assert "<description>description1</description>" in result
    assert "</skill>" in result
    assert "<instructions>" not in result
    assert "</available-skills>" in result


@pytest.mark.asyncio
async def test_skills_instruction_full(mock_registry, mock_ctx):
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="full",
    )
    # Mock skill.load_instructions as it might be used
    for skill in mock_registry.values():
        skill.load_instructions = MagicMock(return_value=skill.instructions)

    result = await provider._generate_skills_instruction(mock_ctx)
    assert "<available-skills>" in result
    assert '<skill_content id="skill1" name="skill1">' in result
    assert "<skill-instruction>" in result
    assert "instructions1" in result
    assert "Base directory for this skill: skill://local/skill1/" in result


@pytest.mark.asyncio
async def test_skills_instruction_max_skills(mock_registry, mock_ctx):
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
        max_skills=1,
    )
    result = await provider._generate_skills_instruction(mock_ctx)

    assert "<id>skill1</id>" in result
    assert "<id>skill2</id>" not in result


@pytest.mark.asyncio
async def test_skills_instruction_override_from_context(mock_registry, mock_ctx):
    # This test checks if provider looks at agent context for overrides
    # We'll need to define how the override is stored in mock_ctx.node

    # Case: Global is metadata, but agent override is full
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
    )

    # Mock a provider in agent that has override attributes
    mock_skills_tool = MagicMock()
    mock_skills_tool.name = "skills"
    mock_skills_tool.injection_mode = "full"
    mock_skills_tool.max_skills = 5

    mock_ctx.node.tools.providers = [mock_skills_tool]

    # For full mode, we need load_instructions
    for skill in mock_registry.values():
        skill.load_instructions = MagicMock(return_value=skill.instructions)

    result = await provider._generate_skills_instruction(mock_ctx)

    assert "<skill-instruction>" in result
    assert "instructions1" in result


@pytest.mark.asyncio
async def test_skills_instruction_override_off(mock_registry, mock_ctx):
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
    )

    mock_skills_tool = MagicMock()
    mock_skills_tool.name = "skills"
    mock_skills_tool.injection_mode = "off"

    mock_ctx.node.tools.providers = [mock_skills_tool]

    result = await provider._generate_skills_instruction(mock_ctx)

    assert result == ""


@pytest.mark.asyncio
async def test_skills_instruction_with_skill_provider(mock_registry, mock_ctx):
    """SkillsInstructionProvider should use skill_provider when available."""
    # Create an MCP skill that's NOT in the local registry
    mcp_skill = Skill(
        name="mcp_skill",
        description="MCP skill description",
        skill_path=PurePosixPath("mcp://server/tool"),
        instructions="MCP skill instructions",
    )

    # Mock skill_provider that returns MCP skills
    mock_skill_provider = MagicMock()
    mock_skill_provider.get_skills = AsyncMock(return_value=[mcp_skill])

    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
        skill_provider=mock_skill_provider,
    )

    result = await provider._generate_skills_instruction(mock_ctx)

    # Should include MCP skills from skill_provider (not registry skills,
    # since skill_provider replaces registry when available)
    assert "<id>mcp-skill</id>" in result
    assert "<name>mcp-skill</name>" in result
    assert "<description>MCP skill description</description>" in result
    assert "<uri>mcp:/server/tool</uri>" in result

    # Verify skill_provider.get_skills was called
    mock_skill_provider.get_skills.assert_called_once()

    # Registry should NOT be consulted when skill_provider is available
    mock_registry.items.assert_not_called()


@pytest.mark.asyncio
async def test_skills_instruction_fallback_to_registry(mock_registry, mock_ctx):
    """SkillsInstructionProvider should fallback to registry when skill_provider is None."""
    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
    )

    result = await provider._generate_skills_instruction(mock_ctx)

    # Should only include local registry skills
    assert "<id>skill1</id>" in result
    assert "<id>skill2</id>" in result
    assert "<id>mcp_skill</id>" not in result
    mock_registry.items.assert_called_once()


@pytest.mark.asyncio
async def test_skills_instruction_skill_provider_empty(mock_registry, mock_ctx):
    """When skill_provider returns empty list, should return empty."""
    mock_skill_provider = MagicMock()
    mock_skill_provider.get_skills = AsyncMock(return_value=[])

    provider = SkillsInstructionProvider(
        skills_registry=mock_registry,
        injection_mode="metadata",
        skill_provider=mock_skill_provider,
    )

    result = await provider._generate_skills_instruction(mock_ctx)

    # When skill_provider is set but returns empty, result is empty
    assert result == ""
    mock_registry.items.assert_not_called()
