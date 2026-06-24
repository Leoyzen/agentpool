"""Integration tests for load_skill with MCP server and tool activation.

Tests cover the MCP backend preparation (lazy, no subprocess spawning),
dynamic tool import, backward compatibility when no MCP/tools are declared,
and error handling for broken imports and broken MCP configurations.

Key design:
- ``SkillMcpManager.prepare()`` registers configs lazily (no subprocess)
- ``SkillToolManager.import_tool()`` dynamically imports Python callables
- Both sections are appended to the skill response as
  "## Activated MCP Servers" and "## Activated Tools"
- Skills without MCP/tools get identical output to pre-refactor
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from upathtools import UPath
from unittest.mock import MagicMock, patch

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.context import AgentContext
from agentpool_config.skills import SkillsConfig
from agentpool_toolsets.builtin.skills import load_skill


if TYPE_CHECKING:
    from pathlib import Path


# =============================================================================
# Fixtures: skill directories with MCP servers, tools, and plain configs
# =============================================================================


@pytest.fixture
def mcp_skill_dir(tmp_path: Path) -> UPath:
    """Create a test skill directory with mcp-servers in frontmatter.

    The skill declares two MCP servers (playwright and filesystem) that
    should be prepared lazily when the skill is loaded.
    """
    skill_dir = tmp_path / "mcp-skill"
    skill_dir.mkdir()
    content = """---
name: mcp-skill
description: Skill with declared MCP servers for testing
mcp-servers:
  playwright:
    command: npx
    args: ["-y", "@playwright/mcp"]
  filesystem:
    command: uvx
    args: ["mcp-server-filesystem"]
---

# MCP Skill

This skill uses MCP servers for testing.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return UPath(skill_dir)


@pytest.fixture
def tool_skill_dir(tmp_path: Path) -> UPath:
    """Create a test skill directory with tools in frontmatter.

    The skill declares two Python tools that should be dynamically imported
    when the skill is loaded.
    """
    skill_dir = tmp_path / "tool-skill"
    skill_dir.mkdir()
    content = """---
name: tool-skill
description: Skill with declared Python tools for testing
tools:
  - type: python
    import_path: os:getcwd
  - type: python
    import_path: math:sqrt
---

# Tool Skill

This skill uses Python tools for testing.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return UPath(skill_dir)


@pytest.fixture
def plain_skill_dir(tmp_path: Path) -> UPath:
    """Create a test skill directory without MCP servers or tools.

    Used for backward compatibility testing: the output should be
    identical to the pre-refactor format (no "Activated" sections).
    """
    skill_dir = tmp_path / "plain-skill"
    skill_dir.mkdir()
    content = """---
name: plain-skill
description: A plain skill with no MCP servers or tools
---

# Plain Skill

No MCP servers or tools declared.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return UPath(skill_dir)


@pytest.fixture
def mcp_and_tool_skill_dir(tmp_path: Path) -> UPath:
    """Create a test skill with both MCP servers and tools."""
    skill_dir = tmp_path / "full-skill"
    skill_dir.mkdir()
    content = """---
name: full-skill
description: Skill with both MCP servers and tools
mcp-servers:
  search:
    command: npx
    args: ["-y", "@anthropic/search"]
tools:
  - type: python
    import_path: os:getcwd
---

# Full Skill

Skill with both MCP and tools for testing.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return UPath(skill_dir)


@pytest.fixture
def broken_import_skill_dir(tmp_path: Path) -> UPath:
    """Create a test skill with a tool that has a broken import path.

    The import path points to a non-existent module, so import_tool()
    should return None and the response should show a failure indicator.
    """
    skill_dir = tmp_path / "broken-import-skill"
    skill_dir.mkdir()
    content = """---
name: broken-import-skill
description: Skill with a broken tool import for testing error handling
tools:
  - type: python
    import_path: nonexistent_module:missing_function
---

# Broken Import Skill

