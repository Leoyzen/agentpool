"""SessionPool core orchestration layer.

Provides session lifecycle management, turn execution, event routing,
and auto-resume capabilities for agent sessions.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
import copy
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final

from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool


logger = get_logger(__name__)

# Constants
DEFAULT_QUEUE_MAXSIZE: Final[int] = 1000
DEFAULT_MAX_AUTO_RESUME: Final[int] = 10
DEFAULT_SESSION_TTL_SECONDS: Final[float] = 3600.0


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


class EventBus:
    """PubSub event bus for cross-turn event streaming.

    Decouples event producers (agents) from consumers (protocol handlers).
    Events are broadcast to all subscribers for a given session.

    Safety features:
    - Bounded queues with dropping strategy (drop oldest)
    - Automatic cleanup of dead subscribers
    - Sentinel-based queue shutdown
    """

    def __init__(self, max_queue_size: int = DEFAULT_QUEUE_MAXSIZE) -> None:
        """Initialize the event bus.

        Args:
            max_queue_size: Maximum size for subscriber queues.
        """
        self._subscribers: dict[str, list[tuple[asyncio.Queue[Any], str]]] = {}
        self._session_tree: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._max_queue_size = max_queue_size

    async def subscribe(
        self, session_id: str, scope: str = "session"
    ) -> asyncio.Queue[Any]:
        """Subscribe to events for a session.

        Args:
            session_id: The session to subscribe to.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).

        Returns:
            A queue to consume events from.
        """
        queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.setdefault(session_id, []).append((queue, scope))
        return queue

    async def unsubscribe(
        self,
        session_id: str,
        queue: asyncio.Queue[Any],
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
        for parent_id, children in self._session_tree.items():
            if session_id in children:
                return parent_id
        return None

    def _is_descendant(self, child_id: str, parent_id: str) -> bool:
        """Check if child_id is a descendant of parent_id."""
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
        return published_sid == subscriber_sid

    async def publish(self, session_id: str, event: Any) -> None:
        """Publish an event to all subscribers for a session.

        If a subscriber's queue is full, drops the oldest event.
        If put fails, removes the dead subscriber.

        Creates a shallow copy of the event for each subscriber to prevent
        one consumer's mutation from affecting others.

        Args:
            session_id: The session to publish to.
            event: The event to broadcast.
        """
        async with self._lock:
            queues: list[tuple[asyncio.Queue[Any], str]] = []
            for subscriber_sid, subscribers in self._subscribers.items():
                for queue, scope in subscribers:
                    if self._should_receive(session_id, subscriber_sid, scope):
                        queues.append((queue, scope))

        dead_queues: list[asyncio.Queue[Any]] = []
        for queue, _scope in queues:
            copied_event = copy.copy(event)
            try:
                queue.put_nowait(copied_event)
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()
                    queue.put_nowait(copied_event)
                except asyncio.QueueEmpty:
                    try:
                        queue.put_nowait(copied_event)
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

        Args:
            session_id: The session to close subscriptions for.
        """
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
        cleanup_callback: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """Initialize the session controller.

        Args:
            pool: The agent pool to resolve agents from.
            cleanup_callback: Optional callback invoked when a session is cleaned up.
        """
        self.pool = pool
        self._cleanup_callback = cleanup_callback
        self._sessions: dict[str, SessionState] = {}
        self._session_agents: dict[str, BaseAgent[Any, Any]] = {}
        self._children: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()
        self._session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._mcp_max_processes: int = 100
        self._mcp_process_count: int = 0

    async def get_or_create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> SessionState:
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
            The session state.
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty or whitespace")

        async with self._lock:
            return await self._get_or_create_session_locked(
                session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
            )

    async def _get_or_create_session_locked(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> SessionState:
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
            The session state.
        """
        if session_id in self._sessions:
            state = self._sessions[session_id]
            state.last_active_at = time.monotonic()
            return state

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
        if parent_session_id:
            self._children.setdefault(parent_session_id, []).append(session_id)
        logger.info("Created session", session_id=session_id, agent_name=state.agent_name)
        return state

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

            session = await self._get_or_create_session_locked(session_id, agent_name)
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
                    self._session_agents[session_id] = base_agent
                    session.agent = base_agent
                    return base_agent

                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": agent_name})
                agent = cfg.get_agent(
                    input_provider=input_provider,
                    pool=self.pool,
                )
                await agent.__aenter__()
                self._session_agents[session_id] = agent
                session.agent = agent
                session.is_per_session_agent = True
                self._increment_mcp_count(agent)
                logger.info(
                    "Created session agent", session_id=session_id, agent_name=agent_name
                )
                return agent

            logger.warning(
                "Using shared agent for session - state may be shared across sessions",
                session_id=session_id,
                agent_name=agent_name,
                agent_type=type(base_agent).__name__,
            )
            self._session_agents[session_id] = base_agent
            session.agent = base_agent
            return base_agent

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
        # Remove from parent's children list
        if session.parent_session_id and session.parent_session_id in self._children:
            self._children[session.parent_session_id] = [
                cid for cid in self._children[session.parent_session_id] if cid != session_id
            ]

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Order matters:
        1. Mark session as closing (prevents new turns from starting)
        2. Handle child sessions based on lifecycle policy
        3. Remove from tracking dicts
        4. Acquire turn_lock to wait for active turn to complete
        5. Exit agent context if per-session
        6. Clean up session state

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

            session.is_closing = True
            session.closed_at = time.monotonic()

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
    ) -> None:
        """Initialize the turn runner.

        Args:
            session_controller: The session controller for agent lifecycle.
            enable_auto_resume: Whether to enable auto-resume loop.
            max_auto_resume: Maximum auto-resume iterations.
        """
        self.sessions = session_controller
        self.event_bus = EventBus()
        self._post_turn_injections: dict[str, list[str]] = {}
        self._post_turn_prompts: dict[str, list[tuple[Any, ...]]] = {}
        self._injection_locks: dict[str, asyncio.Lock] = {}
        self._injection_locks_lock = asyncio.Lock()
        self._enable_auto_resume = enable_auto_resume
        self._max_auto_resume = max_auto_resume
        self._turn_timings: list[tuple[float, float]] = []
        self._max_turn_timing_history: int = 100

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
        agent = await self.sessions.get_or_create_session_agent(session_id)
        _session = self.sessions.get_session(session_id)

        from agentpool.agents.context import AgentRunContext

        run_ctx = AgentRunContext(
            deps=kwargs.get("deps"),
        )
        run_ctx.cancelled = False
        run_ctx.current_task = asyncio.current_task()

        agent._active_run_ctx = run_ctx

        async def _consume_event_queue() -> None:
            """Consume events from run_ctx.event_queue and publish to EventBus.

            Background tasks and injected prompts emit events to
            run_ctx.event_queue. This consumer ensures those events
            reach the EventBus even after the main stream completes.
            """
            try:
                while True:
                    event = await run_ctx.event_queue.get()
                    if event is None:
                        break
                    await self.event_bus.publish(session_id, event)
            except asyncio.CancelledError:
                pass

        turn_start = time.monotonic()
        event_consumer = asyncio.create_task(
            _consume_event_queue(),
            name=f"event_consumer_{session_id}",
        )
        try:
            # Process prompts and handle injections/queued prompts
            # like BaseAgent.run_stream() does.
            async for event in agent._run_stream_once(
                run_ctx, *prompts, session_id=session_id, **kwargs
            ):
                await self.event_bus.publish(session_id, event)

            # After _run_stream_once completes, flush unconsumed injections
            # to queued prompts and continue processing if any remain.
            run_ctx.injection_manager.flush_pending_to_queue()
            while run_ctx.injection_manager.has_queued() and not run_ctx.cancelled:
                current_prompts = run_ctx.injection_manager.pop_queued()
                if current_prompts is None:
                    break
                async for event in agent._run_stream_once(
                    run_ctx, *current_prompts, session_id=session_id, **kwargs
                ):
                    await self.event_bus.publish(session_id, event)
                run_ctx.injection_manager.flush_pending_to_queue()
        finally:
            # Signal the event queue consumer to stop
            await run_ctx.event_queue.put(None)
            # Wait for it to finish (with timeout to prevent hanging)
            try:
                await asyncio.wait_for(event_consumer, timeout=5.0)
            except TimeoutError:
                event_consumer.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await event_consumer

            turn_end = time.monotonic()
            self._turn_timings.append((turn_start, turn_end))
            if len(self._turn_timings) > self._max_turn_timing_history:
                self._turn_timings.pop(0)
            agent._active_run_ctx = None

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
        session = await self.sessions.get_or_create_session(session_id)

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
        session = await self.sessions.get_or_create_session(session_id)

        async with session.turn_lock:
            if session.is_closing:
                logger.debug("Session is closing, skipping turn", session_id=session_id)
                return

            try:
                await self._run_turn_unlocked(session_id, *initial_prompts, **kwargs)
                await self._process_queued_work(session_id, session, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Turn loop failed", session_id=session_id)
                await self._drain_post_turn_injections(session_id)
                await self._drain_post_turn_prompts(session_id)

    async def inject_prompt(self, session_id: str, message: str) -> bool:
        """Inject a message into a session.

        If the session has an active turn, injects immediately.
        Otherwise, queues for the next turn and triggers auto-resume.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.

        Returns:
            True if injected into active turn, False if queued.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            logger.warning(
                "Cannot inject: session not found or closing", session_id=session_id
            )
            return False

        agent = session.agent
        run_ctx = agent.get_active_run_context()
        if run_ctx is not None:
            run_ctx.injection_manager.inject(message)
            return True

        lock = await self._get_injection_lock(session_id)
        async with lock:
            run_ctx = agent.get_active_run_context()
            if run_ctx is not None:
                run_ctx.injection_manager.inject(message)
                return True
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                logger.debug(
                    "Session closed while waiting for lock, discarding injection",
                    session_id=session_id,
                )
                return False
            self._post_turn_injections.setdefault(session_id, []).append(message)

        logger.debug("Queued injection for next turn", session_id=session_id)
        _ = asyncio.create_task(  # noqa: RUF006
            self._trigger_auto_resume(session_id)
        )
        return False

    async def queue_prompt(self, session_id: str, *prompts: Any) -> bool:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.

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

        _ = asyncio.create_task(  # noqa: RUF006
            self._trigger_auto_resume(session_id)
        )
        return False

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
            **kwargs: Additional arguments passed to the agent.
        """
        if session.is_closing:
            logger.debug(
                "Session is closing, skipping initial queued work", session_id=session_id
            )
            return

        injections = await self._drain_post_turn_injections(session_id)
        prompts = await self._drain_post_turn_prompts(session_id)

        if injections:
            await self._run_turn_unlocked(session_id, *injections, **kwargs)

        for prompt_group in prompts:
            await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)

        for iteration in range(self._max_auto_resume):
            if session.is_closing:
                logger.debug("Session closing during auto-resume", session_id=session_id)
                break

            injections = await self._drain_post_turn_injections(session_id)
            prompts = await self._drain_post_turn_prompts(session_id)

            if not injections and not prompts:
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
        else:
            logger.warning(
                "Auto-resume loop exceeded max iterations",
                session_id=session_id,
                max_iterations=self._max_auto_resume,
            )

    async def _trigger_auto_resume(self, session_id: str) -> None:
        """Trigger auto-resume for a session if no turn is active.

        Fire-and-forget task that ensures post-turn work queued after
        run_loop() exits gets processed promptly.

        Args:
            session_id: The session to trigger auto-resume for.
        """
        try:
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                return

            async with session.turn_lock:
                if session.is_closing:
                    return

                current_session = self.sessions.get_session(session_id)
                if current_session is not session:
                    return

                if self._enable_auto_resume:
                    await self._process_queued_work(session_id, session)
                else:
                    injections = await self._drain_post_turn_injections(session_id)
                    prompts = await self._drain_post_turn_prompts(session_id)

                    if injections:
                        await self._run_turn_unlocked(session_id, *injections)
                    for prompt_group in prompts:
                        await self._run_turn_unlocked(session_id, *prompt_group)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Auto-resume trigger failed", session_id=session_id)

    async def _drain_post_turn_injections(self, session_id: str) -> list[str]:
        """Drain and return post-turn injections for a session (atomic).

        Args:
            session_id: The session to drain injections from.

        Returns:
            The drained injection messages.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            return self._post_turn_injections.pop(session_id, [])

    async def _drain_post_turn_prompts(self, session_id: str) -> list[tuple[Any, ...]]:
        """Drain and return post-turn prompts for a session (atomic).

        Args:
            session_id: The session to drain prompts from.

        Returns:
            The drained prompt groups.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            return self._post_turn_prompts.pop(session_id, [])


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
        enable_auto_resume: bool = True,
        enable_event_bus: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
    ) -> None:
        """Initialize the session pool.

        Args:
            pool: The agent pool to resolve agents from.
            enable_auto_resume: Whether to enable auto-resume loop.
            enable_event_bus: Whether to enable cross-turn event routing.
            max_auto_resume: Maximum auto-resume iterations.
        """
        self.pool = pool
        self.sessions = SessionController(pool, cleanup_callback=self.close_session)
        self.turns = TurnRunner(
            self.sessions,
            enable_auto_resume=enable_auto_resume,
            max_auto_resume=max_auto_resume,
        )
        self._enable_auto_resume = enable_auto_resume
        self._enable_event_bus = enable_event_bus

    async def start(self) -> None:
        """Start the session pool and background tasks."""
        await self.sessions.start_cleanup_task()

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
        state = await self.sessions.get_or_create_session(
            session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
        )
        # Persist parent-child relationship via SessionManager for
        # project_id/cwd inheritance and legacy compatibility.
        if parent_session_id is not None:
            await self.pool.sessions.create_child_session(
                parent_session_id=parent_session_id,
                agent_name=agent_name or state.agent_name,
                agent_type=metadata.get("agent_type", "native"),
                child_session_id=session_id,
            )
        return state

    async def close_session(self, session_id: str) -> None:
        """Close a session.

        Order: session first (agent may emit final events), then event bus,
        then turn state.

        Args:
            session_id: The session to close.
        """
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
        if self._enable_auto_resume:
            await self.turns.run_loop(session_id, *prompts, **kwargs)
        else:
            await self.turns.run_turn(session_id, *prompts, **kwargs)

    async def inject_prompt(self, session_id: str, message: str) -> bool:
        """Inject a message into a session.

        Args:
            session_id: The session to inject into.
            message: The message to inject.

        Returns:
            True if injected into active turn, False if queued.
        """
        return await self.turns.inject_prompt(session_id, message)

    async def queue_prompt(self, session_id: str, *prompts: Any) -> bool:
        """Queue prompts for a session.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        return await self.turns.queue_prompt(session_id, *prompts)
