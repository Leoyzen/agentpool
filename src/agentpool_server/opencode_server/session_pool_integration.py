"""OpenCode server integration with SessionPool orchestration.

Provides :class:`OpenCodeSessionPoolIntegration` which bridges OpenCode server
routes with the SessionPool orchestration layer. This is the canonical integration
point for routing messages through :meth:`SessionPool.receive_request` and
consuming events from the SessionPool's EventBus.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.agents.events.events import (
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool.orchestrator.run import RunStatus
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.mixins import ProtocolEventConsumerMixin
from agentpool_server.opencode_server.converters import (
    chat_message_to_opencode,
    opencode_to_chat_message,
)
from agentpool_server.opencode_server.event_adapter import OpenCodeEventAdapter
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    PartUpdatedEvent,
    SessionCreatedEvent,
    SessionErrorEvent,
    SessionStatus,
    SessionStatusEvent,
    TimeCreated,
    TimeCreatedUpdated,
    UserMessage,
)
from agentpool_server.opencode_server.models.parts import (
    TimeStart,
    TimeStartEnd,
    TimeStartEndCompacted,
    ToolPart,
    ToolStateCompleted,
    ToolStateError,
    ToolStateRunning,
)
from agentpool_server.opencode_server.models.session import Session


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.core import EventBus, EventEnvelope, SessionPool, SessionState
    from agentpool.orchestrator.run import RunHandle
    from agentpool.sessions.models import PendingDeferredCall, SessionData
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


async def get_messages_for_session(
    state: ServerState,
    session_id: str,
) -> list[MessageWithParts]:
    """Get messages for a session from SessionPool or fall back to ServerState.

    For subagent/child sessions (identified by ``parent_id``), the in-memory
    ``state.messages`` cache is consulted first because streaming parts are
    updated in-place on those objects and may be more recent than the
    SessionPool snapshot.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to get messages for.

    Returns:
        List of MessageWithParts for the session.
    """
    messages: list[MessageWithParts] = getattr(state, "messages", {}).get(session_id, []) or []

    # Fast-path: subagent sessions are streamed live into memory, so the
    # in-memory copy is always the most up-to-date.
    cached_session = state.sessions.get(session_id)
    is_subagent = cached_session is not None and cached_session.parent_id is not None
    if is_subagent:
        if messages:
            return messages

    session_pool = getattr(state.pool, "session_pool", None)
    if session_pool is not None:
        try:
            sp_messages = await session_pool.get_messages(session_id)
        except (KeyError, TypeError):
            sp_messages = []
        if sp_messages:
            agent = state.agent
            with contextlib.suppress(Exception):
                agent = await session_pool.sessions.get_or_create_session_agent(session_id)
            return [
                chat_message_to_opencode(
                    chat_msg,
                    session_id=session_id,
                    working_dir=state.working_dir,
                    agent_name=agent.name,
                    model_id=getattr(chat_msg, "model_name", None) or "sonnet",
                    provider_id=getattr(chat_msg, "provider_name", None) or "claude-code",
                )
                for chat_msg in sp_messages
            ]
    return messages


async def append_message_to_session(
    state: ServerState,
    session_id: str,
    msg: MessageWithParts,
) -> None:
    """Append a message to a session's history.

    Writes to SessionPool when the feature flag is enabled.
    Also writes to the in-memory messages dict when present for
    backward compatibility with tests and legacy code paths.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to append to.
        msg: The OpenCode message to append.
    """
    session_pool = None
    if hasattr(state, "pool") and state.pool is not None:
        session_pool = getattr(state.pool, "session_pool", None)
    if session_pool is not None:
        chat_msg = opencode_to_chat_message(msg, session_id=session_id)
        try:
            await session_pool.append_message(session_id, chat_msg)
        except (KeyError, TypeError):
            logger.warning(
                "Failed to append message to SessionPool",
                session_id=session_id,
                exc_info=True,
            )

    # Always mirror to the in-memory dict when present for backward compatibility
    messages = getattr(state, "messages", None)
    if messages is not None:
        messages.setdefault(session_id, [])
        messages[session_id].append(msg)


async def set_messages_for_session(
    state: ServerState,
    session_id: str,
    messages: list[MessageWithParts],
) -> None:
    """Replace all in-memory messages for a session.

    This is a bulk operation used after compaction/summarization when
    the UI-visible message list should be reset to a specific set.
    SessionPool storage is managed separately via storage.replace_conversation_messages.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to update.
        messages: The new message list.
    """
    in_memory_messages = getattr(state, "messages", None)
    if in_memory_messages is not None:
        in_memory_messages[session_id] = list(messages)


async def set_session_status(
    state: ServerState,
    session_id: str,
    status: SessionStatus,
) -> None:
    """Set the status of a session.

    Broadcasts ``SessionStatusEvent`` via ``ServerState`` directly.
    Falls back to the in-memory session_status dict for legacy code paths.

    Args:
        state: The OpenCode server state.
        session_id: The session to update.
        status: The new session status.
    """
    await state.broadcast_event(SessionStatusEvent.create(session_id, status))


async def get_session_status(
    state: ServerState,
    session_id: str,
) -> SessionStatus | None:
    """Get the current status of a session.

    Delegates to OpenCodeSessionPoolIntegration when the feature flag is
    enabled, otherwise falls back to the ServerState in-memory dictionary.

    Args:
        state: The OpenCode server state.
        session_id: The session to look up.

    Returns:
        The session status, or None if not found and the fallback is used.
    """
    integration: OpenCodeSessionPoolIntegration | None = getattr(
        state, "session_pool_integration", None
    )
    if integration is not None:
        return await integration.get_session_status(session_id)

    return getattr(state, "session_status", {}).get(session_id)


def _session_state_to_opencode(state: SessionState) -> Session:
    """Convert SessionPool SessionState to OpenCode Session model.

    Args:
        state: SessionState from SessionPool.

    Returns:
        OpenCode Session model.
    """
    import time

    from agentpool_storage.opencode_provider import helpers

    now_mono = time.monotonic()
    now_epoch = time.time()
    created_ms = int((now_epoch - (now_mono - state.created_at)) * 1000)
    updated_ms = int((now_epoch - (now_mono - state.last_active_at)) * 1000)
    directory = state.metadata.get("cwd", "")
    project_id = state.metadata.get("project_id", "")
    if not project_id and directory:
        project_id = helpers.compute_project_id(directory)
    if not project_id:
        project_id = "default"

    return Session(
        id=state.session_id,
        project_id=project_id,
        directory=directory,
        title=state.metadata.get("title", "New Session"),
        version="1",
        time=TimeCreatedUpdated(created=created_ms, updated=updated_ms),
        parent_id=state.parent_session_id,
    )


async def ensure_session(
    state: ServerState,
    session_id: str,
    parent_id: str | None = None,
) -> Session:
    """Ensure a session exists with the given ID.

    Resolution order (store-first, non-overwriting):

    1. **In-memory hit** — if the session already exists in
       ``state.sessions``, return it immediately (broadcasts
       ``session.updated`` so the TUI can upsert).

    2. **Store hit** — if the session is absent from memory but present
       in the session store, convert the stored ``SessionData`` to a UI
       ``Session``, register all in-memory runtime state (messages,
       status, input-provider), mark idle, and broadcast
       ``session.created`` + ``session.updated``.  **Does NOT** call
       ``store.save()`` because the data is already persisted.

    3. **Store miss** — fall back to creating a brand-new session and
       persisting it (original behaviour).

    Concurrent calls for the same ``session_id`` are serialized by a
    per-session lock so that only one in-memory ``Session`` object is
    created.

    Args:
        state: The OpenCode server state.
        session_id: Unique identifier for the session
        parent_id: Optional parent session ID for fork relationships

    Returns:
        The Session object (existing or newly created)
    """
    import asyncio

    from agentpool_server.opencode_server.converters import session_data_to_opencode
    from agentpool_server.opencode_server.models import SessionUpdatedEvent

    # --- Fast path: already in memory -----------------------------------
    if session_id in state.sessions:
        session = state.sessions[session_id]
        await state.broadcast_event(SessionUpdatedEvent.create(session))
        return session

    # --- Serialise concurrent callers for the same session_id -----------
    if session_id not in state.session_locks:
        state.session_locks[session_id] = asyncio.Lock()
    try:
        async with state.session_locks[session_id]:
            if session_id in state.sessions:
                session = state.sessions[session_id]
                await state.broadcast_event(SessionUpdatedEvent.create(session))
                return session

            # --- Store-first path ------------------------------------------
            session_data = None
            if (
                state.pool.session_pool is not None
                and state.pool.session_pool.sessions.store is not None
            ):
                session_data = await state.pool.session_pool.sessions.store.load(session_id)
            if session_data is None:
                session_data = await state.pool.storage.load_session(session_id)

            if session_data is not None:
                session = session_data_to_opencode(session_data)

                state.sessions[session_id] = session
                state.ensure_runtime_session_state(session_id)
                state.ensure_input_provider(session_id)
                await state.mark_session_idle(session_id)

                # --- Checkpoint restoration (Task 27) ---------------------------
                if session_data.status == "checkpointed":
                    await _restore_checkpoint_state(state, session_data, session_id)
                    logger.info(
                        "ensure_session: restored checkpointed session",
                        session_id=session_id,
                        pending_call_count=len(session_data.pending_deferred_calls),
                    )

                # Sync input_provider to SessionPool's SessionState for all sessions
                input_provider = state.ensure_input_provider(session_id)
                if state.pool.session_pool is not None:
                    sp_session = state.pool.session_pool.sessions.get_session(session_id)
                    if sp_session is not None:
                        sp_session.input_provider = input_provider

                from agentpool_server.opencode_server.models import (
                    SessionCreatedEvent,
                )

                await state.broadcast_event(SessionCreatedEvent.create(session))
                await state.broadcast_event(SessionUpdatedEvent.create(session))
                logger.info(
                    "ensure_session: loaded from store",
                    session_id=session_id,
                    parent_id=session_data.parent_id,
                )
                return session

            # --- Store-miss fallback: create new session -------------------
            return await _create_and_persist_session(state, session_id, parent_id)
    finally:
        state.session_locks.pop(session_id, None)


async def _create_and_persist_session(
    state: ServerState,
    session_id: str,
    parent_id: str | None,
) -> Session:
    """Create a brand-new session and persist it (store-miss fallback).

    Args:
        state: The OpenCode server state.
        session_id: Unique identifier for the session.
        parent_id: Optional parent session ID.

    Returns:
        The newly created and persisted ``Session``.
    """
    from agentpool_server.opencode_server.converters import opencode_to_session_data
    from agentpool_server.opencode_server.models import (
        Session,
        SessionCreatedEvent,
        SessionUpdatedEvent,
    )
    from agentpool_storage.opencode_provider import helpers

    now = now_ms()
    if parent_id is not None:
        parent_session = state.sessions.get(parent_id)
        if parent_session:
            project_id = parent_session.project_id
            directory = parent_session.directory
        else:
            project_id = helpers.compute_project_id(state.working_dir)
            directory = state.working_dir
    else:
        project_id = helpers.compute_project_id(state.working_dir)
        directory = state.working_dir
    session = Session(
        id=session_id,
        project_id=project_id,
        directory=directory,
        title="New Session",
        version="1",
        time=TimeCreatedUpdated(created=now, updated=now),
        parent_id=parent_id,
    )

    id_ = state.pool.manifest.config_file_path
    session_data = opencode_to_session_data(session, agent_name=state.agent.name, pool_id=id_)
    try:
        if state.pool.session_pool is not None and state.pool.session_pool.sessions.store:
            await state.pool.session_pool.sessions.store.save(session_data)
        else:
            await state.pool.storage.save_session(session_data)
    except Exception:
        logger.warning(
            "Failed to persist session to storage, degrading to in-memory",
            session_id=session_id,
            exc_info=True,
        )

    state.sessions[session_id] = session
    state.ensure_runtime_session_state(session_id)
    await state.mark_session_idle(session_id)

    # Sync input_provider to SessionPool's SessionState for all sessions
    input_provider = state.ensure_input_provider(session_id)
    if state.pool.session_pool is not None:
        sp_session = state.pool.session_pool.sessions.get_session(session_id)
        if sp_session is not None:
            sp_session.input_provider = input_provider

    await state.broadcast_event(SessionCreatedEvent.create(session))
    await state.broadcast_event(SessionUpdatedEvent.create(session))
    logger.info(
        "ensure_session: created new session",
        session_id=session_id,
        parent_id=parent_id,
    )

    return session


async def _restore_checkpoint_state(
    state: ServerState,
    session_data: SessionData,
    session_id: str,
) -> None:
    """Restore opencode runtime state from a checkpointed session.

    Reconstructs running ToolParts for pending deferred calls and
    restores the parent/child spawn graph topology.

    Args:
        state: The OpenCode server state.
        session_data: The persisted SessionData with checkpoint metadata.
        session_id: The session being restored.
    """
    _reconstruct_tool_parts_from_checkpoint(state, session_id, session_data.pending_deferred_calls)
    _restore_spawn_topology_from_checkpoint(state, session_data, session_id)


def _reconstruct_tool_parts_from_checkpoint(
    state: ServerState,
    session_id: str,
    pending_calls: list[PendingDeferredCall],
) -> None:
    """Reconstruct running ToolParts from pending deferred calls.

    Creates an assistant message (if one does not exist) and appends
    a ``ToolPart`` with ``ToolStateRunning`` for each pending deferred
    call. This restores the visual tool state in the OpenCode TUI so
    the user sees what tools were in-flight at checkpoint time.

    Args:
        state: The OpenCode server state.
        session_id: The session to reconstruct ToolParts for.
        pending_calls: Unresolved deferred tool calls from the checkpoint.
    """
    if not pending_calls:
        return

    from agentpool.utils import identifiers as identifier
    from agentpool.utils.time_utils import now_ms
    from agentpool_server.opencode_server.models.parts import (
        TimeStart,
        ToolPart,
        ToolStateRunning,
    )

    # Create an assistant message to hold the ToolParts
    assistant_msg_id = identifier.ascending("message")
    assistant_msg = MessageWithParts.assistant(
        message_id=assistant_msg_id,
        session_id=session_id,
        time=MessageTime(created=now_ms()),
        agent_name="agentpool",
        model_id="default",
        parent_id=session_id,
        provider_id="agentpool",
        path=MessagePath(cwd=state.working_dir, root=state.working_dir),
    )

    for call in pending_calls:
        ts = TimeStart(start=now_ms())
        running_state = ToolStateRunning(
            time=ts,
            input={
                "description": call.tool_name,
                "tool_call_id": call.tool_call_id,
            },
            metadata={"deferred": True, "deferred_strategy": call.deferred_strategy},
            title=call.tool_name,
        )
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg_id,
            session_id=session_id,
            tool=call.tool_name,
            call_id=call.tool_call_id,
            state=running_state,
        )
        assistant_msg.parts.append(tool_part)

    # Register in the in-memory message list
    messages = getattr(state, "messages", None)
    if messages is not None:
        messages.setdefault(session_id, [])
        messages[session_id].append(assistant_msg)


def _restore_spawn_topology_from_checkpoint(
    state: ServerState,
    session_data: SessionData,
    session_id: str,
) -> None:
    """Restore parent/child spawn graph from checkpoint metadata.

    Reads ``spawn_children`` from the session's metadata and stores it
    on ``state.checkpoint_spawn_graph`` so that
    :class:`OpenCodeSessionPoolIntegration` can reconstruct
    ``_children_of``, ``_child_to_parent``, and ``_child_spawns`` maps
    when the consumer starts.

    Args:
        state: The OpenCode server state.
        session_data: The persisted SessionData with checkpoint metadata.
        session_id: The parent session being restored.
    """
    spawn_children: list[str] = session_data.metadata.get("spawn_children", [])
    if not hasattr(state, "checkpoint_spawn_graph"):
        state.checkpoint_spawn_graph = {}  # type: ignore[attr-defined]
    state.checkpoint_spawn_graph[session_id] = list(spawn_children)  # type: ignore[attr-defined]
    logger.debug(
        "Restored spawn topology from checkpoint",
        session_id=session_id,
        child_count=len(spawn_children),
    )


class OpenCodeSessionPoolIntegration(ProtocolEventConsumerMixin):
    """Integration layer between OpenCode server routes and SessionPool.

    Encapsulates session lifecycle, message routing, event subscription,
    and status synchronization. Protocol handlers should create one instance
    and reuse it across requests.

    Args:
        session_pool: The SessionPool to route through.
        server_state: The OpenCode server state for broadcasting SSE events.
    """

    def __init__(self, session_pool: SessionPool, server_state: ServerState) -> None:
        """Initialize the integration with a SessionPool and ServerState."""
        super().__init__()
        self.session_pool = session_pool
        self.server_state = server_state
        # Per-session state for mixin hooks
        self._contexts: dict[str, EventProcessorContext] = {}
        self._adapters: dict[str, OpenCodeEventAdapter] = {}
        self._message_registered: dict[str, bool] = {}
        self._child_to_parent: dict[str, str] = {}
        self._child_spawns: dict[str, SpawnSessionStart] = {}
        self._children_of: dict[str, set[str]] = {}
        # Serialized context data for session resume (keyed by session_id).
        # Populated by external orchestrator before start_event_consumer() is
        # called for a resumed session. Consumed (popped) by _before_consumer_loop.
        self._resume_contexts: dict[str, dict[str, Any]] = {}

    async def create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        **metadata: Any,
    ) -> Any:
        """Create a session via SessionPool and start its status bridge.

        Uses get_or_create_session so the call is idempotent: bridge and
        consumer are only started when the session is actually new.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state from the SessionPool.
        """
        state, was_created = await self.session_pool.sessions.get_or_create_session(
            session_id, agent_name, **metadata
        )
        if was_created:
            await self._start_event_consumer(session_id)

            # Broadcast session.created event so OpenCode clients can upsert
            session = _session_state_to_opencode(state)
            await self.server_state.broadcast_event(SessionCreatedEvent.create(session))

        return state

    async def fork_session(
        self,
        parent_session_id: str,
        new_session_id: str,
        agent_name: str | None = None,
    ) -> Any:
        """Fork a session, creating a child with a parent reference.

        Uses get_or_create_session so the call is idempotent: bridge is
        only started when the session is actually new.

        Args:
            parent_session_id: The parent session ID.
            new_session_id: The new child session ID.
            agent_name: Name of the agent for the child session.

        Returns:
            The child session state.
        """
        parent_state = self.session_pool.sessions.get_session(parent_session_id)
        metadata: dict[str, Any] = {}
        if parent_state is not None:
            # get_or_create_session may nest kwargs under a "metadata" key;
            # unwrap one level so the child inherits the actual metadata dict.
            raw = parent_state.metadata
            metadata = dict(raw.get("metadata", raw))
        state, was_created = await self.session_pool.sessions.get_or_create_session(
            new_session_id,
            agent_name=agent_name,
            parent_session_id=parent_session_id,
            **metadata,
        )
        if was_created:
            pass  # Forked session inherits parent's event consumer
        return state

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up its resources.

        Stops the session-scoped event consumer and status bridge,
        then delegates to SessionPool.close_session().

        Args:
            session_id: The session to close.
        """
        await self._stop_event_consumer(session_id)
        await self.session_pool.close_session(session_id)

    async def route_message(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        input_provider: Any | None = None,
        agent_name: str | None = None,
        **kwargs: Any,
    ) -> RunHandle | None:
        """Route a message through SessionPool.receive_request().

        Creates the session if it does not yet exist. Stores the input
        provider on the session for auto-resume.

        If the session is checkpointed and ``deferred_tool_results`` is provided
        (via ``**kwargs``), :meth:`SessionPool.resume_session` is called first to
        replay deferred results into the agent loop before accepting new input.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: "when_idle" to queue, "asap" to inject into active turn.
            input_provider: Optional input provider for the agent.
            agent_name: Agent to bind if the session must be created.
            **kwargs: Additional arguments passed to the turn runner.
                Supports ``deferred_tool_results`` for checkpoint replay.

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        # --- Checkpoint replay: resume session before new input ----------
        deferred_results = kwargs.pop("deferred_tool_results", None)
        if deferred_results is not None:
            if self.session_pool.sessions.store is not None:
                stored = await self.session_pool.sessions.store.load(session_id)
                if stored is not None and stored.status == "checkpointed":
                    await self.session_pool.resume_session(
                        session_id,
                        deferred_results,
                        source="opencode_route_message",
                    )

        session_state = self.session_pool.sessions.get_session(session_id)
        if session_state is None:
            await self.create_session(session_id, agent_name=agent_name)
        else:
            # Ensure event consumer is running even for pre-existing sessions.
            # Sessions created via other paths (e.g. get_or_load_session) don't
            # have the consumer started, which would leave EventBus events
            # unconsumed and the frontend blank.
            await self._start_event_consumer(session_id)

        if input_provider is not None:
            session_state = self.session_pool.sessions.get_session(session_id)
            if session_state is not None:
                session_state.input_provider = input_provider

        return await self.session_pool.receive_request(
            session_id=session_id,
            content=content,
            priority=priority,
            input_provider=input_provider,
            **kwargs,
        )

    async def abort_session(self, session_id: str) -> None:
        """Abort the active run for a session.

        Args:
            session_id: The session whose run should be cancelled.
        """
        self.session_pool.sessions.cancel_run_for_session(session_id)
        await self.server_state.broadcast_event(
            SessionErrorEvent.create(
                session_id=session_id,
                error_name="SessionAborted",
                error_message="Session was aborted by the user",
            )
        )

    async def attach_input_provider(
        self,
        session_id: str,
        input_provider: Any,
    ) -> None:
        """Attach an input provider to a session.

        Args:
            session_id: The session to attach the provider to.
            input_provider: The input provider instance.
        """
        session_state = self.session_pool.sessions.get_session(session_id)
        if session_state is not None:
            session_state.input_provider = input_provider

    async def subscribe_to_events(self, session_id: str) -> AsyncIterator[Any]:
        """Subscribe to session events and yield converted OpenCode events.

        Creates a minimal EventProcessorContext so that AgentPool events
        can be converted to OpenCode SSE events via OpenCodeEventAdapter.

        Args:
            session_id: The session to subscribe to.

        Yields:
            OpenCode Event objects.
        """
        assistant_msg_id = identifier.ascending("message")
        assistant_msg = MessageWithParts(
            info=UserMessage(
                id=assistant_msg_id,
                session_id=session_id,
                time=TimeCreated.now(),
            )
        )
        ctx = EventProcessorContext(
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            assistant_msg=assistant_msg,
            state=self.server_state,
            working_dir=self.server_state.working_dir,
        )
        event_adapter = OpenCodeEventAdapter(ctx)
        event_stream = await self.session_pool.event_bus.subscribe(session_id)

        try:
            async for event in event_stream:
                async for oc_event in event_adapter.convert_event(event.event):
                    yield oc_event
        finally:
            await self.session_pool.event_bus.unsubscribe(session_id, event_stream)

    async def get_session_status(self, session_id: str) -> SessionStatus | None:
        """Get the current status of a session.

        Checks the SessionPool for active runs and falls back to the
        server state's session status cache.

        Args:
            session_id: The session to look up.

        Returns:
            The session status, or a default idle status if not found.
        """
        session = self.session_pool.sessions.get_session(session_id)
        if session is not None:
            run_id = session.current_run_id
            if run_id is not None:
                run_handle = self.session_pool.sessions._runs.get(run_id)
                if run_handle is not None and run_handle.status in (
                    RunStatus.pending,
                    RunStatus.running,
                ):
                    return SessionStatus(type="busy")

        return SessionStatus(type="idle")

    async def shutdown(self) -> None:
        """Shutdown the integration and stop all consumers and bridges."""
        for session_id in list(self._session_groups.keys()):
            try:
                await self.stop_event_consumer(session_id)
            except Exception:
                logger.exception(
                    "Failed to stop event consumer during shutdown",
                    session_id=session_id,
                )
        await self.session_pool.shutdown()

    def set_session_context_data(self, session_id: str, data: dict[str, Any]) -> None:
        """Store serialized EventProcessorContext data for session resume.

        The orchestrator calls this before :meth:`start_event_consumer` for
        a resumed session.  The data is consumed (popped) by
        :meth:`_before_consumer_loop` and used to reconstruct the context
        instead of creating a fresh one.

        Args:
            session_id: The session to store context data for.
            data: Serialized context dict from :meth:`EventProcessorContext.serialize`.
        """
        self._resume_contexts[session_id] = data

    def get_session_context_data(self, session_id: str) -> dict[str, Any] | None:
        """Retrieve and consume serialized EventProcessorContext data for resume.

        Returns the stored data and removes it so it is consumed exactly once.

        Args:
            session_id: The session to retrieve context data for.

        Returns:
            The serialized context dict, or ``None`` if no resume data is set.
        """
        return self._resume_contexts.pop(session_id, None)

    # ------------------------------------------------------------------
    # ProtocolEventConsumerMixin hooks
    # ------------------------------------------------------------------

    @property
    def event_bus(self) -> EventBus:
        """Return the EventBus instance to subscribe to."""
        return self.session_pool.event_bus

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Overridden to "session" so that only the exact session's events are
        consumed. Child session events are handled by separate consumers
        created in response to SpawnSessionStart (see _on_spawn_session_start).

        Returns:
            The subscription scope string.
        """
        return "session"

    async def _before_consumer_loop(self, session_id: str) -> None:
        """Set up per-session context before the consumer loop starts.

        On a fresh session, creates a new :class:`EventProcessorContext` and
        :class:`OpenCodeEventAdapter`.  On a **resumed** session (where
        :meth:`set_session_context_data` was called before ``start_event_consumer``),
        restores the context from the serialized data so that accumulated
        text, tool parts, and tracking state are preserved.

        !!! note
            Restored contexts are NOT re-broadcast to the frontend.
            The parts already exist on the client from the original session.

        Args:
            session_id: The session whose consumer is starting.
        """
        # --- Check for persisted resume context -------------------------------
        resume_data = self.get_session_context_data(session_id)
        if resume_data:
            ctx = EventProcessorContext.deserialize(
                resume_data,
                state=self.server_state,
                working_dir=self.server_state.working_dir,
            )
            event_adapter = OpenCodeEventAdapter(ctx)
            self._contexts[session_id] = ctx
            self._adapters[session_id] = event_adapter
            # On resume, the assistant message was already registered in the
            # original session. Mark it as such so _handle_event does not
            # re-broadcast MessageUpdatedEvent.
            self._message_registered[session_id] = True
            logger.info(
                "Restored EventProcessorContext from persisted data",
                session_id=session_id,
                response_text_len=len(ctx.response_text),
            )
            return

        # --- Fresh context (original behaviour) -------------------------------
        assistant_msg_id = identifier.ascending("message")
        assistant_msg = MessageWithParts.assistant(
            message_id=assistant_msg_id,
            session_id=session_id,
            time=MessageTime(created=now_ms()),
            agent_name="agentpool",
            model_id="default",
            parent_id=session_id,
            provider_id="agentpool",
            path=MessagePath(cwd=self.server_state.working_dir, root=self.server_state.working_dir),
        )
        ctx = EventProcessorContext(
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            assistant_msg=assistant_msg,
            state=self.server_state,
            working_dir=self.server_state.working_dir,
        )
        event_adapter = OpenCodeEventAdapter(ctx)
        self._contexts[session_id] = ctx
        self._adapters[session_id] = event_adapter
        self._message_registered[session_id] = False

    async def _on_spawn_session_start(self, session_id: str, envelope: EventEnvelope) -> None:
        """Handle SpawnSessionStart by creating ToolPart and child consumer.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope containing the spawn session start event.
        """
        try:
            event = envelope.event
            if not isinstance(event, SpawnSessionStart):
                return
            child_id = event.child_session_id
            if not child_id or child_id == session_id:
                return

            ctx = self._contexts.get(session_id)
            if ctx is None:
                return

            await self._ensure_child_session_visible(session_id, event)

            # Ensure assistant message is registered before ToolPart creation
            if not self._message_registered.get(session_id, False):
                await append_message_to_session(self.server_state, session_id, ctx.assistant_msg)
                await self.server_state.broadcast_event(
                    MessageUpdatedEvent.create(ctx.assistant_msg.info)
                )
                self._message_registered[session_id] = True

            # Distinguish parent vs child events.  Child events arrive
            # raw via scope="descendants".
            # Use envelope.source_session_id because many streaming events
            # (e.g.PartDeltaEvent from pydantic-ai) do not carry a
            # session_id attribute on the payload itself.
            event_session_id = envelope.source_session_id
            is_child_event = event_session_id != session_id

            tool_part = await self._create_subagent_tool_part(session_id, event)
            if tool_part is not None:
                subagent_key = f"{event.depth}:{event.source_name}:{child_id}"
                ctx.add_subagent_tool_part(subagent_key, tool_part)

            # Track parent-child relationship for later ToolPart updates
            self._child_to_parent[child_id] = session_id
            self._child_spawns[child_id] = event
            self._children_of.setdefault(session_id, set()).add(child_id)

            # Start dedicated consumer for the child session
            await self.start_event_consumer(child_id)
        except Exception:
            logger.exception(
                "SpawnSessionStart handler failed",
                session_id=session_id,
                child_session_id=getattr(envelope.event, "child_session_id", None),
            )

    async def _ensure_child_session_visible(
        self,
        parent_session_id: str,
        spawn_event: SpawnSessionStart,
    ) -> None:
        """Create OpenCode-visible child session scaffolding for task navigation.

        SessionPool owns the execution session. OpenCode also needs a session
        model and at least the delegated prompt in message storage so the TUI
        can open the child task immediately, even before the child stream emits
        its first token.
        """
        child_session_id = spawn_event.child_session_id
        await ensure_session(
            self.server_state,
            child_session_id,
            parent_id=parent_session_id,
        )

        existing_messages = await get_messages_for_session(self.server_state, child_session_id)
        if existing_messages:
            return

        prompt = spawn_event.metadata.get("prompt") or spawn_event.description
        if not prompt:
            prompt = f"Run {spawn_event.source_name or 'subagent'} task"

        user_msg = UserMessage(
            id=identifier.ascending("message"),
            session_id=child_session_id,
            time=TimeCreated.now(),
            agent=spawn_event.source_name or "subagent",
            model=None,
        )
        user_msg_with_parts = MessageWithParts(info=user_msg)
        text_part = user_msg_with_parts.add_text_part(prompt)

        await append_message_to_session(self.server_state, child_session_id, user_msg_with_parts)
        await self.server_state.broadcast_event(MessageUpdatedEvent.create(user_msg))
        await self.server_state.broadcast_event(PartUpdatedEvent.create(text_part))

    async def _handle_event(self, session_id: str, envelope: EventEnvelope) -> None:
        """Handle a single event from the EventBus.

        Distinguishes parent vs child events (via the child-to-parent mapping),
        updates parent ToolParts on child completion/error, and converts all
        events to OpenCode SSE events via the adapter.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope to handle.
        """
        try:
            event = envelope.event

            # SpawnSessionStart is handled in _on_spawn_session_start; skip here
            if isinstance(event, SpawnSessionStart):
                return

            # Check if this event originated from a child session.
            # Child events are handled by the child consumer (started via
            # _on_spawn_session_start). We only process parent events here.
            is_child_event = envelope.source_session_id != session_id

            if is_child_event:
                # Child completion/error: update parent ToolPart, then skip.
                # Other child events (PartDeltaEvent etc.) are handled by the
                # dedicated child consumer started in _on_spawn_session_start.
                parent_id = self._child_to_parent.get(envelope.source_session_id)
                if parent_id is not None:
                    spawn = self._child_spawns.get(envelope.source_session_id)
                    if isinstance(event, StreamCompleteEvent) and spawn is not None:
                        await self._update_parent_toolpart(
                            parent_session_id=parent_id,
                            child_session_id=envelope.source_session_id,
                            spawn_event=spawn,
                            event=event,
                        )
                    elif isinstance(event, RunErrorEvent) and spawn is not None:
                        await self._update_parent_toolpart_error(
                            parent_session_id=parent_id,
                            child_session_id=envelope.source_session_id,
                            spawn_event=spawn,
                            event=event,
                        )
                return

            # When scope="session", child events are received by the child
            # consumer itself (not the parent consumer). In that case,
            # session_id == envelope.source_session_id, so is_child_event is
            # False.  We still need to update the parent ToolPart when this
            # session is a child session.
            parent_id = self._child_to_parent.get(session_id)
            if parent_id is not None:
                spawn = self._child_spawns.get(session_id)
                if isinstance(event, StreamCompleteEvent) and spawn is not None:
                    await self._update_parent_toolpart(
                        parent_session_id=parent_id,
                        child_session_id=session_id,
                        spawn_event=spawn,
                        event=event,
                    )
                elif isinstance(event, RunErrorEvent) and spawn is not None:
                    await self._update_parent_toolpart_error(
                        parent_session_id=parent_id,
                        child_session_id=session_id,
                        spawn_event=spawn,
                        event=event,
                    )

            # Handle run lifecycle events for session status
            match event:
                case RunStartedEvent():
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="busy")
                    )
                case StreamCompleteEvent():
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="idle")
                    )
                case RunFailedEvent(exception=exc):
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="idle")
                    )
                    if isinstance(exc, Exception) and not isinstance(exc, asyncio.CancelledError):
                        await self.server_state.broadcast_event(
                            SessionErrorEvent.from_exception(exc, session_id=session_id)
                        )
                case _:
                    pass

            ctx = self._contexts.get(session_id)
            if ctx is None:
                return

            # Register assistant message on first non-spawn event
            if not self._message_registered.get(session_id, False):
                await append_message_to_session(self.server_state, session_id, ctx.assistant_msg)
                await self.server_state.broadcast_event(
                    MessageUpdatedEvent.create(ctx.assistant_msg.info)
                )
                self._message_registered[session_id] = True

            adapter = self._adapters.get(session_id)
            if adapter is None:
                return

            async for oc_event in adapter.convert_event(event):
                await self.server_state.broadcast_event(oc_event)
        except Exception:
            logger.exception(
                "Event handler failed",
                session_id=session_id,
                event_type=type(envelope.event).__name__,
            )

    async def _after_consumer_loop(self, session_id: str) -> None:
        """Clean up per-session context after the consumer loop exits.

        Args:
            session_id: The session whose consumer has stopped.
        """
        # Stop any child consumers that were started from this session
        for child_id in list(self._children_of.get(session_id, [])):
            await self.stop_event_consumer(child_id)
        self._children_of.pop(session_id, None)

        # Clean up per-session state
        self._contexts.pop(session_id, None)
        self._adapters.pop(session_id, None)
        self._message_registered.pop(session_id, None)
        self._child_to_parent.pop(session_id, None)
        self._child_spawns.pop(session_id, None)

    # ------------------------------------------------------------------
    # Backward-compatible wrappers (used by tests)
    # ------------------------------------------------------------------

    async def _start_event_consumer(self, session_id: str) -> None:
        """Backward-compatible wrapper for the mixin's start_event_consumer."""
        await self.start_event_consumer(session_id)
        logger.info("Started session-scoped event consumer", session_id=session_id)

    async def _stop_event_consumer(self, session_id: str) -> None:
        """Backward-compatible wrapper for the mixin's stop_event_consumer."""
        await self.stop_event_consumer(session_id)
        logger.info("Stopped session-scoped event consumer", session_id=session_id)

    async def _create_subagent_tool_part(
        self,
        parent_session_id: str,
        spawn_event: SpawnSessionStart,
    ) -> ToolPart | None:
        """Create a ToolPart in the parent session representing a subagent.

        This replaces the ToolPart creation that previously happened inside
        EventProcessor._process_subagent_event when events were wrapped in
        SubAgentEvent.

        Args:
            parent_session_id: The parent session ID.
            spawn_event: The spawn event containing subagent metadata.

        Returns:
            The created ToolPart, or None if one already exists for this child.
        """
        # Find the parent session's latest assistant message from in-memory state
        # (not via get_messages_for_session, which may return copies when
        # SessionPool message storage is enabled).
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            logger.warning(
                "No assistant message found for parent session %s, skipping ToolPart creation",
                parent_session_id,
            )
            return None

        # Check if ToolPart already exists for this child session
        child_session_id = spawn_event.child_session_id
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and part.metadata is not None
                and part.metadata.get("sessionId") == child_session_id
            ):
                logger.debug("ToolPart already exists for child session %s", child_session_id)
                return None

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        ts = TimeStart(start=now_ms())
        running_state = ToolStateRunning(
            time=ts,
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            metadata={"sessionId": child_session_id, "title": tool_title},
            title=tool_title,
        )
        tool_part = ToolPart(
            id=identifier.ascending("part"),
            message_id=assistant_msg.info.id,
            session_id=parent_session_id,
            tool="task",
            call_id=identifier.ascending("part"),
            state=running_state,
        )
        assistant_msg.parts.append(tool_part)
        await self.server_state.broadcast_event(PartUpdatedEvent.create(tool_part))
        logger.debug(
            "Created ToolPart for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )
        return tool_part

    async def _update_parent_toolpart(
        self,
        parent_session_id: str,
        child_session_id: str,
        spawn_event: SpawnSessionStart,
        event: StreamCompleteEvent[Any],
    ) -> None:
        """Update parent ToolPart to Completed when child subagent finishes.

        Args:
            parent_session_id: The parent session ID.
            child_session_id: The child session ID.
            spawn_event: The spawn event containing subagent metadata.
            event: The StreamCompleteEvent from the child.
        """
        # Find the parent session's latest assistant message from in-memory state
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            return

        # Find the ToolPart for this child session
        tool_part = None
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and hasattr(part.state, "metadata")
                and isinstance(part.state.metadata, dict)
                and part.state.metadata.get("sessionId") == child_session_id
            ):
                tool_part = part
                break

        if tool_part is None:
            logger.warning(
                "No ToolPart found for child session %s in parent %s",
                child_session_id,
                parent_session_id,
            )
            return

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        complete_msg = event.message
        content = str(complete_msg.content) if complete_msg.content else "(no output)"

        start_time = (
            tool_part.state.time.start
            if isinstance(tool_part.state, ToolStateRunning)
            else now_ms()
        )
        completed_state = ToolStateCompleted(
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            output=content,
            title=tool_title,
            metadata={"sessionId": child_session_id, "title": tool_title},
            time=TimeStartEndCompacted(start=start_time, end=now_ms()),
        )
        updated = ToolPart(
            id=tool_part.id,
            message_id=tool_part.message_id,
            session_id=tool_part.session_id,
            tool=tool_part.tool,
            call_id=tool_part.call_id,
            state=completed_state,
        )

        # Replace the old part in the message
        for i, part in enumerate(assistant_msg.parts):
            if part.id == tool_part.id:
                assistant_msg.parts[i] = updated
                break

        await self.server_state.broadcast_event(PartUpdatedEvent.create(updated))
        logger.debug(
            "Updated ToolPart to Completed for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )

    async def _update_parent_toolpart_error(
        self,
        parent_session_id: str,
        child_session_id: str,
        spawn_event: SpawnSessionStart,
        event: RunErrorEvent,
    ) -> None:
        """Update parent ToolPart to Error when child subagent fails.

        Args:
            parent_session_id: The parent session ID.
            child_session_id: The child session ID.
            spawn_event: The spawn event containing subagent metadata.
            event: The RunErrorEvent from the child.
        """
        # Find the parent session's latest assistant message from in-memory state
        messages = getattr(self.server_state, "messages", {}).get(parent_session_id, []) or []
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            return

        # Find the ToolPart for this child session
        tool_part = None
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and hasattr(part.state, "metadata")
                and isinstance(part.state.metadata, dict)
                and part.state.metadata.get("sessionId") == child_session_id
            ):
                tool_part = part
                break

        if tool_part is None:
            return

        source_name = spawn_event.source_name or "subagent"
        tool_title = source_name
        error_msg = event.message or "Unknown error"

        start_time = (
            tool_part.state.time.start
            if isinstance(tool_part.state, ToolStateRunning)
            else now_ms()
        )
        error_state = ToolStateError(
            error=error_msg,
            input={
                "description": tool_title,
                "subagent_type": tool_title,
                "prompt": spawn_event.metadata.get("prompt", ""),
            },
            metadata={"sessionId": child_session_id, "title": tool_title},
            time=TimeStartEnd(start=start_time, end=now_ms()),
        )
        updated = ToolPart(
            id=tool_part.id,
            message_id=tool_part.message_id,
            session_id=tool_part.session_id,
            tool=tool_part.tool,
            call_id=tool_part.call_id,
            state=error_state,
        )

        # Replace the old part in the message
        for i, part in enumerate(assistant_msg.parts):
            if part.id == tool_part.id:
                assistant_msg.parts[i] = updated
                break

        await self.server_state.broadcast_event(PartUpdatedEvent.create(updated))
        logger.debug(
            "Updated ToolPart to Error for child session %s in parent %s",
            child_session_id,
            parent_session_id,
        )
