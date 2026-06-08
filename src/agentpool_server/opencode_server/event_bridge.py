"""Event bridge between OpenCode SSE broadcasting and SessionPool EventBus.

Provides :class:`OpenCodeEventBridge` which intercepts events destined for
OpenCode SSE subscribers and additionally republishes them to the
SessionPool's :class:`EventBus`. This enables dual-path event delivery during
the migration from legacy SSE-only broadcasting to EventBus-based routing.

**Event flow**

1. Caller invokes ``state.broadcast_event(event)`` (or ``bridge.publish(event)``).
2. Bridge forwards the raw event to the original
   :meth:`ServerState.broadcast_event` so all existing SSE subscribers
   continue to receive events unchanged.
3. Bridge extracts ``session_id`` from the event's ``properties``.
4. If a session_id is present, the event is wrapped in a
   :class:`CustomEvent` and published to the EventBus for that session.
5. EventBus subscribers (status bridges, protocol adapters, test consumers)
   receive the wrapped event.

**Backward compatibility**

When ``session_controller`` is ``None`` (legacy mode) the bridge is not
instantiated and ``broadcast_event`` behaves exactly as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from agentpool.agents.events.events import CustomEvent
from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.orchestrator.core import EventBus
    from agentpool_server.opencode_server.models.events import Event
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


class OpenCodeEventBridge:
    """Bridge that dual-publishes OpenCode events to SSE and EventBus.

    Wraps :meth:`ServerState._broadcast_event_impl` so that every event
    sent to OpenCode SSE subscribers is also made available on the
    SessionPool EventBus.  This allows incremental migration of consumers
    from SSE queues to EventBus subscriptions without breaking existing
    subscribers.

    Args:
        state: The OpenCode server state.
        event_bus: The SessionPool EventBus to republish events into.
    """

    def __init__(self, state: ServerState, event_bus: EventBus) -> None:
        """Initialize the bridge."""
        self._state = state
        self._event_bus = event_bus

    async def publish(self, event: Event) -> None:
        """Publish an event to SSE subscribers and the EventBus.

        Steps:
        1. Forward the raw event to the original SSE broadcast path via
           :meth:`ServerState._broadcast_event_impl`.
        2. Extract ``session_id`` from the event properties.
        3. If a session_id is found, wrap the event in a
           :class:`CustomEvent` and publish it to the EventBus.

        Args:
            event: An OpenCode protocol event (e.g. ``SessionStatusEvent``,
                ``PartUpdatedEvent``, ``MessageUpdatedEvent``).
        """
        # Step 1: backward-compatible SSE broadcast
        await self._state._broadcast_event_impl(event)

        # Step 2: extract session_id
        session_id = self._extract_session_id(event)
        if session_id is None:
            # Global events (server.heartbeat, vcs.branch.updated, etc.)
            # have no session scope and are not republished to the EventBus.
            return

        # Step 3: wrap and republish to EventBus
        wrapped = self._wrap_event(event)
        try:
            await self._event_bus.publish(session_id, wrapped)
        except Exception:
            logger.exception(
                "Failed to republish event to EventBus",
                session_id=session_id,
                event_type=getattr(event, "type", "unknown"),
            )

    @staticmethod
    def _extract_session_id(event: Event) -> str | None:
        """Extract session_id from an OpenCode event's properties.

        Most session-scoped events inherit from ``SessionIdProperties`` and
        expose ``properties.session_id``.  Global events (heartbeats,
        branch updates) do not have a session_id.

        Args:
            event: The OpenCode event to inspect.

        Returns:
            The session ID string, or ``None`` if the event is global.
        """
        properties = getattr(event, "properties", None)
        if properties is None:
            return None
        session_id = getattr(properties, "session_id", None)
        return session_id if isinstance(session_id, str) else None

    @staticmethod
    def _wrap_event(event: Event) -> CustomEvent[Any]:
        """Wrap an OpenCode event in a :class:`CustomEvent`.

        The wrapped event preserves the original event as ``event_data`` and
        uses the OpenCode event type (prefixed with ``opencode:``) as the
        custom event type.  This makes it easy for EventBus consumers to
        distinguish OpenCode protocol events from native agent events.

        Args:
            event: The OpenCode event to wrap.

        Returns:
            A :class:`CustomEvent` carrying the original OpenCode event.
        """
        event_type = getattr(event, "type", "opencode:unknown")
        return CustomEvent(
            event_data=event,
            event_type=f"opencode:{event_type}",
            source="opencode_event_bridge",
        )
