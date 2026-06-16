"""CLI commands for agentpool."""

from __future__ import annotations

from agentpool.agents.native_agent import Agent
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.events import (
    detailed_print_handler,
    resolve_event_handlers,
    simple_print_handler,
)
from agentpool.agents.context import AgentContext
from agentpool.agents.interactions import Interactions
from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.agents.sys_prompts import SystemPrompts
from agentpool.agents.exceptions import DelegationDepthError, MAX_DELEGATION_DEPTH


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
