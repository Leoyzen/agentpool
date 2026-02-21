"""Server state management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from agentpool.diagnostics.lsp_manager import LSPManager
from agentpool_server.opencode_server.models import Config


if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.storage import StorageManager
    from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
    from agentpool_server.opencode_server.models import (
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
    """Working directory for the server."""

    agent: BaseAgent[Any, Any]
    """The agent instance handling requests."""

    start_time: float = field(default_factory=time.time)
    """Server start time (seconds since epoch)."""

    config: Config = field(default_factory=Config)
    """Mutable runtime configuration. Initialized after state creation."""

    sessions: dict[str, Session] = field(default_factory=dict)
    """Cache of active sessions loaded from storage."""

    session_status: dict[str, SessionStatus] = field(default_factory=dict)
    """Current status for each session."""

    messages: dict[str, list[MessageWithParts]] = field(default_factory=dict)
    """Runtime message cache. Also persisted via storage."""

    reverted_messages: dict[str, list[MessageWithParts]] = field(default_factory=dict)
    """Messages removed during revert, kept for unrevert."""

    todos: dict[str, list[Todo]] = field(default_factory=dict)
    """Todo items per session."""

    input_providers: dict[str, OpenCodeInputProvider] = field(default_factory=dict)
    """Input providers for permission handling per session."""

    pending_questions: dict[str, PendingQuestion] = field(default_factory=dict)
    """Pending questions awaiting user response."""

    event_subscribers: list[asyncio.Queue[Event]] = field(default_factory=list)
    """SSE event subscriber queues."""

    on_first_subscriber: OnFirstSubscriberCallback | None = None
    """Callback triggered on first subscriber connection."""

    _first_subscriber_triggered: bool = field(default=False, repr=False)

    background_tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    """Background tasks tracked for cleanup on shutdown."""

    def __post_init__(self) -> None:
        """Initialize derived state."""
        self.lsp_manager = LSPManager(env=self.agent.env)
        self.lsp_manager.register_defaults()

    @property
    def fs(self) -> AsyncFileSystem:
        """Get the fsspec filesystem from the agent's environment."""
        return self.agent.env.get_fs()

    @property
    def storage(self) -> StorageManager:
        """Get the fsspec filesystem from the agent's environment."""
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
