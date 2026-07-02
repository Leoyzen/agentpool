"""Unit tests for SkillCapability in isolation.

Tests cover the 7 spec scenarios plus get_instructions():
  1. No tools/MCP → get_toolset() returns None
  2. MCP servers → get_toolset() returns PrefixedToolset(prefix="{name}__mcp__")
  3. Python tools → get_toolset() returns PrefixedToolset(prefix="{name}__tool__")
  4. Both tools → get_toolset() returns CombinedToolset with both prefixed toolsets
  5. Allowed tools → get_wrapper_toolset() returns FilteredToolset
  6. get_ordering() returns CapabilityOrdering(wrapped_by=[ProcessHistory, NativeTool])
  7. on_run_ended() triggers SkillMcpManager.cleanup(session_id)
  8+ get_instructions() returns raw skill content (including None for empty)
"""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, Mock

from pydantic_ai.capabilities import CapabilityOrdering, NativeTool, ProcessHistory
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import (
    CombinedToolset,
    FilteredToolset,
    FunctionToolset,
    PrefixedToolset,
)
import pytest
from upathtools import UPath

from agentpool.skills.capability import SkillCapability
from agentpool.skills.skill import Skill
from agentpool_config.skills import SkillMcpServerConfig, SkillToolConfig


# ---- Helpers ----


@dataclass
class FakeDeps:
    """Minimal deps with a session_id for RunContext."""

    session_id: str


def make_run_context(session_id: str = "ses_test") -> RunContext[FakeDeps]:
    """Build a minimal RunContext with a FakeDeps."""
    return RunContext(
        deps=FakeDeps(session_id=session_id),
        model=MagicMock(),
        usage=MagicMock(),
        retries={},
    )


def make_skill_with_instructions(
    name: str = "test-skill",
    instructions: str = "some skill instructions",
    **kwargs,
) -> Skill:
    """Create a Skill whose ``load_instructions()`` returns a known string.

    Pre-sets the ``instructions`` field so there is no filesystem I/O.
    The ``skill_path`` still points to a valid (though nonexistent) UPath.
    """
    return Skill(
        name=name,
        description="Test skill",
        skill_path=UPath("/tmp/test-skill"),
        instructions=instructions,
        **kwargs,
    )


@pytest.fixture
def skill_no_tools() -> Skill:
    """Skill with no tools and no MCP servers."""
    return make_skill_with_instructions()


@pytest.fixture
def skill_with_mcp() -> Skill:
    """Skill with one MCP server, no Python tools."""
    return make_skill_with_instructions(
        mcp_servers={
            "playwright": SkillMcpServerConfig(
                command="npx",
                args=["-y", "@playwright/mcp"],
            ),
        },
    )


@pytest.fixture
def skill_with_tools() -> Skill:
    """Skill with one Python tool, no MCP servers."""
    return make_skill_with_instructions(
        tools=[
            SkillToolConfig(type="python", import_path="os:getcwd"),
        ],
    )


@pytest.fixture
def skill_with_both() -> Skill:
    """Skill with both Python tools and MCP servers."""
    return make_skill_with_instructions(
        mcp_servers={
            "filesystem": SkillMcpServerConfig(
                command="uvx",
                args=["mcp-server-filesystem"],
            ),
        },
        tools=[
            SkillToolConfig(type="python", import_path="os:getcwd"),
            SkillToolConfig(type="python", import_path="os:listdir"),
        ],
    )


@pytest.fixture
def skill_with_allowed_tools() -> Skill:
    """Skill with allowed_tools restriction (no tools or MCP needed)."""
    return make_skill_with_instructions(
        allowed_tools="bash read",
    )


@pytest.fixture
def skill_empty_instructions() -> Skill:
    """Skill whose instructions are an empty string."""
    return make_skill_with_instructions(instructions="")


# =========================================================================
# Scenario 1: No tools / MCP
# =========================================================================


class TestNoToolsOrMCP:
    """SkillCapability with no tools and no MCP servers."""

    def test_get_toolset_returns_none(self, skill_no_tools: Skill) -> None:
        """get_toolset() returns None when no tools or MCP are configured."""
        cap = SkillCapability(skill=skill_no_tools)
        assert cap.get_toolset() is None

    def test_get_toolset_none_with_managers(self, skill_no_tools: Skill) -> None:
        """get_toolset() returns None even when managers are present but skill has no tools."""
        mcp_manager = Mock()
        tool_manager = Mock()
        cap = SkillCapability(
            skill=skill_no_tools, mcp_manager=mcp_manager, tool_manager=tool_manager
        )
        # Neither import_tools nor prepare should be called
        tool_manager.import_tools.assert_not_called()
        mcp_manager.prepare.assert_not_called()
        assert cap.get_toolset() is None


# =========================================================================
# Scenario 2: MCP servers only
# =========================================================================


