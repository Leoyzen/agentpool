"""SessionPool core orchestration layer.

Provides session lifecycle management, turn execution, event routing,
and auto-resume capabilities for agent sessions.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
import inspect
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final
import uuid

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import SessionResumeEvent
from agentpool.agents.native_agent.checkpoint import CheckpointData
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.models.pending_interaction import PendingPermission
from agentpool.orchestrator.run import RunHandle, RunStatus
from agentpool.sessions.models import PendingDeferredCall, SessionData
from agentpool_server.opencode_server.models.session_info import SessionInfo


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent
    from agentpool.delegation import AgentPool
    from agentpool.sessions.store import SessionStore


@dataclass(frozen=True)
class EventEnvelope:
    """Wrapper for events published through EventBus.

    Carries routing metadata (source_session_id) separately from the event
    payload so consumers can determine the event's origin without mutating
    the event object.

    Attribute access is transparently forwarded to the wrapped event,
    so consumers can use ``envelope.delta`` or ``envelope.event_kind``
    without unwrapping.
    """

    source_session_id: str
    """The session that produced this event."""
    event: Any
    """The original event payload (unmodified)."""

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the wrapped event."""
        return getattr(self.event, name)

    def __repr__(self) -> str:
        return (
            f"EventEnvelope(source_session_id={self.source_session_id!r}, "
            f"event={self.event!r})"
        )


logger = get_logger(__name__)

# Constants
DEFAULT_QUEUE_MAXSIZE: Final[int] = 1000
DEFAULT_MAX_AUTO_RESUME: Final[int] = 10
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
            + f". Expected tool_call_ids: {sorted(expected)}, "
            f"provided: {sorted(provided)}."
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
    _request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_owner_task: asyncio.Task[Any] | None = None
    input_provider: Any | None = None
    pending_questions: dict[str, Any] = field(default_factory=dict)
    """Pending questions stored on SessionState for per-session isolation."""

    @property
    def closing(self) -> bool:
        """Alias for is_closing."""
        return self.is_closing

    @closing.setter
    def closing(self, value: bool) -> None:
        self.is_closing = value


