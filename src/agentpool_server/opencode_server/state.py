"""Server state management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from agentpool import log
from agentpool.diagnostics.lsp_manager import LSPManager
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.models import SessionStatus
from agentpool_server.opencode_server.provider_auth import create_default_auth_service
from agentpool_storage.opencode_provider import helpers


logger = log.get_logger(__name__)

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem
    from slashed import CommandStore

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.storage import StorageManager
    from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
    from agentpool_server.opencode_server.models import (
        Config,
        Event,
        MessageRequest,
        MessageWithParts,
        QuestionInfo,
        Session,
        Todo,
    )
    from agentpool_server.opencode_server.models.question import QuestionToolInfo
    from agentpool_server.opencode_server.routes.global_routes import GlobalEventFactory

# Type alias for async callback
OnFirstSubscriberCallback = Callable[[], Coroutine[Any, Any, None]]


@dataclass
class PendingQuestion:
    """Pending question awaiting user response."""

    session_id: str
    """Session that owns this question."""

    questions: list[QuestionInfo]
    """Questions to ask."""

    future: asyncio.Future[list[list[str]]]
    """Future that resolves when user answers."""

    tool: QuestionToolInfo | None = None
    """Optional tool context."""


@dataclass
class QueuedAsyncPrompt:
    """Queued async prompt work owned by the OpenCode server."""

    request: MessageRequest
    user_msg_id: str
    user_msg_with_parts: MessageWithParts


@dataclass
class ServerState:
    """Shared state for the OpenCode server.

    Uses agent.agent_pool for session persistence and storage.
    In-memory state tracks active sessions and runtime data.
    """

    working_dir: str
    agent: BaseAgent[Any, Any]
    start_time: float = field(default_factory=time.time)
    # Configuration (mutable runtime config)
    # Initialized after state creation
    config: Config | None = None
    # Active sessions cache (session_id -> OpenCode Session model)
    # This is a cache of sessions loaded from pool.sessions
    sessions: dict[str, Session] = field(default_factory=dict)
    session_status: dict[str, SessionStatus] = field(default_factory=dict)
    # Per-session locks for concurrent message handling
    # Ensures messages to the same session are processed sequentially
    session_locks: dict[str, asyncio.Lock] = field(default_factory=dict)
    # Global lock for the shared OpenCode agent instance.
    # The base agent mutates per-run state (session_id, input provider,
    # active run context, model/mode overrides), so cross-session access must
    # be serialized until the server moves to per-session agent instances.
    agent_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Message storage (session_id -> messages)
    # Runtime cache - messages are also persisted via pool.storage
    messages: dict[str, list[MessageWithParts]] = field(default_factory=dict)
    # Reverted messages storage (session_id -> removed messages)
    # Stores messages removed during revert for unrevert operation
    reverted_messages: dict[str, list[MessageWithParts]] = field(default_factory=dict)
    # Todo storage (session_id -> todos)
    # Uses pool.todos for persistence
    todos: dict[str, list[Todo]] = field(default_factory=dict)
    # Input providers for permission handling (session_id -> provider)
    input_providers: dict[str, OpenCodeInputProvider] = field(default_factory=dict)
    # Question storage (question_id -> pending question info)
    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    # SSE event subscribers
    event_subscribers: list[asyncio.Queue[Event]] = field(default_factory=list)
    _event_factory: GlobalEventFactory | None = field(default=None, repr=False)
    # Callback for first subscriber connection (e.g., for update check)
    on_first_subscriber: OnFirstSubscriberCallback | None = None
    _first_subscriber_triggered: bool = field(default=False, repr=False)
    # Background tasks (for cleanup on shutdown)
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    # Per-session async prompt queue owned by the server runtime.
    pending_async_prompts: dict[str, list[QueuedAsyncPrompt]] = field(default_factory=dict)
    # Event managers for subagent event routing (session_id -> event_manager)
    event_managers: dict[str, Any] = field(default_factory=dict)
    # Provider authentication service
    auth_service: Any = field(default_factory=create_default_auth_service)
    # Skill command bridge for OpenCode
    skill_bridge: Any = field(default=None)
    # Command store for slash commands
    command_store: CommandStore | None = field(default=None)

    def __post_init__(self) -> None:
        """Initialize derived state."""
        self.lsp_manager = LSPManager(env=self.agent.env)
        self.lsp_manager.register_defaults()

    def get_event_factory(self) -> GlobalEventFactory:
        """Get or lazily create the GlobalEventFactory for event wrapping.

        The factory is created on first access using the working directory
        and computed project ID, then cached for the server's lifetime.
        Imports GlobalEventFactory locally to avoid circular imports.
        """
        from agentpool_server.opencode_server.routes.global_routes import GlobalEventFactory

        if self._event_factory is None:
            directory = self.base_path
            project = helpers.compute_project_id(directory)
            self._event_factory = GlobalEventFactory(
                directory=directory,
                project=project,
            )
        return self._event_factory

    def ensure_runtime_session_state(self, session_id: str) -> None:
        """Ensure in-memory runtime buckets exist for a session.

        This is used both for brand-new sessions and for sessions reloaded from
        persisted storage after a server restart. Cold-start recovery should not
        depend on individual routes remembering to initialize each bucket.
        """
        self.messages.setdefault(session_id, [])
        self.reverted_messages.setdefault(session_id, [])
        self.todos.setdefault(session_id, [])

    @property
    def fs(self) -> AsyncFileSystem:
        """Get the fsspec filesystem from the agent's environment."""
        return self.agent.env.get_fs()

    @property
    def base_path(self) -> str:
        """Get the resolved OpenCode project root for routing and file operations.

        OpenCode routes SSE events against the server/project directory the client
        attached to, not an agent-specific execution sandbox. Agent execution
        environments may override `env.cwd` for tool isolation, but routing
        metadata must remain anchored to the server's configured `working_dir`.
        """
        return str(Path(self.working_dir).resolve())

    @property
    def is_local_fs(self) -> bool:
        """Check if the filesystem is local."""
        from fsspec.implementations.local import LocalFileSystem

        return isinstance(self.fs, LocalFileSystem)

    @property
    def pool(self) -> AgentPool[Any]:
        """Get the agent pool from the agent."""
        if self.agent.agent_pool is None:
            msg = "Agent has no agent_pool set"
            raise RuntimeError(msg)
        return self.agent.agent_pool

    def get_session_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create a lock for the given session.

        Per-session locks ensure that messages to the same session
        are processed sequentially, preventing race conditions and
        event interleaving.

        Args:
            session_id: The session ID to get the lock for.

        Returns:
            asyncio.Lock: The lock for the session.
        """
        if session_id not in self.session_locks:
            self.session_locks[session_id] = asyncio.Lock()
        return self.session_locks[session_id]

    def ensure_input_provider(self, session_id: str) -> OpenCodeInputProvider:
        """Get or create the OpenCode input provider for a session."""
        from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider

        input_provider = self.input_providers.get(session_id)
        if input_provider is None:
            input_provider = OpenCodeInputProvider(self, session_id)
            self.input_providers[session_id] = input_provider
        return input_provider

    def bind_agent_to_session(
        self,
        session_id: str,
        *,
        agent: BaseAgent[Any, Any] | None = None,
    ) -> BaseAgent[Any, Any]:
        """Bind an agent instance to the requested session runtime context.

        Callers must already hold ``agent_lock`` when using this helper.
        """
        target_agent = self.agent if agent is None else agent
        input_provider = self.ensure_input_provider(session_id)
        target_agent._input_provider = input_provider
        target_agent.session_id = session_id
        return target_agent

    @property
    def storage(self) -> StorageManager:
        """Get the storage manager from the agent's pool.

        Returns:
            StorageManager: The storage manager for session persistence.

        Raises:
            RuntimeError: If agent storage is not initialized.
        """
        assert self.agent.storage is not None, "Agent storage is not initialized"
        return self.agent.storage

    def create_background_task(self, coro: Any, *, name: str | None = None) -> asyncio.Task[Any]:
        """Create and track a background task."""
        task = asyncio.create_task(coro, name=name)
        self.background_tasks.add(task)
        task.add_done_callback(self.background_tasks.discard)
        return task

    def enqueue_async_prompt(self, session_id: str, queued_prompt: QueuedAsyncPrompt) -> None:
        """Append async prompt work to a session-owned queue."""
        self.pending_async_prompts.setdefault(session_id, []).append(queued_prompt)

    def pop_next_async_prompt(self, session_id: str) -> QueuedAsyncPrompt | None:
        """Pop the next queued async prompt for a session, if any."""
        queue = self.pending_async_prompts.get(session_id)
        if not queue:
            return None
        queued_prompt = queue.pop(0)
        if not queue:
            self.pending_async_prompts.pop(session_id, None)
        return queued_prompt

    def clear_pending_async_prompts(self, session_id: str) -> None:
        """Drop queued async prompt work for a session."""
        self.pending_async_prompts.pop(session_id, None)

    def has_pending_async_prompts(self, session_id: str) -> bool:
        """Return whether a session currently has queued async prompt work."""
        return bool(self.pending_async_prompts.get(session_id))

    def has_session_background_task(self, session_id: str) -> bool:
        """Return whether a per-session prompt worker is already running."""
        task_name = f"process_message_{session_id}"
        return any(
            task.get_name() == task_name and not task.done() for task in self.background_tasks
        )

    async def cancel_session_background_tasks(self, session_id: str) -> None:
        """Cancel background tasks associated with a session."""
        task_name = f"process_message_{session_id}"
        tasks = [task for task in self.background_tasks if task.get_name() == task_name]
        self.clear_pending_async_prompts(session_id)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def cancel_all_pending_questions(self) -> list[str]:
        """Cancel all pending questions and return their IDs.

        Called when the SSE client disconnects to prevent agent_lock deadlock.
        When a question's Future is cancelled, the agent's get_elicitation()
        handler catches CancelledError and returns ElicitResult(action="cancel"),
        which causes question_for_user to raise RunAbortedError. This propagates
        through process_stream and _process_message_locked's except handler,
        properly finalizing the assistant message and releasing agent_lock.

        Returns:
            List of cancelled question IDs.
        """
        cancelled_ids: list[str] = []
        for question_id, pending in self.pending_questions.items():
            if not pending.future.done():
                pending.future.cancel()
                cancelled_ids.append(question_id)
        return cancelled_ids

    async def cleanup_tasks(self) -> None:
        """Cancel and wait for all background tasks."""
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

    async def broadcast_event(self, event: Event) -> None:
        """Broadcast an event to all SSE subscribers.

        Isolates failures: if one subscriber's queue raises,
        other subscribers still receive the event.

        Uses put_nowait() instead of await queue.put() to avoid blocking
        the broadcaster when a subscriber's queue is full. Iterates over
        a copy of event_subscribers to avoid mutation during iteration
        (subscribers can be removed by the _event_generator finally block
        or by error handling below).
        """
        for queue in list(self.event_subscribers):  # iterate copy to avoid mutation
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("SSE subscriber queue full, dropping event")
            except Exception:  # noqa: BLE001
                logger.warning("SSE subscriber queue error, removing subscriber")
                with contextlib.suppress(ValueError):
                    self.event_subscribers.remove(queue)

    async def mark_session_idle(self, session_id: str) -> None:
        """Mark a session idle and broadcast the matching status events."""
        from agentpool_server.opencode_server.models import SessionIdleEvent, SessionStatusEvent

        status = SessionStatus(type="idle")
        self.session_status[session_id] = status
        await self.broadcast_event(SessionStatusEvent.create(session_id, status))
        await self.broadcast_event(SessionIdleEvent.create(session_id))

    async def emit_session_turn_complete(self, session_id: str) -> None:
        """Broadcast the per-turn completion signal without changing busy state.

        OpenCode clients still use ``session.idle`` as an end-of-turn marker.
        For queued async prompts we need that signal after each finished turn,
        even while the server-owned queue still has follow-up work to process.
        """
        from agentpool_server.opencode_server.models import SessionIdleEvent

        await self.broadcast_event(SessionIdleEvent.create(session_id))

    async def ensure_session(
        self,
        session_id: str,
        parent_id: str | None = None,
    ) -> Session:
        """Ensure a session exists with the given ID.

        Returns the existing session if it already exists in memory,
        otherwise creates a new session following the same pattern as
        create_session in session_routes.py.

        Args:
            session_id: Unique identifier for the session
            parent_id: Optional parent session ID for fork relationships

        Returns:
            The Session object (existing or newly created)
        """
        # Check if session already exists in memory
        if session_id in self.sessions:
            session = self.sessions[session_id]
            from agentpool_server.opencode_server.models import SessionUpdatedEvent

            await self.broadcast_event(SessionUpdatedEvent.create(session))
            return session

        # Import here to avoid circular imports at module load time
        from agentpool_server.opencode_server.converters import opencode_to_session_data
        from agentpool_server.opencode_server.models import (
            Session,
            SessionCreatedEvent,
            SessionUpdatedEvent,
            TimeCreatedUpdated,
        )

        now = now_ms()
        if parent_id is not None:
            parent_session = self.sessions.get(parent_id)
            if parent_session:
                project_id = parent_session.project_id
                directory = parent_session.directory
            else:
                project_id = helpers.compute_project_id(self.working_dir)
                directory = self.working_dir
        else:
            project_id = helpers.compute_project_id(self.working_dir)
            directory = self.working_dir
        session = Session(
            id=session_id,
            project_id=project_id,
            directory=directory,
            title="New Session",
            version="1",
            time=TimeCreatedUpdated(created=now, updated=now),
            parent_id=parent_id,
        )

        # Persist to storage
        id_ = self.pool.manifest.config_file_path
        session_data = opencode_to_session_data(session, agent_name=self.agent.name, pool_id=id_)
        if self.pool.sessions.store:
            await self.pool.sessions.store.save(session_data)
        else:
            await self.pool.storage.save_session(session_data)

        # Cache in memory
        self.sessions[session_id] = session
        self.ensure_runtime_session_state(session_id)
        await self.mark_session_idle(session_id)

        # Only bind agent to session for top-level sessions.
        # Child sessions (parent_id is set) live inside the parent's agent stream
        # and must NOT rebind the shared agent — that would overwrite the parent's
        # session_id and also deadlock on agent_lock held by the parent stream.
        if parent_id is None:
            async with self.agent_lock:
                self.bind_agent_to_session(session_id)

        await self.broadcast_event(SessionCreatedEvent.create(session))
        # Broadcast session.updated so the CLI TUI can upsert the session
        # into its SolidJS store.  The CLI TUI's sync.tsx event handler
        # processes session.updated (upsert) but NOT session.created
        # (insert-only), so without this event the TUI would rely solely
        # on the async REST session.sync() call, causing a delay while
        # the store is empty and messages cannot be rendered.
        await self.broadcast_event(SessionUpdatedEvent.create(session))
        logger.info(
            "ensure_session: completed successfully",
            session_id=session_id,
            parent_id=parent_id,
        )

        return session