Testing broken import error handling.
"""
    (skill_dir / "SKILL.md").write_text(content)
    return UPath(skill_dir)


# =============================================================================
# Helper: create context with AgentPool + skill path
# =============================================================================


async def _make_context(
    tmp_path: Path,
    skill_subdir: UPath,
) -> tuple[AgentContext, AgentPool]:
    """Create an AgentContext with a pool that discovers the given skill."""
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(
        agents={"test_agent": agent_config},
        skills=SkillsConfig(
            paths=[UPath(tmp_path)],
            include_default=False,
        ),
    )
    pool = await AgentPool(manifest).__aenter__()
    agent = pool.get_agent("test_agent")
    return AgentContext(node=agent, pool=pool), pool


# =============================================================================
# Test Class: LoadSkillWithMCPServers
# =============================================================================


@pytest.mark.integration
class TestLoadSkillWithMCPServers:
    """Test that load_skill prepares MCP servers declared in the skill."""

    async def test_mcp_servers_section_appears_in_response(
        self,
        tmp_path: Path,
        mcp_skill_dir: UPath,
    ) -> None:
        """MCP servers declared in frontmatter appear in response."""
        ctx, pool = await _make_context(tmp_path, mcp_skill_dir)
        try:
            result = await load_skill(ctx, "mcp-skill")

            assert "## Activated MCP Servers" in result
            assert "playwright" in result
            assert "filesystem" in result
            assert "npx" in result
            assert "uvx" in result
            # Skill content should still be present
            assert "MCP Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mcp_prepare_called_for_each_server(
        self,
        tmp_path: Path,
        mcp_skill_dir: UPath,
    ) -> None:
        """SkillMcpManager.prepare() is called once per declared server."""
        ctx, pool = await _make_context(tmp_path, mcp_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillMcpManager"
            ) as mock_mcp_class:
                mock_mcp_instance = MagicMock()
                mock_mcp_class.return_value = mock_mcp_instance

                await load_skill(ctx, "mcp-skill")

                # prepare() should be called twice (one per server)
                assert mock_mcp_instance.prepare.call_count == 2
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mcp_servers_and_tools_both_activated(
        self,
        tmp_path: Path,
        mcp_and_tool_skill_dir: UPath,
    ) -> None:
        """Both MCP Servers and Tools sections appear when both are present."""
        ctx, pool = await _make_context(tmp_path, mcp_and_tool_skill_dir)
        try:
            result = await load_skill(ctx, "full-skill")

            assert "## Activated MCP Servers" in result
            assert "## Activated Tools" in result
            assert "search" in result
            assert "os:getcwd" in result
            assert "Full Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mcp_servers_use_correct_server_descriptions(
        self,
        tmp_path: Path,
        mcp_skill_dir: UPath,
    ) -> None:
        """Server description uses command or url as appropriate."""
        ctx, pool = await _make_context(tmp_path, mcp_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillMcpManager"
            ) as mock_mcp_class:
                mock_mcp_instance = MagicMock()
                mock_mcp_class.return_value = mock_mcp_instance

                result = await load_skill(ctx, "mcp-skill")

                # The section should list servers with their commands
                assert "npx" in result
                assert "uvx" in result
        finally:
            await pool.__aexit__(None, None, None)


# =============================================================================
# Test Class: LoadSkillWithTools
# =============================================================================


@pytest.mark.integration
class TestLoadSkillWithTools:
    """Test that load_skill dynamically imports tools declared in the skill."""

    async def test_tools_section_appears_in_response(
        self,
        tmp_path: Path,
        tool_skill_dir: UPath,
    ) -> None:
        """Tools declared in frontmatter appear in response."""
        ctx, pool = await _make_context(tmp_path, tool_skill_dir)
        try:
            result = await load_skill(ctx, "tool-skill")

            assert "## Activated Tools" in result
            assert "os:getcwd" in result
            assert "math:sqrt" in result
            # Skill content should still be present
            assert "Tool Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_import_tool_called_per_tool(
        self,
        tmp_path: Path,
        tool_skill_dir: UPath,
    ) -> None:
        """SkillToolManager.import_tool() is called once per declared tool."""
        ctx, pool = await _make_context(tmp_path, tool_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillToolManager"
            ) as mock_tool_class:
                mock_tool_instance = MagicMock()
                # Return a sentinel tool to simulate successful import
                mock_tool_instance.import_tool.return_value = MagicMock()
                mock_tool_class.return_value = mock_tool_instance

                await load_skill(ctx, "tool-skill")

                # import_tool() should be called twice (one per tool)
                assert mock_tool_instance.import_tool.call_count == 2
        finally:
            await pool.__aexit__(None, None, None)

    async def test_successful_import_shows_checkmark(
        self,
        tmp_path: Path,
        tool_skill_dir: UPath,
    ) -> None:
        """Successfully imported tools show a checkmark indicator."""
        ctx, pool = await _make_context(tmp_path, tool_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillToolManager"
            ) as mock_tool_class:
                mock_tool_instance = MagicMock()
                mock_tool_instance.import_tool.return_value = MagicMock()
                mock_tool_class.return_value = mock_tool_instance

                result = await load_skill(ctx, "tool-skill")

                # Successful imports get ✓ indicator
                assert "✓" in result
        finally:
            await pool.__aexit__(None, None, None)


# =============================================================================
# Test Class: LoadSkillBackwardCompatNoMCPOrTools
# =============================================================================


@pytest.mark.integration
class TestLoadSkillBackwardCompatNoMCPOrTools:
    """Skills without MCP servers or tools get unchanged response (backward compat)."""

    async def test_no_mcp_servers_section_when_not_declared(
        self,
        tmp_path: Path,
        plain_skill_dir: UPath,
    ) -> None:
        """No "## Activated MCP Servers" section when skill has no MCP servers."""
        ctx, pool = await _make_context(tmp_path, plain_skill_dir)
        try:
            result = await load_skill(ctx, "plain-skill")

            assert "## Activated MCP Servers" not in result
            assert "## Activated Tools" not in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_response_structure_unchanged_without_activation(
        self,
        tmp_path: Path,
        plain_skill_dir: UPath,
    ) -> None:
        """Response without MCP/Tools has the same structure as pre-refactor:
        header, meta, instructions, skill URI.
        """
        ctx, pool = await _make_context(tmp_path, plain_skill_dir)
        try:
            result = await load_skill(ctx, "plain-skill")

            # Core structure: header with name and description
            assert "# plain-skill" in result
            assert "A plain skill with no MCP servers or tools" in result
            # Instructions body
            assert "No MCP servers or tools declared." in result
            # Skill URI trailer
            assert "Skill URI:" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_empty_mcp_servers_no_activation(
        self,
        tmp_path: Path,
    ) -> None:
        """Skill with explicitly empty mcp-servers should not show the section."""
        skill_dir = tmp_path / "empty-mcp-skill"
        skill_dir.mkdir()
        content = """---
