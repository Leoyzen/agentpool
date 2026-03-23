"""Server state management."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
import contextlib
from dataclasses import dataclass, field
from pathlib import Path
import time
from typing import TYPE_CHECKING, Any

from agentpool.diagnostics.lsp_manager import LSPManager
from agentpool.log import get_logger
from agentpool_server.opencode_server.converters import (
    chat_message_to_opencode,
    opencode_to_chat_message,
    session_data_to_opencode,
)
from agentpool_server.opencode_server.provider_auth import create_default_auth_service
from opencode_sdk.models import (
    Config,
    LspUpdatedEvent,
    SessionStatus,
)


if TYPE_CHECKING:
    from fsspec.asyn import AsyncFileSystem

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.storage import StorageManager
    from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider
    from agentpool_server.opencode_server.provider_auth import ProviderAuthService
    from opencode_sdk.models import (
        AnyMessageWithParts,
        Event,
        QuestionInfo,
        QuestionToolInfo,
        Session,
        Todo,
        WorkspaceInfo,
    )

# Type alias for async callback
OnFirstSubscriberCallback = Callable[[], Coroutine[Any, Any, None]]
logger = get_logger(__name__)


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

    messages: dict[str, list[AnyMessageWithParts]] = field(default_factory=dict)
    """Runtime message cache. Also persisted via storage."""

    reverted_messages: dict[str, list[AnyMessageWithParts]] = field(default_factory=dict)
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

    auth_service: ProviderAuthService = field(default_factory=create_default_auth_service)
    """Provider authentication service."""

    workspaces: dict[str, WorkspaceInfo] = field(default_factory=dict)
    """Active workspaces."""

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

    def _warmup_lsp_for_files(self, file_paths: list[str]) -> None:
        """Warm up LSP servers for the given file paths.

        This starts LSP servers asynchronously based on file extensions.
        Like OpenCode's LSP.touchFile(), this triggers server startup without waiting.

        Args:
            file_paths: List of file paths that were accessed
        """
        logger.info("_warmup_lsp_for_files called with", file_paths=file_paths)
        lsp_manager = self.lsp_manager

        async def warmup_files() -> None:
            """Start LSP servers for each file path."""
            logger.info("warmup_files task started")

            servers_started = False
            for path in file_paths:
                # Find appropriate server for this file
                server_info = lsp_manager.get_server_for_file(path)
                if server_info is None:
                    continue
                server_id = server_info.id
                if lsp_manager.is_running(server_id):
                    logger.info("Server with same id already running", server_id=server_id)
                    continue

                # Start server for workspace root
                root_uri = f"file://{self.working_dir}"
                logger.info("Starting server...", server_id=server_id)
                try:
                    await lsp_manager.start_server(server_id, root_uri)
                    servers_started = True
                    logger.info("Server started successfully", server_id=server_id)
                except Exception as e:  # noqa: BLE001
                    # Don't fail on LSP startup errors
                    logger.info("Failed to start server", error=e, server_id=server_id)

            # Emit lsp.updated event if any servers started
            if servers_started:
                logger.info("Broadcasting LspUpdatedEvent")
                await self.broadcast_event(LspUpdatedEvent())
            logger.info("warmup_files task completed")

        # Run warmup in background (don't block the event handler)
        logger.info("Creating background task for warmup")
        self.create_background_task(warmup_files(), name="lsp-warmup")

    async def persist_message_to_storage(
        self,
        msg: AnyMessageWithParts,
        session_id: str,
    ) -> None:
        """Persist an OpenCode message to storage.

        Converts the OpenCode MessageWithParts to ChatMessage and saves it.

        Args:
            msg: OpenCode message to persist
            session_id: Session/conversation ID
        """
        chat_msg = opencode_to_chat_message(msg, session_id=session_id)
        with contextlib.suppress(Exception):
            await self.storage.log_message(chat_msg)

    async def get_or_load_session(self, session_id: str) -> Session | None:
        """Get session from cache or load via agent.

        Returns None if session not found.
        Uses agent.load_session() which handles loading from the appropriate
        storage (pool storage, Claude storage, ACP server, Codex, etc.).
        """
        # Check if session AND messages are already loaded
        if session_id in self.sessions and session_id in self.messages:
            return self.sessions[session_id]

        # Load via agent - this populates agent.conversation.chat_messages
        data = await self.agent.load_session(session_id)
        if data is None:
            return None

        # Convert SessionData to OpenCode Session
        session = session_data_to_opencode(data)
        # Cache the session
        self.sessions[session_id] = session
        # Initialize runtime state
        if session_id not in self.session_status:
            self.session_status[session_id] = SessionStatus(type="idle")
        # Convert agent's conversation history to OpenCode format
        self.messages[session_id] = [
            chat_message_to_opencode(
                chat_msg,
                session_id=session_id,
                working_dir=self.working_dir,
                agent_name=self.agent.name,
                model_id=chat_msg.model_name or "sonnet",
                provider_id=chat_msg.provider_name or "claude-code",
            )
            for chat_msg in self.agent.conversation.chat_messages
        ]
        return session