class TestMCPOnly:
    """SkillCapability with MCP servers, no Python tools."""

    def test_get_toolset_returns_callable(self, skill_with_mcp: Skill) -> None:
        """get_toolset() returns a callable (ToolsetFunc) when only MCP is configured."""
        mcp_manager = Mock(spec_set=["prepare", "get_tools", "cleanup"])
        mcp_manager.prepare = Mock()
        mcp_manager.get_tools = AsyncMock(return_value=[])
        # Pass the mcp_manager with spec so it behaves correctly
        cap = SkillCapability(skill=skill_with_mcp, mcp_manager=mcp_manager)

        toolset = cap.get_toolset()
        # Must be a callable (the lazy _build_toolset)
        assert callable(toolset)
        mcp_manager.prepare.assert_called_once()

    async def test_build_toolset_prefixed_mcp(self, skill_with_mcp: Skill) -> None:
        r"""The lazy toolset builds a PrefixedToolset with prefix \"{name}__mcp__\"."""
        mcp_manager = AsyncMock()
        mcp_manager.prepare = Mock()
        mcp_manager.get_tools = AsyncMock(return_value=[])
        cap = SkillCapability(skill=skill_with_mcp, mcp_manager=mcp_manager)

        build_fn = cap.get_toolset()
        assert callable(build_fn)

        result = await build_fn(make_run_context())
        assert isinstance(result, PrefixedToolset)
        assert result.prefix == "test-skill__mcp__"

    async def test_get_toolset_calls_get_tools(self, skill_with_mcp: Skill) -> None:
        """Building the toolset calls mcp_manager.get_tools with correct server name and session."""
        mcp_manager = AsyncMock()
        mcp_manager.prepare = Mock()
        mcp_manager.get_tools = AsyncMock(return_value=[])
        cap = SkillCapability(skill=skill_with_mcp, mcp_manager=mcp_manager)

        build_fn = cap.get_toolset()
        ctx = make_run_context(session_id="ses_custom")
        await build_fn(ctx)

        mcp_manager.get_tools.assert_awaited_once_with("playwright", "ses_custom")


# =========================================================================
# Scenario 3: Python tools only
# =========================================================================


class TestToolsOnly:
    """SkillCapability with Python tools, no MCP servers."""

    def test_get_toolset_returns_prefixed_toolset(self, skill_with_tools: Skill) -> None:
        r"""get_toolset() returns a PrefixedToolset with prefix \"{name}__tool__\"."""
        tool_manager = Mock()

        async def _fake_pa_tool() -> None: ...

        tool_manager.import_tools.return_value = [
            Mock(spec_set=["to_pydantic_ai"], to_pydantic_ai=Mock(return_value=_fake_pa_tool)),
        ]
        cap = SkillCapability(skill=skill_with_tools, tool_manager=tool_manager)

        toolset = cap.get_toolset()
        assert isinstance(toolset, PrefixedToolset)
        assert toolset.prefix == "test-skill__tool__"

    def test_get_toolset_calls_import_tools(self, skill_with_tools: Skill) -> None:
        """Construction calls import_tools with the skill's tool configs."""
        tool_manager = Mock()
        tool_manager.import_tools.return_value = []
        SkillCapability(skill=skill_with_tools, tool_manager=tool_manager)

        tool_manager.import_tools.assert_called_once()
        # Verify the configs match
        configs = tool_manager.import_tools.call_args[0][0]
        assert len(configs) == 1
        assert configs[0].import_path == "os:getcwd"

    def test_get_toolset_wraps_function_toolset(self, skill_with_tools: Skill) -> None:
        """The PrefixedToolset wraps a FunctionToolset with pydantic-ai tools."""
        tool_manager = Mock()

        async def _fake_pa_tool() -> None: ...

        tool_manager.import_tools.return_value = [
            Mock(spec_set=["to_pydantic_ai"], to_pydantic_ai=Mock(return_value=_fake_pa_tool)),
        ]
        cap = SkillCapability(skill=skill_with_tools, tool_manager=tool_manager)

        toolset = cap.get_toolset()
        assert isinstance(toolset, PrefixedToolset)
        # The wrapped toolset should be a FunctionToolset
        wrapped = toolset.wrapped
        assert isinstance(wrapped, FunctionToolset)


# =========================================================================
# Scenario 4: Both Python tools and MCP servers
# =========================================================================


class TestBoth:
    """SkillCapability with both Python tools and MCP servers."""

    async def test_get_toolset_combined(self, skill_with_both: Skill) -> None:
        """get_toolset() builds a CombinedToolset with both tool and MCP prefixed toolsets."""
        mcp_manager = AsyncMock()
        mcp_manager.prepare = Mock()
        mcp_manager.get_tools = AsyncMock(return_value=[])
        tool_manager = Mock()

        async def _fake_pa_tool() -> None: ...

        tool_manager.import_tools.return_value = [
            Mock(spec_set=["to_pydantic_ai"], to_pydantic_ai=Mock(return_value=_fake_pa_tool)),
        ]
        cap = SkillCapability(
            skill=skill_with_both,
            mcp_manager=mcp_manager,
            tool_manager=tool_manager,
        )

        build_fn = cap.get_toolset()
        assert callable(build_fn)

        result = await build_fn(make_run_context())
        assert isinstance(result, CombinedToolset)

        # Should contain two toolsets: one tool + one MCP
        assert len(result.toolsets) == 2

        # Verify both prefixes
        prefixes = {ts.prefix for ts in result.toolsets}
        assert "test-skill__tool__" in prefixes
        assert "test-skill__mcp__" in prefixes


