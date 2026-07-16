"""SubagentCapability — native capability for subagent delegation.

Exposes ``spawn_subagent`` and ``get_available_agents`` tools that
delegate to ``ctx.deps.delegation`` (a ``DelegationService`` Protocol)
at runtime. This replaces ``SubagentCapability`` with a lightweight
``AbstractCapability`` that has no direct ``AgentPool`` reference.

In M2+, the preferred path is ``ctx.host.session_pool.run_agent()``
for spawning and ``ctx.agent_registry.list_names()`` for listing.
The old ``DelegationService`` path is used as a fallback when
``session_pool`` is not available, emitting a ``DeprecationWarning``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
import warnings

import logfire
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

from agentpool.capabilities.delegation import AgentNotFoundError, DelegationService
from agentpool.observability.spans import safe_span


if TYPE_CHECKING:
    from agentpool.capabilities.agent_context import AgentContext


class SubagentCapability(AbstractCapability[AgentDepsT]):
    """Capability providing subagent delegation tools.

    Exposes two tools via ``get_toolset()``:

    - ``spawn_subagent(name, prompt)``: delegates to
      ``ctx.host.session_pool.run_agent()`` (preferred) or falls back
      to ``ctx.deps.delegation.spawn_subagent()`` (deprecated).
    - ``get_available_agents()``: delegates to
      ``ctx.agent_registry.list_names()`` (preferred) or falls back
      to ``ctx.deps.delegation.get_available_agents()`` (deprecated).

    The capability holds no ``AgentPool`` reference — all delegation
    goes through the ``AgentContext`` at runtime.
    """

    def __init__(self, *, toolset_id: str = "subagent") -> None:
        """Initialize the subagent capability.

        Args:
            toolset_id: Identifier for the produced ``FunctionToolset``.
        """
        self._toolset_id = toolset_id

    async def __aenter__(self) -> SubagentCapability[AgentDepsT]:
        """Enter async context — no-op (no resources to acquire)."""
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        """Exit async context — no-op (no resources to release)."""

    def get_instructions(self) -> str | None:
        """Return a brief description of available delegation.

        Returns:
            A short instruction string describing the delegation tools.
        """
        return (
            "You can delegate tasks to other agents using the "
            "spawn_subagent tool. Use get_available_agents to see "
            "which agents are available."
        )

    def get_toolset(self) -> AgentToolset[AgentDepsT] | None:
        """Return a ``FunctionToolset`` with delegation tools.

        The tools access ``ctx.deps`` at runtime, which must be an
        ``AgentContext`` with a ``delegation`` field implementing
        ``DelegationService``.
        """
        return FunctionToolset(
            [self.spawn_subagent, self.get_available_agents],
            id=self._toolset_id,
        )

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    @staticmethod
    async def spawn_subagent(
        ctx: RunContext[AgentDepsT],
        name: str,
        prompt: str,
    ) -> str:
        """Delegate a task to a named subagent.

        Args:
            ctx: The run context providing agent dependencies.
            name: Name of the agent to delegate to.
            prompt: Task description to send to the subagent.
        """
        agent_ctx = _resolve_agent_context(ctx)
        # Preferred path: use session_pool.run_agent() (D24).
        session_pool = agent_ctx.host.session_pool
        if session_pool is not None:
            with safe_span(
                "delegation.subagent",
                parent_session_id=agent_ctx.session.session_id,
                child_agent_name=name,
            ):
                from agentpool.observability.trace import get_trace_id

                logfire.info(
                    "Delegating to subagent",
                    trace_id=get_trace_id(),
                    parent_session_id=agent_ctx.session.session_id,
                    child_agent_name=name,
                )
                return await session_pool.run_agent(
                    name,
                    prompt,
                    parent_session_id=agent_ctx.session.session_id,
                )
        # Fallback: old DelegationService path (deprecated).
        warnings.warn(
            "SubagentCapability.spawn_subagent() fell back to the "
            "deprecated DelegationService.spawn_subagent() because "
            "session_pool is None. Use ctx.host.session_pool.run_agent() "
            "instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        stream = agent_ctx.delegation.spawn_subagent(name, prompt)
        chunks = [str(chunk) async for chunk in stream]
        return "\n".join(chunks) if chunks else ""

    @staticmethod
    async def get_available_agents(
        ctx: RunContext[AgentDepsT],
    ) -> list[str]:
        """List all agents available for delegation.

        Returns:
            Sorted list of agent names in the registry.
        """
        agent_ctx = _resolve_agent_context(ctx)
        # Preferred path: use agent_registry.list_names() (D24).
        return agent_ctx.agent_registry.list_names()


def _resolve_agent_context(ctx: RunContext[AgentDepsT]) -> AgentContext:
    """Extract the ``AgentContext`` from the run context deps.

    Args:
        ctx: The pydantic-ai run context.

    Returns:
        The ``AgentContext`` instance from ``ctx.deps``.

    Raises:
        RuntimeError: If deps is not an ``AgentContext``.
    """
    from agentpool.capabilities.agent_context import AgentContext

    deps = ctx.deps
    if isinstance(deps, AgentContext):
        return deps
    msg = (
        "SubagentCapability requires AgentContext as deps with a "
        "'delegation' field. "
        f"Got: {type(deps).__name__}"
    )
    raise RuntimeError(msg)


# Kept for backward compatibility — callers that import
# DelegationService from this module still work.
__all__ = [
    "AgentNotFoundError",
    "DelegationService",
    "SubagentCapability",
]
