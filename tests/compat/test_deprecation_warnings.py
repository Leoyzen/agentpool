"""Deprecation warning tests for shim APIs.

These tests verify that all deprecated classes, methods, and functions
emit DeprecationWarning with the correct message content.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agentpool.hooks.agent_hooks import AgentHooks
from agentpool.tools.manager import ToolManager
from agentpool.utils.context_wrapping import wrap_instruction


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