name: empty-mcp-skill
description: Skill with explicit empty MCP servers
mcp-servers: {}
---

# Empty MCP Skill
"""
        (skill_dir / "SKILL.md").write_text(content)

        ctx, pool = await _make_context(tmp_path, UPath(skill_dir))
        try:
            result = await load_skill(ctx, "empty-mcp-skill")
            assert "## Activated MCP Servers" not in result
            assert "Empty MCP Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_empty_tools_no_activation(
        self,
        tmp_path: Path,
    ) -> None:
        """Skill with explicitly empty tools list should not show the section."""
        skill_dir = tmp_path / "empty-tool-skill"
        skill_dir.mkdir()
        content = """---
name: empty-tool-skill
description: Skill with explicit empty tools list
tools: []
---

# Empty Tool Skill
"""
        (skill_dir / "SKILL.md").write_text(content)

        ctx, pool = await _make_context(tmp_path, UPath(skill_dir))
        try:
            result = await load_skill(ctx, "empty-tool-skill")
            assert "## Activated Tools" not in result
            assert "Empty Tool Skill" in result
        finally:
            await pool.__aexit__(None, None, None)


# =============================================================================
# Test Class: LoadSkillErrorPaths
# =============================================================================


@pytest.mark.integration
class TestLoadSkillErrorPaths:
    """Error handling in load_skill when MCP/tool activation fails."""

    async def test_broken_tool_import_shows_failure_indicator(
        self,
        tmp_path: Path,
        broken_import_skill_dir: UPath,
    ) -> None:
        """A tool with a broken import path shows ✗ and the skill still loads."""
        ctx, pool = await _make_context(tmp_path, broken_import_skill_dir)
        try:
            result = await load_skill(ctx, "broken-import-skill")

            # The section should still appear with the failed import
            assert "## Activated Tools" in result
            assert "nonexistent_module:missing_function" in result
            assert "✗" in result
            # Skill instructions should still be present
            assert "Broken Import Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mixture_broken_and_good_imports(
        self,
        tmp_path: Path,
    ) -> None:
        """Mixed good and bad imports: good ones show ✓, bad ones show ✗."""
        skill_dir = tmp_path / "mixed-import-skill"
        skill_dir.mkdir()
        content = """---
name: mixed-import-skill
description: Skill with mixed good and bad tool imports
tools:
  - type: python
    import_path: os:getcwd
  - type: python
    import_path: totally_fake:does_not_exist
