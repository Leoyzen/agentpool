"""Tests for tool filtering in toolset configurations."""

from __future__ import annotations

from agentpool.capabilities.filtered_toolset import FilteredToolsetCapability
from agentpool_config.toolsets import CodeToolsetConfig, SkillsToolsetConfig, SubagentToolsetConfig


async def test_subagent_tool_filtering():
    """Test filtering tools in subagent toolset."""
    # Unfiltered provider has all tools
    config_all = SubagentToolsetConfig()
    provider_all = config_all.get_provider()
    tools = await provider_all.get_tools()
    tool_names = {t.name for t in tools}

    assert "task" in tool_names
    assert "list_available_nodes" in tool_names

    # Filtered config wraps in FilteredToolsetCapability
    config = SubagentToolsetConfig(tools={"task": True, "list_available_nodes": False})
    provider = config.get_provider()
    assert isinstance(provider, FilteredToolsetCapability)

    # Inner capability still has all tools
    inner_tools = await provider.wrapped.get_tools()
    inner_names = {t.name for t in inner_tools}
    assert "task" in inner_names
    assert "list_available_nodes" in inner_names


async def test_skills_tool_filtering():
    """Test filtering tools in skills toolset."""
    # Unfiltered provider has all tools
    config_all = SkillsToolsetConfig()
    provider_all = config_all.get_provider()
    tools = await provider_all.get_tools()
    tool_names = {t.name for t in tools}

    assert "load_skill" in tool_names
    assert "list_skills" in tool_names

    # Filtered config wraps in FilteredToolsetCapability
    config = SkillsToolsetConfig(tools={"load_skill": False})
    provider = config.get_provider()
    assert isinstance(provider, FilteredToolsetCapability)

    # Inner capability still has all tools
    inner_tools = await provider.wrapped.get_tools()
    inner_names = {t.name for t in inner_tools}
    assert "load_skill" in inner_names
    assert "list_skills" in inner_names


async def test_code_toolset_filtering():
    """Test filtering tools in code toolset."""
    # Unfiltered provider has all tools
    config_all = CodeToolsetConfig()
    provider_all = config_all.get_provider()
    tools = await provider_all.get_tools()
    tool_names = {t.name for t in tools}

    assert "format_code" in tool_names
    assert "run_diagnostics" in tool_names

    # Filtered config wraps in FilteredToolsetCapability
    config = CodeToolsetConfig(tools={"format_code": True, "ast_grep": False})
    provider = config.get_provider()
    assert isinstance(provider, FilteredToolsetCapability)

    # Inner capability still has all tools
    inner_tools = await provider.wrapped.get_tools()
    inner_names = {t.name for t in inner_tools}
    assert "format_code" in inner_names
    assert "run_diagnostics" in inner_names


async def test_filtering_provider_delegates_attributes():
    """Test that FilteredToolsetCapability delegates attributes correctly."""
    config = SubagentToolsetConfig(tools={"task": True})
    provider = config.get_provider()

    assert isinstance(provider, FilteredToolsetCapability)
    # Name delegates to wrapped capability
    assert provider.name == "subagent_tools"
    # Wrapped capability is accessible
    assert provider.wrapped.name == "subagent_tools"
