"""Proxy implementations package with type registry."""

from __future__ import annotations

from acp.proxy.impls.base import ProxyRegistry, default_registry
from acp.proxy.impls.context_injection import ContextInjectionProxy
from acp.proxy.impls.hook_proxy import HookProxy
from acp.proxy.impls.tool_provider import ToolProviderProxy

# Register built-in proxy types
default_registry.register("hook", HookProxy)
default_registry.register("context_injection", ContextInjectionProxy)
default_registry.register("tool_provider", ToolProviderProxy)

__all__ = [
    "ContextInjectionProxy",
    "HookProxy",
    "ProxyRegistry",
    "ToolProviderProxy",
    "default_registry",
]
