"""DelegationService Protocol and AgentNotFoundError exception.

Defines the limited interface that agent tools use to spawn subagents.
The Protocol is implemented by RunLoop in M2 (task group 15), not by
AgentFactory or AgentPool.

The Protocol intentionally exposes only two methods so that tools know
WHAT they can do (spawn a subagent by name), not HOW RunLoop implements
spawning (queue, priority, background task).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class AgentNotFoundError(Exception):
    """Raised when a requested agent is not found within the current scope.

    The error message deliberately does not reveal the existence of
    agents outside the current scope.
    """

    def __init__(self, agent_name: str) -> None:
        self.agent_name = agent_name
        super().__init__(f"Agent not found: {agent_name}")


@runtime_checkable
class DelegationService(Protocol):
    """Limited interface for subagent spawning and child session creation.

    ``spawn_subagent()`` and ``get_available_agents()`` are deprecated.
    ``create_child_session()`` is the recommended way to create a
    persistent child session with ``SpawnSessionStart`` emission.

    Implemented by RunLoop (M2 task group 15). Tools access this
    through ``ctx.deps.delegation`` on an ``AgentContext`` instance.

    Methods:
        - ``create_child_session(agent_name, ...)``: create a persistent
          child session with ``SpawnSessionStart`` emission.
        - ``spawn_subagent(name, prompt)``: deprecated, use
          ``ctx.host.session_pool.run_agent()`` instead.
        - ``get_available_agents()``: deprecated, use
          ``ctx.agent_registry.list_names()`` instead.
    """

    async def create_child_session(
        self,
        agent_name: str,
        *,
        parent_session_id: str | None = None,
        description: str = "",
        **metadata: Any,
    ) -> str:
        """Create a persistent child session and emit ``SpawnSessionStart``.

        Unlike ``run_agent()`` (which creates a temporary session,
        waits for completion, then closes), this creates a session
        that persists until explicitly closed. The caller is
        responsible for sending the initial prompt via
        ``session_pool.send_message()`` and closing the session
        when done.

        Args:
            agent_name: Name of the agent for the child session.
            parent_session_id: Parent session ID. Defaults to the
                current session ID of the delegation service.
            description: Optional human-readable description.
            **metadata: Arbitrary metadata attached to the session
                (e.g. ``team_id``, ``team_role``, ``team_member_name``).

        Returns:
            The child session ID.

        Raises:
            RuntimeError: If SessionPool is not available.
        """
        ...

    def spawn_subagent(
        self,
        name: str,
        prompt: str,
    ) -> AsyncIterator[Any]:
        """Spawn a subagent by name with the given prompt.

        .. deprecated::
            Use ``ctx.host.session_pool.run_agent()`` instead.

        Args:
            name: Name of the agent to spawn.
            prompt: Input prompt for the subagent.

        Yields:
            Stream events or results from the subagent's execution.

        Raises:
            AgentNotFoundError: If the agent is not in the current scope.
        """
        ...

    def get_available_agents(self) -> list[str]:
        """Return names of agents available within the current scope.

        .. deprecated::
            Use ``ctx.agent_registry.list_names()`` instead.

        Only agents authorized for the current RunScope are included.
        Agents from other tenants or configs are excluded.
        """
        ...
