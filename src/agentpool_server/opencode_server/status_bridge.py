"""Session status synchronization bridge.

Syncs ``RunHandle.status`` changes to OpenCode ``SessionStatus`` by subscribing
to the EventBus and broadcasting ``SessionStatusEvent`` via ``ServerState``.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import RunFailedEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.log import get_logger
from agentpool_server.opencode_server.models import SessionStatus, SessionStatusEvent
from agentpool_server.opencode_server.models.events import SessionErrorEvent


if TYPE_CHECKING:
    from agentpool.orchestrator.core import EventBus
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


class SessionStatusBridge:
    """Bridge that syncs run status changes to OpenCode session status.

    Subscribes to the EventBus for run lifecycle events and broadcasts
    corresponding ``SessionStatusEvent`` updates via ``ServerState``.

    Status mapping:
    - ``RunStartedEvent`` -> ``SessionStatus(type="busy")``
    - ``StreamCompleteEvent`` -> ``SessionStatus(type="idle")``
    - ``RunFailedEvent`` -> ``SessionStatus(type="idle")`` + ``SessionErrorEvent``
    """

    def __init__(
        self,
        server_state: ServerState,
        session_id: str,
        event_bus: EventBus,
    ) -> None:
        """Initialize the status bridge.

        Args:
            server_state: The server state for broadcasting SSE events.
            session_id: The session to monitor.
            event_bus: The event bus to subscribe to.
        """
        self._server_state = server_state
        self._session_id = session_id
        self._event_bus = event_bus
        self._queue: asyncio.Queue[Any] | None = None
        self._task: asyncio.Task[Any] | None = None

    async def start(self) -> None:
        """Subscribe to the EventBus and start the consumer task."""
        if self._task is not None:
            return

        self._queue = await self._event_bus.subscribe(self._session_id)
        self._task = asyncio.create_task(
            self._consume(),
            name=f"status_bridge_{self._session_id}",
        )
        logger.debug("Status bridge started", session_id=self._session_id)

    async def stop(self) -> None:
        """Unsubscribe from the EventBus and stop the consumer task."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

        if self._queue is not None:
            await self._event_bus.unsubscribe(self._session_id, self._queue)
            self._queue = None

        logger.debug("Status bridge stopped", session_id=self._session_id)

    async def _consume(self) -> None:
        """Consume events from the EventBus queue and broadcast status changes."""
        if self._queue is None:
            return

        try:
            while True:
                event = await self._queue.get()
                if event is None:
                    break
                await self._handle_event(event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Status bridge consumer failed", session_id=self._session_id)

    async def _handle_event(self, event: Any) -> None:
        """Handle a single event and broadcast status if applicable.

        Args:
            event: The event from the EventBus.
        """
        match event:
            case RunStartedEvent():
                await self._broadcast_busy()
            case StreamCompleteEvent():
                await self._broadcast_idle()
            case RunFailedEvent(exception=exc):
                await self._broadcast_idle()
                if not isinstance(exc, asyncio.CancelledError):
                    await self._broadcast_error(exc)
            case _:
                pass

    async def _broadcast_busy(self) -> None:
        """Broadcast ``session.status`` event with type ``busy``."""
        status = SessionStatus(type="busy")
        self._server_state.session_status[self._session_id] = status
        await self._server_state.broadcast_event(
            SessionStatusEvent.create(self._session_id, status)
        )

    async def _broadcast_idle(self) -> None:
        """Broadcast ``session.status`` event with type ``idle``."""
        status = SessionStatus(type="idle")
        self._server_state.session_status[self._session_id] = status
        await self._server_state.broadcast_event(
            SessionStatusEvent.create(self._session_id, status)
        )

    async def _broadcast_error(self, exception: BaseException) -> None:
        """Broadcast ``session.error`` event for a failed run.

        Args:
            exception: The exception that caused the failure.
        """
        await self._server_state.broadcast_event(
            SessionErrorEvent.from_exception(exception, session_id=self._session_id)
        )
