"""CommChannel dimension: DirectChannel and ProtocolChannel.

The CommChannel abstracts event delivery and feedback reception for the
RunLoop. It owns the Journal reference and handles event persistence
internally (append for deltas, upsert for entity-state events).

Two implementations are provided:

- **DirectChannel** — unidirectional; publishes events to an internal
  ``asyncio.Queue`` that ``RunLoop.start()`` drains via ``get_nowait()``.
  ``recv()`` always returns ``None``.

- **ProtocolChannel** — bidirectional; publishes events to the
  ``EventBus`` for protocol server consumption and maintains a feedback
  queue for steer/followup messages from ``SessionController``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import (
    MessageReplacementEvent,
    PlanUpdateEvent,
    StateUpdate,
    ToolCallUpdateEvent,
)
from agentpool.log import get_logger


if TYPE_CHECKING:
    from agentpool.lifecycle.protocols import Journal
    from agentpool.lifecycle.types import Feedback, RunState
    from agentpool.orchestrator.event_bus import EventBus


logger = get_logger(__name__)


def _derive_upsert_key(event: Any) -> str | None:
    """Derive the journal upsert key for an event.

    Entity-state events (tool call updates, state updates, message
    replacements, plan updates) use upsert semantics so only the latest
    state per entity is retained. Delta events return ``None`` to use
    append semantics.

    Args:
        event: The event to derive a key for.

    Returns:
        Deduplication key string, or ``None`` for append semantics.
    """
    match event:
        case ToolCallUpdateEvent(tool_call_id=tid) if tid:
            return f"tool_call:{tid}"
        case StateUpdate(session_id=sid) if sid:
            return f"state:{sid}"
        case MessageReplacementEvent(message_id=mid) if mid:
            return f"msg:{mid}"
        case PlanUpdateEvent(tool_call_id=tcid) if tcid is not None:
            return f"plan:{tcid}"
        case _:
            return None


class DirectChannel:
    """Unidirectional CommChannel for in-process event delivery.

    Publishes events to an internal ``asyncio.Queue``. The RunLoop's
    ``start()`` method drains this queue via ``get_nowait()`` to
    consume events. ``recv()`` always returns ``None`` since this
    channel does not support feedback.

    Events are journaled (append or upsert) before delivery, unless
    ``_replaying`` is ``True``.
    """

    def __init__(self, journal: Journal) -> None:
        """Initialize the direct channel.

        Args:
            journal: The Journal to persist events to.
        """
        self._journal: Journal = journal
        self._queue: asyncio.Queue[Any] = asyncio.Queue()
        self._replaying: bool = False
        self._closed: bool = False
        self._run_loop: Any = None
        self._state: RunState | None = None

    @property
    def queue(self) -> asyncio.Queue[Any]:
        """The internal event queue, accessible for RunLoop draining."""
        return self._queue

    def attach(self, run_loop: Any) -> None:
        """Store a reference to the RunLoop.

        No-op for DirectChannel since it does not route feedback.

        Args:
            run_loop: The RunLoop instance.
        """
        self._run_loop = run_loop

    def on_state_change(self, state: RunState) -> None:
        """Receive state transitions.

        No-op for DirectChannel but required for Protocol conformance.

        Args:
            state: The new RunState.
        """
        self._state = state

    async def publish(self, event: Any) -> None:
        """Journal and enqueue an event.

        If ``_replaying`` is ``True``, journaling is skipped.
        Otherwise, the event is journaled (append or upsert) before
        being enqueued to the internal queue.

        Args:
            event: The event to publish.

        Raises:
            RuntimeError: If the channel has been closed.
        """
        if self._closed:
            raise RuntimeError("DirectChannel is closed; cannot publish.")

        if not self._replaying:
            key = _derive_upsert_key(event)
            if key is not None:
                self._journal.upsert(key, event)
            else:
                self._journal.append(event)

        self._queue.put_nowait(event)

    def recv(self) -> Feedback | None:
        """Return ``None`` (unidirectional channel).

        DirectChannel does not support feedback reception.

        Returns:
            Always ``None``.
        """
        return None

    def close(self) -> None:
        """Drain the queue and mark the channel as closed.

        After ``close()``, further calls to ``publish()`` raise
        ``RuntimeError``.
        """
        self._closed = True
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break


class ProtocolChannel:
    """Bidirectional CommChannel for protocol server event delivery.

    Publishes events to the ``EventBus`` for consumption by protocol
    servers (ACP, OpenCode, AG-UI, etc.). Maintains a feedback queue
    for steer/followup messages injected by ``SessionController``.

    Events are journaled (append or upsert) before delivery, unless
    ``_replaying`` is ``True``.
    """

    def __init__(
        self,
        journal: Journal,
        event_bus: EventBus,
        session_id: str = "",
    ) -> None:
        """Initialize the protocol channel.

        Args:
            journal: The Journal to persist events to.
            event_bus: The EventBus to publish events to.
            session_id: The session ID for EventBus routing.
        """
        self._journal: Journal = journal
        self._event_bus: EventBus = event_bus
        self._session_id: str = session_id
        self._feedback_queue: asyncio.Queue[Feedback] = asyncio.Queue()
        self._replaying: bool = False
        self._closed: bool = False
        self._run_loop: Any = None
        self._state: RunState | None = None

    def attach(self, run_loop: Any) -> None:
        """Store a reference to the RunLoop for feedback routing.

        Args:
            run_loop: The RunLoop instance.
        """
        self._run_loop = run_loop

    def on_state_change(self, state: RunState) -> None:
        """Track RunLoop state for steer/followup routing.

        Args:
            state: The new RunState.
        """
        self._state = state

    async def publish(self, event: Any) -> None:
        """Journal and deliver an event to the EventBus.

        If ``_replaying`` is ``True``, journaling is skipped.
        Otherwise, the event is journaled (append or upsert) before
        being published to the EventBus.

        Args:
            event: The event to publish.

        Raises:
            RuntimeError: If the channel has been closed.
        """
        if self._closed:
            raise RuntimeError("ProtocolChannel is closed; cannot publish.")

        if not self._replaying:
            key = _derive_upsert_key(event)
            if key is not None:
                self._journal.upsert(key, event)
            else:
                self._journal.append(event)

        await self._event_bus.publish(self._session_id, event)

    def recv(self) -> Feedback | None:
        """Non-blocking dequeue from the feedback queue.

        Returns:
            The next ``Feedback`` if available, or ``None``.
        """
        try:
            return self._feedback_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def deliver_feedback(self, feedback: Feedback) -> None:
        """Enqueue feedback from SessionController.

        This is how steer/followup messages arrive at the RunLoop.

        Args:
            feedback: The feedback to enqueue.
        """
        self._feedback_queue.put_nowait(feedback)

    def close(self) -> None:
        """Clean up the feedback queue and mark as closed.

        After ``close()``, further calls to ``publish()`` raise
        ``RuntimeError``.
        """
        self._closed = True
        while not self._feedback_queue.empty():
            try:
                self._feedback_queue.get_nowait()
            except asyncio.QueueEmpty:
                break


__all__ = [
    "DirectChannel",
    "ProtocolChannel",
]
