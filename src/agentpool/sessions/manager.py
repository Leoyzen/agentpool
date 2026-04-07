"""Session manager for subagent session management."""

from __future__ import annotations

from typing import TYPE_CHECKING, Self

from agentpool.log import get_logger

if TYPE_CHECKING:
    from types import TracebackType

    from agentpool.delegation import AgentPool
    from agentpool.sessions import SessionStore


logger = get_logger(__name__)


class SessionManager:
    """Manages session lifecycle and parent-child relationships."""

    def __init__(self, pool: AgentPool, store: SessionStore | None = None) -> None:
        """Initialize session manager.

        Args:
            pool: The agent pool this manager belongs to
            store: Optional session store for persistence
        """
        self.pool = pool
        self.store = store

    async def __aenter__(self) -> Self:
        """Initialize session manager."""
        if self.store:
            await self.store.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up session manager."""
        if self.store:
            await self.store.__aexit__(exc_type, exc_val, exc_tb)

    async def create_child_session(
        self,
        parent_session_id: str,
        agent_name: str,
        agent_type: str = "native",
    ) -> str:
        """Create a child session for a subagent.

        Args:
            parent_session_id: The parent session ID
            agent_name: The agent name for the child session
            agent_type: The type of agent (native, claude, etc.)

        Returns:
            The new child session ID
        """
        from agentpool.utils.identifiers import generate_session_id

        child_session_id = generate_session_id()

        if self.store:
            # Store the parent-child relationship
            pass  # Implementation depends on storage provider

        logger.debug(
            "Created child session",
            child_session_id=child_session_id,
            parent_session_id=parent_session_id,
            agent_name=agent_name,
        )

        return child_session_id

    async def get_child_sessions(self, parent_session_id: str) -> list[str]:
        """Get all child sessions for a parent session.

        Args:
            parent_session_id: The parent session ID

        Returns:
            List of child session IDs
        """
        if self.store:
            return await self.store.list_sessions(parent_id=parent_session_id)
        return []