# =========================================================================
# Scenario 5: Allowed tools filtering
# =========================================================================


class TestAllowedTools:
    """SkillCapability with allowed_tools filtering."""

    def test_wrapper_returns_filtered_toolset(self, skill_with_allowed_tools: Skill) -> None:
        """get_wrapper_toolset() returns FilteredToolset when allowed_tools is set."""
        cap = SkillCapability(skill=skill_with_allowed_tools)
        inner = PrefixedToolset(prefix="test__tool__", wrapped=FunctionToolset([]))

        wrapped = cap.get_wrapper_toolset(inner)
        assert wrapped is not None
        assert isinstance(wrapped, FilteredToolset)
        # The wrapped toolset should be the original inner
        assert wrapped.wrapped is inner

    def test_wrapper_no_allowed_tools_passthrough(self, skill_no_tools: Skill) -> None:
        """get_wrapper_toolset() returns the toolset unchanged when allowed_tools is None."""
        cap = SkillCapability(skill=skill_no_tools)
        inner = PrefixedToolset(prefix="test__tool__", wrapped=FunctionToolset([]))

        wrapped = cap.get_wrapper_toolset(inner)
        # Should be the same object (passthrough)
        assert wrapped is inner


# =========================================================================
# Scenario 6: Ordering
# =========================================================================


class TestOrdering:
    """SkillCapability ordering."""

    def test_get_ordering_returns_capability_ordering(self) -> None:
        """get_ordering() returns CapabilityOrdering with wrapped_by list.

        The wrapped_by list contains ProcessHistory and NativeTool.
        """
        skill = make_skill_with_instructions()
        cap = SkillCapability(skill=skill)

        ordering = cap.get_ordering()
        assert isinstance(ordering, CapabilityOrdering)
        assert ordering.wrapped_by == [ProcessHistory, NativeTool]


# =========================================================================
# Scenario 7: on_run_ended triggers MCP cleanup
# =========================================================================


class TestOnRunEnded:
    """SkillCapability run lifecycle."""

    async def test_cleanup_called_with_session_id(self) -> None:
        """on_run_ended() calls mcp_manager.cleanup() with the correct session_id."""
        mcp_manager = AsyncMock()
        mcp_manager.prepare = Mock()
        cap = SkillCapability(
            skill=make_skill_with_instructions(),
            mcp_manager=mcp_manager,
        )
        ctx = make_run_context(session_id="ses_cleanup_test")

        await cap.on_run_ended(ctx)

        mcp_manager.cleanup.assert_awaited_once_with("ses_cleanup_test")

    async def test_cleanup_not_called_without_mcp(self) -> None:
        """on_run_ended() does nothing when mcp_manager is None."""
        cap = SkillCapability(skill=make_skill_with_instructions())
        ctx = make_run_context()

        await cap.on_run_ended(ctx)
        # Should not raise — no-op is the expected behavior.

    async def test_cleanup_not_called_without_session_id(self) -> None:
        """on_run_ended() skips cleanup when deps has no session_id."""
        mcp_manager = AsyncMock()
        mcp_manager.prepare = Mock()
        cap = SkillCapability(
            skill=make_skill_with_instructions(),
            mcp_manager=mcp_manager,
        )

        @dataclass
        class DepsNoSession:
            pass

        ctx = make_run_context()
        ctx.deps = DepsNoSession()

        await cap.on_run_ended(ctx)
        mcp_manager.cleanup.assert_not_called()


# =========================================================================
# Scenario 8+: get_instructions
# =========================================================================


class TestGetInstructions:
    """SkillCapability instructions."""

    def test_get_instructions_returns_raw_content(self) -> None:
        """get_instructions() returns raw skill instruction content."""
        skill = make_skill_with_instructions(instructions="do something useful")
        cap = SkillCapability(skill=skill)

        instructions = cap.get_instructions()
        assert instructions == "do something useful"

    def test_get_instructions_empty_returns_none(self, skill_empty_instructions: Skill) -> None:
        """get_instructions() returns None when skill instructions are empty."""
        cap = SkillCapability(skill=skill_empty_instructions)

        instructions = cap.get_instructions()
        assert instructions is None

    def test_get_instructions_no_instructions(self, skill_no_tools: Skill) -> None:
        """get_instructions() returns None when no instructions are pre-set."""
        # Create a skill with no pre-set instructions and a nonexistent path
        skill = Skill(
            name="no-instructions",
            description="No instructions",
            skill_path=UPath("/tmp/nonexistent-skill"),
        )
        cap = SkillCapability(skill=skill)

        instructions = cap.get_instructions()
        assert instructions is None
