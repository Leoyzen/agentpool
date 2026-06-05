"""Backward compatibility and deprecation warning tests for shim APIs.

Consolidated from:
- test_backward_compat.py (deprecated classes still work with _warn=False)
- test_deprecation_warnings.py (DeprecationWarning emitted correctly)
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.hooks.agent_hooks import AgentHooks
from agentpool.tools.manager import ToolManager
from agentpool.utils.context_wrapping import wrap_instruction


# ============================================================================
# Backward compatibility (_warn=False)
# ============================================================================


@pytest.mark.anyio
async def test_tool_manager_still_works() -> None:
    """ToolManager with _warn=False initializes and provides tools."""
    tm = ToolManager(_warn=False)
    assert tm.providers is not None
    tools = await tm.get_tools()
    assert isinstance(tools, list)


@pytest.mark.anyio
async def test_tool_manager_get_tools_warn_false() -> None:
    """ToolManager.get_tools() with _warn=False returns list."""
    tm = ToolManager(_warn=False)
    tools = await tm.get_tools()
    assert isinstance(tools, list)


def test_agent_hooks_still_works() -> None:
    """AgentHooks with _warn=False initializes and accepts hooks."""
    ah = AgentHooks(_warn=False)
    assert ah.has_hooks() is False
    assert ah.pre_run == []
    assert ah.post_run == []
    assert ah.pre_tool_use == []
    assert ah.post_tool_use == []


def test_mcp_manager_still_works() -> None:
    """MCPManager with _warn=False initializes and accepts server configs."""
    from agentpool.mcp_server.manager import MCPManager

    mm = MCPManager(_warn=False)
    assert mm.name == "mcp"
    assert mm.servers == []
    assert mm.providers == []


def test_resolve_history_processors_still_works() -> None:
    """_resolve_history_processors with _warn=False returns list."""
    from agentpool.agents.native_agent.agent import Agent

    agent = Agent.__new__(Agent)
    agent._resolved_history_processors = None
    agent._direct_history_processors = None

    # Mock conversation with a config that has no history_processors
    class FakeConfig:
        history_processors = None

    class FakeConversation:
        _config = FakeConfig()

    agent.conversation = FakeConversation()

    result = agent._resolve_history_processors(_warn=False)
    assert isinstance(result, list)
    assert result == []


def test_wrap_instruction_still_works() -> None:
    """wrap_instruction with _warn=False returns callable."""

    def simple_instruction() -> str:
        return "hello"

    wrapped = wrap_instruction(simple_instruction, _warn=False)
    assert callable(wrapped)


# ============================================================================
# Deprecation warnings
# ============================================================================


def test_tool_manager_init_emits_deprecation_warning() -> None:
    """ToolManager.__init__ emits DeprecationWarning with v0.5.0 and alternative."""
    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        ToolManager()
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "ToolManager is deprecated" in msg
    assert "ResourceProvider.as_capability()" in msg


@pytest.mark.anyio
async def test_tool_manager_get_tools_emits_deprecation_warning() -> None:
    """ToolManager.get_tools() emits DeprecationWarning with v0.5.0 and alternative."""
    tm = ToolManager(_warn=False)
    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        await tm.get_tools()
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "ToolManager.get_tools() is deprecated" in msg
    assert "ResourceProvider.as_capability()" in msg


def test_agent_hooks_post_init_emits_deprecation_warning() -> None:
    """AgentHooks.__post_init__ emits DeprecationWarning with v0.5.0 and alternative."""
    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        AgentHooks()
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "AgentHooks is deprecated" in msg
    assert "as_capability()" in msg


def test_mcp_manager_init_emits_deprecation_warning() -> None:
    """MCPManager.__init__ emits DeprecationWarning with v0.5.0 and alternative."""
    from agentpool.mcp_server.manager import MCPManager

    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        MCPManager()
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "MCPManager is deprecated" in msg
    assert "as_capability()" in msg


def test_resolve_history_processors_emits_deprecation_warning() -> None:
    """_resolve_history_processors emits DeprecationWarning with v0.5.0 and alternative."""
    from agentpool.agents.native_agent.agent import Agent

    agent = Agent.__new__(Agent)
    agent._resolved_history_processors = None
    agent._direct_history_processors = None

    class FakeConfig:
        history_processors = None

    class FakeConversation:
        _config = FakeConfig()

    agent.conversation = FakeConversation()

    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        agent._resolve_history_processors()
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "_resolve_history_processors() is deprecated" in msg
    assert "ProcessHistoryAdapter" in msg


def test_wrap_instruction_emits_deprecation_warning() -> None:
    """wrap_instruction emits DeprecationWarning with v0.5.0 and alternative."""

    def simple_instruction() -> str:
        return "hello"

    with pytest.warns(DeprecationWarning, match="v0\\.5\\.0") as warning_list:
        wrap_instruction(simple_instruction)
    assert len(warning_list) == 1
    msg = str(warning_list[0].message)
    assert "wrap_instruction() is deprecated" in msg
    assert "PydanticAIInstruction" in msg
