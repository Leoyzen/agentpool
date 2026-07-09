"""Tests for SkillCapability ResourceSource protocol implementation.

Verifies that ``SkillCapability`` implements the ``ResourceSource`` protocol:
- ``list()`` returns skills with correct URI scheme
- ``read()`` returns markdown content
- ``exists()`` returns correct bool
- ``isinstance(cap, ResourceSource)`` is True
- ``read()`` on unknown skill raises ``ResourceNotFoundError``
- ``on_change()`` returns None for static sources
"""

from __future__ import annotations

import pathlib
from pathlib import PurePosixPath

import pytest
from pydantic_ai.capabilities import AbstractCapability
from upathtools import UPath

from agentpool.capabilities.resource_source import (
    Resource,
    ResourceContent,
    ResourceNotFoundError,
    ResourceSource,
)
from agentpool.skills.capability import SkillCapability
from agentpool.skills.skill import Skill


pytestmark = pytest.mark.unit


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def sample_skill(tmp_path: pathlib.Path) -> Skill:
    """Create a Skill with a real SKILL.md file on disk."""
    skill_dir = tmp_path / "test-skill"
    skill_dir.mkdir()
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text(
        "---\n"
        "name: test-skill\n"
        "description: A test skill for ResourceSource tests\n"
        "---\n"
        "This is the skill instruction content.\n"
        "It has multiple lines.\n",
        encoding="utf-8",
    )
    return Skill.from_skill_dir(UPath(str(skill_dir)))


@pytest.fixture
def capability(sample_skill: Skill) -> SkillCapability:
    """Create a SkillCapability wrapping the sample skill."""
    return SkillCapability(skill=sample_skill)


@pytest.fixture
def virtual_skill() -> Skill:
    """Create a virtual skill (no filesystem) with pre-set instructions."""
    return Skill(
        name="virtual-skill",
        description="A virtual skill",
        skill_path=PurePosixPath("skill://virtual-skill"),
        instructions="Virtual skill instructions.",
    )


@pytest.fixture
def virtual_capability(virtual_skill: Skill) -> SkillCapability:
    """Create a SkillCapability wrapping a virtual skill."""
    return SkillCapability(skill=virtual_skill)


# =============================================================================
# isinstance checks
# =============================================================================


def test_isinstance_resource_source(capability: SkillCapability) -> None:
    """SkillCapability satisfies the ResourceSource protocol at runtime."""
    assert isinstance(capability, ResourceSource)


def test_isinstance_abstract_capability(capability: SkillCapability) -> None:
    """SkillCapability is still an AbstractCapability."""
    assert isinstance(capability, AbstractCapability)


# =============================================================================
# list()
# =============================================================================


def test_list_returns_single_resource(capability: SkillCapability) -> None:
    """list() returns one Resource for the wrapped skill."""
    resources = capability.list()
    assert len(resources) == 1


def test_list_returns_correct_uri_scheme(capability: SkillCapability) -> None:
    """list() returns a Resource with skill:// URI scheme."""
    resources = capability.list()
    assert resources[0].uri == "skill://test-skill"


def test_list_returns_correct_name(capability: SkillCapability) -> None:
    """list() returns a Resource with the skill's name."""
    resources = capability.list()
    assert resources[0].name == "test-skill"


def test_list_returns_markdown_mime_type(capability: SkillCapability) -> None:
    """list() returns a Resource with text/markdown MIME type."""
    resources = capability.list()
    assert resources[0].mime_type == "text/markdown"


def test_list_returns_description(capability: SkillCapability) -> None:
    """list() returns a Resource with the skill's description."""
    resources = capability.list()
    assert resources[0].description == "A test skill for ResourceSource tests"


def test_list_returns_resource_instances(capability: SkillCapability) -> None:
    """list() returns Resource dataclass instances."""
    resources = capability.list()
    assert all(isinstance(r, Resource) for r in resources)


def test_list_returns_empty_for_virtual_skill(virtual_capability: SkillCapability) -> None:
    """list() still returns a resource for virtual skills."""
    resources = virtual_capability.list()
    assert len(resources) == 1
    assert resources[0].uri == "skill://virtual-skill"


