"""AgentContext — frozen dataclass carrying per-turn runtime state.

Constructed by RunLoop at Turn time (M2 task group 15), not by
AgentFactory at compile time. Provides typed references to all
per-turn services that agent tools and capabilities need.

ResourceSource is imported under TYPE_CHECKING to avoid a circular
dependency with todo 2's ``resource_source.py`` module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast


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


def resolve_agent_context_from_deps(
    deps: Any, *, capability_name: str = "Capability"
) -> AgentContext:
    """Unwrap the M2 ``AgentContext`` from pydantic-ai runtime deps.

    In production, ``ctx.deps`` is ``agents.context.AgentContext`` (the
    PydanticAI runtime context). Our ``capabilities.agent_context.AgentContext``
    is stored at ``deps.data``, set by ``NativeTurn`` (turn.py:
    ``agent_deps.data = run_ctx.deps``). In tests, deps may be directly
    our ``AgentContext``.

    Args:
        deps: The ``ctx.deps`` value from a pydantic-ai ``RunContext``.
        capability_name: Name of the calling capability, used in error messages.

    Returns:
        The ``AgentContext`` instance from ``deps`` (or ``deps.data``).

    Raises:
        RuntimeError: If deps is None, ``.data`` is None, or deps is
            neither ``RuntimeAgentContext`` nor ``AgentContext``.
    """
    from agentpool.agents.context import AgentContext as RuntimeAgentContext

    if deps is None:
        msg = f"{capability_name} requires AgentContext as deps. Got: None"
        raise RuntimeError(msg)
    # Production path: deps is RuntimeAgentContext, M2 AgentContext at .data
    if isinstance(deps, RuntimeAgentContext):
        inner = deps.data
        if inner is None:
            msg = f"{capability_name} requires AgentContext at deps.data. Got: None"
            raise RuntimeError(msg)
        return cast(AgentContext, inner)
    # Test path: deps is directly our AgentContext
    if isinstance(deps, AgentContext):
        return deps
    msg = f"{capability_name} requires AgentContext as deps. Got: {type(deps).__name__}"
    raise RuntimeError(msg)
