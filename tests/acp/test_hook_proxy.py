"""Tests for HookProxy — wraps AgentHooks as a Proxy in the ACP chain.

Covers: Proxy protocol compliance, pre_turn deny/additional_context,
pre_tool_use deny, post_tool_use modified_output, post_turn, passthrough.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from acp.proxy.impls.hook_proxy import HookProxy
from acp.proxy.protocol import Proxy
from agentpool.hooks.agent_hooks import AgentHooks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_hooks(
    *,
    has_hooks: bool = True,
    pre_turn_result: dict[str, Any] | None = None,
    post_turn_result: dict[str, Any] | None = None,
    pre_tool_result: dict[str, Any] | None = None,
    post_tool_result: dict[str, Any] | None = None,
) -> MagicMock:
    """Create an AsyncMock(spec=AgentHooks) with configured return values."""
    mock = AsyncMock(spec=AgentHooks)
    mock.has_hooks.return_value = has_hooks
    mock.run_pre_turn_hooks.return_value = pre_turn_result or {"decision": "allow"}
    mock.run_post_turn_hooks.return_value = post_turn_result or {"decision": "allow"}
    mock.run_pre_tool_hooks.return_value = pre_tool_result or {"decision": "allow"}
    mock.run_post_tool_hooks.return_value = post_tool_result or {"decision": "allow"}
    return mock


# ---------------------------------------------------------------------------
# Proxy protocol compliance
# ---------------------------------------------------------------------------


def test_hook_proxy_implements_proxy_protocol() -> None:
    """HookProxy satisfies the runtime_checkable Proxy protocol."""
    hooks = _make_hooks()
    proxy = HookProxy(hooks=[hooks])
    assert isinstance(proxy, Proxy)


def test_hook_proxy_proxy_initialize_returns_methods() -> None:
    """proxy_initialize returns ['session/prompt', 'session/update']."""
    hooks = _make_hooks()
    proxy = HookProxy(hooks=[hooks])
    result = proxy.proxy_initialize()
    assert result == ["session/prompt", "session/update"]


# ---------------------------------------------------------------------------
# pre_turn (session/prompt request)
# ---------------------------------------------------------------------------


async def test_pre_turn_deny_blocks() -> None:
    """When pre_turn hook returns deny, params replaced with error response."""
    hooks = _make_hooks(pre_turn_result={"decision": "deny", "reason": "blocked by policy"})
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {"content": [{"type": "text", "text": "hello"}]}
    meta: dict[str, Any] = {"agent_name": "test_agent", "response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    assert "error" in result
    assert result["error"]["code"] == -32603
    assert "Blocked by pre_turn hook" in result["error"]["message"]
    assert result["error"]["data"]["reason"] == "blocked by policy"


async def test_pre_turn_additional_context_injected() -> None:
    """When pre_turn hook returns additional_context, it is prepended to content."""
    hooks = _make_hooks(
        pre_turn_result={"decision": "allow", "additional_context": "extra context here"},
    )
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {
        "content": [{"type": "text", "text": "original prompt"}],
    }
    meta: dict[str, Any] = {"agent_name": "test_agent", "response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    content = result["content"]
    assert isinstance(content, list)
    assert len(content) == 2
    assert content[0] == {"type": "text", "text": "extra context here"}
    assert content[1] == {"type": "text", "text": "original prompt"}


# ---------------------------------------------------------------------------
# pre_tool_use (session/update ToolCallStart)
# ---------------------------------------------------------------------------


async def test_pre_tool_use_deny_blocks() -> None:
    """When pre_tool_use hook returns deny, params replaced with error response."""
    hooks = _make_hooks(
        pre_tool_result={"decision": "deny", "reason": "tool not allowed"},
    )
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {
        "update": {
            "type": "tool_call_start",
            "tool_call_id": "bash_tool",
            "raw_input": {"command": "rm -rf /"},
        },
    }
    meta: dict[str, Any] = {"agent_name": "test_agent"}
    result = await proxy.proxy_successor("session/update", params, meta)
    assert "error" in result
    assert result["error"]["code"] == -32603
    assert "bash_tool" in result["error"]["message"]


# ---------------------------------------------------------------------------
# post_tool_use (session/update ToolCallComplete)
# ---------------------------------------------------------------------------


async def test_post_tool_use_modifies_output() -> None:
    """When post_tool_use hook returns modified_output, raw_output is replaced."""
    hooks = _make_hooks(
        post_tool_result={
            "decision": "allow",
            "modified_output": {"sanitized": "clean output"},
        },
    )
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {
        "update": {
            "type": "tool_call_complete",
            "tool_call_id": "read_tool",
            "raw_input": {"path": "/etc/passwd"},
            "raw_output": {"content": "secret data"},
        },
    }
    meta: dict[str, Any] = {"agent_name": "test_agent"}
    result = await proxy.proxy_successor("session/update", params, meta)
    update = result["update"]
    assert update["raw_output"] == {"sanitized": "clean output"}


# ---------------------------------------------------------------------------
# post_turn (session/prompt response)
# ---------------------------------------------------------------------------


async def test_post_turn_on_response() -> None:
    """When session/prompt response arrives, post_turn hooks are called."""
    hooks = _make_hooks(
        post_turn_result={
            "decision": "allow",
            "modified_output": {"result": {"text": "modified response"}},
        },
    )
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {"result": {"text": "original response"}}
    meta: dict[str, Any] = {
        "agent_name": "test_agent",
        "response": True,
        "prompt": "what is 2+2",
        "duration_ms": 150.0,
    }
    result = await proxy.proxy_successor("session/prompt", params, meta)
    hooks.run_post_turn_hooks.assert_awaited_once()
    call_kwargs = hooks.run_post_turn_hooks.call_args
    assert call_kwargs.kwargs["agent_name"] == "test_agent"
    assert call_kwargs.kwargs["prompt"] == "what is 2+2"
    assert call_kwargs.kwargs["duration_ms"] == 150.0
    assert result == {"result": {"text": "modified response"}}


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------


async def test_passthrough_no_hooks() -> None:
    """With empty hooks list, params are returned unchanged."""
    proxy = HookProxy(hooks=[])
    params: dict[str, Any] = {"content": [{"type": "text", "text": "hello"}]}
    meta: dict[str, Any] = {"agent_name": "test_agent", "response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    assert result is params


async def test_passthrough_unrecognized_method() -> None:
    """Unrecognized methods are passed through unchanged."""
    hooks = _make_hooks()
    proxy = HookProxy(hooks=[hooks])
    params: dict[str, Any] = {"some_key": "some_value"}
    meta: dict[str, Any] = {"agent_name": "test_agent"}
    result = await proxy.proxy_successor("session/new", params, meta)
    assert result is params
    hooks.run_pre_turn_hooks.assert_not_awaited()
    hooks.run_post_turn_hooks.assert_not_awaited()