class EventBus:
    """PubSub event bus for cross-turn event streaming.

    Decouples event producers (agents) from consumers (protocol handlers).
    Events are broadcast to all subscribers for a given session.

    Safety features:
    - Bounded queues with dropping strategy (drop oldest)
    - Automatic cleanup of dead subscribers
    - Sentinel-based queue shutdown
    """

    def __init__(
        self,
        max_queue_size: int = DEFAULT_QUEUE_MAXSIZE,
        replay_buffer_size: int = 100,
        session_controller: SessionController | None = None,
    ) -> None:
        """Initialize the event bus.

        Args:
            max_queue_size: Maximum size for subscriber queues.
            replay_buffer_size: Maximum number of events retained per session for replay.
            session_controller: Optional session controller for hierarchy queries.
        """
        self._subscribers: dict[
            str, list[tuple[asyncio.Queue[EventEnvelope | None], str]]
        ] = {}
        self._session_tree: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size
        self._replay_buffer_size = replay_buffer_size
        self._session_controller = session_controller
        self._replay_buffers: dict[str, deque[EventEnvelope]] = {}

    async def subscribe(
        self, session_id: str, scope: str = "session"
    ) -> asyncio.Queue[EventEnvelope | None]:
        """Subscribe to events for a session.

        New subscribers receive replayed historical events from the replay
        buffer before live events. Events published during the replay phase
        are drained and re-inserted after historical events to preserve
        ordering and avoid loss.

        Args:
            session_id: The session to subscribe to.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).

                !!! warning "Deprecated: descendants scope"
                    The "descendants" scope is deprecated for protocol server use.
                    It has known issues with replay buffer data loss, O(N) recursive
                    traversal, and duplicate deliveries. Protocol servers should use
                    "session" scope with explicit child consumers via
                    `ProtocolEventConsumerMixin._on_spawn_session_start()` instead.
                    The "descendants" enum value is retained for backward compatibility.

        Returns:
            A queue to consume events from.
        """
        queue: asyncio.Queue[EventEnvelope | None] = asyncio.Queue(
            maxsize=self._max_queue_size
        )

        # 1. Register subscriber and capture replay buffer atomically
        # (inside the same lock to prevent duplicate delivery)
        async with self._lock:
            self._subscribers.setdefault(session_id, []).append((queue, scope))
            if scope == "all":
                # Global subscriptions collect from all session buffers
                historical_events: list[EventEnvelope] = []
                for buffer in self._replay_buffers.values():
                    historical_events.extend(buffer)
            else:
                buffer = self._replay_buffers.get(session_id, deque())
                historical_events = list(buffer)

        # 3. Drain any live events that arrived during replay
        # (these are already in the queue from publish())
        live_events_during_replay: list[EventEnvelope | None] = []
        while not queue.empty():
            try:
                live_events_during_replay.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        # 4. Replay historical events first (EventEnvelope is immutable, no copy needed)
        for envelope in historical_events:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                break  # Skip remaining if queue full

        # 5. Re-insert live events that arrived during replay
        for event in live_events_during_replay:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                break

        return queue

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[EventEnvelope | None],
    ) -> None:
        """Unsubscribe from events.

        Cleans up empty subscriber lists to prevent memory leaks.

        Args:
            session_id: The session to unsubscribe from.
            queue: The queue to remove.
        """
        async with self._lock:
            if session_id in self._subscribers:
                self._subscribers[session_id] = [
                    item for item in self._subscribers[session_id] if item[0] is not queue
                ]
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]

    def _get_parent(self, session_id: str) -> str | None:
        """Find the parent of a session in the session tree."""
        if self._session_controller is not None:
            parent_state = self._session_controller.get_parent(session_id)
            if parent_state is not None:
                return parent_state.session_id
        for parent_id, children in self._session_tree.items():
            if session_id in children:
                return parent_id
        return None

    def _is_descendant(self, child_id: str, parent_id: str) -> bool:
        """Check if child_id is a descendant of parent_id."""
        if self._session_controller is not None:
            children = self._session_controller.get_children(parent_id)
        else:
            children = self._session_tree.get(parent_id, [])
        return child_id in children or any(
            self._is_descendant(child_id, child) for child in children
        )

    def _are_siblings(self, sid1: str, sid2: str) -> bool:
        """Check if two sessions share the same parent."""
        parent1 = self._get_parent(sid1)
        parent2 = self._get_parent(sid2)
        return parent1 is not None and parent1 == parent2

    def _should_receive(self, published_sid: str, subscriber_sid: str, scope: str) -> bool:
        """Determine if a published event should reach a subscriber."""
        if scope == "session":
            return published_sid == subscriber_sid
        if scope == "descendants":
            return published_sid == subscriber_sid or self._is_descendant(
                published_sid, subscriber_sid
            )
        if scope == "subtree":
            return (
                published_sid == subscriber_sid
                or published_sid == self._get_parent(subscriber_sid)
                or self._are_siblings(published_sid, subscriber_sid)
            )
        if scope == "all":
            return True
        return published_sid == subscriber_sid

    async def publish(self, session_id: str, event: Any) -> None:
        """Publish an event to all subscribers for a session.

        The event is wrapped in an EventEnvelope with the source_session_id
        before storage and distribution.

        If a subscriber's queue is full, drops the oldest event.
        If put fails, removes the dead subscriber.

        Args:
            session_id: The session that produced the event.
            event: The event to broadcast.
        """
        # Wrap event in envelope with routing metadata
        envelope = EventEnvelope(source_session_id=session_id, event=event)

        async with self._lock:
            # Store in replay buffer while holding lock to prevent race
            # with subscribe() snapshotting the buffer
            if session_id not in self._replay_buffers:
                self._replay_buffers[session_id] = deque(maxlen=self._replay_buffer_size)
            self._replay_buffers[session_id].append(envelope)

            queues: list[tuple[asyncio.Queue[EventEnvelope | None], str]] = []
            for subscriber_sid, subscribers in self._subscribers.items():
                for queue, scope in subscribers:
                    if self._should_receive(session_id, subscriber_sid, scope):
                        queues.append((queue, scope))

        dead_queues: list[asyncio.Queue[EventEnvelope | None]] = []
        for queue, _scope in queues:
            try:
                queue.put_nowait(envelope)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(envelope)
                except asyncio.QueueEmpty:
                    try:
                        queue.put_nowait(envelope)
                    except asyncio.QueueFull:
                        dead_queues.append(queue)
                except asyncio.QueueFull:
                    dead_queues.append(queue)
            except (RuntimeError, ConnectionError):
                dead_queues.append(queue)

        if dead_queues:
            dead_set = set(dead_queues)
            async with self._lock:
                for subscriber_sid in list(self._subscribers):
                    self._subscribers[subscriber_sid] = [
                        item
                        for item in self._subscribers[subscriber_sid]
                        if item[0] not in dead_set
                    ]
                    if not self._subscribers[subscriber_sid]:
                        del self._subscribers[subscriber_sid]

    async def close_session(self, session_id: str) -> None:
        """Close all subscriptions for a session.

        Drains queues to make room, then sends sentinel (None) to unblock consumers.
        Clears the replay buffer for the session.

        Args:
            session_id: The session to close subscriptions for.
        """
        # Clear replay buffer
        self._replay_buffers.pop(session_id, None)

        async with self._lock:
            subscribers = self._subscribers.pop(session_id, [])
            queues = [queue for queue, _scope in subscribers]

        for queue in queues:
            while True:
                try:
                    queue.put_nowait(None)
                    break
                except asyncio.QueueFull:
                    try:
                        queue.get_nowait()
                    except asyncio.QueueEmpty:
                        pass

    async def get_subscriber_counts(self) -> dict[str, int]:
        """Get subscriber counts per session.

        Returns:
            A snapshot mapping session IDs to subscriber counts.
        """
        async with self._lock:
            return {sid: len(items) for sid, items in self._subscribers.items()}


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
        self._lock = asyncio.Lock()
        self._session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._mcp_max_processes: int = 100
        self._mcp_process_count: int = 0
        self._runs: dict[str, RunHandle] = {}
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._max_concurrent_runs: int | None = max_concurrent_runs
        self._turn_runner: TurnRunner | None = None
        self._pending_run_ids: dict[str, str] = {}

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

        state = SessionState(
            session_id=session_id,
            agent_name=agent_name or self.pool.main_agent.name or "default",
            parent_session_id=parent_session_id,
            lifecycle_policy=effective_policy,
            metadata=metadata,
        )
        self._sessions[session_id] = state
        if self.store is not None:
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

        Creates per-session agent for NativeAgentConfig only.
        Falls back to shared agent for other agent types.

        NOTE: Always acquires self._lock to prevent races with close_session().

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to use.
            input_provider: Optional input provider for the agent.

        Returns:
            The agent instance (per-session or shared).
        """
        async with self._lock:
            if session_id in self._session_agents:
                return self._session_agents[session_id]

            session, _was_created = await self._get_or_create_session_locked(session_id, agent_name)
            agent_name = agent_name or session.agent_name

            base_agent = self.pool.get_agent(agent_name)

            from agentpool.models.agents import NativeAgentConfig

            cfg = self.pool.manifest.agents.get(agent_name)

            if isinstance(cfg, NativeAgentConfig):
                if self._count_mcp_processes() >= self._mcp_max_processes:
                    logger.warning(
                        "MCP process limit reached, falling back to shared agent",
                        session_id=session_id,
                        limit=self._mcp_max_processes,
                    )
                    # Store input_provider on session, NOT on shared agent
                    if input_provider is not None:
                        session.input_provider = input_provider
                    self._session_agents[session_id] = base_agent
                    session.agent = base_agent
                    session.is_per_session_agent = False
                    return base_agent

                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": agent_name})
                from agentpool_config.context import ConfigContextManager

                with ConfigContextManager(self.pool._config_file_path):
                    agent: Agent[Any, Any] = cfg.get_agent(
                        input_provider=input_provider,
                        pool=self.pool,
                    )
                # Preserve runtime model configuration from shared agent
                base_model = getattr(base_agent, "_model", None)
                if base_model is not None:
                    agent._model = base_model
                    agent.model_settings = getattr(base_agent, "model_settings", None)
                await agent.__aenter__()
                # Add pool-level providers to per-session agent
                # (same as shared agents get in AgentPool.__aenter__)
                if self.pool is not None:
                    agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())
                    if self.pool.skills_instruction_provider:
                        agent.tools.add_provider(self.pool.skills_instruction_provider)
                    agent.tools.add_provider(self.pool.skills_tools_provider)
                # Inherit parent session's MCP providers for subagent sessions
                # (only providers with kind="mcp", not lead-agent-specific tools)
                if session.parent_session_id:
                    parent_agent = self._session_agents.get(session.parent_session_id)
                    if parent_agent is not None:
                        mcp_providers = [
                            p
                            for p in parent_agent.tools.external_providers
                            if getattr(p, "kind", None) == "mcp"
                        ]
                        for provider in mcp_providers:
                            if provider not in agent.tools.external_providers:
                                agent.tools.add_provider(provider)
                        if mcp_providers:
                            logger.info(
                                "Inherited parent session MCP providers",
                                session_id=session_id,
                                parent_session_id=session.parent_session_id,
                                num_providers=len(mcp_providers),
                            )
                self._session_agents[session_id] = agent
                session.agent = agent
                session.is_per_session_agent = True
                self._increment_mcp_count(agent)
                logger.info("Created session agent", session_id=session_id, agent_name=agent_name)
                return agent

            logger.warning(
                "Using shared agent for session - state may be shared across sessions",
                session_id=session_id,
                agent_name=agent_name,
                agent_type=type(base_agent).__name__,
            )
            # Store input_provider on session, NOT on shared agent
            if input_provider is not None:
                session.input_provider = input_provider
            self._session_agents[session_id] = base_agent
            session.agent = base_agent
            session.is_per_session_agent = False
            return base_agent

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
            await self._close_session_unlocked(child_id)
        self._session_agents.pop(session_id, None)
        self._sessions.pop(session_id, None)
        if self.store is not None:
            await self.store.delete(session_id)
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
        expired: list[PendingDeferredCall] = []
        for call in session_data.pending_deferred_calls:
            if call.timeout is not None and (now - call.created_at) > call.timeout:
                expired.append(call)
        return expired

    async def _save_close_checkpoint(
        self, session_id: str, data: SessionData
    ) -> bool:
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
            return True
        except Exception:
            logger.exception(
                "Failed to save checkpoint before close",
                session_id=session_id,
            )
            return False

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Order matters:
        1. Mark session as closing (prevents new turns from starting)
        2. Checkpoint-on-close: if pending deferred calls exist, save
           checkpointed status before releasing resources
        3. Handle child sessions based on lifecycle policy
        4. Remove from tracking dicts
        5. Acquire turn_lock to wait for active turn to complete
        6. Exit agent context if per-session
        7. Clean up session state

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

            session.is_closing = True
            session.closed_at = time.monotonic()

            # Checkpoint-before-close: if pending deferred calls exist, save
            # checkpoint state before releasing resources so the session can
            # be resumed later. If the checkpoint save fails, do NOT release
            # resources (agent stays alive).
            was_checkpointed = False
            if self.store is not None:
                data = await self.store.load(session_id)
                if self._should_checkpoint_on_close(data):
                    assert data is not None  # _should_checkpoint_on_close ensures this
                    checkpoint_ok = await self._save_close_checkpoint(session_id, data)
                    if not checkpoint_ok:
                        logger.error(
                            "Close checkpoint failed - resources NOT released",
                            session_id=session_id,
                        )
                        return  # Keep session alive
                    was_checkpointed = True

            # Handle child sessions based on lifecycle policy
            children = self._children.pop(session_id, [])
            if children:
                for child_id in children:
                    child_session = self._sessions.get(child_id)
                    if (
                        child_session is not None
                        and child_session.lifecycle_policy == "independent"
                    ):
                        continue
                    await self._close_session_unlocked(child_id)

            agent = self._session_agents.pop(session_id, None)
            self._sessions.pop(session_id, None)
            if self.store is not None and not was_checkpointed:
                await self.store.delete(session_id)
            # Remove from parent's children list
            if session.parent_session_id and session.parent_session_id in self._children:
                self._children[session.parent_session_id] = [
                    cid for cid in self._children[session.parent_session_id] if cid != session_id
                ]

        turn_completed = False
        acquired = False
        if session is not None:
            lock = session.turn_lock
            try:
                await asyncio.wait_for(lock.acquire(), timeout=30.0)
                acquired = True
                turn_completed = True
            except TimeoutError:
                logger.warning(
                    "Timeout waiting for turn to complete during close_session",
                    session_id=session_id,
                )
            finally:
                if acquired:
                    lock.release()

        if agent is not None and session is not None and turn_completed:
            if session.is_per_session_agent:
                try:
                    await agent.__aexit__(None, None, None)
                except Exception:
                    logger.exception("Failed to exit agent context", session_id=session_id)
                finally:
                    self._decrement_mcp_count(agent)
        elif agent is not None and session is not None and session.is_per_session_agent:
            logger.error(
                "Turn did not complete within timeout - agent context NOT exited",
                session_id=session_id,
            )
            self._decrement_mcp_count(agent)

        logger.info("Closed session", session_id=session_id)

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

    async def receive_request(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Receive an incoming request for a session.

        If the session is idle, creates a RunHandle and starts execution.
        If the session has an active run, delegates to inject_prompt or queue_prompt.

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

        async with session._request_lock:
            if session.closing or session.is_closing:
                return None

            if self._max_concurrent_runs is not None:
                async with self._runs_lock:
                    if len(self._runs) >= self._max_concurrent_runs:
                        return None

            # Store input_provider on session for auto-resume
            if "input_provider" in kwargs:
                session.input_provider = kwargs["input_provider"]

            if session.current_run_id is None:
                run_handle = self._create_run(session_id, content)
                self._runs[run_handle.run_id] = run_handle
                session.current_run_id = run_handle.run_id
                if self._turn_runner is not None:
                    self._pending_run_ids[session_id] = run_handle.run_id
                    task = asyncio.create_task(
                        self._turn_runner.run_loop(session_id, content, **kwargs),
                    )
                    run_handle.start(task)

                    def _cleanup_on_done(
                        _t: asyncio.Task[None], rid: str = run_handle.run_id
                    ) -> None:
                        self._cleanup_run(rid)

                    task.add_done_callback(_cleanup_on_done)
                return run_handle

        # Map user-facing priority aliases to internal values
        _priority_aliases: dict[str, str] = {
            "steer": "asap",
            "followup": "when_idle",
        }
        resolved_priority = _priority_aliases.get(priority, priority)

        # Session has an active run - delegate after releasing the request lock
        if self._turn_runner is not None:
            if resolved_priority == "asap":
                await self._turn_runner.inject_prompt(session_id, content, **kwargs)
            else:
                await self._turn_runner.queue_prompt(session_id, content, **kwargs)
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

    def _create_run(
        self,
        session_id: str,
        initial_prompt: Any,
        agent: BaseAgent[Any, Any] | None = None,
    ) -> RunHandle:
        """Create a new RunHandle for a session.

        Args:
            session_id: The session to create the run for.
            initial_prompt: The initial prompt content.
            agent: Optional agent. When provided, uses ``agent.AGENT_TYPE``
                instead of ``session.metadata["agent_type"]``.

        Returns:
            A new RunHandle.

        Raises:
            ValueError: If the session does not exist.
        """
        session = self.get_session(session_id)
        if session is None:
            raise ValueError("Session not found")
        if agent is not None:
            agent_type = agent.AGENT_TYPE
        else:
            agent_type = session.metadata.get("agent_type", "unknown")
        return RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent_type,
        )

    def _cleanup_run(self, run_id: str) -> None:
        """Clean up a run after it completes.

        Removes the handle from _runs and signals completion.

        Args:
            run_id: The run ID to clean up.
        """
        run_handle = self._runs.pop(run_id, None)
        if run_handle is not None:
            run_handle.complete_event.set()

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
        """Start background task to periodically clean up expired sessions."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        """Stop the cleanup background task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

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
        """Close all sessions that have exceeded TTL."""
        now = time.monotonic()
        expired_sessions: list[str] = []

        async with self._lock:
            for session_id, session in list(self._sessions.items()):
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
                                c for c in data.pending_deferred_calls
                                if c.tool_call_id not in {e.tool_call_id for e in expired}
                            ]
                            updated = data.model_copy(
                                update={"pending_deferred_calls": remaining}
                            )
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


