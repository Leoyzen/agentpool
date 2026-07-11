"""Integration tests for SkillManagerCap with embedded MCP server.

Tests cover:
  1. Skill with embedded MCP server — tools from MCP available alongside skill instructions
  2. MCP child lifecycle (enter/exit)
  3. Partial failure (MCP server fails, skill still works)
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart
import pytest

from agentpool.capabilities.resource_protocols import (
    SkillEntry,
    SkillResource,
    ToolEntry,
    ToolResult,
)
from agentpool.capabilities.skill_manager_cap import SkillManagerCap
from agentpool.skills.skill import Skill


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

    async def __aenter__(self) -> MockMcpServerCap:
        self._entered = True
        return self

    async def __aexit__(self, *args: Any) -> None:
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

    # get_instructions returns metadata for local skills
    instructions = cap.get_instructions()
    assert instructions is not None
    assert 'name="my-skill"' in instructions

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
    assert 'name="resilient-skill"' in instructions

    # list_skills doesn't crash — returns local only
    skills = await cap.list_skills()
    names = [s.name for s in skills]
    assert "resilient-skill" in names

    # read_skill for local skill works
    content = await cap.read_skill("resilient-skill")
    assert content == "Local instructions still available."

    # skill_exists for local skill works
    assert await cap.skill_exists("resilient-skill")


# ---- Test 4: before_model_request injects local skill with MCP child present ----


async def test_before_model_request_with_mcp_child() -> None:
    """before_model_request injects local skill instructions even with MCP children."""
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

    sys_part = SystemPromptPart(content="Base prompt.")
    req = ModelRequest(parts=[sys_part, UserPromptPart("Hello.")])
    ctx_mock = MagicMock()
    rc = MagicMock()
    rc.messages = [req]

    await cap.before_model_request(ctx_mock, rc)  # type: ignore[arg-type]

    assert "Injected instructions content." in sys_part.content
    assert "Base prompt." in sys_part.content