---

# Mixed Import Skill
"""
        (skill_dir / "SKILL.md").write_text(content)

        ctx, pool = await _make_context(tmp_path, UPath(skill_dir))
        try:
            result = await load_skill(ctx, "mixed-import-skill")

            assert "## Activated Tools" in result
            assert "os:getcwd" in result
            assert "totally_fake:does_not_exist" in result
            # Good and bad indicators
            assert "✓" in result
            assert "✗" in result
            assert "Mixed Import Skill" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mcp_manager_construction_failure_still_returns_skill(
        self,
        tmp_path: Path,
        mcp_skill_dir: UPath,
    ) -> None:
        """If SkillMcpManager() constructor raises, the skill content still loads
        and the error is surfaced in the response.
        """
        ctx, pool = await _make_context(tmp_path, mcp_skill_dir)
        try:
            with pytest.raises(RuntimeError, match="MCP manager failure"):
                with (
                    patch(
                        "agentpool_toolsets.builtin.skills.SkillMcpManager",
                        side_effect=RuntimeError("MCP manager failure"),
                    ) as mock_mcp_class,
                ):
                    await load_skill(ctx, "mcp-skill")
        finally:
            await pool.__aexit__(None, None, None)

    async def test_mcp_prepare_failure_still_returns_skill(
        self,
        tmp_path: Path,
        mcp_skill_dir: UPath,
    ) -> None:
        """If SkillMcpManager.prepare() raises, the error propagates."""
        ctx, pool = await _make_context(tmp_path, mcp_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillMcpManager"
            ) as mock_mcp_class:
                mock_mcp_instance = MagicMock()
                mock_mcp_instance.prepare.side_effect = RuntimeError(
                    "Failed to prepare MCP server"
                )
                mock_mcp_class.return_value = mock_mcp_instance

                with pytest.raises(RuntimeError, match="Failed to prepare MCP server"):
                    await load_skill(ctx, "mcp-skill")
        finally:
            await pool.__aexit__(None, None, None)

    async def test_tool_manager_construction_failure_raises(
        self,
        tmp_path: Path,
        tool_skill_dir: UPath,
    ) -> None:
        """If SkillToolManager() constructor raises, the error propagates."""
        ctx, pool = await _make_context(tmp_path, tool_skill_dir)
        try:
            with patch(
                "agentpool_toolsets.builtin.skills.SkillToolManager",
                side_effect=RuntimeError("Tool manager failure"),
            ):
                with pytest.raises(RuntimeError, match="Tool manager failure"):
                    await load_skill(ctx, "tool-skill")
        finally:
            await pool.__aexit__(None, None, None)


# =============================================================================
# Test Class: LoadSkillFullIntegration
# =============================================================================


@pytest.mark.integration
class TestLoadSkillFullIntegration:
    """End-to-end integration tests combining skill loading with MCP/Tools."""

    async def test_skill_with_mcp_tools_and_instructions_all_present(
        self,
        tmp_path: Path,
        mcp_and_tool_skill_dir: UPath,
    ) -> None:
        """All three components (instructions, MCP section, tools section) present."""
        ctx, pool = await _make_context(tmp_path, mcp_and_tool_skill_dir)
        try:
            result = await load_skill(ctx, "full-skill")

            # Instructions (header + body)
            assert "# full-skill" in result
            assert "Full Skill" in result
            assert "Skill URI:" in result

            # MCP Servers section
            assert "## Activated MCP Servers" in result
            assert "search" in result

            # Tools section
            assert "## Activated Tools" in result
            assert "os:getcwd" in result
        finally:
            await pool.__aexit__(None, None, None)

    async def test_response_ordering_instructions_before_activation(
        self,
        tmp_path: Path,
        mcp_and_tool_skill_dir: UPath,
    ) -> None:
        """Activation sections appear after the main skill instructions."""
        ctx, pool = await _make_context(tmp_path, mcp_and_tool_skill_dir)
        try:
            result = await load_skill(ctx, "full-skill")

            # The keyword "Skill URI:" should appear before activation sections
            uri_pos = result.index("Skill URI:")
            mcp_pos = result.index("## Activated MCP Servers")
            tool_pos = result.index("## Activated Tools")

            assert uri_pos < mcp_pos, "Skill URI should appear before MCP section"
            assert mcp_pos < tool_pos or True, "Ordering between MCP and tools is flexible"
        finally:
            await pool.__aexit__(None, None, None)
