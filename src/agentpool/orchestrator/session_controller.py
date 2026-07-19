"""Session controller for per-session agent lifecycle management.

Extracted from orchestrator/core.py as part of the thin-wrapper refactor.
Manages session creation, run tracking, and agent resolution.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final

import anyio

from agentpool.log import get_logger
from agentpool.orchestrator.runtime_registry import RuntimeAgentRegistry
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from agentpool.delegation import AgentPool
    from agentpool.models.pending_interaction import PendingPermission
    from agentpool.orchestrator.event_bus import EventBus
    from agentpool_storage.protocols import SessionPersistence


logger = get_logger(__name__)

DEFAULT_SESSION_TTL_SECONDS: Final[float] = 3600.0


class SessionNotFoundError(Exception):
    """Raised when a session cannot be found for resume."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id


class SessionBusyError(Exception):
    """Raised when trying to resume a session that has an active run."""

    def __init__(self, session_id: str, run_id: str) -> None:
        super().__init__(
            f"Session '{session_id}' already has an active run '{run_id}'. "
            "Wait for it to complete or cancel it first."
        )
        self.session_id = session_id
        self.run_id = run_id


class SessionClosedError(Exception):
    """Raised when rejecting pending elicitation futures on session close.

    This exception is set on pending ``asyncio.Future`` instances in
    ``ElicitationFutureRegistry`` when a session is closed, ensuring
    any suspended ``handle_elicitation()`` calls unblock immediately.

    Attributes:
        session_id: The session that was closed.
    """

    def __init__(self, session_id: str) -> None:
        """Initialize with the closed session ID.

        Args:
            session_id: The session that was closed.
        """
        self.session_id = session_id
        super().__init__(f"Session closed: {session_id}")


class CheckpointMismatchError(Exception):
    """Raised when deferred_tool_results don't cover all pending_deferred_calls."""

    def __init__(
        self,
        session_id: str,
        expected: set[str],
        provided: set[str],
        missing: set[str],
        extra: set[str],
    ) -> None:
        parts: list[str] = []
        if missing:
            parts.append(f"missing results for: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected results for: {sorted(extra)}")
        msg = (
            f"Checkpoint mismatch for session '{session_id}': "
            + "; ".join(parts)
            + f". Expected tool_call_ids: {sorted(expected)}, provided: {sorted(provided)}."
        )
        super().__init__(msg)
        self.session_id = session_id
        self.expected = expected
        self.provided = provided
        self.missing = missing
        self.extra = extra


class SessionLifecyclePolicy:
    """Session lifecycle policy constants and helpers."""

    VALID: ClassVar[tuple[str, str, str]] = ("independent", "cascade", "bound")

    @classmethod
    def default(cls) -> str:
        return "cascade"

    @classmethod
    def is_valid(cls, policy: str) -> bool:
        return policy in cls.VALID


def _create_cancel_scope() -> anyio.CancelScope | None:
    """Create CancelScope if an event loop is running, else return None.

    Allows SessionState to be instantiated in synchronous contexts (e.g. tests)
    where no async event loop is available.
    """
    try:
        return anyio.CancelScope()
    except anyio.NoEventLoopError:
        return None


@dataclass
class SessionState:
    """Per-session state managed by the session pool.

    Attributes:
        session_id: Unique identifier for the session.
        agent_name: Name of the agent associated with this session.
        agent: The actual agent instance (shared or per-session).
        metadata: Arbitrary metadata attached to the session.
        created_at: Timestamp when the session was created.
        last_active_at: Timestamp of the most recent activity.
        closed_at: Timestamp when the session was closed, or None if active.
        is_per_session_agent: Whether the agent is dedicated to this session.
        turn_lock: Lock ensuring only one turn runs per session at a time.
        is_closing: Flag indicating the session is being closed.
    """

    session_id: str
    agent_name: str
    agent: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    last_active_at: float = field(default_factory=time.monotonic)
    created_at_wall: datetime = field(default_factory=get_now)
    """Wall-clock creation timestamp (UTC datetime) for persistence.

    ``created_at`` and ``last_active_at`` use ``time.monotonic()`` for
    elapsed-time calculations (idle detection, session age).  They must
    NOT be passed to ``datetime.fromtimestamp()`` — that function treats
    its argument as a Unix epoch timestamp, producing a 1970 datetime.
    This field stores the real wall-clock creation time so that
    ``_state_to_data()`` can persist a correct ``created_at``.
    """
    closed_at: float | None = None
    is_per_session_agent: bool = False
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_closing: bool = False
    parent_session_id: str | None = None
    lifecycle_policy: str = field(default_factory=SessionLifecyclePolicy.default)
    current_run_id: str | None = None
    cancel_scope: anyio.CancelScope | None = field(default_factory=_create_cancel_scope)
    _request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_owner_task: asyncio.Task[Any] | None = None
    input_provider: Any | None = None
    pending_questions: dict[str, Any] = field(default_factory=dict)
    """Pending questions stored on SessionState for per-session isolation."""
    checkpoint_enabled: bool = False
    """Whether durable elicitation/checkpointing is enabled for this session."""

    @property
    def closing(self) -> bool:
        """Alias for is_closing."""
        return self.is_closing

    @closing.setter
    def closing(self, value: bool) -> None:
        self.is_closing = value


