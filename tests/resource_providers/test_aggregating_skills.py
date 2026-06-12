"""Tests for AggregatingResourceProvider skill aggregation.

This module provides comprehensive tests for:
- get_skills() aggregates skills from all providers
- Skills from multiple providers with same name both included
- Empty provider list handling
- Skills changed signal forwarding from child providers
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.resource_providers.aggregating import AggregatingResourceProvider
from agentpool.resource_providers.base import ResourceChangeEvent, ResourceProvider
from agentpool.skills.exceptions import SkillNotFoundError

if TYPE_CHECKING:
    from agentpool.skills.skill import Skill


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_skill() -> MagicMock:
    """Create a mock skill."""
    skill = MagicMock(spec="Skill")
    skill.name = "test-skill"
    return skill


@pytest.fixture
def mock_provider() -> MagicMock:
    """Create a mock resource provider."""
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[])
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()
    return provider


# =============================================================================
# get_skills() - Aggregation Tests
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_aggregates_from_all_providers() -> None:
    """Test that get_skills() aggregates skills from all child providers."""
    # Create mock skills
    skill1 = MagicMock(spec="Skill")
    skill1.name = "skill-1"
    skill2 = MagicMock(spec="Skill")
    skill2.name = "skill-2"
    skill3 = MagicMock(spec="Skill")
    skill3.name = "skill-3"

    # Create providers with different skills
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[skill1, skill2])
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill3])
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    # Create aggregating provider
    aggregating = AggregatingResourceProvider([provider1, provider2])

    # Get skills
    result = await aggregating.get_skills()

    # Should have all 3 skills
    assert len(result) == 3
    assert skill1 in result
    assert skill2 in result
    assert skill3 in result


@pytest.mark.asyncio
async def test_get_skills_includes_duplicates_from_different_providers() -> None:
    """Test that skills with same name from different providers are both included."""
    # Create skills with same name from different providers
    skill1 = MagicMock(spec="Skill")
    skill1.name = "duplicate-skill"
    skill2 = MagicMock(spec="Skill")
    skill2.name = "duplicate-skill"

    # Create providers
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[skill1])
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill2])
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    # Create aggregating provider
    aggregating = AggregatingResourceProvider([provider1, provider2])

    # Get skills
    result = await aggregating.get_skills()

    # Should have both skills (duplicates preserved)
    assert len(result) == 2
    assert skill1 in result
    assert skill2 in result


@pytest.mark.asyncio
async def test_get_skills_with_empty_provider_list() -> None:
    """Test that get_skills() returns empty list with no providers."""
    aggregating = AggregatingResourceProvider([])

    result = await aggregating.get_skills()

    assert result == []


@pytest.mark.asyncio
async def test_get_skills_with_single_provider() -> None:
    """Test that get_skills() works with single provider."""
    skill1 = MagicMock(spec="Skill")
    skill1.name = "skill-1"
    skill2 = MagicMock(spec="Skill")
    skill2.name = "skill-2"

    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[skill1, skill2])
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    result = await aggregating.get_skills()

    assert len(result) == 2
    assert skill1 in result
    assert skill2 in result


@pytest.mark.asyncio
async def test_get_skills_with_empty_providers() -> None:
    """Test that get_skills() handles providers with no skills."""
    skill = MagicMock(spec="Skill")
    skill.name = "only-skill"

    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[])
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill])
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    result = await aggregating.get_skills()

    assert len(result) == 1
    assert result[0] is skill


@pytest.mark.asyncio
async def test_get_skills_preserves_order() -> None:
    """Test that get_skills() preserves order from providers."""
    skill1 = MagicMock(spec="Skill")
    skill1.name = "skill-1"
    skill2 = MagicMock(spec="Skill")
    skill2.name = "skill-2"
    skill3 = MagicMock(spec="Skill")
    skill3.name = "skill-3"

    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[skill1])
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill2, skill3])
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    result = await aggregating.get_skills()

    # Order should be preserved: provider1 skills first, then provider2
    assert result[0] is skill1
    assert result[1] is skill2
    assert result[2] is skill3


# =============================================================================
# Provider Registration and Signal Connection Tests
# =============================================================================


def test_aggregating_connects_to_child_signals_on_init() -> None:
    """Test that aggregating provider connects to child skills_changed signals."""
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    # Should connect to skills_changed for both providers
    assert provider1.skills_changed.connect.call_count == 1
    assert provider2.skills_changed.connect.call_count == 1


def test_aggregating_disconnects_from_old_providers_when_setting_new() -> None:
    """Test that old provider signals are disconnected when setting new providers."""
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    # Create with first provider
    aggregating = AggregatingResourceProvider([provider1])

    # Set new providers
    aggregating.providers = [provider2]

    # Should disconnect from old provider
    provider1.skills_changed.disconnect.assert_called_once()

    # Should connect to new provider
    provider2.skills_changed.connect.assert_called_once()


# =============================================================================
# Signal Forwarding Tests
# =============================================================================


@pytest.mark.asyncio
async def test_skills_changed_signal_forwarded() -> None:
    """Test that skills_changed signals are forwarded from child providers."""
    provider = MagicMock(spec=ResourceProvider)
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    # Track forwarded events
    forwarded_events: list[ResourceChangeEvent] = []
    aggregating.skills_changed.connect(lambda event: forwarded_events.append(event))

    # Simulate child provider emitting skills_changed
    event = ResourceChangeEvent(
        provider_name="child",
        provider_kind="mcp",
        resource_type="skills",
    )

    # Call the forward handler directly
    await aggregating._forward_skills_changed(event)

    # Event should be forwarded
    assert len(forwarded_events) == 1
    assert forwarded_events[0] is event


@pytest.mark.asyncio
async def test_tools_changed_signal_forwarded() -> None:
    """Test that tools_changed signals are forwarded from child providers."""
    provider = MagicMock(spec=ResourceProvider)
    provider.tools_changed = MagicMock()
    provider.tools_changed.connect = MagicMock()
    provider.tools_changed.disconnect = MagicMock()
    provider.prompts_changed = MagicMock()
    provider.prompts_changed.connect = MagicMock()
    provider.prompts_changed.disconnect = MagicMock()
    provider.resources_changed = MagicMock()
    provider.resources_changed.connect = MagicMock()
    provider.resources_changed.disconnect = MagicMock()
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    # Track forwarded events
    forwarded_events: list[ResourceChangeEvent] = []
    aggregating.tools_changed.connect(lambda event: forwarded_events.append(event))

    # Simulate child provider emitting tools_changed
    event = ResourceChangeEvent(
        provider_name="child",
        provider_kind="mcp",
        resource_type="tools",
    )

    await aggregating._forward_tools_changed(event)

    assert len(forwarded_events) == 1
    assert forwarded_events[0] is event


@pytest.mark.asyncio
async def test_prompts_changed_signal_forwarded() -> None:
    """Test that prompts_changed signals are forwarded from child providers."""
    provider = MagicMock(spec=ResourceProvider)
    provider.tools_changed = MagicMock()
    provider.tools_changed.connect = MagicMock()
    provider.tools_changed.disconnect = MagicMock()
    provider.prompts_changed = MagicMock()
    provider.prompts_changed.connect = MagicMock()
    provider.prompts_changed.disconnect = MagicMock()
    provider.resources_changed = MagicMock()
    provider.resources_changed.connect = MagicMock()
    provider.resources_changed.disconnect = MagicMock()
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    forwarded_events: list[ResourceChangeEvent] = []
    aggregating.prompts_changed.connect(lambda event: forwarded_events.append(event))

    event = ResourceChangeEvent(
        provider_name="child",
        provider_kind="mcp",
        resource_type="prompts",
    )

    await aggregating._forward_prompts_changed(event)

    assert len(forwarded_events) == 1
    assert forwarded_events[0] is event


@pytest.mark.asyncio
async def test_resources_changed_signal_forwarded() -> None:
    """Test that resources_changed signals are forwarded from child providers."""
    provider = MagicMock(spec=ResourceProvider)
    provider.tools_changed = MagicMock()
    provider.tools_changed.connect = MagicMock()
    provider.tools_changed.disconnect = MagicMock()
    provider.prompts_changed = MagicMock()
    provider.prompts_changed.connect = MagicMock()
    provider.prompts_changed.disconnect = MagicMock()
    provider.resources_changed = MagicMock()
    provider.resources_changed.connect = MagicMock()
    provider.resources_changed.disconnect = MagicMock()
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    forwarded_events: list[ResourceChangeEvent] = []
    aggregating.resources_changed.connect(lambda event: forwarded_events.append(event))

    event = ResourceChangeEvent(
        provider_name="child",
        provider_kind="mcp",
        resource_type="resources",
    )

    await aggregating._forward_resources_changed(event)

    assert len(forwarded_events) == 1
    assert forwarded_events[0] is event


# =============================================================================
# Provider Property Tests
# =============================================================================


def test_providers_property_returns_list() -> None:
    """Test that providers property returns the list of providers."""
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    providers = aggregating.providers

    assert len(providers) == 2
    assert provider1 in providers
    assert provider2 in providers


def test_providers_property_returns_internal_list() -> None:
    """Test that providers property returns the internal list (not a copy)."""
    provider = MagicMock(spec=ResourceProvider)
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    providers = aggregating.providers
    # The implementation returns the internal list directly
    assert providers is aggregating._providers


# =============================================================================
# Edge Cases
# =============================================================================


@pytest.mark.asyncio
async def test_get_skills_with_multiple_skills_per_provider() -> None:
    """Test aggregation with many skills from each provider."""
    skills1 = [MagicMock(spec="Skill") for _ in range(5)]
    for i, skill in enumerate(skills1):
        skill.name = f"provider1-skill-{i}"

    skills2 = [MagicMock(spec="Skill") for _ in range(3)]
    for i, skill in enumerate(skills2):
        skill.name = f"provider2-skill-{i}"

    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=skills1)
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=skills2)
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    result = await aggregating.get_skills()

    assert len(result) == 8
    for skill in skills1:
        assert skill in result
    for skill in skills2:
        assert skill in result


@pytest.mark.asyncio
async def test_get_skills_all_providers_empty() -> None:
    """Test aggregation when all providers return empty lists."""
    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[])
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[])
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    result = await aggregating.get_skills()

    assert result == []


@pytest.mark.asyncio
async def test_get_skill_instructions_propagates_provider_read_error() -> None:
    """Do not mask a provider instruction-loading failure as skill-not-found."""
    skill = MagicMock(spec="Skill")
    skill.name = "fta-causal-path-review"
    provider = MagicMock(spec=ResourceProvider)
    provider.get_skills = AsyncMock(return_value=[skill])
    provider.get_skill_instructions = AsyncMock(side_effect=RuntimeError("resource read failed"))
    provider.skills_changed = MagicMock()
    provider.skills_changed.connect = MagicMock()
    provider.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider])

    with pytest.raises(RuntimeError, match="resource read failed"):
        await aggregating.get_skill_instructions("fta-causal-path-review")


@pytest.mark.asyncio
async def test_get_skill_instructions_continues_after_skill_not_found() -> None:
    """A provider-level SkillNotFoundError still allows later providers to satisfy the skill."""
    skill = MagicMock(spec="Skill")
    skill.name = "fta-causal-path-review"

    provider1 = MagicMock(spec=ResourceProvider)
    provider1.get_skills = AsyncMock(return_value=[skill])
    provider1.get_skill_instructions = AsyncMock(
        side_effect=SkillNotFoundError("fta-causal-path-review")
    )
    provider1.skills_changed = MagicMock()
    provider1.skills_changed.connect = MagicMock()
    provider1.skills_changed.disconnect = MagicMock()

    provider2 = MagicMock(spec=ResourceProvider)
    provider2.get_skills = AsyncMock(return_value=[skill])
    provider2.get_skill_instructions = AsyncMock(return_value="instructions")
    provider2.skills_changed = MagicMock()
    provider2.skills_changed.connect = MagicMock()
    provider2.skills_changed.disconnect = MagicMock()

    aggregating = AggregatingResourceProvider([provider1, provider2])

    assert await aggregating.get_skill_instructions("fta-causal-path-review") == "instructions"
