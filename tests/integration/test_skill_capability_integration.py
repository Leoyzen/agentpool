"""Integration tests for SkillCapability — end-to-end skill loading with MCP and tools.

Tests cover:
- Full skill load: MCP servers prepared, Python tools imported, instructions injected
- ``allowed_tools`` filtering via ``FilteredToolset``
- Graceful degradation when MCP server config is broken
- Integration through AgentPool (discovery → capability → agentlet)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from pydantic_ai import RunContext
from pydantic_ai.toolsets import (
    FilteredToolset,
    FunctionToolset,
)
import pytest
from upathtools import UPath

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.skills.capability import SkillCapability
from agentpool.skills.skill import Skill
from agentpool.skills.skill_mcp_manager import SkillMcpManager
from agentpool.skills.skill_tool_manager import SkillToolManager
from agentpool_config.skills import SkillMcpServerConfig, SkillsConfig, SkillToolConfig


if TYPE_CHECKING:
    import pathlib

    from pydantic_ai import Agent as PydanticAgent


# =============================================================================
# Helper: create a temp skill in a tmp_path with frontmatter
# =============================================================================


def _create_skill(
    skill_dir: pathlib.Path,
    *,
    name: str = "test-capability-skill",
    description: str = "A test skill for capability integration",
    allowed_tools: str | None = None,
    mcp_servers: dict[str, dict[str, Any]] | None = None,
    tools: list[dict[str, str]] | None = None,
    instructions: str = "# Instructions\n\nThis is the skill content.",
) -> None:
    """Write a SKILL.md into *skill_dir*."""
    skill_dir.mkdir(parents=True, exist_ok=True)

    frontmatter_lines = [
        "---",
        f"name: {name}",
        f"description: {description}",
    ]
    if allowed_tools is not None:
        frontmatter_lines.append(f"allowed-tools: {allowed_tools}")
    if mcp_servers:
        # YAML inline block for mcp-servers
        frontmatter_lines.append("mcp-servers:")
        for srv_name, srv_cfg in mcp_servers.items():
            frontmatter_lines.append(f"  {srv_name}:")
            frontmatter_lines.append(f"    command: {srv_cfg['command']}")
            for arg in srv_cfg.get("args", []):
                frontmatter_lines.append(f"    args: [{arg}]")
    if tools:
        frontmatter_lines.append("tools:")
        for t in tools:
            frontmatter_lines.append(f"  - type: {t['type']}")
            frontmatter_lines.append(f"    import_path: {t['import_path']}")
    frontmatter_lines.append("---")
    frontmatter_lines.append("")
    frontmatter_lines.append(instructions)

    (skill_dir / "SKILL.md").write_text("\n".join(frontmatter_lines))


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def skill_dir_with_tools_and_mcp(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a skill directory with both MCP servers and Python tools."""
    _create_skill(
        tmp_path / "multi-skill",
        name="multi-skill",
        description="Skill with MCP servers and Python tools",
        mcp_servers={
            "filesystem": {"command": "uvx", "args": ["mcp-server-filesystem"]},
        },
        tools=[
            {"type": "python", "import_path": "os:getcwd"},
            {"type": "python", "import_path": "os:listdir"},
        ],
        instructions="# Multi Skill\n\nThis skill has both MCP and Python tools.",
    )
    return tmp_path


