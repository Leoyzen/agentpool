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
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
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
    SessionStatus,
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
from agentpool_server.opencode_server.status_bridge import SessionStatusBridge


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.core import SessionPool, SessionState
    from agentpool.orchestrator.run import RunHandle
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


def _use_session_pool_for_messages(state: ServerState) -> bool:
    """Check if SessionPool should be used for messages."""
    if state.config is None:
        return True
    return getattr(state.config, "use_session_pool_for_messages", True)


def _use_session_pool_for_status(state: ServerState) -> bool:
    """Check if SessionPool should be used for session status."""
    if state.config is None:
        return True
    return getattr(state.config, "use_session_pool_for_status", True)


async def get_messages_for_session(
    state: ServerState,
    session_id: str,
) -> list[MessageWithParts]:
    """Get messages for a session from SessionPool or fall back to ServerState.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to get messages for.

    Returns:
        List of MessageWithParts for the session.
    """
    if _use_session_pool_for_messages(state):
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
    return state.messages.get(session_id, [])


async def append_message_to_session(
    state: ServerState,
    session_id: str,
    msg: MessageWithParts,
) -> None:
    """Append a message to a session's history.

    Writes to SessionPool when the feature flag is enabled, and also
    appends to ``state.messages`` for backward compatibility during
    the transition period.

    Args:
        state: The OpenCode server state.
        session_id: The session ID to append to.
        msg: The OpenCode message to append.
    """
    if _use_session_pool_for_messages(state):
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
    # Always keep state.messages in sync during transition
    state.messages.setdefault(session_id, []).append(msg)


async def set_session_status(
    state: ServerState,
    session_id: str,
    status: SessionStatus,
) -> None:
    """Set the status of a session.

    Uses SessionStatusBridge when the feature flag is enabled, otherwise
    falls back to the ServerState in-memory dictionary.

    Args:
        state: The OpenCode server state.
        session_id: The session to update.
        status: The new session status.
    """
    state.session_status[session_id] = status


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