class TurnRunner:
    """Manages turn lifecycle and auto-resume.

    Replaces the implicit turn loop in BaseAgent.run_stream() with an
    explicit orchestration layer.

    Safety features:
    - Per-session injection queue locks
    - Max auto-resume iterations (configurable)
    - Turn serialization via SessionState.turn_lock
    - Atomic drain operations
    """

    def __init__(
        self,
        session_controller: SessionController,
        enable_auto_resume: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
        replay_buffer_size: int = 100,
    ) -> None:
        """Initialize the turn runner.

        Args:
            session_controller: The session controller for agent lifecycle.
            enable_auto_resume: Whether to enable auto-resume loop.
            max_auto_resume: Maximum auto-resume iterations.
            replay_buffer_size: Maximum number of events retained per session for replay.
        """
        self.sessions = session_controller
        self.event_bus = EventBus(
            session_controller=session_controller,
            replay_buffer_size=replay_buffer_size,
        )
        self._post_turn_injections: dict[str, list[str]] = {}
        self._post_turn_prompts: dict[str, list[tuple[Any, ...]]] = {}
        self._injection_locks: dict[str, asyncio.Lock] = {}
        self._injection_locks_lock = asyncio.Lock()
        self._enable_auto_resume = enable_auto_resume
        self._max_auto_resume = max_auto_resume
        self._turn_timings: list[tuple[float, float]] = []
        self._max_turn_timing_history: int = 100
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._runs: dict[str, AgentRunContext] = {}

    async def _get_injection_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create per-session injection lock.

        Always acquires _injection_locks_lock to prevent concurrent creation
        of locks for the same session_id.

        Args:
            session_id: The session to get the lock for.

        Returns:
            The per-session injection lock.
        """
        async with self._injection_locks_lock:
            lock = self._injection_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._injection_locks[session_id] = lock
            return lock

    async def _publish_event(self, session_id: str, event: Any) -> None:
        """Publish event to EventBus.

        Events are wrapped in EventEnvelope by the EventBus with the
        source_session_id set to the publishing session.
        """
        await self.event_bus.publish(session_id, event)

    async def _run_turn_unlocked(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a single turn - caller MUST hold session.turn_lock.

        Internal method used by both run_turn() (single turn) and run_loop()
        (auto-resume loop) to avoid reentrancy issues with asyncio.Lock.

        Events are published to the EventBus from two sources:
        1. The main agent stream (_run_stream_once)
        2. The run_ctx event_queue (background tasks, inject_prompt, etc.)

        Args:
            session_id: The session to run the turn for.
            *prompts: Prompts to pass to the agent.
            **kwargs: Additional arguments passed to the agent.
        """
        # Extract input_provider for agent creation, pass remaining kwargs to _run_stream_once
        input_provider = kwargs.pop("input_provider", None)
        agent = await self.sessions.get_or_create_session_agent(
            session_id, input_provider=input_provider
        )
        _session = self.sessions.get_session(session_id)

        from agentpool.agents.base_agent import _current_run_ctx_var, _in_turn_context
        from agentpool.orchestrator.run import RunHandle, RunStatus

        run_id_override = self.sessions._pending_run_ids.pop(session_id, None)
        # If no pending run_id, check if session already has a current_run_id
        # (e.g., manually created RunHandle in tests)
        if run_id_override is None and _session is not None and _session.current_run_id is not None:
            run_id_override = _session.current_run_id
        run_id = run_id_override or uuid.uuid4().hex

        # Get or create RunHandle (create if called directly, not via receive_request)
        run_handle = self.sessions._runs.get(run_id)
        created_run_handle = False
        if run_handle is None:
            agent_type = agent.AGENT_TYPE
            run_handle = RunHandle(
                run_id=run_id,
                session_id=session_id,
                agent_type=agent_type,
            )
            self.sessions._runs[run_id] = run_handle
            created_run_handle = True
        run_handle.start(asyncio.current_task())

        # Use RunHandle's run_ctx as the authoritative context
        run_ctx = run_handle.run_ctx
        # Wire run_handle for RunExecutor lifecycle management.
        # RunExecutor.execute() uses run_handle to set/clear active_agent_run.
        run_ctx._run_handle = run_handle  # type: ignore[attr-defined]
        run_ctx.deps = kwargs.get("deps")
        run_ctx.run_id = run_id
        run_ctx.cancelled = False
        run_ctx.current_task = asyncio.current_task()
        run_ctx.event_bus = self.event_bus
        run_ctx.session_id = session_id
        _current_run_ctx_var.set(run_ctx)

        if _session is not None and _session.current_run_id is None:
            _session.current_run_id = run_id
        self._runs[run_ctx.run_id] = run_ctx

        # Consume events from run_ctx.event_queue and publish to EventBus.
        # This is needed because StreamEventEmitter no longer has a global
        # EventBus set, so tool events go into run_ctx.event_queue.
        async def _consume_event_queue() -> None:
            """Consume events from run_ctx.event_queue and publish to EventBus."""
            try:
                while True:
                    event = await run_ctx.event_queue.get()
                    if event is None:
                        break
                    await self._publish_event(session_id, event)
            except asyncio.CancelledError:
                pass

        event_consumer: asyncio.Task[None] | None = None
        if agent_type != "native":
            event_consumer = asyncio.create_task(
                _consume_event_queue(),
                name=f"event_consumer_{session_id}",
            )

        turn_start = time.monotonic()
        # Filter kwargs to only include parameters _run_stream_once accepts.
        # If the function has **kwargs, allow all kwargs through.
        sig = inspect.signature(agent._run_stream_once)
        stream_params = set(sig.parameters)
        has_var_keyword = any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        # input_provider was popped for get_or_create_session_agent;
        # include it back if _run_stream_once also accepts it.
        stream_kwargs = dict(kwargs)
        if input_provider is not None and (has_var_keyword or "input_provider" in stream_params):
            stream_kwargs["input_provider"] = input_provider
        if _session is not None:
            _session._turn_owner_task = asyncio.current_task()
        _in_turn_context.set(True)
        try:
            try:
                # Process prompts and handle injections/queued prompts
                # like BaseAgent.run_stream() does.
                async for event in agent._run_stream_once(
                    run_ctx, *prompts, session_id=session_id, **stream_kwargs
                ):
                    await self._publish_event(session_id, event)

                # After _run_stream_once completes, handle unconsumed injections.
                # Native agents use PydanticAI's PendingMessageDrainCapability
                # instead of the manual flush/queue loop.
                if agent.AGENT_TYPE != "native":
                    run_ctx.injection_manager.flush_pending_to_queue()
                    while run_ctx.injection_manager.has_queued() and not run_ctx.cancelled:
                        current_prompts = run_ctx.injection_manager.pop_queued()
                        if current_prompts is None:
                            break
                        async for event in agent._run_stream_once(
                            run_ctx, *current_prompts, session_id=session_id, **stream_kwargs
                        ):
                            await self._publish_event(session_id, event)
                        run_ctx.injection_manager.flush_pending_to_queue()
                elif run_ctx.injection_manager.has_pending():
                    logger.warning(
                        "Native agent has unconsumed injections — these will not be "
                        "flushed to the manual queue. PendingMessageDrainCapability "
                        "should handle them.",
                        pending_count=len(run_ctx.injection_manager._pending_injections),
                    )
            except (Exception, asyncio.CancelledError) as exc:
                if run_handle is not None and run_handle.status not in (
                    RunStatus.completed,
                    RunStatus.failed,
                    RunStatus.checkpointed,
                ):
                    run_handle.fail(exception=exc, event_bus=self.event_bus)
                raise
        finally:
            # CRITICAL: Mark run as completed BEFORE any await so that
            # inject_prompt() sees completed=True and falls back to
            # post-turn queuing instead of returning True (active turn)
            # and dropping the message in a dead pending queue.
            run_ctx.completed = True

            # CRITICAL: Clear session.current_run_id BEFORE any await to prevent
            # race condition where inject_prompt returns True but message
            # gets stuck in pending (flush_pending_to_queue() already passed).
            if _session is not None:
                _session.current_run_id = None

            self._runs.pop(run_ctx.run_id, None)
            _current_run_ctx_var.set(None)
            if _session is not None:
                _session._turn_owner_task = None
            _in_turn_context.set(False)

            # Cancel the event consumer task
            if event_consumer is not None:
                event_consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await event_consumer

            turn_end = time.monotonic()
            self._turn_timings.append((turn_start, turn_end))
            if len(self._turn_timings) > self._max_turn_timing_history:
                self._turn_timings.pop(0)

            # Clean up RunHandle if we created it
            if created_run_handle and run_handle is not None:
                if run_handle.status not in (
                    RunStatus.completed,
                    RunStatus.failed,
                    RunStatus.checkpointed,
                ):
                    if run_ctx.checkpointed:
                        run_handle.checkpoint()
                    else:
                        run_handle.complete()
                # Note: complete_event is NOT set here — it is deferred to
                # run_loop() so that it covers the full run loop including
                # auto-resume turns.  Per-request waiters (e.g. sync HTTP
                # endpoint) should wait for the entire session run cycle.
                self.sessions._runs.pop(run_id, None)

    async def run_turn(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a single turn for a session.

        Acquires session.turn_lock to enforce "1 turn per session".
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to run the turn for.
            *prompts: Prompts to pass to the agent.
            **kwargs: Additional arguments passed to the agent.
        """
        session, _was_created = await self.sessions.get_or_create_session(session_id)

        async with session.turn_lock:
            if session.is_closing:
                logger.debug("Session is closing, skipping turn", session_id=session_id)
                return
            await self._run_turn_unlocked(session_id, *prompts, **kwargs)

    async def run_loop(
        self,
        session_id: str,
        *initial_prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a turn loop until no more post-turn work.

        Only one run_loop per session at a time (enforced by SessionState.turn_lock).
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to run the loop for.
            *initial_prompts: Initial prompts to start the loop.
            **kwargs: Additional arguments passed to the agent.
        """
        session, _was_created = await self.sessions.get_or_create_session(session_id)

        async with session.turn_lock:
            if session.is_closing:
                logger.debug("Session is closing, skipping turn", session_id=session_id)
                return

            try:
                await self._run_turn_unlocked(session_id, *initial_prompts, **kwargs)
                await self._process_queued_work(session_id, session, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.exception("Turn loop failed", session_id=session_id)
                # Publish RunFailedEvent so protocol handlers can notify clients
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = self.sessions._runs.get(run_id)
                    if run_handle is not None:
                        run_handle.fail(exception=exc, event_bus=self.event_bus)
                await self._drain_post_turn_injections(session_id)
                await self._drain_post_turn_prompts(session_id)
            finally:
                # Signal completion after the full run loop (including auto-resume)
                # so that per-request waiters observe the full session run cycle.
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = self.sessions._runs.get(run_id)
                    if run_handle is not None:
                        run_handle.complete_event.set()

    async def inject_prompt(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a message into a session.

        If the session has an active turn, injects immediately.
        Otherwise, queues for the next turn and triggers auto-resume.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if injected into active turn, False if queued.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            logger.debug(
                "Cannot inject: session=%s agent=%s is_closing=%s",
                session is not None,
                session.agent is not None if session else False,
                session.is_closing if session else False,
            )
            return False

        agent = session.agent
        run_ctx = agent.get_active_run_context()
        if run_ctx is not None and not run_ctx.completed:
            run_ctx.injection_manager.inject(message)
            return True

        lock = await self._get_injection_lock(session_id)
        async with lock:
            run_ctx = agent.get_active_run_context()
            if run_ctx is not None and not run_ctx.completed:
                run_ctx.injection_manager.inject(message)
                return True
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                logger.debug("Session closed while waiting for lock")
                return False
            self._post_turn_injections.setdefault(session_id, []).append(message)

        logger.debug("Queued injection for next turn, triggering auto-resume")
        task = asyncio.create_task(self._trigger_auto_resume(session_id, **kwargs))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def queue_prompt(self, session_id: str, *prompts: Any, **kwargs: Any) -> bool:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            return False

        agent = session.agent
        run_ctx = agent.get_active_run_context()
        if run_ctx is not None:
            run_ctx.injection_manager.queue(*prompts)
            return True

        lock = await self._get_injection_lock(session_id)
        async with lock:
            run_ctx = agent.get_active_run_context()
            if run_ctx is not None:
                run_ctx.injection_manager.queue(*prompts)
                return True
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                return False
            self._post_turn_prompts.setdefault(session_id, []).append(prompts)

        logger.debug("Queued prompt for next turn, triggering auto-resume")
        task = asyncio.create_task(self._trigger_auto_resume(session_id, **kwargs))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def steer(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a steer message with agent-type-aware routing.

        Routes based on agent type (native vs non-native) and session state
        (active run vs idle):

        - Native + active: enqueues via ``agent_run.enqueue(priority='asap')``.
        - Native + idle: delegates to
          :meth:`SessionController.receive_request` with ``priority='steer'``.
        - Non-native + active: injects via
          ``run_handle.run_ctx.injection_manager.inject()``.
        - Non-native + idle: stores in ``_post_turn_injections`` and triggers
          auto-resume.

        Uses TOCTOU-safe pattern: reads ``active_agent_run`` into a local
        variable to prevent double-read races.

        Args:
            session_id: Target session.
            message: The steer message to deliver.
            **kwargs: Additional arguments passed to
                :meth:`SessionController.receive_request` or
                :meth:`_trigger_auto_resume`.

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            return False

        agent = session.agent
        agent_type: str = agent.AGENT_TYPE

        if agent_type == "native":
            run_id = session.current_run_id
            if run_id is not None:
                run_handle = self.sessions._runs.get(run_id)
                if run_handle is not None:
                    agent_run = run_handle.active_agent_run  # TOCTOU: read once
                    if agent_run is not None:
                        agent_run.enqueue(message, priority="asap")
                        return True
            # Native idle: delegate to receive_request
            await self.sessions.receive_request(session_id, message, priority="steer", **kwargs)
            return False

        # Non-native routing
        run_id = session.current_run_id
        if run_id is not None:
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None and run_handle.status == RunStatus.running:
                run_ctx = run_handle.run_ctx
                run_ctx.injection_manager.inject(message)
                return True

        # Non-native idle: store for next turn
        self._post_turn_injections.setdefault(session_id, []).append(message)
        task = asyncio.create_task(self._trigger_auto_resume(session_id, **kwargs))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def followup(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Queue a follow-up message with agent-type-aware routing.

        Routes based on agent type (native vs non-native) and session state
        (active run vs idle):

        - Native + active: enqueues via ``agent_run.enqueue(priority='when_idle')``.
        - Native + idle: delegates to
          :meth:`SessionController.receive_request` with ``priority='followup'``.
        - Non-native + active: queues via
          ``run_handle.run_ctx.injection_manager.queue()``.
        - Non-native + idle: stores in ``_post_turn_prompts`` and triggers
          auto-resume.

        Uses TOCTOU-safe pattern: reads ``active_agent_run`` into a local
        variable to prevent double-read races.

        Args:
            session_id: Target session.
            message: The follow-up message to deliver.
            **kwargs: Additional arguments passed to
                :meth:`SessionController.receive_request` or
                :meth:`_trigger_auto_resume`.

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            return False

        agent = session.agent
        agent_type: str = agent.AGENT_TYPE

        if agent_type == "native":
            run_id = session.current_run_id
            if run_id is not None:
                run_handle = self.sessions._runs.get(run_id)
                if run_handle is not None:
                    agent_run = run_handle.active_agent_run  # TOCTOU: read once
                    if agent_run is not None:
                        agent_run.enqueue(message, priority="when_idle")
                        return True
            # Native idle: delegate to receive_request
            await self.sessions.receive_request(session_id, message, priority="followup", **kwargs)
            return False

        # Non-native routing
        run_id = session.current_run_id
        if run_id is not None:
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None and run_handle.status == RunStatus.running:
                run_ctx = run_handle.run_ctx
                run_ctx.injection_manager.queue(message)
                return True

        # Non-native idle: store for next turn
        self._post_turn_prompts.setdefault(session_id, []).append((message,))
        task = asyncio.create_task(self._trigger_auto_resume(session_id, **kwargs))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def _drain_post_turn_injections(self, session_id: str) -> list[str]:
        """Drain and return post-turn injections for a session.

        Args:
            session_id: Session to drain.

        Returns:
            List of injection messages.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            injections = self._post_turn_injections.pop(session_id, [])
            return injections

    async def _drain_post_turn_prompts(self, session_id: str) -> list[tuple[Any, ...]]:
        """Drain and return post-turn prompts for a session.

        Args:
            session_id: Session to drain.

        Returns:
            List of prompt tuples.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            prompts = self._post_turn_prompts.pop(session_id, [])
            return prompts

    async def _process_queued_work(
        self,
        session_id: str,
        session: SessionState,
        **kwargs: Any,
    ) -> None:
        """Process queued post-turn work under turn_lock.

        Shared logic used by both run_loop() and _trigger_auto_resume().
        Caller MUST hold session.turn_lock.

        Args:
            session_id: The session to process queued work for.
            session: The session state.
            **kwargs: Additional arguments passed to the agent run.
        """
        if session.is_closing:
            logger.debug("Session is closing, skipping queued work")
            return

        # Use session-stored input_provider if not provided in kwargs
        if "input_provider" not in kwargs and session.input_provider is not None:
            kwargs["input_provider"] = session.input_provider

        injections = await self._drain_post_turn_injections(session_id)
        prompts = await self._drain_post_turn_prompts(session_id)

        logger.debug(
            "Drained injections=%s prompts=%s",
            len(injections),
            len(prompts),
        )

        if injections:
            logger.debug("Running turn with injections")
            await self._run_turn_unlocked(session_id, *injections, **kwargs)
            logger.debug("Turn with injections completed")

        for prompt_group in prompts:
            await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)

        for iteration in range(self._max_auto_resume):
            if session.is_closing:
                logger.debug("Session closing during auto-resume")
                break

            injections = await self._drain_post_turn_injections(session_id)
            prompts = await self._drain_post_turn_prompts(session_id)

            if not injections and not prompts:
                logger.debug("No more queued work, stopping auto-resume")
                break

            logger.info(
                "Auto-resuming turn",
                session_id=session_id,
                iteration=iteration + 1,
                injections=len(injections),
                prompts=len(prompts),
            )

            if injections:
                await self._run_turn_unlocked(session_id, *injections, **kwargs)
            for prompt_group in prompts:
                await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)

        logger.info(
            "Auto-resume complete",
            session_id=session_id,
            max_iterations=self._max_auto_resume,
        )

    async def _trigger_auto_resume(self, session_id: str, **kwargs: Any) -> None:
        """Trigger auto-resume for a session if no turn is active.

        Fire-and-forget task that ensures post-turn work queued after
        run_loop() exits gets processed promptly.

        Args:
            session_id: The session to trigger auto-resume for.
            **kwargs: Additional arguments passed to the agent run.
        """
        logger.debug("_trigger_auto_resume called for %s", session_id)
        try:
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                logger.debug("Session not found or closing")
                return

            async with session.turn_lock:
                if session.is_closing:
                    logger.debug("Session closing after acquiring lock")
                    return

                current_session = self.sessions.get_session(session_id)
                if current_session is not session:
                    logger.debug("Session changed")
                    return

                # Use session-stored input_provider if not provided in kwargs
                if "input_provider" not in kwargs and session.input_provider is not None:
                    kwargs["input_provider"] = session.input_provider

                if self._enable_auto_resume:
                    logger.debug("Processing queued work")
                    await self._process_queued_work(session_id, session, **kwargs)
                    logger.debug("Finished processing queued work")
                else:
                    injections = await self._drain_post_turn_injections(session_id)
                    prompts = await self._drain_post_turn_prompts(session_id)

                    if injections:
                        await self._run_turn_unlocked(session_id, *injections, **kwargs)
                    for prompt_group in prompts:
                        await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)
        except asyncio.CancelledError:
            return


class SessionPool:
    """High-level session pool combining session and turn management.

    This is the main interface used by protocol handlers.

    Feature flags:
    - enable_auto_resume: Enable auto-resume loop
    - enable_event_bus: Enable cross-turn event routing
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        store: SessionStore | None = None,
        enable_auto_resume: bool = True,
        enable_event_bus: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
        max_concurrent_runs: int | None = None,
        replay_buffer_size: int = 100,
    ) -> None:
        """Initialize the session pool.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            enable_auto_resume: Whether to enable auto-resume loop.
            enable_event_bus: Whether to enable cross-turn event routing.
            max_auto_resume: Maximum auto-resume iterations.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
            replay_buffer_size: Maximum number of events retained per session for replay.
        """
        self.pool = pool
        self.sessions = SessionController(
            pool,
            store=store,
            cleanup_callback=self.close_session,
            max_concurrent_runs=max_concurrent_runs,
        )
        self.turns = TurnRunner(
            self.sessions,
            enable_auto_resume=enable_auto_resume,
            max_auto_resume=max_auto_resume,
            replay_buffer_size=replay_buffer_size,
        )
        self.sessions._turn_runner = self.turns
        self._enable_auto_resume = enable_auto_resume
        self._enable_event_bus = enable_event_bus
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._resume_locks: dict[str, asyncio.Lock] = {}
        self._resume_locks_lock = asyncio.Lock()
        self._message_cache: dict[str, list[ChatMessage[Any]]] = {}

    async def start(self) -> None:
        """Start the session pool and background tasks."""
        await self.sessions.start_cleanup_task()
        logger.info("SessionPool started")

    async def shutdown(self) -> None:
        """Shutdown the session pool and cancel background tasks."""
        await self.sessions.stop_cleanup_task()
        active_sessions = list(self.sessions._sessions.keys())
        for session_id in active_sessions:
            try:
                await self.close_session(session_id)
            except Exception:
                logger.exception(
                    "Failed to close session during shutdown",
                    session_id=session_id,
                )
        logger.info("SessionPool shut down")

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus for cross-turn event routing."""
        return self.turns.event_bus

    async def create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> SessionState:
        """Create or get a session.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state.
        """
        if parent_session_id is not None and self.sessions.store is not None:
            parent_data = await self.sessions.store.load(parent_session_id)
            if parent_data is not None:
                metadata.setdefault("project_id", parent_data.project_id)
                metadata.setdefault("cwd", parent_data.cwd)
        state, _was_created = await self.sessions.get_or_create_session(
            session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
        )
        return state

    async def _get_resume_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create per-session lock for resume serialization.

        Args:
            session_id: Session identifier.

        Returns:
            The per-session resume lock.
        """
        async with self._resume_locks_lock:
            lock = self._resume_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._resume_locks[session_id] = lock
            return lock

    @contextlib.asynccontextmanager
    async def _with_resume_lock(
        self, session_id: str
    ) -> AsyncIterator[SessionState | None]:
        """Acquire per-session resume lock with state validation.

        Ensures only one resume runs per session at a time and that
        the session is in a resumable state (no active run, persisted
        status is ``"checkpointed"``).

        Args:
            session_id: Session to lock.

        Yields:
            The live ``SessionState``, or ``None`` if no live session exists.

        Raises:
            SessionBusyError: If the session has an active run or its
                persisted status is not ``"checkpointed"``.
        """
        resume_lock = await self._get_resume_lock(session_id)
        async with resume_lock:
            session = self.sessions.get_session(session_id)
            if session is not None and session.current_run_id is not None:
                raise SessionBusyError(session_id, session.current_run_id)

            if self.sessions.store is not None:
                current_data = await self.sessions.store.load(session_id)
                if current_data is not None and current_data.status != "checkpointed":
                    raise SessionBusyError(session_id, current_data.status)

            yield session

    async def _load_checkpoint_data(
        self, session_id: str
    ) -> CheckpointData:
        """Load checkpoint data for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Checkpoint data.

        Raises:
            SessionNotFoundError: If no checkpoint exists for the session.
        """
        from agentpool.agents.native_agent.checkpoint import CheckpointManager

        storage = self.pool.storage
        if storage is None:
            raise SessionNotFoundError(session_id)

        checkpoint_mgr = CheckpointManager(storage)
        data = await checkpoint_mgr.load_checkpoint(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)
        return data

    async def _reconstruct_native_agent(
        self,
        session_id: str,
        agent_name: str,
    ) -> Agent[Any, Any]:
        """Reconstruct a native agent from config for session resume.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed native agent instance.

        Raises:
            SessionNotFoundError: If the agent config is not found.
        """
        from agentpool.models.agents import NativeAgentConfig
        from agentpool_config.context import ConfigContextManager

        cfg = self.pool.manifest.agents.get(agent_name)
        if cfg is None:
            raise SessionNotFoundError(session_id)

        if not isinstance(cfg, NativeAgentConfig):
            raise SessionNotFoundError(session_id)

        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        session = self.sessions.get_session(session_id)
        input_provider = session.input_provider if session else None

        with ConfigContextManager(self.pool._config_file_path):
            agent: Agent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=self.pool,
            )

        # Add pool-level providers
        if self.pool is not None:
            agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())
            if self.pool.skills_instruction_provider:
                agent.tools.add_provider(self.pool.skills_instruction_provider)
            agent.tools.add_provider(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _reconstruct_acp_agent(
        self,
        _session_id: str,
        agent_name: str,
    ) -> BaseAgent[Any, Any]:
        """Reconstruct an ACP agent by reopening the subprocess.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed ACP agent with reopened subprocess.
        """
        agent = self.pool.get_agent(agent_name)

        # For ACP agents, reopen the subprocess via __aenter__
        if hasattr(agent, "__aenter__"):
            await agent.__aenter__()
        return agent

    async def _resume_native_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume a native agent from checkpoint with deferred results.

        Loads message_history from checkpoint, reconstructs the agent from its
        original config, and calls agent.run() with the restored history and
        deferred results.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data with message_history and pending_calls.
            results: DeferredToolResults for resolving pending deferred calls.

        Raises:
            SessionNotFoundError: If agent config is not found.
            RuntimeError: If agent.run() fails (pending_calls remain uncleared).
        """
        agent = await self._reconstruct_native_agent(
            session_data.session_id, session_data.agent_name
        )

        # Detect agent config drift between checkpoint and resume.
        # The hash check is advisory: if we can't compute the current hash
        # (e.g. agent has no tools attribute, or tools is a mock in tests),
        # we skip the comparison and proceed with resume.
        if session_data.agent_config_hash:
            try:
                from agentpool.agents.native_agent.checkpoint import (
                    compute_agent_config_hash,
                )

                agent_tools = await agent.tools.get_tools()  # type: ignore[union-attr]
                current_hash = compute_agent_config_hash(agent_tools)
                if current_hash != session_data.agent_config_hash:
                    logger.warning(
                        "Agent config hash mismatch — tools may have changed since checkpoint",
                        session_id=session_data.session_id,
                        stored_hash=session_data.agent_config_hash,
                        current_hash=current_hash,
                    )
            except Exception:
                logger.debug(
                    "Could not compute agent config hash for drift check",
                    session_id=session_data.session_id,
                    exc_info=True,
                )

        try:
            message_history: list[Any] = list(checkpoint.message_history)
            # deferred_tool_results is forwarded to pydantic-ai Agent.run()
            # which accepts it natively; cast to Any since BaseAgent.run()
            # doesn't declare this kwarg in its signature.
            run_fn: Any = agent.run
            await run_fn(
                message_history=message_history,
                deferred_tool_results=results,
            )
        finally:
            await agent.__aexit__(None, None, None)

    async def _resume_acp_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume an ACP agent by reopening the subprocess and sending session/resume.

        Reopens the ACP subprocess and calls agent.run() to restart the
        session with restored state.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data (used for metadata only; ACP agents
                manage their own message history).
            results: DeferredToolResults for resolving pending deferred calls.
        """
        agent = await self._reconstruct_acp_agent(
            session_data.session_id, session_data.agent_name
        )
        try:
            # ACP agents receive the resumed session context through run()
            run_fn: Any = agent.run
            await run_fn(
                message_history=list(checkpoint.message_history),
                deferred_tool_results=results,
            )
        finally:
            if hasattr(agent, "__aexit__"):
                await agent.__aexit__(None, None, None)  # type: ignore[union-attr]

    async def resume_session(
        self,
        session_id: str,
        deferred_tool_results: Any,
        *,
        source: str = "resume_prompt",
    ) -> None:
        """Resume a paused session with resolved deferred tool results.

        Loads the persisted SessionData, validates that deferred_tool_results
        cover all pending_deferred_calls (raising CheckpointMismatchError if not),
        and resumes execution via the appropriate path:
        - Native agent: load checkpoint → reconstruct agent from config →
          agent.run(message_history=restored, deferred_tool_results=results)
        - ACP agent: load session data → reopen subprocess →
          agent.run(message_history=restored, deferred_tool_results=results)

        Per-session resume_lock ensures only one resume at a time.
        Emits SessionResumeEvent on success.

        Args:
            session_id: Session to resume.
            deferred_tool_results: Results for pending deferred tool calls
                (DeferredToolResults-compatible object with .calls dict).
            source: Identifier for the entity triggering the resume.

        Raises:
            SessionNotFoundError: If the session does not exist in storage.
            SessionBusyError: If the session has an active run.
            CheckpointMismatchError: If results don't cover all pending calls.
        """
        store = self.sessions.store
        if store is None:
            raise SessionNotFoundError(session_id)

        # Load persisted session data
        data = await store.load(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)

        # Fast-path: check for active run in live sessions (before lock).
        # The authoritative check is inside _with_resume_lock, but this
        # early check avoids unnecessary store operations for busy sessions.
        session = self.sessions.get_session(session_id)
        if session is not None and session.current_run_id is not None:
            raise SessionBusyError(session_id, session.current_run_id)

        # Validate deferred_tool_results cover all pending_deferred_calls
        pending_call_ids: set[str] = {
            call.tool_call_id for call in data.pending_deferred_calls
        }
        provided_call_ids: set[str] = set(
            getattr(deferred_tool_results, "calls", {}).keys()
        )

        missing = pending_call_ids - provided_call_ids
        extra = provided_call_ids - pending_call_ids
        if missing or extra:
            raise CheckpointMismatchError(
                session_id=session_id,
                expected=pending_call_ids,
                provided=provided_call_ids,
                missing=missing,
                extra=extra,
            )

        # Determine agent type
        agent_type = data.metadata.get("agent_type", "native")

        # Per-session resume lock with state validation (Decision 8, Task 19)
        async with self._with_resume_lock(session_id) as session:
            try:
                # Load checkpoint data
                checkpoint = await self._load_checkpoint_data(session_id)

                # Mark session as resuming
                data = data.model_copy(update={"status": "resuming"})
                await store.save(data)

                # Route to appropriate resume path
                if agent_type == "acp":
                    await self._resume_acp_agent(data, checkpoint, deferred_tool_results)
                else:
                    await self._resume_native_agent(data, checkpoint, deferred_tool_results)

                # Clear pending_deferred_calls ONLY after agent.run() succeeds (Decision 8)
                data = data.model_copy(
                    update={
                        "status": "active",
                        "pending_deferred_calls": [],
                    }
                )
                data.touch()
                await store.save(data)

                # Update live session if one exists
                if session is not None:
                    session.last_active_at = time.monotonic()

                # Emit SessionResumeEvent
                await self.event_bus.publish(
                    session_id,
                    SessionResumeEvent(
                        session_id=session_id,
                        resolved_call_count=len(pending_call_ids),
                        source=source,
                    ),
                )

                logger.info(
                    "Session resumed successfully",
                    session_id=session_id,
                    agent_type=agent_type,
                    resolved_calls=len(pending_call_ids),
                )

            except Exception:
                # On failure, keep status as checkpointed and do NOT clear pending calls
                data = data.model_copy(update={"status": "checkpointed"})
                data.touch()
                await store.save(data)
                raise

    async def close_session(self, session_id: str) -> None:
        """Close a session.

        Waits for any active run to complete before proceeding.
        Order: wait for run, session cleanup, event bus, then turn state.

        Args:
            session_id: The session to close.
        """
        session = self.sessions.get_session(session_id)
        run_handle: RunHandle | None = None
        if session is not None:
            async with session._request_lock:
                session.closing = True
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = self.sessions._runs.get(run_id)

            if run_handle is not None:
                try:
                    await asyncio.wait_for(run_handle.complete_event.wait(), timeout=30.0)
                except TimeoutError:
                    self.cancel_run(run_handle.run_id)
                    await asyncio.sleep(0.1)

        await self.sessions.close_session(session_id)
        await self.event_bus.close_session(session_id)
        has_turn_state = (
            session_id in self.turns._post_turn_injections
            or session_id in self.turns._post_turn_prompts
            or session_id in self.turns._injection_locks
        )
        if has_turn_state:
            lock = await self.turns._get_injection_lock(session_id)
            async with lock:
                self.turns._post_turn_injections.pop(session_id, None)
                self.turns._post_turn_prompts.pop(session_id, None)
                self.turns._injection_locks.pop(session_id, None)

        self._message_cache.pop(session_id, None)

    async def _await_inflight_checkpoints(self) -> None:
        """Wait for any in-flight checkpoint operations to complete.

        During normal operation, checkpoint-on-close happens synchronously
        inside :meth:`close_session`, so there are no in-flight operations
        to await. This method is a future-proof hook for graceful teardown:
        if the checkpoint mechanism ever becomes asynchronous (e.g.,
        background flush), this method ensures the shutdown waits for
        completion.

        Called from :meth:`AgentPool.__aexit__` during pool shutdown.
        """
        # Currently no-op: all checkpoint operations complete synchronously
        # within SessionController.close_session() under its lock.
        logger.debug("No in-flight checkpoint operations to await")

    async def process_prompt(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Process a prompt through the turn loop.

        Main entry point for protocol handlers.
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            **kwargs: Additional arguments passed to the agent.
        """
        # Keep blocking behavior for backward compatibility during migration.
        # Protocol handlers that need fire-and-forget should use receive_request().
        if self._enable_auto_resume:
            await self.turns.run_loop(session_id, *prompts, **kwargs)
        else:
            await self.turns.run_turn(session_id, *prompts, **kwargs)

    async def receive_request(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Route an incoming request for a session (fire-and-forget).

        Creates a background task that processes the prompt through
        the turn runner. Protocol handlers should subscribe to the
        EventBus *before* calling this method so no events are dropped.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: "when_idle" to queue, "asap" to inject into active turn.
            **kwargs: Additional arguments passed to the turn runner.

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        return await self.sessions.receive_request(session_id, content, priority=priority, **kwargs)

    @property
    def active_runs(self) -> list[RunHandle]:
        """Get all currently active (running) RunHandles."""
        return [rh for rh in self.sessions._runs.values() if rh.status == RunStatus.running]

    def get_run(self, run_id: str) -> RunHandle | None:
        """Get a RunHandle by ID.

        Args:
            run_id: The run ID to look up.

        Returns:
            The RunHandle, or None if not found.
        """
        return self.sessions._runs.get(run_id)

    def cancel_run(self, run_id: str) -> None:
        """Cancel a run by ID.

        Args:
            run_id: The run ID to cancel.

        Raises:
            ValueError: If no active run with the given ID exists.
        """
        run_handle = self.sessions._runs.get(run_id)
        if run_handle is None:
            raise ValueError("No active run found with ID: " + run_id)
        run_handle.cancel()

    async def run_stream(
        self,
        session_id: str,
        *prompts: str,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Process prompts and yield events from the EventBus.

        Convenience method for tests and standalone clients that want
        an async iterator over session events.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).
            **kwargs: Additional arguments passed to the turn runner
                (e.g. ``input_provider``).

        Yields:
            Events published to the EventBus for this session.
        """
        queue = await self.event_bus.subscribe(session_id, scope=scope)
        process_task = asyncio.create_task(self.process_prompt(session_id, *prompts, **kwargs))
        get_task: asyncio.Task[Any] | None = None
        try:
            while not process_task.done():
                if get_task is None:
                    get_task = asyncio.create_task(queue.get())
                done, _pending = await asyncio.wait(
                    {process_task, get_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if get_task in done:
                    event = get_task.result()
                    get_task = None
                    if event is not None:
                        yield event.event
            if get_task is not None and not get_task.done():
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task
                get_task = None
            while not queue.empty():
                event = queue.get_nowait()
                if event is not None:
                    yield event.event
            if (exc := process_task.exception()) is not None:
                raise exc
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task
            if not process_task.done():
                process_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await process_task
            await self.event_bus.unsubscribe(session_id, queue)

    async def inject_prompt(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a message into a session.

        If the session has an active turn, injects immediately.
        Otherwise, queues for the next turn and triggers auto-resume.

        For native agents, delegates to :meth:`steer` for agent-type-aware
        routing. For non-native agents, falls through to
        :meth:`TurnRunner.inject_prompt` for backward compatibility.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if injected into active turn, False if queued.
        """
        session = self.sessions.get_session(session_id)
        if session is not None and session.agent is not None:
            agent_type: str = session.agent.AGENT_TYPE
            if agent_type == "native":
                return await self.turns.steer(session_id, message, **kwargs)
        return await self.turns.inject_prompt(session_id, message, **kwargs)

    async def queue_prompt(self, session_id: str, *prompts: Any, **kwargs: Any) -> bool:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        For native agents, delegates to :meth:`followup` for agent-type-aware
        routing. For non-native agents, falls through to
        :meth:`TurnRunner.queue_prompt` for backward compatibility.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        session = self.sessions.get_session(session_id)
        if session is not None and session.agent is not None:
            agent_type: str = session.agent.AGENT_TYPE
            if agent_type == "native":
                # followup accepts a single message; use first prompt if multiple
                message = prompts[0] if prompts else ""
                return await self.turns.followup(session_id, str(message), **kwargs)
        return await self.turns.queue_prompt(session_id, *prompts, **kwargs)

    async def steer(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a steer message with agent-type-aware routing.

        Delegates to :meth:`TurnRunner.steer` which routes based on agent
        type (native vs non-native) and session state (active run vs idle).

        This is the preferred method for delivering urgent messages into
        native agent sessions. For backward compatibility, :meth:`inject_prompt`
        also delegates here for native agents.

        Args:
            session_id: Target session.
            message: The steer message to deliver.
            **kwargs: Additional arguments forwarded to :meth:`TurnRunner.steer`.

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        return await self.turns.steer(session_id, message, **kwargs)

    async def followup(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Queue a follow-up message with agent-type-aware routing.

        Delegates to :meth:`TurnRunner.followup` which routes based on agent
        type (native vs non-native) and session state (active run vs idle).

        This is the preferred method for queuing follow-up messages into
        native agent sessions. For backward compatibility, :meth:`queue_prompt`
        also delegates here for native agents.

        Args:
            session_id: Target session.
            message: The follow-up message to deliver.
            **kwargs: Additional arguments forwarded to :meth:`TurnRunner.followup`.

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        return await self.turns.followup(session_id, message, **kwargs)

    async def get_messages(
        self,
        session_id: str,
    ) -> list[ChatMessage[Any]]:
        """Get message history for a session.

        Results are cached per session_id (full message list) to avoid
        repeated storage queries. Cache is invalidated by append_message,
        truncate_messages, and copy_messages.

        Args:
            session_id: The session to retrieve messages for.

        Returns:
            List of messages ordered by timestamp (oldest first).

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        if session_id in self._message_cache:
            return list(self._message_cache[session_id])

        storage = self.pool.storage
        if storage is not None:
            messages = await storage.get_session_messages(session_id)
            self._message_cache[session_id] = list(messages)
            return messages

        return []

    async def append_message(
        self,
        session_id: str,
        message: ChatMessage[Any],
    ) -> str:
        """Append a message to a session's history.

        Args:
            session_id: The session to append to.
            message: The message to append.

        Returns:
            The ID of the appended message.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            await storage.log_message(message=message)

        self._message_cache.pop(session_id, None)
        return message.message_id

    async def copy_messages(
        self,
        source_session_id: str,
        target_session_id: str,
        *,
        up_to_message_id: str | None = None,
    ) -> str | None:
        """Copy messages from one session to another.

        Used by share_session (copy all) and revert_session (copy up to
        a specific message).

        Args:
            source_session_id: Session to copy from.
            target_session_id: Session to copy to.
            up_to_message_id: If set, only copy messages up to and
                including this message ID. If None, copy all messages.

        Returns:
            The ID of the fork point message (last copied message),
            or None if no messages were copied.

        Raises:
            KeyError: If either session does not exist.
        """
        if self.sessions.get_session(source_session_id) is None:
            raise KeyError(source_session_id)
        if self.sessions.get_session(target_session_id) is None:
            raise KeyError(target_session_id)

        storage = self.pool.storage
        if storage is not None:
            result = await storage.fork_conversation(
                source_session_id=source_session_id,
                new_session_id=target_session_id,
                fork_from_message_id=up_to_message_id,
            )
            self._message_cache.pop(target_session_id, None)
            return result

        return None

    async def truncate_messages(
        self,
        session_id: str,
        up_to_message_id: str,
    ) -> int:
        """Truncate messages after a specific message ID.

        Used by revert_session to remove messages after the revert point.

        Args:
            session_id: The session to truncate.
            up_to_message_id: Keep messages up to and including this ID,
                remove everything after.

        Returns:
            Number of messages removed.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            removed = await storage.truncate_messages(session_id, up_to_message_id)
            self._message_cache.pop(session_id, None)
            return removed

        return 0
