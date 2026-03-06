"""Server state management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from agentpool.diagnostics.lsp_manager import LSPManager
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.provider_auth import create_default_auth_service

if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
    from agentpool_server.opencode_server.models import (
        Config,
        Event,
        MessageWithParts,
        QuestionInfo,
        Session,
        SessionStatus,
        Todo,
    )
    from agentpool_server.opencode_server.models.question import QuestionToolInfo

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
    # Callback for first subscriber connection (e.g., for update check)
    on_first_subscriber: OnFirstSubscriberCallback | None = None
    _first_subscriber_triggered: bool = field(default=False, repr=False)
    # Background tasks (for cleanup on shutdown)
    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    # Event managers for subagent event routing (session_id -> event_manager)
    event_managers: dict[str, Any] = field(default_factory=dict)
    # Provider authentication service
    auth_service: Any = field(default_factory=create_default_auth_service)

    def __post_init__(self) -> None:
        """Initialize derived state."""
        self.lsp_manager = LSPManager(env=self.agent.env)
        self.lsp_manager.register_defaults()

    @property
    def fs(self) -> AsyncFileSystem:
        """Get the fsspec filesystem from the agent's environment."""
        return self.agent.env.get_fs()

    @property
    def storage(self) -> Any:
        """Get the storage manager from the agent's pool.

        Returns:
            StorageManager: The storage manager for session persistence.

        Raises:
            RuntimeError: If agent storage is not initialized.
        """
        assert self.agent.storage is not None, "Agent storage is not initialized"
        return self.agent.storage

    @property
    def base_path(self) -> str:
        """Get the resolved root directory for file operations."""
        raw_path = self.agent.env.cwd or self.working_dir
        return str(Path(raw_path).resolve())

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

    async def cleanup_tasks(self) -> None:
        """Cancel and wait for all background tasks."""
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

    async def broadcast_event(self, event: Event) -> None:
        """Broadcast an event to all SSE subscribers."""
        print(f"Broadcasting event: {event.type} to {len(self.event_subscribers)} subscribers")
        for queue in self.event_subscribers:
            await queue.put(event)

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
            return self.sessions[session_id]

        # Import here to avoid circular imports at module load time
        from agentpool_server.opencode_server.converters import opencode_to_session_data
        from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
        from agentpool_server.opencode_server.models import (
            Session,
            SessionCreatedEvent,
            SessionStatus,
            TimeCreatedUpdated,
        )

        now = now_ms()
        session = Session(
            id=session_id,
            project_id="default",  # TODO: Get from config
            directory=self.working_dir,
            title="New Session",
            version="1",
            time=TimeCreatedUpdated(created=now, updated=now),
            parent_id=parent_id,
        )

        # Persist to storage
        id_ = self.pool.manifest.config_file_path
        session_data = opencode_to_session_data(session, agent_name=self.agent.name, pool_id=id_)
        await self.pool.sessions.store.save(session_data)

        # Cache in memory
        self.sessions[session_id] = session
        self.messages[session_id] = []
        self.session_status[session_id] = SessionStatus(type="idle")
        self.todos[session_id] = []

        # Create input provider for this session
        input_provider = OpenCodeInputProvider(self, session_id)
        self.input_providers[session_id] = input_provider

        await self.broadcast_event(SessionCreatedEvent.create(session))

        return session
