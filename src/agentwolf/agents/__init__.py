"""CLI commands for agentwolf."""

from __future__ import annotations

from agentwolf.agents.native_agent import Agent
from agentwolf.agents.acp_agent import ACPAgent
from agentwolf.agents.events import (
    detailed_print_handler,
    resolve_event_handlers,
    simple_print_handler,
)
from agentwolf.agents.context import AgentContext
from agentwolf.agents.interactions import Interactions
from agentwolf.agents.prompt_injection import PromptInjectionManager
from agentwolf.agents.sys_prompts import SystemPrompts
from agentwolf.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH


__all__ = [
    "MAX_DELEGATION_DEPTH",
    "ACPAgent",
    "Agent",
    "AgentContext",
    "DelegationDepthError",
    "Interactions",
    "PromptInjectionManager",
    "SystemPrompts",
    "detailed_print_handler",
    "resolve_event_handlers",
    "simple_print_handler",
]
