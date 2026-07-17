"""Session store protocol and implementations."""

from __future__ import annotations

from abc import abstractmethod
from typing import TYPE_CHECKING, Protocol, Self, runtime_checkable

from agentpool.log import get_logger


if TYPE_CHECKING:
    from types import TracebackType

    from agentpool.sessions.models import SessionData

logger = get_logger(__name__)


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session persistence backends.

    !!! warning "Deprecated"
        Use ``SessionPersistence`` from ``agentpool_storage.protocols`` instead.
        This Protocol is kept for backward compatibility and will be removed
        in a future version.

    Implementations handle storing and retrieving SessionData to/from
    various backends (SQL, file, memory, etc.).
    """

    async def __aenter__(self) -> Self:
        """Initialize store resources."""
        ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up store resources."""
        ...

    @abstractmethod
    async def save(self, data: SessionData) -> None:
        """Save or update session data.

        Args:
            data: Session data to persist
        """
        ...

    @abstractmethod
    async def load(self, session_id: str) -> SessionData | None:
        """Load session data by ID.

        Args:
            session_id: Session identifier

        Returns:
            Session data if found, None otherwise
        """
        ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: Session identifier

        Returns:
            True if session was deleted, False if not found
        """
        ...

    @abstractmethod
    async def list_sessions(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
        parent_id: str | None = None,
    ) -> list[str]:
        """List session IDs, optionally filtered.

        Args:
            pool_id: Filter by pool/manifest ID
            agent_name: Filter by agent name
            parent_id: Filter by parent session ID

        Returns:
            List of session IDs
        """
        ...


class MemorySessionStore(SessionStore):
    """In-memory session store for testing and development."""

    def __init__(self) -> None:
        self._sessions: dict[str, SessionData] = {}
        self._checkpoints: dict[str, dict[str, object]] = {}

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass

    async def save(self, data: SessionData) -> None:
        self._sessions[data.session_id] = data
        logger.debug("Saved session", session_id=data.session_id)

    async def load(self, session_id: str) -> SessionData | None:
        return self._sessions.get(session_id)

    async def delete(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            self._checkpoints.pop(session_id, None)
            logger.debug("Deleted session", session_id=session_id)
            return True
        return False

    async def list_sessions(
        self,
        pool_id: str | None = None,
        agent_name: str | None = None,
        parent_id: str | None = None,
    ) -> list[str]:
        result = []
        for session_id, data in self._sessions.items():
            if pool_id is not None and data.pool_id != pool_id:
                continue
            if agent_name is not None and data.agent_name != agent_name:
                continue
            if parent_id is not None and data.parent_id != parent_id:
                continue
            result.append(session_id)
        return result

    # -- SessionPersistence Protocol conformance -------------------------

    async def save_session(self, data: SessionData) -> None:
        """Alias for ``save`` to conform to ``SessionPersistence`` Protocol."""
        return await self.save(data)

    async def load_session(self, session_id: str) -> SessionData | None:
        """Alias for ``load`` to conform to ``SessionPersistence`` Protocol."""
        return await self.load(session_id)

    async def delete_session(self, session_id: str) -> bool:
        """Alias for ``delete`` to conform to ``SessionPersistence`` Protocol."""
        return await self.delete(session_id)

    async def list_session_ids(
        self,
        *,
        pool_id: str | None = None,
        agent_name: str | None = None,
        cwd: str | None = None,
    ) -> list[str]:
        """Alias for ``list_sessions`` to conform to ``SessionPersistence`` Protocol.

        Note: ``MemorySessionStore.list_sessions`` uses ``parent_id`` not ``cwd``.
        The ``cwd`` parameter is accepted but not filtered (in-memory store
        does not index by cwd).
        """
        return await self.list_sessions(pool_id=pool_id, agent_name=agent_name)

    async def load_sessions_batch(
        self,
        session_ids: list[str],
        *,
        agent_name: str | None = None,
    ) -> list[SessionData]:
        """Load multiple sessions by IDs."""
        result: list[SessionData] = []
        for sid in session_ids:
            session = await self.load(sid)
            if session is not None:
                if agent_name is not None and session.agent_name != agent_name:
                    continue
                result.append(session)
        return result

    async def update_sdk_session_id(
        self,
        session_id: str,
        sdk_session_id: str,
    ) -> None:
        """No-op for in-memory store."""

    async def save_checkpoint(
        self,
        session_id: str,
        messages_json: str,
        pending_calls: list[dict[str, object]],
    ) -> None:
        """Save checkpoint data for a session.

        Args:
            session_id: Session identifier
            messages_json: Serialized message history
            pending_calls: List of pending deferred call info dicts
        """
        self._checkpoints[session_id] = {
            "messages_json": messages_json,
            "pending_calls": pending_calls,
        }
        logger.debug("Saved checkpoint", session_id=session_id)

    async def load_checkpoint(self, session_id: str) -> dict[str, object] | None:
        """Load checkpoint data for a session.

        Args:
            session_id: Session identifier

        Returns:
            Dict with messages_json and pending_calls, or None if no checkpoint exists
        """
        return self._checkpoints.get(session_id)

    async def delete_checkpoint(self, session_id: str) -> bool:
        """Delete checkpoint data for a session.

        Args:
            session_id: Session identifier

        Returns:
            True if checkpoint was deleted, False if not found
        """
        if session_id in self._checkpoints:
            del self._checkpoints[session_id]
            logger.debug("Deleted checkpoint", session_id=session_id)
            return True
        return False
