"""F3 Manual QA: Skill/MCP integration verification.

Tests 4 scenarios:
  (a) SkillCapability appears in capability chain at position after MCP
  (b) load_skill response includes "## Activated MCP Servers" section
  (c) load_skill without tools/MCP returns identical response to pre-refactor
  (d) allowed_tools filtering works at runtime (filter function actually rejects tools)
"""

from __future__ import annotations

import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.toolsets import FilteredToolset, FunctionToolset, PrefixedToolset
import pytest
from upathtools import UPath

from agentpool import Agent
from agentpool.skills.capability import SkillCapability
from agentpool.skills.skill import Skill


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def mock_agent() -> Agent[Any]:
    """Create an agent with TestModel for get_agentlet testing."""
    model = TestModel(custom_output_text="test")
    return Agent(name="f3-qa-test-agent", model=model)


@pytest.fixture
def mock_mcp_manager() -> MagicMock:
    """Mock MCP manager that returns capabilities."""
    mcp_mgr = MagicMock()
    cap1 = MagicMock()
    cap2 = MagicMock()
    mcp_mgr.as_capability = AsyncMock(return_value=[cap1, cap2])
    return mcp_mgr


@pytest.fixture
def mock_history_processor() -> MagicMock:
    """Mock history processor callable."""
    processor = MagicMock()
    processor.__name__ = "mock_processor"
    return processor


def make_skill(
    name: str = "f3-test-skill",
    instructions: str = "f3 test instructions",
    **kwargs: Any,
) -> Skill:
    """Create a Skill with pre-set instructions (no filesystem I/O)."""
    return Skill(
        name=name,
        description="F3 verification test skill",
        skill_path=UPath("/tmp/f3-test-skill"),
        instructions=instructions,
        **kwargs,
    )


# =========================================================================
# Scenario (a): SkillCapability in capability chain after MCP
# =========================================================================


@pytest.mark.anyio
async def test_scenario_a_skill_capability_in_chain_after_mcp(
    mock_agent: Agent[Any],
    mock_mcp_manager: MagicMock,
    mock_history_processor: MagicMock,
) -> None:
    """(a) SkillCapability appears after MCP capabilities and before ProcessHistory."""
    from agentpool.skills.capability import SkillCapability

    mock_agent.mcp = mock_mcp_manager

    sk1 = make_skill("f3-skill-a")
    sk2 = make_skill("f3-skill-b")
    cap1 = SkillCapability(sk1)
    cap2 = SkillCapability(sk2)
    mock_pool = MagicMock()
    mock_pool.skill_capabilities = [cap1, cap2]
    mock_agent.agent_pool = mock_pool

    import agentpool.agents.native_agent.agent as agent_module

    captured_kwargs: dict[str, Any] = {}

    class CapturingPydanticAgent:  # type: ignore[misc]
        def __new__(cls, *args: Any, **kwargs: Any) -> Any:
            captured_kwargs.update(kwargs)
            return MagicMock()

    with (
        patch.object(
            mock_agent, "_resolve_history_processors", return_value=[mock_history_processor]
        ),
        patch.object(agent_module, "PydanticAgent", CapturingPydanticAgent),
    ):
        await mock_agent.get_agentlet(None, None, None)

    capabilities = captured_kwargs.get("capabilities", []) or []

    # Find SkillCapabilities
    skill_caps = [cap for cap in capabilities if isinstance(cap, SkillCapability)]
    assert len(skill_caps) == 2, f"Expected 2 SkillCapability instances, got {len(skill_caps)}"

    # MCP caps are MagicMock instances — track via identity
    mcp_caps = mock_mcp_manager.as_capability.return_value
    mcp_indices = [capabilities.index(c) for c in mcp_caps]

    # Find ProcessHistory index
    process_history_indices = [
        i for i, cap in enumerate(capabilities) if isinstance(cap, ProcessHistory)
    ]

    # Find SkillCapability indices
    skill_indices = [capabilities.index(cap) for cap in skill_caps]

    # Verify ordering: MCP < Skills < ProcessHistory
    last_mcp_idx = max(mcp_indices)
    first_skill_idx = min(skill_indices)
    first_ph_idx = min(process_history_indices)

    assert last_mcp_idx < first_skill_idx, (
        f"MCP caps (max idx {last_mcp_idx}) should come before "
        f"SkillCaps (min idx {first_skill_idx})"
    )
    assert max(skill_indices) < first_ph_idx, (
        f"SkillCaps (max idx {max(skill_indices)}) should come before "
        f"ProcessHistory (first idx {first_ph_idx})"
    )


