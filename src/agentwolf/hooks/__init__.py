"""Runtime hook classes for agent lifecycle events."""

from __future__ import annotations

from agentwolf.hooks.agent_hooks import AgentHooks
from agentwolf.hooks.base import Hook, HookEvent, HookInput, HookResult
from agentwolf.hooks.callable import CallableHook
from agentwolf.hooks.command import CommandHook
from agentwolf.hooks.prompt import PromptHook

__all__ = [
    "AgentHooks",
    "CallableHook",
    "CommandHook",
    "Hook",
    "HookEvent",
    "HookInput",
    "HookResult",
    "PromptHook",
]
