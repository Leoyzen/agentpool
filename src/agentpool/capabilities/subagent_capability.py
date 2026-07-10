"""SubagentCapability — native capability for subagent delegation.

Exposes ``spawn_subagent`` and ``get_available_agents`` tools that
delegate to ``ctx.deps.delegation`` (a ``DelegationService`` Protocol)
at runtime. This replaces ``SubagentCapability`` with a lightweight
``AbstractCapability`` that has no direct ``AgentPool`` reference.
"""

from __future__ import annotations

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.tools import AgentDepsT, RunContext
from pydantic_ai.toolsets import AgentToolset, FunctionToolset

from agentpool.capabilities.delegation import AgentNotFoundError, DelegationService


class SubagentCapability(AbstractCapability[AgentDepsT]):
    """Capability providing subagent delegation tools.

    Exposes two tools via ``get_toolset()``:

    - ``spawn_subagent(name, prompt)``: delegates to
      ``ctx.deps.delegation.spawn_subagent()`` and collects the
      streaming output into a final string.
    - ``get_available_agents()``: delegates to
      ``ctx.deps.delegation.get_available_agents()`` returning the
      list of agent names available for delegation.

    The capability holds no ``AgentPool`` reference — all delegation
    goes through the ``DelegationService`` Protocol at runtime.
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
        delegation = _resolve_delegation(ctx)
        stream = delegation.spawn_subagent(name, prompt)
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
        delegation = _resolve_delegation(ctx)
        return delegation.get_available_agents()


def _resolve_delegation(ctx: RunContext[AgentDepsT]) -> DelegationService:
    """Extract the ``DelegationService`` from the run context deps.

    Args:
        ctx: The pydantic-ai run context.

    Returns:
        The ``DelegationService`` instance from ``ctx.deps.delegation``.

    Raises:
        RuntimeError: If deps does not have a ``delegation`` field.
    """
    from agentpool.capabilities.agent_context import AgentContext

    deps = ctx.deps
    if isinstance(deps, AgentContext):
        return deps.delegation
    msg = (
        "SubagentCapability requires AgentContext as deps with a "
        "'delegation' field. "
        f"Got: {type(deps).__name__}"
    )
    raise RuntimeError(msg)


__all__ = [
    "AgentNotFoundError",
    "DelegationService",
    "SubagentCapability",
]