# =========================================================================
# Scenario (b): load_skill response includes "## Activated MCP Servers"
# =========================================================================


async def _make_context_for_load_skill(
    tmp_path: Any,
    skill_dir: UPath,
) -> tuple[Any, Any]:
    """Create minimal context for load_skill testing."""
    from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
    from agentpool.agents.context import AgentContext
    from agentpool_config.skills import SkillsConfig

    agent_config = NativeAgentConfig(
        name="f3_test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(
        agents={"f3_test_agent": agent_config},
        skills=SkillsConfig(
            paths=[UPath(tmp_path)],
            include_default=False,
        ),
    )
    pool = await AgentPool(manifest).__aenter__()
    agent = pool.manifest.agents["f3_test_agent"].get_agent(pool=pool)
    return AgentContext(node=agent, pool=pool), pool


@pytest.mark.integration
@pytest.mark.anyio
async def test_scenario_b_load_skill_mcp_section(tmp_path: Any) -> None:
    """) load_skill with mcp_servers returns "## Activated MCP Servers" section."""
    from agentpool_toolsets.builtin.skills import load_skill

    # Create a skill dir with MCP servers
    skill_dir = tmp_path / "f3-mcp-skill"
    skill_dir.mkdir()
    content = """---
name: f3-mcp-skill
description: F3 skill with MCP server for testing
mcp-servers:
  f3-playwright:
    command: npx
    args: ["-y", "@playwright/mcp"]
  f3-filesystem:
    command: uvx
    args: ["mcp-server-filesystem"]
---

# F3 MCP Skill

This skill tests MCP activation in load_skill response.
"""
    (skill_dir / "SKILL.md").write_text(content)

    ctx, pool = await _make_context_for_load_skill(tmp_path, UPath(skill_dir))
    try:
        result = await load_skill(ctx, "f3-mcp-skill")

        # Verify "## Activated MCP Servers" section exists
        assert "## Activated MCP Servers" in result, (
            "Response should contain '## Activated MCP Servers' section"
        )
        # Verify server names appear
        assert "f3-playwright" in result, "MCP server name 'f3-playwright' should appear"
        assert "f3-filesystem" in result, "MCP server name 'f3-filesystem' should appear"
        # Verify commands appear
        assert "npx" in result, "MCP command 'npx' should appear"
        assert "uvx" in result, "MCP command 'uvx' should appear"
        # Skill content preserved
        assert "F3 MCP Skill" in result
    finally:
        await pool.__aexit__(None, None, None)


# =========================================================================
# Scenario (c): load_skill without tools/MCP returns identical response
# =========================================================================


@pytest.mark.integration
@pytest.mark.anyio
async def test_scenario_c_load_skill_plain_no_activation_sections(tmp_path: Any) -> None:
    """) load_skill without MCP/tools does NOT show activation sections."""
    from agentpool_toolsets.builtin.skills import load_skill

    # Create a plain skill dir with no MCP or tools
    skill_dir = tmp_path / "f3-plain-skill"
    skill_dir.mkdir()
    content = """---
name: f3-plain-skill
description: A plain F3 skill with no MCP servers or tools
---

# F3 Plain Skill

No MCP servers or tools declared.
"""
    (skill_dir / "SKILL.md").write_text(content)

    ctx, pool = await _make_context_for_load_skill(tmp_path, UPath(skill_dir))
    try:
        result = await load_skill(ctx, "f3-plain-skill")

        # Core structure unchanged from pre-refactor
        assert "# f3-plain-skill" in result, "Header with skill name"
        assert "A plain F3 skill with no MCP servers or tools" in result, "Description"
        assert "No MCP servers or tools declared." in result, "Instructions body"
        assert "Skill URI:" in result, "Skill URI trailer"

        # No activation sections
        assert "## Activated MCP Servers" not in result, "No MCP section for plain skill"
        assert "## Activated Tools" not in result, "No Tools section for plain skill"
    finally:
        await pool.__aexit__(None, None, None)


