"""EventBus infrastructure for cross-turn event streaming.

Extracted from orchestrator/core.py as part of the thin-wrapper refactor.
Provides pub/sub event routing with replay buffers and overflow handling.
"""

from __future__ import annotations

from collections import deque
import contextlib
from dataclasses import dataclass
from itertools import groupby
from typing import TYPE_CHECKING, Any, Final

import anyio
from pydantic_ai import TextPartDelta, ThinkingPartDelta, ToolCallPartDelta

from agentpool.agents.events import (
    CompactionEvent,
    PartDeltaEvent,
    PlanUpdateEvent,
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    SessionResumeEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
    ToolCallContentItem,
    ToolCallDeferredEvent,
    ToolCallProgressEvent,
    ToolCallStartEvent,
)
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.session_controller import SessionController


logger = get_logger(__name__)

DEFAULT_QUEUE_MAXSIZE: Final[int] = 1000


@dataclass(frozen=True)
class EventEnvelope:
    """Wrapper for events published through EventBus.

    Carries routing metadata (source_session_id) separately from the event
    payload so consumers can determine the event's origin without mutating
    the event object.

    Attribute access is transparently forwarded to the wrapped event,
    so consumers can use ``envelope.delta`` or ``envelope.event_kind``
    without unwrapping.
    """

    source_session_id: str
    """The session that produced this event."""
    event: Any
    """The original event payload (unmodified)."""

    def __getattr__(self, name: str) -> Any:
        """Forward attribute access to the wrapped event."""
        return getattr(self.event, name)

    def __repr__(self) -> str:
        return f"EventEnvelope(source_session_id={self.source_session_id!r}, event={self.event!r})"


# ---------------------------------------------------------------------------
# Event coalescing infrastructure (Task 1 — fields + functions only)
# ---------------------------------------------------------------------------


def _is_immediate(event: Any) -> bool:
    """Check if an event is a lifecycle event that bypasses coalescing.

    Immediate events are dispatched right away and trigger a buffer drain
    of any pending batchable events for the session.

    Returns:
        True if the event is an immediate lifecycle event.
    """
    match event:
        case (
            RunStartedEvent()
            | RunErrorEvent()
            | RunFailedEvent()
            | StreamCompleteEvent()
            | SpawnSessionStart()
            | CompactionEvent()
            | SessionResumeEvent()
            | ToolCallStartEvent()
            | ToolCallCompleteEvent()
            | ToolCallDeferredEvent()
        ):
            return True
        case _:
            return False


def _merge_key(event: Any) -> tuple[str, str | None] | None:  # noqa: PLR0911
    """Compute the coalescing merge key for an event.

    Returns:
        A tuple key for batchable events, or None for passthrough events.
        Passthrough events are dispatched individually (after draining the buffer).
    """
    match event:
        case PartDeltaEvent(delta=TextPartDelta()):
            return ("delta_text", "")
        case PartDeltaEvent(delta=ThinkingPartDelta()):
            return ("delta_thinking", "")
        case PartDeltaEvent(delta=ToolCallPartDelta(tool_call_id=tcid)):
            return ("delta_tool_call", tcid)
        case PartDeltaEvent():
            # delta is None — classified as passthrough, will be dropped in _merge_envelopes
            return None
        case ToolCallProgressEvent(tool_call_id=tcid, status=status):
            return ("progress", f"{tcid}:{status}")
        case PlanUpdateEvent():
            return ("plan", "")
        case _:
            return None


def _merge_text_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate TextPartDelta content_delta strings. Uses first event's index."""
    parts = [
        event.delta.content_delta
        for event in events
        if isinstance(event.delta, TextPartDelta) and event.delta.content_delta is not None
    ]
    return PartDeltaEvent(
        index=events[0].index,
        delta=TextPartDelta(content_delta="".join(parts)),
    )


