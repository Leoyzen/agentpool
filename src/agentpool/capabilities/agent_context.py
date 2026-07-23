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
    from agentpool.capabilities.extension_registry import ExtensionRegistry
    from agentpool.host.context import HostContext, RunScope
    from agentpool.host.registry import AgentRegistry
    from agentpool.orchestrator.session_controller import SessionState
    from agentpool_config.team_mode import TeamModeConfig


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
        extension_registry: ExtensionRegistry for scoped capability access.
        team_mode_config: Global team mode config from manifest, if enabled.
    """

    agent_registry: AgentRegistry
    delegation: DelegationService
    session: SessionState
    scope: RunScope
    host: HostContext
    extension_registry: ExtensionRegistry | None = None
    team_mode_config: TeamModeConfig | None = None