# =========================================================================
# Scenario (d): allowed_tools filtering works at runtime
# =========================================================================


def _make_tool_def(name: str) -> ToolDefinition:
    """Create a minimal ToolDefinition with the given name."""
    return ToolDefinition(
        name=name,
        description="",
        parameters_json_schema={"type": "object", "properties": {}},
    )


@pytest.mark.anyio
async def test_scenario_d_allowed_tools_filter_func_rejects_unwanted() -> None:
    """(d) FilteredToolset's filter_func rejects tools not in allowed_tools.

    Tests the actual filter function at runtime.
    """
    # Create a Skill with allowed_tools and a SkillCapability from it
    skill = make_skill(
        name="f3-filtered-skill",
        allowed_tools="bash read",
    )
    cap = SkillCapability(skill=skill)

    # Get the wrapper toolset — returns FilteredToolset
    inner = PrefixedToolset(prefix="f3__tool__", wrapped=FunctionToolset([]))
    wrapped = cap.get_wrapper_toolset(inner)

    assert wrapped is not None
    assert isinstance(wrapped, FilteredToolset), (
        f"Expected FilteredToolset, got {type(wrapped).__name__}"
    )

    # Access the filter function from the dataclass
    filter_func = wrapped.filter_func
    assert callable(filter_func), "filter_func must be callable"

    # Create a minimal RunContext for the filter
    run_ctx = MagicMock()
    run_ctx.deps = MagicMock()

    # Test tools that ARE allowed (must be skill-prefixed to be filtered)
    for tool_name in ("bash", "read"):
        tool_def = _make_tool_def(f"f3-filtered-skill__tool__{tool_name}")
        result_or_coro = filter_func(run_ctx, tool_def)
        if inspect.isawaitable(result_or_coro):
            result = await result_or_coro
        else:
            result = result_or_coro
        assert result is True, f"Tool '{tool_name}' should be allowed but got {result}"

    # Test tools that are NOT allowed
    for tool_name in ("grep", "write", "edit"):
        tool_def = _make_tool_def(f"f3-filtered-skill__tool__{tool_name}")
        result_or_coro = filter_func(run_ctx, tool_def)
        if inspect.isawaitable(result_or_coro):
            result = await result_or_coro
        else:
            result = result_or_coro
        assert result is False, f"Tool '{tool_name}' should be rejected but got {result}"

    # Verify non-skill tools (no skill prefix) pass through regardless
    for tool_name in ("list_available_nodes", "task", "grep", "write"):
        tool_def = _make_tool_def(tool_name)
        result_or_coro = filter_func(run_ctx, tool_def)
        if inspect.isawaitable(result_or_coro):
            result = await result_or_coro
        else:
            result = result_or_coro
        assert result is True, f"Non-skill tool '{tool_name}' should pass through but got {result}"


@pytest.mark.anyio
async def test_scenario_d_allowed_tools_passthrough_when_none() -> None:
    """(d) When allowed_tools is None, get_wrapper_toolset() returns toolset unchanged."""
    skill = make_skill("f3-no-filter-skill")
    cap = SkillCapability(skill=skill)

    inner = PrefixedToolset(prefix="f3__tool__", wrapped=FunctionToolset([]))
    wrapped = cap.get_wrapper_toolset(inner)

    # Should be the same object — no filtering
    assert wrapped is inner, "Toolset should pass through unchanged when no allowed_tools"
