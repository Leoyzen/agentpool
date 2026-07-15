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
from collections import deque
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

    @property
    def publishes_to_event_bus(self) -> bool:
        """DirectChannel does not publish to the EventBus.

        Returns:
            Always ``False``.
        """
        return False

    def set_replaying(self, flag: bool) -> None:
        """Set the replaying flag.

        When ``True``, journaling is skipped during ``publish()``.

        Args:
            flag: ``True`` to enable replaying mode, ``False`` to disable.
        """
        self._replaying = flag

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

    def deliver_feedback(self, feedback: Feedback) -> bool:
        """Reject feedback (unidirectional channel).

        DirectChannel does not support feedback delivery. Returns
        ``False`` so the caller can fall back to the queue-based path.

        Args:
            feedback: Ignored.

        Returns:
            Always ``False``.
        """
        return False

    def revoke(self, message_id: str) -> bool:
        """No-op revoke for unidirectional channel.

        DirectChannel has no feedback queue, so there is nothing to
        revoke. Always returns ``False``.

        Args:
            message_id: Ignored.

        Returns:
            Always ``False``.
        """
        return False

    def replace(self, message_id: str, new_content: str | list[Any]) -> bool:
        """No-op replace for unidirectional channel.

        DirectChannel has no feedback queue, so there is nothing to
        replace. Always returns ``False``.

        Args:
            message_id: Ignored.
            new_content: Ignored.

        Returns:
            Always ``False``.
        """
        return False

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
    (``collections.deque``) for steer/followup messages injected by
    ``SessionController``.

    The feedback queue is backed by ``deque`` (not ``asyncio.Queue``)
    to support O(1) removal by value via ``revoke()``. Three tracking
    structures provide ID-based lifecycle management:

    - ``_pending``: ``dict[str, Feedback]`` — feedback waiting in the
      queue, keyed by ``message_id``.
    - ``_revoked``: ``set[str]`` — revoked message IDs; rejected if
      re-delivered.
    - ``_delivered``: ``set[str]`` — delivered message IDs; ``revoke()``
      returns ``False`` for these.
    - ``_enqueued``: ``dict[str, list]`` — ``PendingMessage`` references
      for PydanticAI-layer revoke, keyed by ``message_id``.

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
        self._feedback_queue: deque[Feedback] = deque()
        self._pending: dict[str, Feedback] = {}
        self._revoked: set[str] = set()
        self._delivered: set[str] = set()
        self._enqueued: dict[str, list[Any]] = {}
        self._replaying: bool = False
        self._closed: bool = False
        self._run_loop: Any = None
        self._state: RunState | None = None

    def set_replaying(self, flag: bool) -> None:
        """Set the replaying flag.

        When ``True``, journaling is skipped during ``publish()``.

        Args:
            flag: ``True`` to enable replaying mode, ``False`` to disable.
        """
        self._replaying = flag

    @property
    def publishes_to_event_bus(self) -> bool:
        """ProtocolChannel publishes to the EventBus internally.

        Returns:
            Always ``True``.
        """
        return True

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

        ``StateUpdate`` events are journaled but NOT published to the
        EventBus. They are internal lifecycle signals (state machine
        transitions) that protocol servers do not need to receive.
        This preserves backward compatibility with tests and protocol
        handlers that do not expect ``StateUpdate`` on the EventBus.

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

        # StateUpdate events are internal lifecycle signals — journal
        # them but do not publish to EventBus. This prevents protocol
        # servers and EventBus subscribers from receiving state machine
        # transitions they don't know how to handle.
        if not isinstance(event, StateUpdate):
            await self._event_bus.publish(self._session_id, event)

    def recv(self) -> Feedback | None:
        """Non-blocking dequeue from the feedback queue.

        Transitions the dequeued feedback from ``_pending`` to
        ``_delivered`` to prevent revoking already-delivered messages.

        Returns:
            The next ``Feedback`` if available, or ``None``.
        """
        if not self._feedback_queue:
            return None
        feedback = self._feedback_queue.popleft()
        msg_id = feedback.message_id
        self._pending.pop(msg_id, None)
        self._delivered.add(msg_id)
        return feedback

    def deliver_feedback(self, feedback: Feedback) -> bool:
        """Enqueue feedback from SessionController.

        This is how steer/followup messages arrive at the RunLoop.
        Revoked messages are rejected.

        Args:
            feedback: The feedback to enqueue.

        Returns:
            Always ``True`` (ProtocolChannel supports feedback delivery).
        """
        if feedback.message_id in self._revoked:
            return True
        self._feedback_queue.append(feedback)
        self._pending[feedback.message_id] = feedback
        return True

    def revoke(self, message_id: str) -> bool:
        """Revoke a pending feedback message by ID.

        Operates at two layers:

        1. **CommChannel layer** (``_pending``): If the feedback is
           still in the queue, remove it from both ``_feedback_queue``
           and ``_pending``, add to ``_revoked``, and return ``True``.
        2. **PydanticAI layer** (``_enqueued``): If the feedback was
           already enqueued into ``agent_run.pending_messages`` via
           ``steer()``, remove each ``PendingMessage`` from the live
           list via ``list.remove(pm)``. If ``list.remove()`` raises
           ``ValueError`` (already drained), catch it and treat as
           success. Clean up the ``_enqueued`` entry.
        3. If already in ``_delivered``, return ``False``.
        4. Otherwise, return ``True`` (idempotent unknown).

        Args:
            message_id: The ID of the feedback message to revoke.

        Returns:
            ``True`` if revoked or already gone, ``False`` if delivered.
        """
        # Layer 3: Already delivered — cannot revoke.
        if message_id in self._delivered:
            return False

        # Layer 1: Still pending in CommChannel queue.
        if message_id in self._pending:
            feedback = self._pending.pop(message_id)
            self._feedback_queue.remove(feedback)
            self._revoked.add(message_id)
            return True

        # Layer 2: Already enqueued into PydanticAI pending_messages.
        if message_id in self._enqueued:
            pending_items = self._enqueued.pop(message_id)
            for pm in pending_items:
                try:
                    # pm is a PendingMessage from agent_run.pending_messages.
                    # list.remove() uses identity (is) comparison.
                    # The run_loop holds the agent_run with pending_messages.
                    if self._run_loop is not None:
                        run_loop: Any = self._run_loop
                        agent_run: Any = run_loop._active_agent_run
                        if agent_run is not None:
                            agent_run.pending_messages.remove(pm)
                except ValueError:
                    # Already drained by _drain_by_priority() — same
                    # end state as revoke. Idempotent.
                    pass
            self._revoked.add(message_id)
            return True

        # Layer 4: Unknown message_id — idempotent success.
        return True

    def replace(self, message_id: str, new_content: str | list[Any]) -> bool:
        """Replace the content of a pending feedback message in-place.

        Updates the content of a feedback message that is still pending
        in the channel's feedback queue, preserving its position. When
        ``new_content`` is a ``list``, updates ``Feedback.content_blocks``;
        when ``str``, updates ``Feedback.content``.

        Returns ``False`` if the message has already been enqueued into
        the PydanticAI layer (past CommChannel scope) or already
        delivered.

        Args:
            message_id: The ID of the feedback message to replace.
            new_content: New content (``str`` or ``list[Any]``).

        Returns:
            ``True`` if replaced, ``False`` if past CommChannel scope.
        """
        # Cannot replace if already enqueued or delivered.
        if message_id in self._enqueued or message_id in self._delivered:
            return False

        if message_id not in self._pending:
            # Unknown message_id — nothing to replace.
            return False

        feedback = self._pending[message_id]
        if isinstance(new_content, list):
            feedback.content_blocks = new_content
            feedback.content = ""
        else:
            feedback.content = new_content
            feedback.content_blocks = None
        return True

    def _track_enqueued(self, message_id: str, items: list[Any]) -> None:
        """Track PendingMessage references for PydanticAI-layer revoke.

        Called by ``RunHandle.steer()`` after ``agent_run.enqueue()``.
        Stores the newly appended ``PendingMessage`` references so
        ``revoke()`` can remove them from ``agent_run.pending_messages``
        via ``list.remove(pm)`` (identity comparison).

        Args:
            message_id: The feedback message ID associated with the
                enqueued items.
            items: List of ``PendingMessage`` references appended by
                ``enqueue()``.
        """
        if not items:
            return
        self._enqueued[message_id] = items

    def close(self) -> None:
        """Clean up all tracking structures and mark as closed.

        After ``close()``, further calls to ``publish()`` raise
        ``RuntimeError``.
        """
        self._closed = True
        self._feedback_queue.clear()
        self._pending.clear()
        self._revoked.clear()
        self._delivered.clear()
        self._enqueued.clear()


__all__ = [
    "DirectChannel",
    "ProtocolChannel",
]
