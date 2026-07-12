"""Session controller for per-session agent lifecycle management.

Extracted from orchestrator/core.py as part of the thin-wrapper refactor.
Manages session creation, run tracking, and agent resolution.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final
import uuid

import anyio

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    RunFailedEvent,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool.orchestrator.run import RunHandle, RunStatus, inject_cancelled_tool_results
from agentpool.orchestrator.runtime_registry import RuntimeAgentRegistry
from agentpool.sessions.models import PendingDeferredCall, SessionData
from agentpool_server.opencode_server.models.session_info import SessionInfo


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.models.pending_interaction import PendingPermission
    from agentpool.orchestrator.event_bus import EventBus
    from agentpool.sessions.store import SessionStore


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
    agent: BaseAgent[Any, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    last_active_at: float = field(default_factory=time.monotonic)
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


class SessionController:
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
        store: SessionStore | None = None,
        cleanup_callback: Callable[[str], Awaitable[None]] | None = None,
        max_concurrent_runs: int | None = None,
    ) -> None:
        """Initialize the session controller.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            cleanup_callback: Optional callback invoked when a session is cleaned up.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
        """
        self.pool = pool
        self.store = store
        self._cleanup_callback = cleanup_callback
        self._sessions: dict[str, SessionState] = {}
        self._session_agents: dict[str, BaseAgent[Any, Any]] = {}
        self._children: dict[str, list[str]] = {}
        self._session_scopes: dict[str, anyio.CancelScope] = {}
        self._lock = asyncio.Lock()
        self._session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._deferred_cleanup_task: asyncio.Task[Any] | None = None
        self._mcp_max_processes: int = 100
        self._mcp_process_count: int = 0
        self._runs: dict[str, RunHandle] = {}
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

    async def get_or_create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> tuple[SessionState, bool]:
        """Get or create a session.

        Uses single global lock for simplicity and safety.
        Session creation is infrequent - no need for DCL optimization.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            A tuple of (session_state, was_created) where was_created is True
            if the session was newly created, False if it already existed.
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty or whitespace")

        async with self._lock:
            return await self._get_or_create_session_locked(
                session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
            )

    def _state_to_data(self, state: SessionState) -> SessionData:
        """Convert SessionState to persistable SessionData.

        Args:
            state: The session state to convert.

        Returns:
            Persistable session data.
        """
        return SessionData(
            session_id=state.session_id,
            agent_name=state.agent_name,
            parent_id=state.parent_session_id,
            project_id=state.metadata.get("project_id"),
            cwd=state.metadata.get("cwd"),
            agent_type=state.metadata.get("agent_type"),
            created_at=datetime.fromtimestamp(state.created_at),
            last_active=datetime.fromtimestamp(state.last_active_at),
            metadata=state.metadata,
        )

    async def _get_or_create_session_locked(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> tuple[SessionState, bool]:
        """Get or create a session - caller MUST hold self._lock.

        This internal method avoids deadlock when called from
        get_or_create_session_agent() which already holds the lock.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            A tuple of (session_state, was_created) where was_created is True
            if the session was newly created, False if it already existed.
        """
        if session_id in self._sessions:
            state = self._sessions[session_id]
            state.last_active_at = time.monotonic()
            return state, False

        effective_policy = lifecycle_policy or (
            self._sessions.get(parent_session_id, SessionState("", "")).lifecycle_policy
            if parent_session_id and parent_session_id in self._sessions
            else SessionLifecyclePolicy.default()
        )

        # Ensure agent_name is always a real string (guards against Mock
        # attributes in tests where pool.main_agent_name is a MagicMock).
        _main_agent_name = self.pool.main_agent_name
        if not isinstance(_main_agent_name, str):
            _main_agent_name = "default"
        state = SessionState(
            session_id=session_id,
            agent_name=agent_name or _main_agent_name,
            parent_session_id=parent_session_id,
            lifecycle_policy=effective_policy,
            metadata=metadata,
            checkpoint_enabled=self.store is not None,
        )
        self._sessions[session_id] = state

        # Clear todos for new top-level sessions only (not subagents)
        # This prevents accumulation of todos from previous sessions
        # Use dedicated lock to prevent race conditions with concurrent sessions
        if (
            parent_session_id is None
            and hasattr(self.pool, "todos")
            and self.pool.todos is not None
        ):
            _entries = self.pool.todos.entries
            if isinstance(_entries, (list, tuple)) and len(_entries) > 0:
                async with self._todo_lock:
                    # Double-check after acquiring lock
                    _entries = self.pool.todos.entries
                    if isinstance(_entries, (list, tuple)) and len(_entries) > 0:
                        cleared_count = len(_entries)
                        self.pool.todos.clear()
                        logger.info(
                            "Cleared todos for new top-level session",
                            session_id=session_id,
                            agent_name=state.agent_name,
                            cleared_entries=cleared_count,
                        )

        if parent_session_id and effective_policy in ("cascade", "bound"):
            parent_scope = self._session_scopes.get(parent_session_id)
            if parent_scope is not None:
                child_scope = anyio.CancelScope()
                self._session_scopes[session_id] = child_scope
            else:
                self._session_scopes[session_id] = anyio.CancelScope()
        else:
            self._session_scopes[session_id] = anyio.CancelScope()
        if self.store is not None:
            # Only save if no existing data — callers like
            # ACPSessionManager.create_session() may have already
            # persisted richer SessionData (with cwd, project_id, etc.)
            existing = await self.store.load(session_id)
            if existing is None:
                await self.store.save(self._state_to_data(state))
        if parent_session_id:
            self._children.setdefault(parent_session_id, []).append(session_id)
        logger.info("Created session", session_id=session_id, agent_name=state.agent_name)
        return state, True

    async def get_or_create_session_agent(
        self,
        session_id: str,
        agent_name: str | None = None,
        input_provider: Any | None = None,
    ) -> BaseAgent[Any, Any]:
        """Get or create a dedicated agent for a session.

        Delegates agent creation to ``AgentFactory.create_session_agent()``,
        handling caching, session lookup, and config resolution locally.

        NOTE: Always acquires self._lock to prevent races with close_session().

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to use.
            input_provider: Optional input provider for the agent.

        Returns:
            The agent instance (per-session or shared).
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty or whitespace")

        async with self._lock:
            if session_id in self._session_agents:
                agent = self._session_agents[session_id]
                # Update input_provider on cached agent if a new one is provided.
                # Without this, the agent keeps the stale (or None) input_provider
                # from when it was first cached, causing elicitation failures.
                if input_provider is not None:
                    session = self._sessions.get(session_id)
                    if session is not None:
                        session.input_provider = input_provider
                    agent._input_provider = input_provider
                return agent

            session, _was_created = await self._get_or_create_session_locked(session_id, agent_name)
            agent_name = agent_name or session.agent_name

            cfg = self.pool.manifest.agents.get(agent_name)
            if cfg is None:
                cfg = self._runtime_registry.lookup(agent_name)

            if cfg is None:
                # Config not found
                available_manifest = list(self.pool.manifest.agents.keys())
                available_runtime = self._runtime_registry.names()
                msg = (
                    f"Agent config not found: {agent_name!r}. "
                    f"Available in manifest: {available_manifest}. "
                    f"Available in runtime registry: {available_runtime}."
                )
                raise RuntimeError(msg)

            # Resolve parent agent for child sessions.
            parent_agent: BaseAgent[Any, Any] | None = None
            if session.parent_session_id:
                parent_state = self._sessions.get(session.parent_session_id)
                parent_agent = parent_state.agent if parent_state else None

            # Delegate creation to AgentFactory.
            agent = await self.pool._factory.create_session_agent(
                agent_name=agent_name,
                session_id=session_id,
                host_context=self.pool.get_context(),
                session=session,
                cfg=cfg,
                input_provider=input_provider,
                parent_agent=parent_agent,
            )

            # Cache and session state.
            if input_provider is not None:
                session.input_provider = input_provider
            self._session_agents[session_id] = agent
            session.agent = agent

            # Path A (child): False — parent manages lifecycle.
            # Paths B/C (main/non-native): True — close_session calls __aexit__.
            if session.parent_session_id:
                session.is_per_session_agent = False
            else:
                session.is_per_session_agent = True
                self._increment_mcp_count(agent)

            logger.info("Created session agent", session_id=session_id, agent_name=agent_name)
            return agent
            available_manifest = list(self.pool.manifest.agents.keys())
            available_runtime = self._runtime_registry.names()
            msg = (
                f"Agent config not found: {agent_name!r}. "
                f"Available in manifest: {available_manifest}. "
                f"Available in runtime registry: {available_runtime}."
            )
            raise RuntimeError(msg)

    def list_sessions(self) -> list[SessionInfo]:
        """List all active sessions.

        Returns:
            A list of SessionInfo DTOs for all active sessions.
        """
        return [
            SessionInfo(
                session_id=s.session_id,
                agent_name=s.agent_name,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                is_per_session_agent=s.is_per_session_agent,
                status="busy" if s.current_run_id is not None else "idle",
            )
            for s in self._sessions.values()
        ]

    def get_session_agent(self, session_id: str) -> BaseAgent[Any, Any] | None:
        """Get the agent for a session.

        Returns the per-session agent if one exists, otherwise the shared
        agent that was assigned to the session.  If the session has no
        agent assigned yet, a warning is logged and None is returned.

        Args:
            session_id: The session ID to look up.

        Returns:
            The agent instance, or None if the session is unknown.
        """
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("Session not found", session_id=session_id)
            return None
        agent = self._session_agents.get(session_id)
        if agent is None:
            logger.warning(
                "No agent assigned for session - falling back to shared agent",
                session_id=session_id,
            )
            return None
        return agent

    async def _close_session_unlocked(self, session_id: str) -> None:
        """Close a session without acquiring the main lock (caller must hold lock)."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.is_closing = True
        session.closed_at = time.monotonic()
        # Recursively close children, respecting their lifecycle policies
        children = self._children.pop(session_id, [])
        for child_id in children:
            child_session = self._sessions.get(child_id)
            if child_session is not None and child_session.lifecycle_policy == "independent":
                continue
            try:
                await self._close_session_unlocked(child_id)
            except Exception:
                logger.exception(
                    "Failed to close child session during cascade close",
                    child_id=child_id,
                )
        self._session_agents.pop(session_id, None)
        self._sessions.pop(session_id, None)
        if self.store is not None:
            await self._mark_session_closed(session_id)
        # Remove from parent's children list
        if session.parent_session_id and session.parent_session_id in self._children:
            self._children[session.parent_session_id] = [
                cid for cid in self._children[session.parent_session_id] if cid != session_id
            ]

    @staticmethod
    def _should_checkpoint_on_close(data: SessionData | None) -> bool:
        """Check whether a session should be checkpointed before close.

        A session needs checkpoint-on-close when it has pending deferred calls
        that must be preserved for later resume.

        Args:
            data: The session data loaded from the store, or None.

        Returns:
            True if the session has pending deferred calls that require
            checkpointing before releasing resources.
        """
        return data is not None and bool(data.pending_deferred_calls)

    @staticmethod
    def _check_expired_calls(session_data: SessionData) -> list[PendingDeferredCall]:
        """Return pending calls whose timeout has elapsed.

        Args:
            session_data: The session data to check for expired calls.

        Returns:
            A list of ``PendingDeferredCall`` entries whose timeout has
            elapsed. Returns an empty list if none have expired.
        """
        now = datetime.now()
        return [
            call
            for call in session_data.pending_deferred_calls
            if call.timeout is not None and (now - call.created_at) > call.timeout
        ]

    async def _save_close_checkpoint(self, session_id: str, data: SessionData) -> bool:
        """Save session data with checkpointed status before close.

        Marks the session as ``"checkpointed"`` so it can be located by
        :meth:`resume_session` later. Returns ``True`` on success, ``False``
        if the storage write fails (caller should NOT release resources).

        Args:
            session_id: Session identifier (for logging).
            data: The session data to persist as checkpointed.

        Returns:
            True if the checkpoint was saved successfully, False on failure.
        """
        try:
            data = data.model_copy(update={"status": "checkpointed"})
            data.touch()
            if self.store is not None:
                await self.store.save(data)
            logger.info(
                "Session checkpointed before close",
                session_id=session_id,
                pending_call_count=len(data.pending_deferred_calls),
            )
        except Exception:
            logger.exception(
                "Failed to save checkpoint before close",
                session_id=session_id,
            )
            return False
        else:
            return True

    async def _mark_session_closed(self, session_id: str) -> None:
        """Mark a session as closed in the store instead of deleting it.

        This preserves session data across server restarts so that clients
        can resume sessions via ``session/resume`` or ``session/load`` after
        a server restart.

        Args:
            session_id: Session identifier to mark as closed.
        """
        assert self.store is not None
        data = await self.store.load(session_id)
        if data is None:
            logger.debug("Session not in store, skipping close mark", session_id=session_id)
            return
        data = data.model_copy(update={"status": "closed"})
        data.touch()
        await self.store.save(data)
        logger.debug("Session marked as closed in store", session_id=session_id)

    async def _close_session_run_turn(self, session_id: str) -> None:  # noqa: PLR0915
        """Close a session using the RunHandle lifecycle.

        Flow:
        1. Signal ``RunHandle.close()`` (sets ``_closing``, wakes idle loop).
        2. Mark ``session.closing = True``.
        3. Cancel the session ``CancelScope``.
        4. Acquire ``turn_lock`` (10 s timeout) — graceful turn completion.
        5. Await ``complete_event`` (10 s timeout) — graceful run completion.
        6. On timeout: call ``RunHandle.cancel()``.
        7. Clean up tracking dicts and agent context.

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

            run_handle: RunHandle | None = None
            if session.current_run_id:
                run_handle = self._runs.get(session.current_run_id)
            if run_handle is not None:
                run_handle.close()

            session.closing = True
            session.closed_at = time.monotonic()

            scope = self._session_scopes.pop(session_id, None)
            if scope is not None:
                scope.cancel()

        acquired = False
        try:
            try:
                async with asyncio.timeout(10):
                    await session.turn_lock.acquire()
                acquired = True
            except TimeoutError:
                logger.warning(
                    "Timeout waiting for turn_lock during close_session (run-turn path)",
                    session_id=session_id,
                )

            if run_handle is not None and acquired:
                # Signal the idle/wake loop to exit so complete_event gets set.
                run_handle.close()
                try:
                    async with asyncio.timeout(2):
                        await run_handle.complete_event.wait()
                except TimeoutError:
                    logger.warning(
                        "Timeout waiting for run completion, cancelling",
                        session_id=session_id,
                    )
                    run_handle.cancel()
            elif run_handle is not None:
                run_handle.cancel()
        finally:
            if acquired:
                session.turn_lock.release()

        # Checkpoint-on-close: if pending deferred calls exist, save as
        # checkpointed before releasing resources. If checkpoint fails,
        # keep session in memory so it can be retried.
        _checkpointed = False
        if self.store is not None:
            _data = await self.store.load(session_id)
            if self._should_checkpoint_on_close(_data):
                assert _data is not None
                _checkpointed = await self._save_close_checkpoint(session_id, _data)
                if not _checkpointed:
                    logger.warning(
                        "Checkpoint failed, keeping session in memory",
                        session_id=session_id,
                    )
                    return

        async with self._lock:
            children = self._children.pop(session_id, [])
            if children:
                for child_id in children:
                    child_session = self._sessions.get(child_id)
                    if (
                        child_session is not None
                        and child_session.lifecycle_policy == "independent"
                    ):
                        continue
                    try:
                        await self._close_session_unlocked(child_id)
                    except Exception:
                        logger.exception(
                            "Failed to close child session during cascade close",
                            child_id=child_id,
                        )

            agent = self._session_agents.pop(session_id, None)
            self._sessions.pop(session_id, None)
            if self.store is not None and not _checkpointed:
                await self._mark_session_closed(session_id)
            if session.parent_session_id and session.parent_session_id in self._children:
                self._children[session.parent_session_id] = [
                    cid for cid in self._children[session.parent_session_id] if cid != session_id
                ]

        # Cleanup MCP session-scoped resources (toolset cache, connection pool,
        # ACP connection manager) BEFORE agent.__aexit__ so the agent context
        # is still live. Runs for ALL agents, not just per-session agents,
        # because shared MCPManager also has session-scoped contexts.
        if agent is not None:
            try:
                await agent.mcp.cleanup_session(session_id)
            except Exception:
                logger.exception("Failed to cleanup MCP session", session_id=session_id)

        if agent is not None and session.is_per_session_agent:
            try:
                await agent.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to exit agent context", session_id=session_id)
            finally:
                self._decrement_mcp_count(agent)

        logger.info("Closed session (run-turn path)", session_id=session_id)

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Uses the RunHandle lifecycle:
        1. Signal ``RunHandle.close()`` (sets ``_closing``, wakes idle loop).
        2. Mark ``session.closing = True``.
        3. Cancel the session ``CancelScope``.
        4. Acquire ``turn_lock`` (10 s timeout) — graceful turn completion.
        5. Await ``complete_event`` (10 s timeout) — graceful run completion.
        6. On timeout: call ``RunHandle.cancel()``.
        7. Clean up tracking dicts and agent context.

        Args:
            session_id: The session to close.
        """
        await self._close_session_run_turn(session_id)

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

    async def _consume_run(self, run_handle: RunHandle, initial_prompt: str) -> None:
        """Drive a RunHandle.start() async generator to completion.

        Events are published to the EventBus inside ``start()``, so this
        coroutine only needs to keep the generator alive until the first
        turn completes (StreamCompleteEvent or RunErrorEvent). After that,
        the generator is closed so that ``start()`` exits its idle/wake
        loop and ``complete_event`` is set.

        If ``start()`` raises an exception before yielding a terminal
        event, a ``RunErrorEvent`` and ``RunFailedEvent`` are published to
        the EventBus so that subscribers (e.g. background_output in
        BackgroundTaskCapability) are unblocked instead of waiting forever.

        Args:
            run_handle: The run handle whose ``start()`` to consume.
            initial_prompt: The first user prompt.
        """
        gen = run_handle.start(initial_prompt)
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                    break
        except Exception as exc:
            logger.exception(
                "RunHandle.start() raised for run_id=%s session_id=%s",
                run_handle.run_id,
                run_handle.session_id,
            )
            error_event = RunErrorEvent(
                message=f"{type(exc).__name__}: {exc}",
                run_id=run_handle.run_id,
                agent_name=run_handle.agent_type,
            )
            if self._event_bus is not None:
                await self._event_bus.publish(run_handle.session_id, error_event)
                await self._event_bus.publish(
                    run_handle.session_id,
                    RunFailedEvent(
                        run_id=run_handle.run_id,
                        session_id=run_handle.session_id,
                        exception=exc,
                    ),
                )
        finally:
            await gen.aclose()

    def _start_run_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
        content: str,
        *,
        deps: Any = None,
    ) -> RunHandle:
        """Create, register, and launch a RunHandle via the new path.

        Creates the RunHandle with ProtocolTrigger and ProtocolChannel
        lifecycle dimensions when an EventBus is available. The
        ProtocolChannel publishes events to the EventBus (replacing
        the direct ``event_bus.publish()`` calls in ``start()``),
        and the ProtocolTrigger allows steer/followup delivery via
        ``trigger.deliver()``.

        Args:
            session: The session state.
            agent: The agent instance (native or ACP).
            session_id: The session identifier.
            content: The initial prompt text.
            deps: Optional dependencies to pass to the agent run context
                (e.g. delegation_depth from BackgroundTaskCapability).

        Returns:
            The newly created RunHandle.
        """
        from agentpool.lifecycle import (
            MemoryJournal,
            ProtocolChannel,
            ProtocolTrigger,
        )

        event_bus = self._event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus, deps=deps)
        # Bridge agent.conversation (ChatMessage list) → list[ModelMessage]
        # so the new RunHandle has the full conversation history from prior
        # turns. Without this, each new RunHandle starts with empty
        # _message_history and the model loses all context.
        # Not all agent types have a conversation attribute (e.g. ACP agents),
        # so use getattr with a fallback.
        model_messages: list[ModelMessage] = []
        conversation = getattr(agent, "conversation", None)
        if conversation is not None:
            for chat_msg in conversation.get_history():
                model_messages.extend(chat_msg.messages)
        # Inject RetryPromptPart for any trailing unprocessed tool calls
        # (e.g. from a cancelled turn). Without this, PydanticAI rejects
        # the next user prompt with "unprocessed tool calls" error.
        model_messages = inject_cancelled_tool_results(model_messages)

        # Create lifecycle dimensions for protocol server integration.
        trigger = ProtocolTrigger()
        comm_channel: ProtocolChannel | None = None
        if event_bus is not None:
            journal = MemoryJournal()
            comm_channel = ProtocolChannel(
                journal=journal,
                event_bus=event_bus,
                session_id=session_id,
            )

        host_ctx = self.pool.get_context()
        # Build an AgentRegistry with agent names from the manifest.
        # Agents are created lazily, so the registry stores names only.
        # The DelegationService uses session_pool to spawn actual instances.
        from agentpool.host.registry import AgentRegistry

        agent_registry = AgentRegistry(
            dict.fromkeys(self.pool.manifest.agents),  # type: ignore[arg-type]
        )
        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
            _message_history=model_messages,
            _trigger_source=trigger,
            _comm_channel=comm_channel,
            _host_context=host_ctx,
            _agent_registry=agent_registry,
        )
        self._runs[run_handle.run_id] = run_handle
        session.current_run_id = run_handle.run_id
        task = asyncio.create_task(self._consume_run(run_handle, content))
        # Keep a strong reference to prevent GC from destroying the task.
        self._background_tasks.add(task)

        def _on_run_done(t: asyncio.Task[Any], rid: str = run_handle.run_id) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "Background run task failed for run_id=%s: %s",
                    rid,
                    t.exception(),
                )
            self._cleanup_run(rid)

        task.add_done_callback(_on_run_done)
        return run_handle

    async def receive_request(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Receive an incoming request for a session.

        Routes through the RunHandle path: idle sessions create a
        RunHandle, busy sessions call ``steer()`` / ``followup()``.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: ``"when_idle"`` to queue, ``"asap"`` to inject into active turn.
                Aliases: ``"steer"`` → ``"asap"``, ``"followup"`` → ``"when_idle"``.
            **kwargs: Additional arguments passed to the turn runner (e.g. input_provider).

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        # Update last_active_at on every incoming request so the TTL sweeper
        # does not expire sessions that are actively receiving messages.
        session.last_active_at = time.monotonic()
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        # Extract deps from kwargs so they are passed to AgentRunContext
        # for the child agent run (e.g. delegation_depth from
        # BackgroundTaskCapability._task_async).
        deps = kwargs.pop("deps", None)
        agent = await self.get_or_create_session_agent(session_id, input_provider=input_provider)
        if agent is None:
            return None
        # RunHandle path (always)
        resolved = {"steer": "asap", "followup": "when_idle"}.get(priority, priority)
        # Convert content to string safely. Empty list (from ACP handler when
        # user sends only a slash command) must become "" not "[]".
        # Lists with content should be joined, not str()'d (which produces "['hello']").
        if isinstance(content, list):
            content_str = " ".join(str(c) for c in content) if content else ""
        elif not content:
            content_str = ""
        else:
            content_str = str(content)
        async with session._request_lock:
            if session.closing or session.is_closing:
                return None
            # Stale-run detection: if current_run_id points to a missing
            # or terminal run, clear it and start a new run.
            if session.current_run_id is not None:
                existing_run = self._runs.get(session.current_run_id)
                if existing_run is None or existing_run._status in (
                    RunStatus.failed,
                    RunStatus.completed,
                    RunStatus.done,
                ):
                    session.current_run_id = None
            if session.current_run_id is None:
                return self._start_run_handle(session, agent, session_id, content_str, deps=deps)
            run = self._runs.get(session.current_run_id) if session.current_run_id else None
            if run is not None:
                if resolved == "asap":
                    run.steer(content_str)
                else:
                    run.followup(content_str)
        return None

    def cancel_run_for_session(self, session_id: str) -> None:
        """Cancel the active run for a session.

        Args:
            session_id: The session whose run should be cancelled.
        """
        session = self.get_session(session_id)
        if session is None:
            return
        run_id = session.current_run_id
        if run_id is None:
            return
        run_handle = self._runs.get(run_id)
        if run_handle is None:
            return
        run_handle.cancel()

    def _cleanup_run(self, run_id: str) -> None:
        """Clean up a run after it completes.

        Removes the handle from _runs and signals completion.

        Args:
            run_id: The run ID to clean up.
        """
        run_handle = self._runs.pop(run_id, None)
        if run_handle is not None:
            run_handle.complete_event.set()
            # Clear current_run_id if it still points to this run.
            # This is a safety net — normally start() clears it, but if
            # the run died unexpectedly, current_run_id would be stale.
            session = self.get_session(run_handle.session_id)
            if session is not None and session.current_run_id == run_id:
                session.current_run_id = None

    def _count_mcp_processes(self) -> int:
        """Count active MCP processes across all per-session agents.

        Returns:
            The tracked MCP process count.
        """
        return self._mcp_process_count

    def _increment_mcp_count(self, _agent: BaseAgent[Any, Any]) -> None:
        """Increment MCP process count when a per-session agent is created.

        Args:
            _agent: The agent whose creation triggered the increment.
        """
        self._mcp_process_count += 1

    def _decrement_mcp_count(self, _agent: BaseAgent[Any, Any]) -> None:
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

        Runs every session_ttl_seconds / 2 (default: 30 minutes).
        A session is expired if last_active_at is older than session_ttl_seconds.
        """
        while True:
            try:
                await asyncio.sleep(self._session_ttl_seconds / 2)
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

        Runs indefinitely in a background task. Checks every 60 seconds
        for pending deferred calls whose timeout has elapsed and removes
        them from the session data.
        """
        while True:
            try:
                await asyncio.sleep(60)
                if self.store is None:
                    continue
                async with self._lock:
                    for session_id in list(self._sessions.keys()):
                        data = await self.store.load(session_id)
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
                            await self.store.save(updated)
                            logger.info(
                                "Removed expired deferred calls",
                                session_id=session_id,
                                count=len(expired),
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Deferred call cleanup loop failed")
