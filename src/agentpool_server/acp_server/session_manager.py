"""ACP Session Manager - delegates to pool's SessionManager."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal, Self

from acp.schema import ClientCapabilities
from agentpool.log import get_logger
from agentpool.sessions import SessionData
from agentpool_server.acp_server.session import ACPSession


if TYPE_CHECKING:
    from collections.abc import Sequence

    from acp import Client
    from acp.schema import Implementation, McpServer
    from agentpool import AgentPool
    from agentpool.orchestrator import SessionController
    from agentpool.sessions import SessionStore
    from agentpool.storage.manager import StorageManager
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent


logger = get_logger(__name__)


class ACPSessionManager:
    """Manages ACP sessions, delegating persistence to pool's StorageManager.

    This is a thin coordinator that:
    - Creates and tracks active ACP sessions
    - Delegates session persistence to pool.storage (StorageManager)
    - Handles ACP-specific session initialization
    """

    def __init__(self, pool: AgentPool[Any]) -> None:
        """Initialize ACP session manager.

        Args:
            pool: Agent pool containing StorageManager for persistence
        """
        self._pool = pool
        self._acp_sessions: dict[str, ACPSession] = {}
        self._connection_sessions: dict[str, set[str]] = {}
        self._command_update_task: asyncio.Task[None] | None = None
        logger.info("Initialized ACP session manager")

    @property
    def storage(self) -> StorageManager:
        """Get the pool's storage manager for persistence."""
        return self._pool.storage

    @property
    def session_store(self) -> SessionStore | None:
        """Get the pool's session store for session CRUD operations."""
        if self._pool.session_pool is not None:
            return self._pool.session_pool.sessions.store
        return None

    @property
    def _session_controller(self) -> SessionController | None:
        """Get the SessionController from the pool, if available."""
        if self._pool.session_pool is not None:
            return self._pool.session_pool.sessions
        return None

    async def create_session(
        self,
        agent_name: str,
        cwd: str,
        client: Client,
        acp_agent: AgentPoolACPAgent,
        mcp_servers: Sequence[McpServer] | None = None,
        session_id: str | None = None,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        client_locale: str | None = None,
        subagent_display_mode: Literal["legacy", "zed", "qwen"] = "legacy",
        raw_input_mode: Literal["dict", "skip", "json_str"] = "dict",
        parent_session_id: str | None = None,
        connection_id: str | None = None,
    ) -> str:
        """Create a new ACP session.

        Args:
            agent_name: Name of the agent (from manifest) to use for this session
            cwd: Working directory for the session
            client: ACP client connection
            acp_agent: ACP agent instance
            mcp_servers: Optional MCP server configurations
            session_id: Optional specific session ID (generated if None)
            client_capabilities: Client capabilities for tool registration
            client_info: Client implementation info (name, version)
            client_locale: Client locale for i18n (e.g. "en", "zh-CN")
            subagent_display_mode: Display mode for subagent outputs
            raw_input_mode: How to emit tool call raw_input
            parent_session_id: Optional parent session ID for child sessions.
                When provided, creates a child session that inherits
                project_id/cwd from the parent via SessionManager.
            connection_id: Optional WebSocket connection ID for tracking
                sessions per connection. When provided, the session is
                registered in ``_connection_sessions`` for cleanup on
                disconnect.

        Returns:
            Session ID for the created session
        """
        # Check for existing session (before generating ID)
        if session_id is not None and session_id in self._acp_sessions:
            logger.warning("Session ID already exists", session_id=session_id)
            msg = f"Session {session_id} already exists"
            raise ValueError(msg)

        if parent_session_id is not None and self._pool.session_pool is not None:
            # Child session path: delegate to SessionPool which
            # inherits project_id/cwd from the parent automatically.
            from agentpool.utils.identifiers import generate_session_id

            child_session_id = session_id or generate_session_id()
            await self._pool.session_pool.create_session(
                session_id=child_session_id,
                agent_name=agent_name,
                parent_session_id=parent_session_id,
                agent_type="acp",
            )
            # If caller provided a specific session_id, we cannot
            # override the one generated by create_session().
            # Log a warning and use the generated ID.
            if session_id is not None and session_id != child_session_id:
                logger.warning(
                    "Ignoring caller-provided session_id for child session",
                    requested=session_id,
                    generated=child_session_id,
                )
            session_id = child_session_id

            # Load persisted child data to get inherited cwd
            if self.session_store is not None:
                child_data = await self.session_store.load(child_session_id)
                if child_data is not None and child_data.cwd is not None:
                    cwd = child_data.cwd
            effective_cwd = cwd
        else:
            # Top-level session path: compute project_id from cwd.
            # Generate session ID if not provided
            if session_id is None:
                session_id = self.storage.generate_session_id()

            # Compute project_id from cwd so that ACP sessions are
            # correctly associated with the project in the TUI sidebar.
            # Without this, project_id defaults to None which breaks
            # per-project session filtering.
            from agentpool_storage.opencode_provider.helpers import compute_project_id

            project_id = compute_project_id(cwd)

            # Create and persist session data directly
            data = SessionData(
                session_id=session_id,
                agent_name=agent_name,
                cwd=cwd,
                project_id=project_id,
                metadata={"protocol": "acp", "mcp_server_count": len(mcp_servers or [])},
            )
            if self.session_store:
                await self.session_store.save(data)
            effective_cwd = cwd

        # Use per-session agent from SessionPool so that
        # initialize_mcp_servers() updates the same agent object
        # that child sessions inherit MCP configs from.
        if self._pool.session_pool is not None:
            session_agent = await self._pool.session_pool.sessions.get_or_create_session_agent(
                session_id, agent_name=agent_name
            )
        else:
            # Fallback: create directly from manifest (tests only)

            cfg = self._pool.manifest.agents.get(agent_name)
            if cfg is None:
                msg = f"Agent {agent_name!r} not found in manifest"
                raise ValueError(msg)
            if cfg.name is None:
                cfg = cfg.model_copy(update={"name": agent_name})
            from agentpool_config.context import ConfigContextManager

            with ConfigContextManager(self._pool._config_file_path):
                session_agent = cfg.get_agent(pool=self._pool)
            await session_agent.__aenter__()

        # Create the ACP-specific runtime session
        session = ACPSession(
            session_id=session_id,
            agent=session_agent,
            cwd=effective_cwd,
            client=client,
            mcp_servers=mcp_servers,
            acp_agent=acp_agent,
            client_capabilities=client_capabilities or ClientCapabilities(),
            client_info=client_info,
            client_locale=client_locale,
            manager=self,
            subagent_display_mode=subagent_display_mode,
            raw_input_mode=raw_input_mode,
        )
        session.register_update_callback(self._on_commands_updated)
        await session.initialize()
        await session.initialize_mcp_servers()
        self._acp_sessions[session_id] = session
        if connection_id is not None:
            self._connection_sessions.setdefault(connection_id, set()).add(session_id)
        logger.info("Created ACP session", session_id=session_id, agent=session_agent.name)
        return session_id

    def get_session(self, session_id: str) -> ACPSession | None:
        """Get an active session by ID.

        Resolves the protocol-specific ACPSession runtime object from
        _acp_sessions. Does not gate on SessionController registration,
        because during session creation the ACPSession exists in
        _acp_sessions before the orchestrator registers it with the
        controller asynchronously.
        """
        return self._acp_sessions.get(session_id)

    async def resume_session(
        self,
        session_id: str,
        client: Client,
        acp_agent: AgentPoolACPAgent,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        client_locale: str | None = None,
        subagent_display_mode: Literal["legacy", "zed", "qwen"] = "legacy",
        raw_input_mode: Literal["dict", "skip", "json_str"] = "dict",
        mcp_servers: Sequence[McpServer] | None = None,
        connection_id: str | None = None,
    ) -> ACPSession | None:
        """Resume a session from storage.

        Args:
            session_id: Session identifier
            client: ACP client connection
            acp_agent: ACP agent instance
            client_capabilities: Client capabilities
            client_info: Client implementation info (name, version)
            client_locale: Client locale for i18n (e.g. "en", "zh-CN")
            subagent_display_mode: Display mode for subagent outputs
            raw_input_mode: How to emit tool call raw_input
            mcp_servers: MCP server configurations to (re-)initialize
            connection_id: Optional WebSocket connection ID for tracking
                sessions per connection. When provided, the session is
                registered in ``_connection_sessions`` for cleanup on
                disconnect.

        Returns:
            Resumed ACPSession if found, None otherwise
        """
        # Close existing session if active, then recreate fresh.
        # This prevents stale MCP connections, toolset caches, and agent state
        # from leaking across session resume cycles.
        existing_session = self._acp_sessions.pop(session_id, None)
        if existing_session is not None:
            logger.info(
                "Closing existing session before resume",
                session_id=session_id,
            )
            # Remove session_id from all connection mappings to prevent
            # the old connection's disconnect handler from closing the
            # newly resumed session.
            for conn_id, sessions in list(self._connection_sessions.items()):
                if session_id in sessions:
                    sessions.discard(session_id)
                    if not sessions:
                        self._connection_sessions.pop(conn_id, None)
            # SessionController handles RunHandle lifecycle (10s timeout + cancel),
            # agent.mcp.cleanup_session(), and agent.__aexit__().
            controller = self._session_controller
            if controller is not None:
                try:
                    await controller.close_session(session_id)
                except Exception:
                    logger.exception(
                        "Failed to close session via SessionController",
                        session_id=session_id,
                    )
            # ACPSession.close() handles ACP-specific cleanup:
            # acp_env, signals, prompts. Also calls cleanup_session() via T15,
            # but idempotent via per-session asyncio.Lock.
            try:
                await existing_session.close()
            except Exception:
                logger.exception(
                    "Failed to close ACPSession",
                    session_id=session_id,
                )
        # Try to load from pool's session store
        data = await self.session_store.load(session_id) if self.session_store else None
        if data is None:
            logger.warning("Session not found in store", session_id=session_id)
            return None

        # Validate agent still exists
        if data.agent_name not in self._pool.manifest.agents:
            msg = "Session agent no longer exists"
            logger.warning(msg, session_id=session_id, agent=data.agent_name)
            return None

        # Create session agent via SessionPool (pool-level agents removed)
        if self._pool.session_pool is not None:
            session_agent = await self._pool.session_pool.sessions.get_or_create_session_agent(
                data.session_id, agent_name=data.agent_name
            )
        else:
            msg = "SessionPool is required for session resume"
            raise RuntimeError(msg)

        session = ACPSession(
            session_id=session_id,
            agent=session_agent,
            cwd=data.cwd or "",
            client=client,
            mcp_servers=mcp_servers,
            acp_agent=acp_agent,
            client_capabilities=client_capabilities or ClientCapabilities(),
            client_info=client_info,
            client_locale=client_locale,
            manager=self,
            subagent_display_mode=subagent_display_mode,
            raw_input_mode=raw_input_mode,
        )
        session.register_update_callback(self._on_commands_updated)
        await session.initialize()
        await session.initialize_mcp_servers()
        self._acp_sessions[session_id] = session
        if connection_id is not None:
            self._connection_sessions.setdefault(connection_id, set()).add(session_id)
        logger.info("Resumed ACP session", session_id=session_id)

        # Conversation history is loaded by SessionPool's get_or_create_session_agent()
        # when it creates the per-session agent. Loading history here would
        # pollute the shared agent (session.agent) across sessions.

        return session

    async def close_session(self, session_id: str, *, delete: bool = False) -> None:
        """Close and optionally delete a session.

        Checkpoint-aware close: when ``delete=True`` but the session has
        pending deferred calls (``pending_deferred_calls`` is non-empty),
        the session is preserved with ``status='checkpointed'`` instead of
        being deleted.  This allows the session to be resumed later.

        Args:
            session_id: Session identifier to close
            delete: Whether to also delete from persistent storage
        """
        session = self._acp_sessions.pop(session_id, None)

        if session:
            await session.close()
            logger.info("Closed ACP session", session_id=session_id)

        if delete and self.session_store:
            # Checkpoint-aware: if the session has pending deferred calls,
            # preserve it as "checkpointed" instead of deleting.
            data = await self.session_store.load(session_id)
            if data is not None and data.pending_deferred_calls:
                data = data.model_copy(update={"status": "checkpointed"})
                data.touch()
                await self.session_store.save(data)
                logger.info(
                    "Session checkpointed before close (pending deferred calls)",
                    session_id=session_id,
                    pending_call_count=len(data.pending_deferred_calls),
                )
            else:
                await self.session_store.delete(session_id)
                logger.info("Deleted session from store", session_id=session_id)

    async def update_session_agent(self, session_id: str, agent_name: str) -> None:
        """Update the agent for a session and persist.

        Args:
            session_id: Session identifier
            agent_name: New agent name
        """
        if not self._acp_sessions.get(session_id):
            return
        # Load, update, and save session data
        data = await self.session_store.load(session_id) if self.session_store else None
        if data and self.session_store:
            updated = data.with_agent(agent_name)
            await self.session_store.save(updated)

    async def list_sessions(self, *, active_only: bool = False) -> list[str]:
        """List session IDs.

        Delegates to SessionController.list_sessions() for lifecycle-aware
        session listing, then resolves ACPSession objects from _acp_sessions.

        Args:
            active_only: Only return currently active sessions

        Returns:
            List of session IDs
        """
        if active_only:
            return list(self._acp_sessions.keys())

        # Delegate to SessionController for lifecycle-aware session listing
        if self._session_controller is not None:
            controller_sessions = self._session_controller.list_sessions()
            controller_ids = {s.session_id for s in controller_sessions}
            return [sid for sid in controller_ids if sid in self._acp_sessions]

        if self.session_store:
            return await self.session_store.list_sessions()
        return []

    async def close_all_sessions(self) -> int:
        """Close all active sessions.

        This is used during pool hot-switching to cleanly shut down
        all sessions before swapping the pool.

        Returns:
            Number of sessions that were closed
        """
        sessions = list(self._acp_sessions.values())
        self._acp_sessions.clear()

        closed_count = 0
        for session in sessions:
            try:
                await session.close()
                closed_count += 1
            except Exception:
                logger.exception("Error closing session", session=session.session_id)
        logger.info("Closed all sessions.", count=closed_count)
        return closed_count

    async def close_all_sessions_for_connection(self, connection_id: str) -> None:
        """Close all sessions associated with a WebSocket connection.

        Called when a WebSocket connection is lost unexpectedly. Iterates
        all sessions tracked for this connection and closes each one via
        ``SessionController.close_session()`` (RunHandle lifecycle with
        timeout + cancel) and ``ACPSession.close()`` (ACP-specific cleanup).

        Idempotent: if ``connection_id`` is not in ``_connection_sessions``,
        returns immediately without error.

        Args:
            connection_id: The WebSocket connection ID (UUID4 hex string
                set on ``AgentSideConnection`` at accept time).
        """
        session_ids = self._connection_sessions.pop(connection_id, None)
        if session_ids is None:
            return

        controller = self._session_controller
        for session_id in session_ids:
            if controller is not None:
                try:
                    await controller.close_session(session_id)
                except Exception:
                    logger.exception(
                        "Failed to close session via SessionController",
                        session_id=session_id,
                        connection_id=connection_id,
                    )
            session = self._acp_sessions.pop(session_id, None)
            if session is not None:
                try:
                    await session.close()
                except Exception:
                    logger.exception(
                        "Failed to close ACPSession",
                        session_id=session_id,
                        connection_id=connection_id,
                    )
        logger.info(
            "Closed sessions for connection",
            connection_id=connection_id,
            session_count=len(session_ids),
        )

    async def __aenter__(self) -> Self:
        """Async context manager entry."""
        return self

    async def __aexit__(self, *args: object) -> None:
        """Async context manager exit - close all active sessions."""
        sessions = list(self._acp_sessions.values())
        self._acp_sessions.clear()
        for session in sessions:
            try:
                await session.close()
            except Exception:
                logger.exception("Error closing session", session=session.session_id)
        logger.info("Closed all %d ACP sessions", len(sessions))

    def _on_commands_updated(self) -> None:
        """Handle command updates by notifying all active sessions."""
        task = asyncio.create_task(self._update_all_sessions_commands())
        self._command_update_task = task

    async def _update_all_sessions_commands(self) -> None:
        """Update available commands for all active sessions."""
        sessions = list(self._acp_sessions.values())
        for session in sessions:
            try:
                await session.send_available_commands_update()
            except Exception:
                msg = "Failed to update commands"
                logger.exception(msg, session_id=session.session_id)