@pytest.fixture
def skill_dir_with_allowed_tools(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a skill directory with allowed_tools filtering."""
    _create_skill(
        tmp_path / "restricted-skill",
        name="restricted-skill",
        description="Skill with restricted tool access",
        allowed_tools="bash read",
        tools=[
            {"type": "python", "import_path": "os:getcwd"},
            {"type": "python", "import_path": "os:listdir"},
        ],
        instructions="# Restricted Skill\n\nOnly bash and read tools are allowed.",
    )
    return tmp_path


@pytest.fixture
def skill_dir_broken_mcp(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a skill directory with a deliberately broken MCP server config."""
    _create_skill(
        tmp_path / "broken-mcp-skill",
        name="broken-mcp-skill",
        description="Skill with broken MCP server",
        mcp_servers={
            "nonexistent": {"command": "this-command-does-not-exist", "args": []},
        },
        instructions="# Broken MCP Skill\n\nThis skill has a broken MCP server.",
    )
    return tmp_path


@pytest.fixture
def sample_skill() -> Skill:
    """A Skill object constructed in-memory (no filesystem needed)."""
    return Skill(
        name="sample-skill",
        description="In-memory sample skill",
        skill_path=UPath("/tmp/nonexistent-sample-skill"),
        instructions="# Sample Skill\n\nPre-loaded instructions.",
        mcp_servers={
            "demo-server": SkillMcpServerConfig(command="echo", args=["hello"]),
        },
        tools=[SkillToolConfig(type="python", import_path="os:getcwd")],
    )


# =============================================================================
# Test: SkillCapability — MCP prepared, tools imported, instructions injected
# =============================================================================


@pytest.mark.integration
class TestSkillCapabilityFullLoad:
    """Verify SkillCapability eagerly prepares MCP servers and imports tools."""

    def test_mcp_servers_are_prepared(self, sample_skill: Skill) -> None:
        """SkillMcpManager.prepare() is called for each mcp_server entry."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        assert len(mcp_manager._configs) == 0
        SkillCapability(sample_skill, mcp_manager, tool_manager)

        # After construction, each MCP server config should be registered
        assert "demo-server" in mcp_manager._configs
        assert mcp_manager._configs["demo-server"].command == "echo"
        assert len(mcp_manager._configs) == 1

    def test_tools_are_imported(self, sample_skill: Skill) -> None:
        """Python tools are eagerly imported at SkillCapability construction."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        cap = SkillCapability(sample_skill, mcp_manager, tool_manager)

        # _python_tools should contain the imported tools
        assert len(cap._python_tools) == 1
        tool = cap._python_tools[0]
        assert tool.name == "getcwd"  # from os:getcwd

    def test_instructions_are_injectable(self, sample_skill: Skill) -> None:
        """SkillCapability.get_instructions() returns the skill content."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        cap = SkillCapability(sample_skill, mcp_manager, tool_manager)
        instructions = cap.get_instructions()

        assert instructions is not None
        assert isinstance(instructions, str)
        assert "Pre-loaded instructions." in instructions

    def test_get_toolset_returns_combined_toolset(self, sample_skill: Skill) -> None:
        """Both Python tools and MCP tools contribute to a single toolset."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        cap = SkillCapability(sample_skill, mcp_manager, tool_manager)
        toolset = cap.get_toolset()

        # Should be a callable (ToolsetFunc) because we have both Python and MCP
        assert callable(toolset)

    def test_eager_construction_does_not_connect_mcp(self, sample_skill: Skill) -> None:
        """Construction only *prepares* configs, does NOT connect MCP servers."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        SkillCapability(sample_skill, mcp_manager, tool_manager)

        # No providers should be connected yet
        assert len(mcp_manager._providers) == 0


# =============================================================================
# Test: SkillCapability — allowed_tools filtering
# =============================================================================


@pytest.mark.integration
class TestSkillCapabilityAllowedTools:
    """Verify allowed_tools filtering via FilteredToolset."""

    def test_no_allowed_tools_returns_unmodified(self, sample_skill: Skill) -> None:
        """When allowed_tools is None, get_wrapper_toolset returns input unchanged."""
        # sample_skill has no allowed_tools
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        cap = SkillCapability(sample_skill, mcp_manager, tool_manager)
        dummy_toolset = FunctionToolset([])

        result = cap.get_wrapper_toolset(dummy_toolset)

        # No FilteredToolset wrapping when allowed_tools is empty
        assert result is dummy_toolset

    def test_allowed_tools_wraps_in_filtered_toolset(
        self, skill_dir_with_allowed_tools: pathlib.Path
    ) -> None:
        """When allowed_tools is set, the toolset is wrapped in FilteredToolset."""
        # Load the skill from the filesystem skill we created
        skill = Skill.from_skill_dir(UPath(str(skill_dir_with_allowed_tools / "restricted-skill")))

        assert skill.allowed_tools is not None
        assert skill.parsed_allowed_tools() == ["bash", "read"]

        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()
        cap = SkillCapability(skill, mcp_manager, tool_manager)

        dummy_toolset = FunctionToolset([])
        result = cap.get_wrapper_toolset(dummy_toolset)

        # Should now be wrapped in FilteredToolset
        assert isinstance(result, FilteredToolset)

    async def test_filtered_toolset_only_allows_matching_tools(
        self, skill_dir_with_allowed_tools: pathlib.Path
    ) -> None:
        """The FilteredToolset filter_func rejects tools not in allowed_tools."""
        skill = Skill.from_skill_dir(UPath(str(skill_dir_with_allowed_tools / "restricted-skill")))
        set(skill.parsed_allowed_tools())

        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()
        cap = SkillCapability(skill, mcp_manager, tool_manager)

        # Build a mock RunContext
        ctx = MagicMock(spec=RunContext)

        # Create tool definitions
        from pydantic_ai.tools import ToolDefinition

        # Tools must be prefixed with {skill.name}__ to trigger filtering.
        # Non-prefixed tools pass through unconditionally.
        allowed_tool = ToolDefinition(
            name=f"{skill.name}__tool__bash",
            description="Bash tool",
            parameters_json_schema={},
        )
        denied_tool = ToolDefinition(
            name=f"{skill.name}__tool__subagent",
            description="Subagent tool",
            parameters_json_schema={},
        )
        passthrough_tool = ToolDefinition(
            name="global_tool",
            description="Global tool without skill prefix",
            parameters_json_schema={},
        )

        # The filter function comes from get_wrapper_toolset
        dummy_toolset = FunctionToolset([])
        result = cap.get_wrapper_toolset(dummy_toolset)

        assert isinstance(result, FilteredToolset)
        # Access the inner filter_func to test it
        filter_func = result.filter_func

        # Run the filter function (it is actually async in SkillCapability)

        result_allowed = await filter_func(ctx, allowed_tool)  # type: ignore[misc]
        result_denied = await filter_func(ctx, denied_tool)  # type: ignore[misc]
        result_passthrough = await filter_func(ctx, passthrough_tool)  # type: ignore[misc]
        assert result_allowed is True
        assert result_denied is False
        # Non-prefixed tools always pass through
        assert result_passthrough is True


# =============================================================================
# Test: SkillCapability — graceful degradation on broken MCP
# =============================================================================


@pytest.mark.integration
class TestSkillCapabilityBrokenMCP:
    """Verify graceful degradation when MCP server config is broken.

    Construction of SkillCapability should not fail even if MCP configs
    point to nonexistent executables, because prepare() only registers
    configs — actual connection is lazy.
    """

    def test_broken_mcp_prepare_does_not_fail(self, skill_dir_broken_mcp: pathlib.Path) -> None:
        """SkillCapability construction succeeds with a broken MCP server config."""
        skill = Skill.from_skill_dir(UPath(str(skill_dir_broken_mcp / "broken-mcp-skill")))

        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()

        # Should NOT raise — prepare() only stores the config
        SkillCapability(skill, mcp_manager, tool_manager)

        assert "nonexistent" in mcp_manager._configs

    def test_broken_mcp_toolset_still_works(self, skill_dir_broken_mcp: pathlib.Path) -> None:
        """The toolset can still be obtained even if MCP server is broken.

        The MCP connection is lazy — it only fails at connect() time,
        not at construction or toolset building time.
        """
        skill = Skill.from_skill_dir(UPath(str(skill_dir_broken_mcp / "broken-mcp-skill")))

        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()
        cap = SkillCapability(skill, mcp_manager, tool_manager)

        # get_toolset should return a ToolsetFunc (because mcp_servers is set)
        toolset = cap.get_toolset()
        assert callable(toolset)


# =============================================================================
# Test: Full AgentPool integration — skill discovery → capability → agentlet
# =============================================================================


@pytest.mark.integration
class TestSkillCapabilityAgentPoolIntegration:
    """End-to-end test through AgentPool: skill discovery, capability creation,
    agentlet building, and instruction injection with TestModel.
    """

    async def test_skill_discovered_and_capability_built(
        self,
        skill_dir_with_tools_and_mcp: pathlib.Path,
    ) -> None:
        """AgentPool discovers skills and builds SkillCapability instances."""
        manifest = AgentsManifest(
            skills=SkillsConfig(
                paths=[UPath(str(skill_dir_with_tools_and_mcp))],
                include_default=False,
            ),
            agents={
                "test_agent": NativeAgentConfig(
                    name="test_agent",
                    model="test",
                    system_prompt="You are a test agent.",
                ),
            },
        )

        async with AgentPool(manifest) as pool:
            # Skills should be discovered
            all_skills = pool.skills.list_skills()
            skill_names = {s.name for s in all_skills}
            assert "multi-skill" in skill_names

            # SkillCapabilities should be built
            assert len(pool.skill_capabilities) > 0
            cap_names = {c._skill.name for c in pool.skill_capabilities}
            assert "multi-skill" in cap_names

    async def test_agentlet_includes_skill_capabilities(
        self,
        skill_dir_with_tools_and_mcp: pathlib.Path,
    ) -> None:
        """Agent built from pool includes skill capabilities in its agentlet."""
        manifest = AgentsManifest(
            skills=SkillsConfig(
                paths=[UPath(str(skill_dir_with_tools_and_mcp))],
                include_default=False,
            ),
            agents={
                "test_agent": NativeAgentConfig(
                    name="test_agent",
                    model="test",
                    system_prompt="You are a test agent.",
                ),
            },
        )

        async with AgentPool(manifest) as pool:
            agent = pool.manifest.agents["test_agent"].get_agent(pool=pool)

            # get_agentlet creates a pydantic-ai Agent with capabilities
            agentlet: PydanticAgent[Any, str] = await agent.get_agentlet(  # type: ignore[attr-defined]
                None, None, None
            )

            # Capability toolset entries should be present
            assert len(agentlet._cap_toolsets) > 0

    async def test_skill_instructions_available_via_capability(
        self,
        skill_dir_with_tools_and_mcp: pathlib.Path,
    ) -> None:
        """Skill instructions are available through SkillCapability.get_instructions()."""
        manifest = AgentsManifest(
            skills=SkillsConfig(
                paths=[UPath(str(skill_dir_with_tools_and_mcp))],
                include_default=False,
            ),
            agents={
                "test_agent": NativeAgentConfig(
                    name="test_agent",
                    model="test",
                    system_prompt="You are a test agent.",
                ),
            },
        )

        async with AgentPool(manifest) as pool:
            # Verify that at least one SkillCapability has instructions content
            found = False
            for cap in pool.skill_capabilities:
                inst = cap.get_instructions()
                if (
                    inst is not None
                    and isinstance(inst, str)
                    and "This skill has both MCP and Python tools." in inst
                ):
                    found = True
                    break
            assert found, "No SkillCapability contained the expected instructions"


# =============================================================================
# Test: SkillCapability ordering includes ProcessHistory + NativeTool wrapping
# =============================================================================


@pytest.mark.integration
class TestSkillCapabilityOrdering:
    """Verify SkillCapability ordering metadata."""

    def test_ordering_includes_history_and_native_tool(self, sample_skill: Skill) -> None:
        """get_ordering() returns wrapped_by=[ProcessHistory, NativeTool]."""
        mcp_manager = SkillMcpManager()
        tool_manager = SkillToolManager()
        cap = SkillCapability(sample_skill, mcp_manager, tool_manager)

        ordering = cap.get_ordering()
        assert ordering is not None

        from pydantic_ai.capabilities import NativeTool, ProcessHistory

        assert ProcessHistory in ordering.wrapped_by
        assert NativeTool in ordering.wrapped_by