# =============================================================================
# read()
# =============================================================================


def test_read_returns_content(capability: SkillCapability) -> None:
    """read() returns the SKILL.md instruction content."""
    content = capability.read("skill://test-skill")
    assert isinstance(content, ResourceContent)
    assert isinstance(content.content, str)
    assert "This is the skill instruction content." in content.content


def test_read_returns_correct_uri(capability: SkillCapability) -> None:
    """read() returns ResourceContent with the requested URI."""
    content = capability.read("skill://test-skill")
    assert content.uri == "skill://test-skill"


def test_read_returns_markdown_mime_type(capability: SkillCapability) -> None:
    """read() returns ResourceContent with text/markdown MIME type."""
    content = capability.read("skill://test-skill")
    assert content.mime_type == "text/markdown"


def test_read_virtual_skill_returns_content(virtual_capability: SkillCapability) -> None:
    """read() works for virtual skills with pre-set instructions."""
    content = virtual_capability.read("skill://virtual-skill")
    assert content.content == "Virtual skill instructions."


def test_read_unknown_skill_raises_resource_not_found(capability: SkillCapability) -> None:
    """read() with an unknown URI raises ResourceNotFoundError."""
    with pytest.raises(ResourceNotFoundError) as exc_info:
        capability.read("skill://nonexistent-skill")
    assert exc_info.value.uri == "skill://nonexistent-skill"


def test_read_non_skill_uri_raises_resource_not_found(capability: SkillCapability) -> None:
    """read() with a non-skill URI raises ResourceNotFoundError."""
    with pytest.raises(ResourceNotFoundError):
        capability.read("mcp://some-server/resource")


def test_read_empty_uri_raises_resource_not_found(capability: SkillCapability) -> None:
    """read() with an empty URI raises ResourceNotFoundError."""
    with pytest.raises(ResourceNotFoundError):
        capability.read("")


# =============================================================================
# exists()
# =============================================================================


def test_exists_returns_true_for_valid_uri(capability: SkillCapability) -> None:
    """exists() returns True for the skill's own URI."""
    assert capability.exists("skill://test-skill") is True


def test_exists_returns_false_for_unknown_uri(capability: SkillCapability) -> None:
    """exists() returns False for an unknown skill URI."""
    assert capability.exists("skill://nonexistent-skill") is False


def test_exists_returns_false_for_non_skill_uri(capability: SkillCapability) -> None:
    """exists() returns False for a non-skill URI."""
    assert capability.exists("mcp://some-server/resource") is False


def test_exists_returns_false_for_empty_uri(capability: SkillCapability) -> None:
    """exists() returns False for an empty URI."""
    assert capability.exists("") is False


def test_exists_does_not_raise(capability: SkillCapability) -> None:
    """exists() must not raise for any input."""
    # Should not raise even for malformed URIs
    assert capability.exists("not-a-uri") is False
    assert capability.exists("skill://") is False


# =============================================================================
# on_change()
# =============================================================================


def test_on_change_returns_none(capability: SkillCapability) -> None:
    """on_change() returns None for static SkillCapability."""
    assert capability.on_change() is None


def test_on_change_returns_none_for_virtual(virtual_capability: SkillCapability) -> None:
    """on_change() returns None for virtual skills too."""
    assert virtual_capability.on_change() is None


# =============================================================================
# ResourceSource protocol completeness
# =============================================================================


def test_all_protocol_methods_exist(capability: SkillCapability) -> None:
    """SkillCapability has all four ResourceSource protocol methods."""
    assert callable(capability.list)
    assert callable(capability.read)
    assert callable(capability.exists)
    assert callable(capability.on_change)


def test_existing_methods_unchanged(capability: SkillCapability) -> None:
    """Existing SkillCapability methods still work after ResourceSource addition."""
    # get_instructions should still work
    instructions = capability.get_instructions()
    assert instructions is not None
    assert "This is the skill instruction content." in instructions

    # get_toolset should still return None (no tools configured)
    assert capability.get_toolset() is None

    # get_ordering should still return the expected ordering
    ordering = capability.get_ordering()
    assert ordering is not None
