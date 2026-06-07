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

from agentpool.agents.events.events import SpawnSessionStart
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.event_adapter import OpenCodeEventAdapter
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    SessionCreatedEvent,
    SessionStatus,
    TimeCreated,
    TimeCreatedUpdated,
    UserMessage,
)
from agentpool_server.opencode_server.models.session import Session
from agentpool_server.opencode_server.status_bridge import SessionStatusBridge


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.core import SessionPool, SessionState
    from agentpool.orchestrator.run import RunHandle
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


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

                if session_data.parent_id is None:
                    async with state.agent_lock:
                        target_agent = state.agent
                        input_provider = state.ensure_input_provider(session_id)
                        target_agent._input_provider = input_provider

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

    if parent_id is None:
        async with state.agent_lock:
            target_agent = state.agent
            input_provider = state.ensure_input_provider(session_id)
            target_agent._input_provider = input_provider

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

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state from the SessionPool.
        """
        state = await self.session_pool.create_session(session_id, agent_name, **metadata)
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

        Args:
            parent_session_id: The parent session ID.
            new_session_id: The new child session ID.
            agent_name: Name of the agent for the child session.

        Returns:
            The child session state.
        """
        state = await self.session_pool.create_session(
            new_session_id,
            agent_name=agent_name,
            parent_session_id=parent_session_id,
        )
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

        Args:
            session_id: The session to start consuming events for.
        """
        if session_id in self._event_consumers:
            return
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

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break

                # Spawn child-session consumers for nested subagents
                if isinstance(event, SpawnSessionStart):
                    child_task = asyncio.create_task(
                        self._event_consumer_loop(event.child_session_id),
                        name=f"event_consumer_{event.child_session_id}",
                    )
                    child_tasks[event.child_session_id] = child_task
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
