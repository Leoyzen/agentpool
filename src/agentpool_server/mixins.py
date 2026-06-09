"""Server mixins providing reusable protocol handler behaviour.

This module contains mixin classes that capture cross-cutting concerns
shared by multiple protocol servers (ACP, OpenCode, AG-UI, etc.).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import RichAgentStreamEvent, SpawnSessionStart
from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.orchestrator import SessionPool


logger = get_logger(__name__)


class ProtocolEventConsumerMixin(ABC):
    """Mixin that auto-manages an EventBus consumer loop per session.

    Subclasses must implement :meth:`_handle_event` to process each
    :class:`~agentpool.agents.events.RichAgentStreamEvent` that arrives from
    the :class:`~agentpool.orchestrator.EventBus`.  The mixin takes care of
    subscribing, running the ``while True`` consumer loop, handling the
    ``None`` sentinel, cancelling cleanly, and unsubscribing in ``finally``.

    Attributes:
        _consumer_tasks: Mapping of *session_id* -> running asyncio Task.
        _consumer_queues: Mapping of *session_id* -> subscribed queue.
            Kept for tracking / introspection; the loop itself reads from
            the queue passed into it.
    """

    _consumer_tasks: dict[str, asyncio.Task[None]]
    _consumer_queues: dict[str, asyncio.Queue[Any]]

    # ------------------------------------------------------------------ #
    # Hooks that subclasses MUST / MAY implement
    # ------------------------------------------------------------------ #

    @abstractmethod
    async def _handle_event(
        self,
        session_id: str,
        event: RichAgentStreamEvent[Any],
    ) -> None:
        """Process a single event from the EventBus.

        Args:
            session_id: The session the event belongs to.
            event: The rich stream event to handle.
        """
        ...

    async def _handle_spawn_session_start(  # noqa: B027
        self,
        session_id: str,
        event: SpawnSessionStart,
    ) -> None:
        """Optional hook fired when a :class:`SpawnSessionStart` event is seen.

        The default implementation is a no-op.  Subclasses may override this
        to start child-session consumers, update UI state, or log telemetry.

        Args:
            session_id: The session the event was received on.
            event: The spawn event describing the new child session.
        """

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Returns:
            One of ``"session"``, ``"descendants"`` (default), or
            ``"subtree"``.  See
            :meth:`~agentpool.orchestrator.EventBus.subscribe`.
        """
        return "descendants"

    # ------------------------------------------------------------------ #
    # Concrete lifecycle methods
    # ------------------------------------------------------------------ #

    async def start_event_consumer(self, session_id: str) -> None:
        """Subscribe to the EventBus and start the consumer loop for *session_id*.

        Idempotent: if a consumer is already running for *session_id* this
        is a no-op.  The mixin resolves the :class:`SessionPool` from
        ``self.agent_pool.session_pool`` (subclasses must ensure this
        attribute exists).

        Args:
            session_id: The session to consume events for.
        """
        existing_task = self._consumer_tasks.get(session_id)
        if existing_task is not None and not existing_task.done():
            return

        session_pool: SessionPool | None = getattr(
            getattr(self, "agent_pool", None), "session_pool", None
        )
        if session_pool is None:
            logger.warning(
                "Cannot start event consumer: no session_pool available",
                session_id=session_id,
            )
            return

        queue = await session_pool.event_bus.subscribe(
            session_id=session_id,
            scope=self._get_subscription_scope(),
        )
        self._consumer_queues[session_id] = queue

        task = asyncio.create_task(
            self._event_consumer_loop(session_id),
            name=f"event_consumer_{session_id}",
        )
        self._consumer_tasks[session_id] = task
        await asyncio.sleep(0)  # Let the task start before returning

        logger.debug(
            "Event consumer started",
            session_id=session_id,
        )

    async def stop_event_consumer(self, session_id: str) -> None:
        """Cancel the consumer task and unsubscribe from the EventBus.

        Safe to call even if no consumer is running for *session_id*.

        Args:
            session_id: The session whose consumer should stop.
        """
        task = self._consumer_tasks.pop(session_id, None)
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        queue = self._consumer_queues.pop(session_id, None)
        session_pool: SessionPool | None = getattr(
            getattr(self, "agent_pool", None), "session_pool", None
        )
        if queue is not None and session_pool is not None:
            await session_pool.event_bus.unsubscribe(session_id, queue)

        logger.debug(
            "Event consumer stopped",
            session_id=session_id,
        )

    async def _event_consumer_loop(self, session_id: str) -> None:
        """Read events from the EventBus queue until cancelled or sentinel.

        This loop:
        1. Subscribes to the EventBus using :meth:`_get_subscription_scope`.
        2. Pulls events from the queue (``None`` acts as a stop sentinel).
        3. Dispatches each event to :meth:`_handle_event`.
        4. Handles :exc:`asyncio.CancelledError` and performs cleanup.

        Args:
            session_id: The session whose events are being consumed.
        """
        session_pool: SessionPool | None = getattr(
            getattr(self, "agent_pool", None), "session_pool", None
        )
        if session_pool is None:
            return

        queue = self._consumer_queues.get(session_id)
        if queue is None:
            return

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break

                if isinstance(event, SpawnSessionStart):
                    await self._handle_spawn_session_start(session_id, event)
                    await self.start_event_consumer(event.child_session_id)
                    continue

                try:
                    await self._handle_event(session_id, event)
                except Exception:
                    logger.exception(
                        "Event handler failed",
                        session_id=session_id,
                        event_type=type(event).__name__,
                    )
        except asyncio.CancelledError:
            logger.debug(
                "Event consumer cancelled",
                session_id=session_id,
            )
            raise
        except Exception:
            logger.exception(
                "Event consumer loop failed",
                session_id=session_id,
            )
        finally:
            self._consumer_tasks.pop(session_id, None)
            self._consumer_queues.pop(session_id, None)
            await session_pool.event_bus.unsubscribe(session_id, queue)