def _merge_thinking_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ThinkingPartDelta content_delta strings. Uses first event's index."""
    parts = [
        event.delta.content_delta
        for event in events
        if isinstance(event.delta, ThinkingPartDelta) and event.delta.content_delta is not None
    ]
    return PartDeltaEvent(
        index=events[0].index,
        delta=ThinkingPartDelta(content_delta="".join(parts)),
    )


def _merge_tool_call_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ToolCallPartDelta args_delta strings.

    Uses first event's index and tool_call_id.
    """
    parts: list[str] = []
    tool_call_id: str | None = None
    for event in events:
        delta = event.delta
        if isinstance(delta, ToolCallPartDelta) and isinstance(delta.args_delta, str):
            parts.append(delta.args_delta)
            if not tool_call_id:
                tool_call_id = delta.tool_call_id
    return PartDeltaEvent(
        index=events[0].index,
        delta=ToolCallPartDelta(args_delta="".join(parts), tool_call_id=tool_call_id),
    )


def _merge_progress_events(events: list[ToolCallProgressEvent]) -> ToolCallProgressEvent:
    """Concatenate items sequences from progress events.

    Uses last event's title, status, replace_content, and tool_name.
    Items with duplicate TerminalContentItem.terminal_id are kept (consumer handles dedup).
    """
    all_items: list[ToolCallContentItem] = []
    for event in events:
        all_items.extend(event.items)
    last = events[-1]
    return ToolCallProgressEvent(
        tool_call_id=last.tool_call_id,
        status=last.status,
        title=last.title,
        items=all_items,
        replace_content=last.replace_content,
        tool_name=last.tool_name,
        progress=last.progress,
        total=last.total,
        message=last.message,
        tool_input=last.tool_input,
        session_id=last.session_id,
    )


def _rebind(template: EventEnvelope, new_event: Any) -> EventEnvelope:
    """Create new EventEnvelope with merged event, preserving source_session_id."""
    return EventEnvelope(source_session_id=template.source_session_id, event=new_event)


def _merge_envelopes(envelopes: list[EventEnvelope]) -> list[EventEnvelope]:
    """Merge a list of envelopes using itertools.groupby.

    Groups consecutive envelopes by _merge_key. For batchable groups, merges
    events into a single event. For passthrough groups (key is None), extends
    without merging. For the ("plan", "") key, keeps the last event (last-wins).
    Drops PartDeltaEvent instances where delta is None.

    Returns:
        List of merged (or passthrough) envelopes ready for dispatch.
    """
    # Drop PartDeltaEvent with delta=None
    filtered: list[EventEnvelope] = [
        env
        for env in envelopes
        if not (isinstance(env.event, PartDeltaEvent) and env.event.delta is None)
    ]

    result: list[EventEnvelope] = []
    for key, group in groupby(filtered, key=lambda env: _merge_key(env.event)):
        group_list = list(group)
        if key is None:
            # Passthrough: extend without merging
            result.extend(group_list)
        elif key[0] == "plan":
            # Last-wins: keep last event
            result.append(group_list[-1])
        else:
            events = [env.event for env in group_list]
            merged: PartDeltaEvent | ToolCallProgressEvent
            match key[0]:
                case "delta_text":
                    merged = _merge_text_deltas(events)
                case "delta_thinking":
                    merged = _merge_thinking_deltas(events)
                case "delta_tool_call":
                    merged = _merge_tool_call_deltas(events)
                case "progress":
                    merged = _merge_progress_events(events)
                case _:
                    result.extend(group_list)
                    continue
            result.append(_rebind(group_list[0], merged))
    return result


async def drain_and_merge(
    stream: anyio.abc.ObjectReceiveStream[Any],
) -> AsyncIterator[EventEnvelope]:
    """Drain all queued events from a subscriber stream and merge consecutive same-type events.

    Performs subscriber-side coalescing: blocks on ``await stream.receive()``
    until at least one item is available, then drains all immediately-available
    items via ``receive_nowait()`` until ``WouldBlock``. The resulting batch is
    merged via ``_merge_envelopes()`` and each merged envelope is yielded.
    Repeats until the stream signals ``EndOfStream`` or ``ClosedResourceError``.

    Raw events (not wrapped in ``EventEnvelope``) are automatically wrapped with
    an empty ``source_session_id`` for compatibility with test streams.

    Usage:
        send_stream, receive_stream = anyio.create_memory_object_stream(64)
        async for envelope in drain_and_merge(receive_stream):
            event = envelope.event
            # handle event

    Args:
        stream: The ``ObjectReceiveStream`` to drain. Items may be
            ``EventEnvelope`` instances or raw events.

    Yields:
        Merged ``EventEnvelope`` instances ready for dispatch.

    !!! note "Blocking behavior"
        This function blocks on ``stream.receive()`` between batches. Once an
        item arrives, it non-blockingly drains ``receive_nowait()`` until
        ``WouldBlock`` to form a batch, merges via ``_merge_envelopes()``,
        yields each merged envelope, then blocks again for the next batch.
    """
    while True:
        # Block until at least one item is available.
        try:
            first = await stream.receive()
        except (anyio.EndOfStream, anyio.ClosedResourceError):
            return

        # Wrap raw events (e.g., from test streams) in EventEnvelope.
        if not isinstance(first, EventEnvelope):
            first = EventEnvelope(source_session_id="", event=first)

        batch: list[EventEnvelope] = [first]

        # Drain all immediately-available items without blocking.
        while True:
            try:
                item = stream.receive_nowait()  # type: ignore[attr-defined]
            except anyio.WouldBlock:
                break
            except (anyio.EndOfStream, anyio.ClosedResourceError):
                # Stream closed mid-drain: process batch then terminate.
                for env in _merge_envelopes(batch):
                    yield env
                return
            if not isinstance(item, EventEnvelope):
                item = EventEnvelope(source_session_id="", event=item)
            batch.append(item)

        # Merge and yield the batch.
        for env in _merge_envelopes(batch):
            yield env


class EventBus:
    """PubSub event bus for cross-turn event streaming.

    Decouples event producers (agents) from consumers (protocol handlers).
    Events are broadcast to all subscribers for a given session via ``_send()``
    with no publish-side buffering or coalescing.

    Event coalescing/merging is the subscriber's responsibility. Subscribers
    should drain their receive stream using ``drain_and_merge()`` to batch-merge
    consecutive same-type events (e.g., ``PartDeltaEvent`` text chunks) for
    efficient processing.

    Safety features:
    - Bounded memory streams with hybrid backpressure (drop oldest, then drop subscriber)
    - Automatic cleanup of dead subscribers
    - EndOfStream-based shutdown (no sentinel None)
    """

    def __init__(
        self,
        max_queue_size: int = DEFAULT_QUEUE_MAXSIZE,
        replay_buffer_size: int = 100,
        session_controller: SessionController | None = None,
    ) -> None:
        """Initialize the event bus.

        Args:
            max_queue_size: Maximum buffer size for subscriber memory streams.
            replay_buffer_size: Maximum number of events retained per session for replay.
            session_controller: Optional session controller for hierarchy queries.
        """
        self._subscribers: dict[
            str, list[tuple[anyio.abc.ObjectSendStream[EventEnvelope], str]]
        ] = {}
        self._stream_pairs: dict[int, anyio.abc.ObjectSendStream[EventEnvelope]] = {}
        self._session_tree: dict[str, list[str]] = {}
        self._lock = anyio.Lock()
        self._max_queue_size = max_queue_size
        self._replay_buffer_size = replay_buffer_size
        self._session_controller = session_controller
        self._replay_buffers: dict[str, deque[EventEnvelope]] = {}

    async def subscribe(
        self, session_id: str, scope: str = "session"
    ) -> anyio.abc.ObjectReceiveStream[EventEnvelope]:
        """Subscribe to events for a session.

        New subscribers receive replayed historical events from the replay
        buffer before live events. Events published during the replay phase
        are drained and re-inserted after historical events to preserve
        ordering and avoid loss.

        Args:
            session_id: The session to subscribe to.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).

                !!! warning "Deprecated: descendants scope"
                    The "descendants" scope is deprecated for protocol server use.
                    It has known issues with replay buffer data loss, O(N) recursive
                    traversal, and duplicate deliveries. Protocol servers should use
                    "session" scope with explicit child consumers via
                    `ProtocolEventConsumerMixin._on_spawn_session_start()` instead.
                    The "descendants" enum value is retained for backward compatibility.

        Returns:
            A memory object receive stream to consume events from.
        """
        send_stream, receive_stream = anyio.create_memory_object_stream(
            max_buffer_size=self._max_queue_size
        )

        async with self._lock:
            self._subscribers.setdefault(session_id, []).append((send_stream, scope))
            self._stream_pairs[id(receive_stream)] = send_stream
            if scope == "all":
                historical_events: list[EventEnvelope] = []
                for buffer in self._replay_buffers.values():
                    historical_events.extend(buffer)
            else:
                buffer = self._replay_buffers.get(session_id, deque())
                historical_events = list(buffer)

        for envelope in historical_events:
            try:
                send_stream.send_nowait(envelope)
            except anyio.WouldBlock:
                break

        return receive_stream

    def clear_replay_buffer(self, session_id: str) -> None:
        """Clear the replay buffer for a session.

        Removes all historical events from the replay buffer so that
        new subscribers only receive events from this point forward.
        This should be called at the start of each turn to prevent
        stale events (including terminal events like StreamCompleteEvent)
        from previous turns being replayed to new subscribers.

        Args:
            session_id: The session whose replay buffer to clear.
        """
        self._replay_buffers.pop(session_id, None)

    async def unsubscribe(
        self,
        session_id: str,
        receive_stream: anyio.abc.ObjectReceiveStream[EventEnvelope],
    ) -> None:
        """Unsubscribe from events.

        Closes the send stream counterpart so the consumer receives EndOfStream.
        Cleans up empty subscriber lists to prevent memory leaks.

        Args:
            session_id: The session to unsubscribe from.
            receive_stream: The receive stream returned by subscribe().
        """
        send_to_close: anyio.abc.ObjectSendStream[EventEnvelope] | None = None
        async with self._lock:
            send_to_close = self._stream_pairs.pop(id(receive_stream), None)
            if send_to_close is not None and session_id in self._subscribers:
                self._subscribers[session_id] = [
                    (s, sc) for s, sc in self._subscribers[session_id] if s is not send_to_close
                ]
                if not self._subscribers[session_id]:
                    del self._subscribers[session_id]

        if send_to_close is not None:
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await send_to_close.aclose()

    def _get_parent(self, session_id: str) -> str | None:
        """Find the parent of a session in the session tree."""
        if self._session_controller is not None:
            parent_state = self._session_controller.get_parent(session_id)
            if parent_state is not None:
                return parent_state.session_id
        for parent_id, children in self._session_tree.items():
            if session_id in children:
                return parent_id
        return None

    def _is_descendant(self, child_id: str, parent_id: str) -> bool:
        """Check if child_id is a descendant of parent_id."""
        if self._session_controller is not None:
            children = self._session_controller.get_children(parent_id)
        else:
            children = self._session_tree.get(parent_id, [])
        return child_id in children or any(
            self._is_descendant(child_id, child) for child in children
        )

    def _are_siblings(self, sid1: str, sid2: str) -> bool:
        """Check if two sessions share the same parent."""
        parent1 = self._get_parent(sid1)
        parent2 = self._get_parent(sid2)
        return parent1 is not None and parent1 == parent2

    def _should_receive(self, published_sid: str, subscriber_sid: str, scope: str) -> bool:
        """Determine if a published event should reach a subscriber."""
        if scope == "session":
            return published_sid == subscriber_sid
        if scope == "descendants":
            return published_sid == subscriber_sid or self._is_descendant(
                published_sid, subscriber_sid
            )
        if scope == "subtree":
            return (
                published_sid == subscriber_sid
                or published_sid == self._get_parent(subscriber_sid)
                or self._are_siblings(published_sid, subscriber_sid)
            )
        if scope == "all":
            return True
        return published_sid == subscriber_sid

    async def _send(self, session_id: str, envelope: EventEnvelope) -> None:
        """Send a single envelope to all matching subscribers.

        Appends to the replay buffer, collects target subscribers under
        ``_lock``, then sends to each target with hybrid backpressure
        (0.1s timeout → ``send_nowait`` → drop subscriber). Cleans up
        dead streams afterwards.

        Args:
            session_id: The session that produced the event.
            envelope: The pre-constructed event envelope to broadcast.
        """
        async with self._lock:
            if session_id not in self._replay_buffers:
                self._replay_buffers[session_id] = deque(maxlen=self._replay_buffer_size)
            self._replay_buffers[session_id].append(envelope)

            targets: list[tuple[anyio.abc.ObjectSendStream[EventEnvelope], str]] = []
            for subscriber_sid, subscribers in self._subscribers.items():
                for send_stream, scope in subscribers:
                    if self._should_receive(session_id, subscriber_sid, scope):
                        targets.append((send_stream, scope))

        dead_streams: list[anyio.abc.ObjectSendStream[EventEnvelope]] = []
        for send_stream, _scope in targets:
            try:
                with anyio.fail_after(0.1):
                    await send_stream.send(envelope)
            except TimeoutError:
                try:
                    send_stream.send_nowait(envelope)  # type: ignore[attr-defined]
                except anyio.WouldBlock:
                    dead_streams.append(send_stream)
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                dead_streams.append(send_stream)

        if dead_streams:
            dead_set = set(dead_streams)
            async with self._lock:
                for subscriber_sid in list(self._subscribers):
                    self._subscribers[subscriber_sid] = [
                        item
                        for item in self._subscribers[subscriber_sid]
                        if item[0] not in dead_set
                    ]
                    if not self._subscribers[subscriber_sid]:
                        del self._subscribers[subscriber_sid]
                dead_ids = {sid for sid, stream in self._stream_pairs.items() if stream in dead_set}
                for sid in dead_ids:
                    self._stream_pairs.pop(sid, None)

        for stream in dead_streams:
            try:
                await stream.aclose()
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
            except Exception:
                logger.exception("Failed to close dead stream")

    async def publish(self, session_id: str, event: Any) -> None:
        """Publish an event to all subscribers for a session.

        Wraps the event in an EventEnvelope and sends it directly via _send().
        PartDeltaEvent with delta=None is dropped (no content to deliver).
        Coalescing is handled subscriber-side by drain_and_merge().

        Args:
            session_id: The session that produced the event.
            event: The event to broadcast.
        """
        if isinstance(event, PartDeltaEvent) and event.delta is None:
            return
        envelope = EventEnvelope(source_session_id=session_id, event=event)
        await self._send(session_id, envelope)

    async def close_session(self, session_id: str) -> None:
        """Close all subscriptions for a session.

        Closes all send streams to signal EndOfStream to consumers,
        and clears the replay buffer.

        Args:
            session_id: The session to close subscriptions for.
        """
        self._replay_buffers.pop(session_id, None)

        async with self._lock:
            subscribers = self._subscribers.pop(session_id, [])
            send_streams = [send_stream for send_stream, _scope in subscribers]

        for send_stream in send_streams:
            try:
                await send_stream.aclose()
            except (anyio.BrokenResourceError, anyio.ClosedResourceError):
                pass
            except Exception:
                logger.exception("Failed to close send stream during session close")

    async def get_subscriber_counts(self) -> dict[str, int]:
        """Get subscriber counts per session.

        Returns:
            A snapshot mapping session IDs to subscriber counts.
        """
        async with self._lock:
            return {sid: len(items) for sid, items in self._subscribers.items()}
