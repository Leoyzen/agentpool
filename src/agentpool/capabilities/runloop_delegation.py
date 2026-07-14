"""RunLoopDelegationService — concrete DelegationService for RunLoop.

Implements the ``DelegationService`` Protocol by delegating subagent
spawning to the ``AgentPool``'s session infrastructure. This is the
runtime bridge between ``SubagentCapability`` (which calls
``ctx.deps.delegation.spawn_subagent()``) and the actual agent spawning
machinery in ``SessionController``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.host.context import HostContext
    from agentpool.host.registry import AgentRegistry


class RunLoopDelegationService:
    """Concrete ``DelegationService`` backed by the AgentPool registry.

    Constructed by ``RunHandle`` at turn start using the agent's
    ``HostContext`` and the compiled ``AgentRegistry``. Provides
    subagent spawning by creating a new session via the pool's
    ``SessionPool``.

    Attributes:
        _registry: Read-only registry of available agents.
        _host: Host context for infrastructure access.
        _session_id: Current session ID for parent-child linking.
    """

    def __init__(
        self,
        registry: AgentRegistry,
        host: HostContext,
        session_id: str,
    ) -> None:
        """Initialize the delegation service.

        Args:
            registry: Read-only registry of compiled agents.
            host: Host context with infrastructure handles.
            session_id: Current session ID for parent-child linking.
        """
        self._registry = registry
        self._host = host
        self._session_id = session_id

    async def spawn_subagent(
        self,
        name: str,
        prompt: str,
    ) -> AsyncIterator[Any]:
        """Spawn a subagent by name with the given prompt.

        Delegates to the pool's ``SessionController`` to create a child
        session, run the named agent, and stream events back.

        Args:
            name: Name of the agent to spawn.
            prompt: Input prompt for the subagent.

        Yields:
            Stream events from the subagent's execution.

        Raises:
            AgentNotFoundError: If the agent is not in the registry.
        """
        from agentpool.capabilities.delegation import AgentNotFoundError

        if not self._registry.exists(name):
            raise AgentNotFoundError(name)

        session_pool = self._host.session_pool
        if session_pool is None:
            msg = "SessionPool is not available for subagent spawning"
            raise RuntimeError(msg)

        controller = session_pool.sessions
        run_handle = await controller.receive_request(
            session_id=f"{self._session_id}::child::{name}",
            content=prompt,
        )
        if run_handle is None:
            return

        async for event in run_handle.start(prompt):
            yield event

    def get_available_agents(self) -> list[str]:
        """Return names of agents available within the current scope.

        Returns:
            Sorted list of agent names in the registry.
        """
        return self._registry.list_names()