class OpenCodeSessionPoolIntegration:
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
        self.session_pool = session_pool
        self.server_state = server_state
        self._status_bridges: dict[str, SessionStatusBridge] = {}
        self._event_consumers: dict[str, asyncio.Task[Any]] = {}

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
            await self._start_status_bridge(session_id)
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
        state, was_created = await self.session_pool.sessions.get_or_create_session(
            new_session_id,
            agent_name=agent_name,
            parent_session_id=parent_session_id,
        )
        if was_created:
            await self._start_status_bridge(new_session_id)
        return state

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up its resources.

        Stops the session-scoped event consumer and status bridge,
        then delegates to SessionPool.close_session().

        Args:
            session_id: The session to close.
        """
        await self._stop_event_consumer(session_id)
        await self._stop_status_bridge(session_id)
        await self.session_pool.close_session(session_id)

    async def route_message(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        input_provider: Any | None = None,
        **kwargs: Any,
    ) -> RunHandle | None:
        """Route a message through SessionPool.receive_request().

        Creates the session if it does not yet exist. Stores the input
        provider on the session for auto-resume.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: "when_idle" to queue, "asap" to inject into active turn.
            input_provider: Optional input provider for the agent.
            **kwargs: Additional arguments passed to the turn runner.

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        session_state = self.session_pool.sessions.get_session(session_id)
        if session_state is None:
            await self.create_session(session_id)
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
        event_queue = await self.session_pool.event_bus.subscribe(session_id)

        try:
            while True:
                event = await event_queue.get()
                if event is None:
                    break
                async for oc_event in event_adapter.convert_event(event):
                    yield oc_event
        finally:
            await self.session_pool.event_bus.unsubscribe(session_id, event_queue)

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
                if run_handle is not None and run_handle.status.value in ("pending", "running"):
                    return SessionStatus(type="busy")

        status = self.server_state.session_status.get(session_id)
        if status is None:
            status = SessionStatus(type="idle")
            self.server_state.session_status[session_id] = status
        return status

    async def shutdown(self) -> None:
        """Shutdown the integration and stop all consumers and bridges."""
        for session_id in list(self._event_consumers.keys()):
            try:
                await self._stop_event_consumer(session_id)
            except Exception:
                logger.exception("Failed to stop event consumer during shutdown", session_id=session_id)
        for session_id in list(self._status_bridges.keys()):
            try:
                await self._stop_status_bridge(session_id)
            except Exception:
                logger.exception("Failed to stop status bridge during shutdown", session_id=session_id)
        await self.session_pool.shutdown()

    async def _start_status_bridge(self, session_id: str) -> None:
        """Start a SessionStatusBridge for a session.

        Args:
            session_id: The session to monitor.
        """
        if session_id in self._status_bridges:
            return
        bridge = SessionStatusBridge(
            server_state=self.server_state,
            session_id=session_id,
            event_bus=self.session_pool.event_bus,
        )
        self._status_bridges[session_id] = bridge
        await bridge.start()

    async def _stop_status_bridge(self, session_id: str) -> None:
        """Stop the SessionStatusBridge for a session.

        Args:
            session_id: The session to stop monitoring.
        """
        bridge = self._status_bridges.pop(session_id, None)
        if bridge is not None:
            await bridge.stop()

    async def _start_event_consumer(self, session_id: str) -> None:
        """Start a session-scoped EventBus consumer for a session.

        The consumer runs for the entire session lifecycle, converting
        AgentPool events to OpenCode SSE events via EventBus subscription.

        If a previous consumer task exists but is done (e.g. crashed), it is
        cleaned up and a new consumer is started.

        Args:
            session_id: The session to start consuming events for.
        """
        existing = self._event_consumers.get(session_id)
        if existing is not None:
            if not existing.done():
                return
            # Clean up finished/crashed task before starting a new one
            self._event_consumers.pop(session_id, None)
        task = asyncio.create_task(
            self._event_consumer_loop(session_id),
            name=f"event_consumer_{session_id}",
        )
        self._event_consumers[session_id] = task
        logger.info("Started session-scoped event consumer", session_id=session_id)

    async def _stop_event_consumer(self, session_id: str) -> None:
        """Stop the session-scoped EventBus consumer for a session.

        Args:
            session_id: The session to stop consuming events for.
        """
        task = self._event_consumers.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        logger.info("Stopped session-scoped event consumer", session_id=session_id)

    async def _event_consumer_loop(self, session_id: str) -> None:
        """Consume events from EventBus and broadcast as OpenCode SSE events.

        Subscribes with ``scope="descendants"`` so that child session events
        (e.g. subagent output) are also received and forwarded.

        Handles ``SpawnSessionStart`` by creating child-session consumers
        recursively so nested subagents also stream to the frontend.

        Also handles child-session completion events (StreamCompleteEvent /
        RunErrorEvent) to update the parent session's ToolPart, since
        TurnRunner no longer wraps child events in SubAgentEvent.

        Args:
            session_id: The session whose events to consume.
        """
        queue = await self.session_pool.event_bus.subscribe(
            session_id, scope="descendants"
        )

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
        child_tasks: dict[str, asyncio.Task[Any]] = {}
        message_registered = False
        # Track child spawns so we can update parent ToolParts on completion
        child_spawns: dict[str, SpawnSessionStart] = {}

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break

                # Spawn child-session consumers for nested subagents
                if isinstance(event, SpawnSessionStart):
                    # Record spawn info for later ToolPart updates
                    child_spawns[event.child_session_id] = event
                    # Ensure assistant message is registered before creating
                    # ToolPart, since _create_subagent_tool_part looks it up in
                    # server_state.messages.
                    if not message_registered:
                        self.server_state.messages.setdefault(session_id, []).append(assistant_msg)
                        await self.server_state.broadcast_event(MessageUpdatedEvent.create(assistant_msg.info))
                        message_registered = True
                    # Create ToolPart in parent session before spawning child
                    tool_part = await self._create_subagent_tool_part(session_id, event)
                    # Also register in EventProcessorContext so SubAgentEvent
                    # handling can find and update the ToolPart later.
                    if tool_part is not None:
                        subagent_key = f"{event.depth}:{event.source_name}:{event.child_session_id}"
                        event_adapter.context.add_subagent_tool_part(subagent_key, tool_part)
                    child_task = asyncio.create_task(
                        self._event_consumer_loop(event.child_session_id),
                        name=f"event_consumer_{event.child_session_id}",
                    )
                    child_tasks[event.child_session_id] = child_task
                    continue

                # Distinguish parent vs child events.  With
                # TurnRunner._maybe_wrap_event removed, child events arrive
                # raw via scope="descendants".
                event_session_id = getattr(event, "session_id", None)
                is_child_event = (
                    event_session_id is not None and event_session_id != session_id
                )

                if is_child_event:
                    # For child completion events, update the parent ToolPart
                    # before letting the child consumer handle them.
                    child_id: str = event_session_id  # type: ignore[assignment]
                    if isinstance(event, StreamCompleteEvent):
                        spawn = child_spawns.get(child_id)
                        if spawn is not None:
                            await self._update_parent_toolpart(
                                parent_session_id=session_id,
                                child_session_id=child_id,
                                spawn_event=spawn,
                                event=event,
                            )
                    elif isinstance(event, RunErrorEvent):
                        spawn = child_spawns.get(child_id)
                        if spawn is not None:
                            await self._update_parent_toolpart_error(
                                parent_session_id=session_id,
                                child_session_id=child_id,
                                spawn_event=spawn,
                                event=event,
                            )
                    # Child consumer (subscribed to the child session)
                    # will render the child UI, so parent skips the rest.
                    continue

                # Register message on first non-spawn event so the TUI
                # can render parts. Without this, PartUpdatedEvents are
                # ignored because the message store lacks the entry.
                if not message_registered:
                    self.server_state.messages.setdefault(session_id, []).append(assistant_msg)
                    await self.server_state.broadcast_event(MessageUpdatedEvent.create(assistant_msg.info))
                    message_registered = True

                async for oc_event in event_adapter.convert_event(event):
                    await self.server_state.broadcast_event(oc_event)
        except asyncio.CancelledError:
            logger.debug("Event consumer cancelled", session_id=session_id)
            raise
        except Exception:
            logger.exception("Event consumer loop failed", session_id=session_id)
        finally:
            # Cancel and await any child consumers
            for task in child_tasks.values():
                if not task.done():
                    task.cancel()
            for task in child_tasks.values():
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await self.session_pool.event_bus.unsubscribe(session_id, queue)

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
        # Find the parent session's latest assistant message
        messages = self.server_state.messages.get(parent_session_id, [])
        assistant_msg = None
        for msg in reversed(messages):
            if msg.info.role == "assistant":
                assistant_msg = msg
                break

        if assistant_msg is None:
            logger.warning(
                "No assistant message found for parent session %s, "
                "skipping ToolPart creation",
                parent_session_id,
            )
            return

        # Check if ToolPart already exists for this child session
        child_session_id = spawn_event.child_session_id
        for part in assistant_msg.parts:
            if (
                isinstance(part, ToolPart)
                and part.metadata is not None
                and part.metadata.get("sessionId") == child_session_id
            ):
                logger.debug(
                    "ToolPart already exists for child session %s", child_session_id
                )
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
        messages = self.server_state.messages.get(parent_session_id, [])
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
        msg = event.message
        content = str(msg.content) if msg.content else "(no output)"

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
        messages = self.server_state.messages.get(parent_session_id, [])
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