# Mixin imports placed after SessionState/exception definitions to avoid
# circular import issues (mixins need SessionState at runtime).
from agentpool.orchestrator.session_controller_agent import (  # noqa: E402
    SessionControllerAgentMixin,
)
from agentpool.orchestrator.session_controller_close import (  # noqa: E402
    SessionControllerCloseMixin,
)
from agentpool.orchestrator.session_controller_runs import SessionControllerRunsMixin  # noqa: E402


class SessionController(
    SessionControllerAgentMixin,
    SessionControllerRunsMixin,
    SessionControllerCloseMixin,
):
    """Manages per-session agent lifecycle.

    Extracted from ACP's AgentPoolACPAgent._session_agents and
    OpenCode's ServerState._session_agents.

    Safety features:
    - Single global lock for session creation (no DCL)
    - Per-session turn lock for serialization
    - Explicit cleanup of all resources
    - Support for all agent types (with per-session agents for NativeAgentConfig only)
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        store: SessionPersistence | None = None,
        cleanup_callback: Callable[[str], Awaitable[None]] | None = None,
        max_concurrent_runs: int | None = None,
        session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS,
        cleanup_interval_seconds: float | None = None,
        deferred_cleanup_interval_seconds: float = 60.0,
    ) -> None:
        """Initialize the session controller.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            cleanup_callback: Optional callback invoked when a session is cleaned up.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
            session_ttl_seconds: TTL for idle sessions in seconds.
            cleanup_interval_seconds: Interval for the session TTL cleanup
                loop. Defaults to ``session_ttl_seconds / 2``.
            deferred_cleanup_interval_seconds: Interval for the deferred
                call expiry cleanup loop in seconds.
        """
        self.pool = pool
        self.store = store
        self._cleanup_callback = cleanup_callback
        self._sessions: dict[str, SessionState] = {}
        self._session_agents: dict[str, Any] = {}
        self._children: dict[str, list[str]] = {}
        self._session_scopes: dict[str, anyio.CancelScope] = {}
        self._lock = asyncio.Lock()
        self._session_ttl_seconds: float = session_ttl_seconds
        self._cleanup_interval_seconds: float = (
            cleanup_interval_seconds
            if cleanup_interval_seconds is not None
            else session_ttl_seconds / 2
        )
        self._deferred_cleanup_interval_seconds: float = deferred_cleanup_interval_seconds
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._deferred_cleanup_task: asyncio.Task[Any] | None = None
        self._mcp_max_processes: int = 100
        self._mcp_process_count: int = 0
        self._runs: dict[str, Any] = {}
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._max_concurrent_runs: int | None = max_concurrent_runs
        self._event_bus: EventBus | None = None
        self._pending_run_ids: dict[str, str] = {}
        self._todo_lock: asyncio.Lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._runtime_registry = RuntimeAgentRegistry()

    @property
    def runtime_registry(self) -> RuntimeAgentRegistry:
        """Runtime agent registry for programmatically-created agents."""
        return self._runtime_registry

    def get_session(self, session_id: str) -> SessionState | None:
        """Get a session by ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            The session state, or None if not found.
        """
        return self._sessions.get(session_id)

    def get_children(self, session_id: str) -> list[str]:
        """Get child session IDs for a session.

        Args:
            session_id: The parent session ID.

        Returns:
            List of child session IDs.
        """
        return list(self._children.get(session_id, []))

    def get_parent(self, session_id: str) -> SessionState | None:
        """Get the parent session state for a session.

        Args:
            session_id: The child session ID.

        Returns:
            The parent session state, or None if not found.
        """
        session = self._sessions.get(session_id)
        if session is None or session.parent_session_id is None:
            return None
        return self._sessions.get(session.parent_session_id)

    def find_sessions_by_agent_name(self, agent_name: str) -> list[SessionState]:
        """Find all active sessions associated with a given agent name.

        Args:
            agent_name: The agent name to search for.

        Returns:
            List of session states matching the agent name, excluding closing sessions.
        """
        return [
            s for s in self._sessions.values() if s.agent_name == agent_name and not s.is_closing
        ]

    def _count_mcp_processes(self) -> int:
        """Count active MCP processes across all per-session agents.

        Returns:
            The tracked MCP process count.
        """
        return self._mcp_process_count

    def _increment_mcp_count(self, _agent: Any) -> None:
        """Increment MCP process count when a per-session agent is created.

        Args:
            _agent: The agent whose creation triggered the increment.
        """
        self._mcp_process_count += 1

    def _decrement_mcp_count(self, _agent: Any) -> None:
        """Decrement MCP process count when a per-session agent is destroyed.

        Args:
            _agent: The agent whose destruction triggered the decrement.
        """
        self._mcp_process_count = max(0, self._mcp_process_count - 1)

    def list_pending_questions(self) -> list[Any]:
        """List all pending questions across sessions.

        Aggregates pending questions from each session's SessionState.

        Returns:
            A list of pending question objects.
        """
        result: list[Any] = []
        for session in self._sessions.values():
            result.extend(session.pending_questions.values())
        return result

    def cancel_all_pending_questions(self) -> list[str]:
        """Cancel all pending questions across all sessions.

        Iterates over every session, cancels each pending question's future,
        and returns the IDs of all cancelled questions.

        Returns:
            List of cancelled question IDs.
        """
        cancelled_ids: list[str] = []
        for session in self._sessions.values():
            for question_id, pending in list(session.pending_questions.items()):
                future = getattr(pending, "future", None)
                if future is not None and not future.done():
                    future.cancel()
                    cancelled_ids.append(question_id)
        return cancelled_ids

    def cancel_session_pending_questions(self, session_id: str) -> list[str]:
        """Cancel pending questions for a specific session.

        Args:
            session_id: The session whose pending questions should be cancelled.

        Returns:
            List of cancelled question IDs.
        """
        cancelled_ids: list[str] = []
        session = self._sessions.get(session_id)
        if session is None:
            return cancelled_ids
        for question_id, pending in list(session.pending_questions.items()):
            future = getattr(pending, "future", None)
            if future is not None and not future.done():
                future.cancel()
                cancelled_ids.append(question_id)
        return cancelled_ids

    def list_pending_permissions(self) -> list[PendingPermission]:
        """List all pending permissions across sessions.

        Returns:
            A list of pending permissions. Currently returns an empty list.
        """
        return []

    async def start_cleanup_task(self) -> None:
        """Start background tasks for session cleanup.

        Launches two background tasks:
        - ``_cleanup_loop``: periodically closes expired sessions (TTL-based).
        - ``_start_cleanup_loop``: periodically expires stale deferred calls.

        Both tasks are stored in ``_background_tasks`` to prevent garbage
        collection mid-execution (per asyncio.create_task best practice).
        """
        if self._cleanup_task is None:
            task = asyncio.create_task(self._cleanup_loop())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            self._cleanup_task = task
        if self._deferred_cleanup_task is None:
            task = asyncio.create_task(self._start_cleanup_loop())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)
            self._deferred_cleanup_task = task

    async def stop_cleanup_task(self) -> None:
        """Stop both background cleanup tasks."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        if self._deferred_cleanup_task is not None:
            self._deferred_cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._deferred_cleanup_task
            self._deferred_cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Periodically scan and close expired sessions.

        Runs every ``cleanup_interval_seconds`` (default: 30 minutes).
        A session is expired if last_active_at is older than session_ttl_seconds.
        """
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval_seconds)
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Session cleanup failed")

    async def _cleanup_expired_sessions(self) -> None:
        """Close all sessions that have exceeded TTL.

        Sessions with an active run are never expired — the run itself
        is proof of activity regardless of ``last_active_at`` age.
        """
        now = time.monotonic()
        expired_sessions: list[str] = []

        async with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.current_run_id is not None:
                    continue
                if now - session.last_active_at > self._session_ttl_seconds:
                    expired_sessions.append(session_id)

        for session_id in expired_sessions:
            logger.info("Closing expired session", session_id=session_id)
            try:
                if self._cleanup_callback is not None:
                    await self._cleanup_callback(session_id)
                else:
                    await self.close_session(session_id)
            except Exception:
                logger.exception(
                    "Failed to close expired session during cleanup",
                    session_id=session_id,
                )

    async def _start_cleanup_loop(self) -> None:
        """Periodically scan and expire deferred calls whose timeout has elapsed.

        Runs indefinitely in a background task. Checks every
        ``deferred_cleanup_interval_seconds`` (default: 60 seconds)
        for pending deferred calls whose timeout has elapsed and removes
        them from the session data.
        """
        while True:
            try:
                await asyncio.sleep(self._deferred_cleanup_interval_seconds)
                if self.store is None:
                    continue
                async with self._lock:
                    for session_id in list(self._sessions.keys()):
                        data = await self.store.load_session(session_id)
                        if data is None:
                            continue
                        expired = self._check_expired_calls(data)
                        if expired:
                            remaining = [
                                c
                                for c in data.pending_deferred_calls
                                if c.tool_call_id not in {e.tool_call_id for e in expired}
                            ]
                            updated = data.model_copy(update={"pending_deferred_calls": remaining})
                            await self.store.save_session(updated)
                            logger.info(
                                "Removed expired deferred calls",
                                session_id=session_id,
                                count=len(expired),
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Deferred call cleanup loop failed")
