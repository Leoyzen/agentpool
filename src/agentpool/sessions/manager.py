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
        child_session_id: str | None = None,
    ) -> str:
        """Create a child session for a subagent.

        Args:
            parent_session_id: The parent session ID
            agent_name: The agent name for the child session
            agent_type: The type of agent (native, claude, etc.)
            child_session_id: Optional explicit child session ID. If provided,
                this ID is used instead of generating a new one.

        Returns:
            The child session ID
        """
        from agentpool.utils.identifiers import generate_session_id

        session_id = child_session_id or generate_session_id()

        if self.store:
            from agentpool.sessions.models import SessionData
            from agentpool.utils.time_utils import get_now

            # Inherit project_id and cwd from parent session so that
            # child sessions appear in the same workspace/project filter
            # in the TUI.  Without this, project_id defaults to None,
            # which later falls back to "default" or "global" and breaks
            # the per-project session listing.
            parent_data = await self.store.load(parent_session_id)
            parent_project_id = parent_data.project_id if parent_data else None
            parent_cwd = parent_data.cwd if parent_data else None

            # Create session data with parent-child relationship
            session_data = SessionData(
                session_id=session_id,
                agent_name=agent_name,
                agent_type=agent_type,
                parent_id=parent_session_id,
                pool_id=self.pool.manifest.name if self.pool.manifest else None,
                project_id=parent_project_id,
                cwd=parent_cwd,
                created_at=get_now(),
                last_active=get_now(),
            )

            # Persist to store
            await self.store.save(session_data)

        logger.debug(
            "Created child session",
            child_session_id=session_id,
            parent_session_id=parent_session_id,
            agent_name=agent_name,
        )

        return session_id

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
