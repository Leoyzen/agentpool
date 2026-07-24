"""Integration tests for SkillManagerCap with embedded MCP server.

Tests cover:
  1. Skill with embedded MCP server — tools from MCP available alongside skill instructions
  2. MCP child lifecycle (enter/exit)
  3. Partial failure (MCP server fails, skill still works)
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any, Self
from unittest.mock import MagicMock

import pytest

from agentpool.capabilities.resource_protocols import (
    SkillEntry,
    SkillResource,
)
from agentpool.capabilities.skill_manager_cap import SkillManagerCap
from agentpool.skills.skill import Skill


pytestmark = pytest.mark.unit


# ---- Mock MCP server capability ----


class MockMcpServerCap(SkillResource):
    """Mock McpServerCap that implements SkillResource for integration testing."""

    def __init__(
        self,
        name: str = "mock-mcp",
        skills: list[SkillEntry] | None = None,
        content_map: dict[str, str] | None = None,
        fail_on_connect: bool = False,
    ) -> None:
        self.name = name
        self._skills = skills or []
        self._content_map = content_map or {}
        self._fail = fail_on_connect
        self._entered = False

    def get_serialization_name(self) -> str:
        return self.name

    async def list_skills(self) -> Any:
        if self._fail:
            raise RuntimeError("MCP server connection failed")
        return list(self._skills)

    async def read_skill(self, name: str) -> str | None:
        if self._fail:
            raise RuntimeError("MCP server connection failed")
        return self._content_map.get(name)

    async def skill_exists(self, name: str) -> bool:
        if self._fail:
            raise RuntimeError("MCP server connection failed")
        return name in self._content_map

    async def __aenter__(self) -> Self:
        self._entered = True
        return self

    async def __aexit__(self, *args: object) -> None:
        self._entered = False

    def get_toolset(self) -> Any:
        return None

    def get_instructions(self) -> str | None:
        return None


# ---- Test 1: Skill with embedded MCP server ----


async def test_skill_with_mcp_instructions_and_remote_skills() -> None:
    """Skill instructions available alongside MCP-provided remote skills."""
    local_skill = Skill(
        name="my-skill",
        description="A local skill",
        skill_path=PurePosixPath("skill://local/my-skill"),
        instructions="Use this skill for awesome things.",
    )

    remote_skills = [
        SkillEntry(
            name="mcp-skill",
            description="Skill from MCP server",
            uri="skill://mock-mcp/mcp-skill",
            source="remote",
        ),
    ]
    mcp_child = MockMcpServerCap(
        name="mock-mcp",
        skills=remote_skills,
        content_map={"mcp-skill": "Remote skill content"},
    )

    cap = SkillManagerCap(
        local_skills={"my-skill": local_skill},
        children=[mcp_child],  # type: ignore[arg-type]
    )

    # get_instructions returns [metadata_str, dynamic_callable]
    instructions = cap.get_instructions()
    assert instructions is not None
    assert isinstance(instructions, list)
    # First element is static metadata string
    metadata = instructions[0]
    assert isinstance(metadata, str)
    assert 'name="my-skill"' in metadata
    # Second element is the dynamic callable
    assert callable(instructions[1])

    # list_skills returns both local and remote
    all_skills = await cap.list_skills()
    names = [s.name for s in all_skills]
    assert "my-skill" in names
    assert "mcp-skill" in names

    # read_skill works for both
    local_content = await cap.read_skill("my-skill")
    assert local_content == "Use this skill for awesome things."

    remote_content = await cap.read_skill("mcp-skill")
    assert remote_content == "Remote skill content"


# ---- Test 2: MCP child lifecycle (enter/exit) ----


async def test_mcp_child_lifecycle_enter_exit() -> None:
    """MCP child is entered on __aenter__ and exited on __aexit__."""
    mcp_child = MockMcpServerCap(name="lifecycle-mcp")
    cap = SkillManagerCap(
        local_skills={},
        children=[mcp_child],  # type: ignore[arg-type]
    )

    assert not mcp_child._entered
    await cap.__aenter__()
    assert mcp_child._entered
    await cap.__aexit__(None, None, None)
    assert not mcp_child._entered


# ---- Test 3: Partial failure (MCP server fails, skill still works) ----


async def test_partial_failure_mcp_fails_skill_still_works() -> None:
    """When MCP server fails, local skills still work."""
    local_skill = Skill(
        name="resilient-skill",
        description="Works even when MCP fails",
        skill_path=PurePosixPath("skill://local/resilient-skill"),
        instructions="Local instructions still available.",
    )

    failing_mcp = MockMcpServerCap(
        name="failing-mcp",
        fail_on_connect=True,
    )

    cap = SkillManagerCap(
        local_skills={"resilient-skill": local_skill},
        children=[failing_mcp],  # type: ignore[arg-type]
    )

    # Local skill instructions still available
    instructions = cap.get_instructions()
    assert instructions is not None
    assert isinstance(instructions, list)
    metadata = instructions[0]
    assert isinstance(metadata, str)
    assert 'name="resilient-skill"' in metadata
    assert callable(instructions[1])

    # list_skills doesn't crash — returns local only
    skills = await cap.list_skills()
    names = [s.name for s in skills]
    assert "resilient-skill" in names

    # read_skill for local skill works
    content = await cap.read_skill("resilient-skill")
    assert content == "Local instructions still available."

    # skill_exists for local skill works
    assert await cap.skill_exists("resilient-skill")


# ---- Test 4: get_instructions returns [metadata, callable] with dynamic content ----


async def test_get_instructions_dynamic_callable_produces_skill_content() -> None:
    """get_instructions returns [metadata, callable]; callable produces <skill_content>."""
    local_skill = Skill(
        name="injected-skill",
        description="Skill to inject",
        skill_path=PurePosixPath("skill://local/injected-skill"),
        instructions="Injected instructions content.",
    )

    mcp_child = MockMcpServerCap(name="present-mcp")
    cap = SkillManagerCap(
        local_skills={"injected-skill": local_skill},
        children=[mcp_child],  # type: ignore[arg-type]
    )

    result = cap.get_instructions()
    assert result is not None
    assert isinstance(result, list)
    assert len(result) == 2

    # Static metadata
    metadata = result[0]
    assert isinstance(metadata, str)
    assert "<available-skills>" in metadata
    assert 'name="injected-skill"' in metadata

    # Dynamic callable — no matcher, so all skills are injected (backward compat)
    dynamic_fn = result[1]
    assert callable(dynamic_fn)

    # Build a mock RunContext with messages
    ctx = MagicMock()
    ctx.messages = []

    content = await dynamic_fn(ctx)
    assert content is not None
    assert "Injected instructions content." in content
    assert '<skill_content name="injected-skill">' in content


async def test_get_instructions_with_matcher_fn() -> None:
    """get_instructions callable respects matcher_fn for skill selection."""
    skill_a = Skill(
        name="skill-a",
        description="Skill A",
        skill_path=PurePosixPath("skill://local/skill-a"),
        instructions="Content A.",
    )
    skill_b = Skill(
        name="skill-b",
        description="Skill B",
        skill_path=PurePosixPath("skill://local/skill-b"),
        instructions="Content B.",
    )

    def matcher(messages: list[object]) -> list[str]:
        return ["skill-a"]  # Only match skill-a

    cap = SkillManagerCap(
        local_skills={"skill-a": skill_a, "skill-b": skill_b},
        matcher_fn=matcher,
    )

    result = cap.get_instructions()
    assert result is not None
    assert isinstance(result, list)

    ctx = MagicMock()
    ctx.messages = []

    content = await result[1](ctx)
    assert content is not None
    assert "Content A." in content
    assert "Content B." not in content


async def test_get_instructions_with_always_active() -> None:
    """get_instructions callable includes always_active skills even with matcher."""
    skill_a = Skill(
        name="skill-a",
        description="Skill A",
        skill_path=PurePosixPath("skill://local/skill-a"),
        instructions="Content A.",
    )
    skill_b = Skill(
        name="skill-b",
        description="Skill B",
        skill_path=PurePosixPath("skill://local/skill-b"),
        instructions="Content B.",
    )

    def matcher(messages: list[object]) -> list[str]:
        return ["skill-a"]

    cap = SkillManagerCap(
        local_skills={"skill-a": skill_a, "skill-b": skill_b},
        matcher_fn=matcher,
        always_active={"skill-b"},
    )

    result = cap.get_instructions()
    assert result is not None

    ctx = MagicMock()
    ctx.messages = []

    content = await result[1](ctx)
    assert content is not None
    assert "Content A." in content
    assert "Content B." in content  # always_active bypasses matcher
