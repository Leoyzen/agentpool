"""Tests for ToolProviderProxy — experimental passthrough proxy for MCP-over-ACP.

Covers: passthrough behavior, registry registration, proxy_initialize.
"""

from __future__ import annotations

from typing import Any

from acp.proxy.impls.base import default_registry
from acp.proxy.impls.tool_provider import ToolProviderProxy
from acp.proxy.protocol import Proxy


# ---------------------------------------------------------------------------
# Passthrough
# ---------------------------------------------------------------------------


async def test_tool_provider_passthrough() -> None:
    """All methods pass through unchanged (experimental stub)."""
    proxy = ToolProviderProxy()
    params: dict[str, Any] = {"content": [{"type": "text", "text": "hello"}]}
    meta: dict[str, Any] = {"response": False}
    result = await proxy.proxy_successor("session/prompt", params, meta)
    assert result is params


async def test_tool_provider_passthrough_any_method() -> None:
    """ToolProviderProxy passes through any method, not just session/prompt."""
    proxy = ToolProviderProxy()
    params: dict[str, Any] = {"sessionId": "abc"}
    meta: dict[str, Any] = {}
    result = await proxy.proxy_successor("session/new", params, meta)
    assert result is params


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_tool_provider_registered_in_registry() -> None:
    """default_registry has 'tool_provider' registered."""
    assert "tool_provider" in default_registry
    assert default_registry.is_registered("tool_provider")


# ---------------------------------------------------------------------------
# Proxy protocol + initialize
# ---------------------------------------------------------------------------


def test_tool_provider_proxy_initialize() -> None:
    """proxy_initialize returns ['session/prompt']."""
    proxy = ToolProviderProxy()
    result = proxy.proxy_initialize()
    assert result == ["session/prompt"]


def test_tool_provider_implements_proxy_protocol() -> None:
    """ToolProviderProxy satisfies the runtime_checkable Proxy protocol."""
    proxy = ToolProviderProxy()
    assert isinstance(proxy, Proxy)
