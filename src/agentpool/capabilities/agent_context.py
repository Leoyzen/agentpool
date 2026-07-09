"""AgentContext — frozen dataclass carrying per-turn runtime state.

Constructed by RunLoop at Turn time (M2 task group 15), not by
AgentFactory at compile time. Provides typed references to all
per-turn services that agent tools and capabilities need.

ResourceSource is imported under TYPE_CHECKING to avoid a circular
dependency with todo 2's ``resource_source.py`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from agentpool.capabilities.delegation import DelegationService
    from agentpool.capabilities.resource_source import ResourceSource
    from agentpool.host.context import HostContext, RunScope
    from agentpool.host.registry import AgentRegistry
    from agentpool.orchestrator.session_controller import SessionState


@dataclass(frozen=True, slots=True)
class AgentContext:
    """Immutable per-turn context injected into pydantic-ai RunContext.

    Carries typed references to per-turn runtime state. A new instance
    is created for each Turn — no reuse across turns.

    Attributes:
        agent_registry: Read-only access to compiled agents for delegation.
        delegation: Limited interface for spawning subagents.
        session: Current session state (message history, metadata).
        scope: Run scope (config_id, tenant_id, user_id, session_id).
        host: Infrastructure handles (mcp, storage, skills, etc.).
        resources: Aggregated resource access, or None if agent has none.
    """

    agent_registry: AgentRegistry
    delegation: DelegationService
    session: SessionState
    scope: RunScope
    host: HostContext
    resources: ResourceSource | None = None
