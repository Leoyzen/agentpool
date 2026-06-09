"""Protocol server mixins.

Shared utility mixins for AgentPool protocol server implementations.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.agents.events.events import RichAgentStreamEvent, SpawnSessionStart


if TYPE_CHECKING:
    from agentpool.orchestrator.core import EventBus


class ConsumerShutdown(Exception):  # noqa: N818
    """Signal raised by _handle_event() to request graceful consumer loop shutdown."""


class ProtocolEventConsumerMixin(ABC):
    """Mixin providing EventBus consumer lifecycle management for protocol servers.

    This mixin extracts the common pattern of subscribing to the EventBus,
    running an async consumer loop, and cleaning up on shutdown. Protocol
    handlers (ACP, OpenCode, AG-UI, etc.) can inherit from it and implement
    protocol-specific event handling via abstract hooks.

    Subclasses MUST call super().__init__() if they override __init__.

    !!! note
        The mixin does not automatically create child consumers when a
        SpawnSessionStart event is received. Subclasses that want child
        consumers must override _on_spawn_session_start() and call
        start_event_consumer(child_session_id) themselves.
    """

    def __init__(self) -> None:
        """Initialize mixin state.

        Sets up internal tracking for consumer tasks, queues, and locks.
        """
        super().__init__()
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._consumer_queues: dict[str, asyncio.Queue[Any]] = {}
        self._consumer_locks: dict[str, asyncio.Lock] = {}
        self._consumer_lock_creation_lock: asyncio.Lock = asyncio.Lock()

    @property
    @abstractmethod
    def event_bus(self) -> EventBus:
        """Return the EventBus instance to subscribe to."""

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Defaults to "descendants" so that child session events are
        received automatically. Subclasses may override to return
        "session" or "subtree" for different visibility.

        Returns:
            The subscription scope string.
        """
        return "descendants"

    async def _before_consumer_loop(self, session_id: str) -> None:  # noqa: B027
        """Hook called before the consumer loop starts reading from queue.

        Subclasses may override to set up per-session context (e.g.
        creating an event converter or adapter).

        Args:
            session_id: The session whose consumer is starting.
        """

    async def _after_consumer_loop(self, session_id: str) -> None:  # noqa: B027
        """Hook called after the consumer loop exits and unsubscribes.

        Only called if the consumer had actually started (i.e.
        _before_consumer_loop completed without raising). Subclasses
        may override to perform per-session cleanup.

        Args:
            session_id: The session whose consumer has stopped.
        """

    async def _on_spawn_session_start(  # noqa: B027
        self, session_id: str, event: SpawnSessionStart
    ) -> None:
        """Hook called when a SpawnSessionStart event is received.

        The default implementation is a no-op. Subclasses may override
        to start child consumers or perform other setup (e.g. registering
        a ToolPart for the subagent in OpenCode).

        !!! note
            This hook is called BEFORE _handle_event() for the same
            SpawnSessionStart event. Exceptions raised here are NOT
            caught by the mixin and will propagate out, triggering
            cleanup in the finally block.

        Args:
            session_id: The session whose consumer received the event.
            event: The spawn session start event.
        """

    @abstractmethod
    async def _handle_event(
        self, session_id: str, event: RichAgentStreamEvent[Any]
    ) -> None:
        """Handle a single event from the EventBus.

        Subclasses MUST implement this method with protocol-specific
        conversion and delivery logic.

        Args:
            session_id: The session whose consumer received the event.
            event: The event to handle.

        Raises:
            ConsumerShutdown: To request graceful loop shutdown.
        """

    async def start_event_consumer(self, session_id: str) -> None:
        """Start an event consumer for the given session.

        This method is idempotent: if a consumer is already running for
        the session, it returns immediately. Concurrent calls for the
        same session are serialized by a per-session lock.

        Args:
            session_id: The session to start consuming events for.
        """
        async with self._consumer_lock_creation_lock:
            if session_id not in self._consumer_locks:
                self._consumer_locks[session_id] = asyncio.Lock()

        async with self._consumer_locks[session_id]:
            task = self._consumer_tasks.get(session_id)
            if task is not None and not task.done():
                return

            queue = await self.event_bus.subscribe(
                session_id, scope=self._get_subscription_scope()
            )
            self._consumer_queues[session_id] = queue

            task = asyncio.create_task(
                self._event_consumer_loop(session_id),
                name=f"event_consumer_{session_id}",
            )
            self._consumer_tasks[session_id] = task

    async def stop_event_consumer(self, session_id: str) -> None:
        """Stop the event consumer for the given session.

        Cancels the consumer task, unsubscribes from the EventBus,
        and cleans up internal state. Safe to call even if no consumer
        is running for the session.

        Args:
            session_id: The session to stop consuming events for.
        """
        task = self._consumer_tasks.get(session_id)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        self._consumer_tasks.pop(session_id, None)
        queue = self._consumer_queues.pop(session_id, None)
        if queue is not None:
            await self.event_bus.unsubscribe(session_id, queue)

        self._consumer_locks.pop(session_id, None)

    async def _event_consumer_loop(self, session_id: str) -> None:
        """Read events from the subscription queue and dispatch to hooks.

        The loop exits gracefully when a None sentinel is received,
        when ConsumerShutdown is raised from _handle_event(), or
        when the task is cancelled.

        SpawnSessionStart events are dispatched to BOTH
        _on_spawn_session_start() AND _handle_event(). All other
        non-None events go only to _handle_event().

        Cleanup (unsubscribe, _after_consumer_loop) is performed in a
        finally block regardless of how the loop exits.

        Args:
            session_id: The session whose events to consume.
        """
        queue = self._consumer_queues.get(session_id)
        if queue is None:
            return

        started = False
        try:
            await self._before_consumer_loop(session_id)
            started = True

            while True:
                event = await queue.get()
                if event is None:
                    break

                if isinstance(event, SpawnSessionStart):
                    await self._on_spawn_session_start(session_id, event)

                try:
                    await self._handle_event(session_id, event)
                except ConsumerShutdown:
                    break
        finally:
            await self.event_bus.unsubscribe(session_id, queue)
            self._consumer_queues.pop(session_id, None)
            self._consumer_tasks.pop(session_id, None)
            if started:
                await self._after_consumer_loop(session_id)
