"""SessionPool core orchestration layer.

Provides session lifecycle management, turn execution, event routing,
and auto-resume capabilities for agent sessions.
"""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from itertools import groupby
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final
import uuid

import anyio
from pydantic_ai import TextPartDelta, ThinkingPartDelta, ToolCallPartDelta
from pydantic_ai.messages import ModelMessage

from agentpool.agents.context import AgentRunContext
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
from agentpool.agents.native_agent.checkpoint import CheckpointData
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.models.pending_interaction import PendingPermission
from agentpool.orchestrator.run import RunHandle, RunStatus, inject_cancelled_tool_results
from agentpool.orchestrator.runtime_registry import RuntimeAgentRegistry
from agentpool.sessions.models import PendingDeferredCall, SessionData
from agentpool_server.opencode_server.models.session_info import SessionInfo


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.native_agent import Agent
    from agentpool.delegation import AgentPool
    from agentpool.delegation.team import Team
    from agentpool.delegation.teamrun import TeamRun
    from agentpool.mcp_server.config_snapshot import McpConfigEntry, McpConfigSnapshot
    from agentpool.mcp_server.session_pool import SessionConnectionPool
    from agentpool.sessions.store import SessionStore
    from agentpool_config.teams import TeamConfig


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


logger = get_logger(__name__)

# Constants
DEFAULT_QUEUE_MAXSIZE: Final[int] = 1000
DEFAULT_MAX_AUTO_RESUME: Final[int] = 10
DEFAULT_SESSION_TTL_SECONDS: Final[float] = 3600.0


class SessionNotFoundError(Exception):
    """Raised when a session cannot be found for resume."""

    def __init__(self, session_id: str) -> None:
        super().__init__(f"Session not found: {session_id}")
        self.session_id = session_id


class SessionBusyError(Exception):
    """Raised when trying to resume a session that has an active run."""

    def __init__(self, session_id: str, run_id: str) -> None:
        super().__init__(
            f"Session '{session_id}' already has an active run '{run_id}'. Wait for it to complete or cancel it first."
        )
        self.session_id = session_id
        self.run_id = run_id


class CheckpointMismatchError(Exception):
    """Raised when deferred_tool_results don't cover all pending_deferred_calls."""

    def __init__(
        self,
        session_id: str,
        expected: set[str],
        provided: set[str],
        missing: set[str],
        extra: set[str],
    ) -> None:
        parts: list[str] = []
        if missing:
            parts.append(f"missing results for: {sorted(missing)}")
        if extra:
            parts.append(f"unexpected results for: {sorted(extra)}")
        msg = (
            f"Checkpoint mismatch for session '{session_id}': "
            + "; ".join(parts)
            + f". Expected tool_call_ids: {sorted(expected)}, provided: {sorted(provided)}."
        )
        super().__init__(msg)
        self.session_id = session_id
        self.expected = expected
        self.provided = provided
        self.missing = missing
        self.extra = extra


class SessionLifecyclePolicy:
    """Session lifecycle policy constants and helpers."""

    VALID: ClassVar[tuple[str, str, str]] = ("independent", "cascade", "bound")

    @classmethod
    def default(cls) -> str:
        return "cascade"

    @classmethod
    def is_valid(cls, policy: str) -> bool:
        return policy in cls.VALID


def _create_cancel_scope() -> anyio.CancelScope | None:
    """Create CancelScope if an event loop is running, else return None.

    Allows SessionState to be instantiated in synchronous contexts (e.g. tests)
    where no async event loop is available.
    """
    try:
        return anyio.CancelScope()
    except anyio.NoEventLoopError:
        return None


@dataclass
class SessionState:
    """Per-session state managed by the session pool.

    Attributes:
        session_id: Unique identifier for the session.
        agent_name: Name of the agent associated with this session.
        agent: The actual agent instance (shared or per-session).
        metadata: Arbitrary metadata attached to the session.
        created_at: Timestamp when the session was created.
        last_active_at: Timestamp of the most recent activity.
        closed_at: Timestamp when the session was closed, or None if active.
        is_per_session_agent: Whether the agent is dedicated to this session.
        turn_lock: Lock ensuring only one turn runs per session at a time.
        is_closing: Flag indicating the session is being closed.
    """

    session_id: str
    agent_name: str
    agent: BaseAgent[Any, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.monotonic)
    last_active_at: float = field(default_factory=time.monotonic)
    closed_at: float | None = None
    is_per_session_agent: bool = False
    turn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    is_closing: bool = False
    parent_session_id: str | None = None
    lifecycle_policy: str = field(default_factory=SessionLifecyclePolicy.default)
    current_run_id: str | None = None
    cancel_scope: anyio.CancelScope | None = field(default_factory=_create_cancel_scope)
    _request_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _turn_owner_task: asyncio.Task[Any] | None = None
    input_provider: Any | None = None
    pending_questions: dict[str, Any] = field(default_factory=dict)
    """Pending questions stored on SessionState for per-session isolation."""

    @property
    def closing(self) -> bool:
        """Alias for is_closing."""
        return self.is_closing

    @closing.setter
    def closing(self, value: bool) -> None:
        self.is_closing = value


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


def _merge_key(event: Any) -> tuple[str, str] | None:
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
    parts: list[str] = []
    for event in events:
        if isinstance(event.delta, TextPartDelta) and event.delta.content_delta is not None:
            parts.append(event.delta.content_delta)
    return PartDeltaEvent(
        index=events[0].index,
        delta=TextPartDelta(content_delta="".join(parts)),
    )


def _merge_thinking_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ThinkingPartDelta content_delta strings. Uses first event's index."""
    parts: list[str] = []
    for event in events:
        if isinstance(event.delta, ThinkingPartDelta) and event.delta.content_delta is not None:
            parts.append(event.delta.content_delta)
    return PartDeltaEvent(
        index=events[0].index,
        delta=ThinkingPartDelta(content_delta="".join(parts)),
    )


def _merge_tool_call_deltas(events: list[PartDeltaEvent]) -> PartDeltaEvent:
    """Concatenate ToolCallPartDelta args_delta strings.

    Uses first event's index and tool_call_id.
    """
    parts: list[str] = []
    tool_call_id = ""
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
                item = stream.receive_nowait()
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
                    send_stream.send_nowait(envelope)
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
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await stream.aclose()

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
            with contextlib.suppress(anyio.BrokenResourceError, anyio.ClosedResourceError):
                await send_stream.aclose()

    async def get_subscriber_counts(self) -> dict[str, int]:
        """Get subscriber counts per session.

        Returns:
            A snapshot mapping session IDs to subscriber counts.
        """
        async with self._lock:
            return {sid: len(items) for sid, items in self._subscribers.items()}


class SessionController:
    """Manages per-session agent lifecycle.

    Extracted from ACP's AgentPoolACPAgent._session_agents and
    OpenCode's ServerState._session_agents.

    Safety features:
    - Single global lock for session creation (no DCL)
    - Per-session turn lock for serialization
    - Explicit cleanup of all resources
    - Support for all agent types (with per-session agents for NativeAgentConfig only)
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        store: SessionStore | None = None,
        cleanup_callback: Callable[[str], Awaitable[None]] | None = None,
        max_concurrent_runs: int | None = None,
    ) -> None:
        """Initialize the session controller.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            cleanup_callback: Optional callback invoked when a session is cleaned up.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
        """
        self.pool = pool
        self.store = store
        self._cleanup_callback = cleanup_callback
        self._sessions: dict[str, SessionState] = {}
        self._session_agents: dict[str, BaseAgent[Any, Any]] = {}
        self._children: dict[str, list[str]] = {}
        self._session_scopes: dict[str, anyio.CancelScope] = {}
        self._lock = asyncio.Lock()
        self._session_ttl_seconds: float = DEFAULT_SESSION_TTL_SECONDS
        self._cleanup_task: asyncio.Task[Any] | None = None
        self._mcp_max_processes: int = 100
        self._mcp_process_count: int = 0
        self._runs: dict[str, RunHandle] = {}
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._max_concurrent_runs: int | None = max_concurrent_runs
        self._event_bus: EventBus | None = None
        self._pending_run_ids: dict[str, str] = {}
        self._todo_lock: asyncio.Lock = asyncio.Lock()
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._runtime_registry = RuntimeAgentRegistry()

    @property
    def runtime_registry(self) -> RuntimeAgentRegistry:
        """Runtime agent registry for programmatically-created agents."""
        return self._runtime_registry

    async def get_or_create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> tuple[SessionState, bool]:
        """Get or create a session.

        Uses single global lock for simplicity and safety.
        Session creation is infrequent - no need for DCL optimization.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            A tuple of (session_state, was_created) where was_created is True
            if the session was newly created, False if it already existed.
        """
        if not session_id or not session_id.strip():
            raise ValueError("session_id cannot be empty or whitespace")

        async with self._lock:
            return await self._get_or_create_session_locked(
                session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
            )

    def _state_to_data(self, state: SessionState) -> SessionData:
        """Convert SessionState to persistable SessionData.

        Args:
            state: The session state to convert.

        Returns:
            Persistable session data.
        """
        return SessionData(
            session_id=state.session_id,
            agent_name=state.agent_name,
            parent_id=state.parent_session_id,
            project_id=state.metadata.get("project_id"),
            cwd=state.metadata.get("cwd"),
            agent_type=state.metadata.get("agent_type"),
            created_at=datetime.fromtimestamp(state.created_at),
            last_active=datetime.fromtimestamp(state.last_active_at),
            metadata=state.metadata,
        )

    async def _get_or_create_session_locked(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> tuple[SessionState, bool]:
        """Get or create a session - caller MUST hold self._lock.

        This internal method avoids deadlock when called from
        get_or_create_session_agent() which already holds the lock.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            A tuple of (session_state, was_created) where was_created is True
            if the session was newly created, False if it already existed.
        """
        if session_id in self._sessions:
            state = self._sessions[session_id]
            state.last_active_at = time.monotonic()
            return state, False

        effective_policy = lifecycle_policy or (
            self._sessions.get(parent_session_id, SessionState("", "")).lifecycle_policy
            if parent_session_id and parent_session_id in self._sessions
            else SessionLifecyclePolicy.default()
        )

        # Ensure agent_name is always a real string (guards against Mock
        # attributes in tests where pool.main_agent_name is a MagicMock).
        _main_agent_name = self.pool.main_agent_name
        if not isinstance(_main_agent_name, str):
            _main_agent_name = "default"
        state = SessionState(
            session_id=session_id,
            agent_name=agent_name or _main_agent_name,
            parent_session_id=parent_session_id,
            lifecycle_policy=effective_policy,
            metadata=metadata,
        )
        self._sessions[session_id] = state

        # Clear todos for new top-level sessions only (not subagents)
        # This prevents accumulation of todos from previous sessions
        # Use dedicated lock to prevent race conditions with concurrent sessions
        if (
            parent_session_id is None
            and hasattr(self.pool, "todos")
            and self.pool.todos is not None
        ):
            _entries = self.pool.todos.entries
            if isinstance(_entries, (list, tuple)) and len(_entries) > 0:
                async with self._todo_lock:
                    # Double-check after acquiring lock
                    _entries = self.pool.todos.entries
                    if isinstance(_entries, (list, tuple)) and len(_entries) > 0:
                        cleared_count = len(_entries)
                        self.pool.todos.clear()
                        logger.info(
                            "Cleared todos for new top-level session",
                            session_id=session_id,
                            agent_name=state.agent_name,
                            cleared_entries=cleared_count,
                        )

        if parent_session_id and effective_policy in ("cascade", "bound"):
            parent_scope = self._session_scopes.get(parent_session_id)
            if parent_scope is not None:
                child_scope = anyio.CancelScope()
                self._session_scopes[session_id] = child_scope
            else:
                self._session_scopes[session_id] = anyio.CancelScope()
        else:
            self._session_scopes[session_id] = anyio.CancelScope()
        if self.store is not None:
            # Only save if no existing data — callers like
            # ACPSessionManager.create_session() may have already
            # persisted richer SessionData (with cwd, project_id, etc.)
            existing = await self.store.load(session_id)
            if existing is None:
                await self.store.save(self._state_to_data(state))
        if parent_session_id:
            self._children.setdefault(parent_session_id, []).append(session_id)
        logger.info("Created session", session_id=session_id, agent_name=state.agent_name)
        return state, True

    async def get_or_create_session_agent(
        self,
        session_id: str,
        agent_name: str | None = None,
        input_provider: Any | None = None,
    ) -> BaseAgent[Any, Any]:
        """Get or create a dedicated agent for a session.

        Creates per-session agent for NativeAgentConfig only.
        Falls back to shared agent for other agent types.

        NOTE: Always acquires self._lock to prevent races with close_session().

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to use.
            input_provider: Optional input provider for the agent.

        Returns:
            The agent instance (per-session or shared).
        """
        async with self._lock:
            if session_id in self._session_agents:
                agent = self._session_agents[session_id]
                # Update input_provider on cached agent if a new one is provided.
                # Without this, the agent keeps the stale (or None) input_provider
                # from when it was first cached, causing elicitation failures.
                if input_provider is not None:
                    session = self._sessions.get(session_id)
                    if session is not None:
                        session.input_provider = input_provider
                    agent._input_provider = input_provider
                return agent

            session, _was_created = await self._get_or_create_session_locked(session_id, agent_name)
            agent_name = agent_name or session.agent_name

            from agentpool.models.agents import NativeAgentConfig
            from agentpool_config.context import ConfigContextManager

            cfg = self.pool.manifest.agents.get(agent_name)
            if cfg is None:
                cfg = self._runtime_registry.lookup(agent_name)

            if isinstance(cfg, NativeAgentConfig):
                if session.parent_session_id:
                    # Child session: create lightweight agent inheriting
                    # from parent session's agent.  Shares MCP manager
                    # to avoid duplicate subprocess spawning.
                    parent_state = self._sessions.get(session.parent_session_id)
                    parent_agent = parent_state.agent if parent_state else None

                    if cfg.name is None:
                        cfg = cfg.model_copy(update={"name": agent_name})

                    with ConfigContextManager(self.pool._config_file_path):
                        agent: Agent[Any, Any] = cfg.get_agent(
                            input_provider=input_provider,
                            pool=self.pool,
                        )

                    # Preserve runtime resources from parent agent.
                    # Model is NOT inherited — each agent uses its own configured
                    # model from the manifest. Inheriting the parent's model would
                    # cause e.g. TestModel with call_tools=['task'] to override
                    # the child's own model configuration.
                    if parent_agent is not None:
                        if parent_agent.env is not None:
                            agent.env = parent_agent.env
                        agent._internal_fs = parent_agent._internal_fs

                    await agent.__aenter__()

                    # Build MCP config snapshot from parent's snapshot and
                    # child's own agent configs. pool_configs and
                    # session_configs are inherited from the parent so that
                    # child agents share the same pool-level MCP servers and
                    # any session-scoped injections. agent_configs come from
                    # the child's own YAML. skill_configs are empty at
                    # creation time (populated later by skill loading).
                    from agentpool.mcp_server.config_snapshot import (
                        McpConfigSnapshot as _McpConfigSnapshot,
                    )
                    from agentpool.mcp_server.session_pool import (
                        SessionConnectionPool as _SessionConnectionPool,
                    )

                    parent_snapshot: McpConfigSnapshot | None = None
                    if parent_agent is not None:
                        from agentpool.agents.native_agent import Agent as _NativeAgent

                        if isinstance(parent_agent, _NativeAgent):
                            parent_snapshot = parent_agent._mcp_snapshot

                    snapshot = _McpConfigSnapshot(
                        pool_configs=(
                            parent_snapshot.pool_configs
                            if parent_snapshot is not None
                            else ()
                        ),
                        agent_configs=agent._build_agent_configs(),
                        session_configs=(
                            parent_snapshot.session_configs
                            if parent_snapshot is not None
                            else ()
                        ),
                        skill_configs=(),
                    )
                    agent._mcp_snapshot = snapshot
                    agent._session_connection_pool = _SessionConnectionPool(session_id)

                    # Share pre-created ACP transports from parent.
                    # AcpMcpTransport now supports concurrent connect_session()
                    # calls — each creates an independent per-session stream
                    # pair, so parent and child can share the same transport.
                    if (
                        parent_agent is not None
                        and isinstance(parent_agent, _NativeAgent)
                        and parent_agent._session_connection_pool is not None
                    ):
                        await agent._session_connection_pool.copy_pre_created_transports(
                            parent_agent._session_connection_pool
                        )

                    # Add non-MCP pool-level providers (skills instruction
                    # and skills tools). MCP no longer goes through providers —
                    # it uses the snapshot-based capability path in
                    # get_agentlet() instead.
                    # ACP MCP servers still need the aggregating provider
                    # so ACP agents can serialize MCP configs to child
                    # sessions via mcp_config_to_acp().
                    if self.pool is not None:
                        if self.pool.skills_instruction_provider:
                            agent.tools.add_provider(self.pool.skills_instruction_provider)
                        agent.tools.add_provider(self.pool.skills_tools_provider)
                        agent.tools.add_provider(self.pool.mcp.get_aggregating_provider())

                    if input_provider is not None:
                        session.input_provider = input_provider
                    self._session_agents[session_id] = agent
                    session.agent = agent
                    # is_per_session_agent=False: close_session() skips
                    # agent.__aexit__() since parent manages lifecycle
                    session.is_per_session_agent = False
                    logger.info(
                        "Created child session agent",
                        session_id=session_id,
                        agent_name=agent_name,
                        parent_session_id=session.parent_session_id,
                    )
                    return agent

                # Main path: create fresh per-session agent from config
                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": agent_name})

                with ConfigContextManager(self.pool._config_file_path):
                    agent: Agent[Any, Any] = cfg.get_agent(
                        input_provider=input_provider,
                        pool=self.pool,
                    )

                await agent.__aenter__()

                # Load conversation history into per-session agent from storage
                try:
                    await agent.load_session(session_id)
                except Exception:
                    logger.exception(
                        "Failed to load session for per-session agent",
                        session_id=session_id,
                    )

                # Build MCP config snapshot at agent creation time.
                # pool_configs come from the pool's MCPManager, agent_configs
                # from the agent's own MCPManager. session_configs and
                # skill_configs are empty at creation time.
                from agentpool.mcp_server.config_snapshot import (
                    McpConfigSnapshot as _McpConfigSnapshot,
                )
                from agentpool.mcp_server.session_pool import (
                    SessionConnectionPool as _SessionConnectionPool,
                )

                snapshot = _McpConfigSnapshot(
                    pool_configs=agent._build_pool_configs(),
                    agent_configs=agent._build_agent_configs(),
                    session_configs=(),
                    skill_configs=(),
                )
                agent._mcp_snapshot = snapshot
                agent._session_connection_pool = _SessionConnectionPool(session_id)

                # Add non-MCP pool-level providers (skills instruction
                # and skills tools). MCP no longer goes through providers.
                if self.pool is not None:
                    if self.pool.skills_instruction_provider:
                        agent.tools.add_provider(self.pool.skills_instruction_provider)
                    agent.tools.add_provider(self.pool.skills_tools_provider)

                self._session_agents[session_id] = agent
                session.agent = agent
                session.is_per_session_agent = True
                self._increment_mcp_count(agent)
                logger.info("Created session agent", session_id=session_id, agent_name=agent_name)
                return agent

            # Non-native agents (ACP, etc.): create per-session agent from config
            if cfg is not None:
                if cfg.name is None:
                    cfg = cfg.model_copy(update={"name": agent_name})

                with ConfigContextManager(self.pool._config_file_path):
                    agent = cfg.get_agent(
                        input_provider=input_provider,
                        pool=self.pool,
                    )

                await agent.__aenter__()

                # Build MCP config snapshot directly for non-native agents.
                # Non-native agents (ACP, etc.) don't have _build_pool_configs
                # or _build_agent_configs methods, so we construct the entries
                # from the pool's MCPManager and the agent config's
                # get_mcp_servers() method.
                from agentpool.mcp_server.config_snapshot import (
                    McpConfigEntry as _McpConfigEntry,
                    McpConfigSnapshot as _McpConfigSnapshot,
                )
                from agentpool.mcp_server.session_pool import (
                    SessionConnectionPool as _SessionConnectionPool,
                )

                pool_configs: tuple[McpConfigEntry, ...] = ()
                if self.pool is not None:
                    pool_configs = tuple(
                        _McpConfigEntry(server_config=server, source="pool")
                        for server in self.pool.mcp.servers
                        if server.enabled
                    )
                agent_configs: tuple[McpConfigEntry, ...] = tuple(
                    _McpConfigEntry(server_config=server, source="agent")
                    for server in cfg.get_mcp_servers()
                    if server.enabled
                )
                snapshot = _McpConfigSnapshot(
                    pool_configs=pool_configs,
                    agent_configs=agent_configs,
                    session_configs=(),
                    skill_configs=(),
                )
                agent._mcp_snapshot = snapshot
                agent._session_connection_pool = _SessionConnectionPool(session_id)

                # Add non-MCP pool-level providers (skills instruction
                # and skills tools). MCP no longer goes through providers.
                if self.pool is not None:
                    if self.pool.skills_instruction_provider:
                        agent.tools.add_provider(self.pool.skills_instruction_provider)
                    agent.tools.add_provider(self.pool.skills_tools_provider)

                self._session_agents[session_id] = agent
                session.agent = agent
                session.is_per_session_agent = True
                self._increment_mcp_count(agent)
                logger.info("Created session agent", session_id=session_id, agent_name=agent_name)
                return agent

            # Config not found
            available_manifest = list(self.pool.manifest.agents.keys())
            available_runtime = self._runtime_registry.names()
            msg = (
                f"Agent config not found: {agent_name!r}. "
                f"Available in manifest: {available_manifest}. "
                f"Available in runtime registry: {available_runtime}."
            )
            raise RuntimeError(msg)

    def list_sessions(self) -> list[SessionInfo]:
        """List all active sessions.

        Returns:
            A list of SessionInfo DTOs for all active sessions.
        """
        return [
            SessionInfo(
                session_id=s.session_id,
                agent_name=s.agent_name,
                created_at=s.created_at,
                last_active_at=s.last_active_at,
                is_per_session_agent=s.is_per_session_agent,
                status="busy" if s.current_run_id is not None else "idle",
            )
            for s in self._sessions.values()
        ]

    def get_session_agent(self, session_id: str) -> BaseAgent[Any, Any] | None:
        """Get the agent for a session.

        Returns the per-session agent if one exists, otherwise the shared
        agent that was assigned to the session.  If the session has no
        agent assigned yet, a warning is logged and None is returned.

        Args:
            session_id: The session ID to look up.

        Returns:
            The agent instance, or None if the session is unknown.
        """
        session = self._sessions.get(session_id)
        if session is None:
            logger.warning("Session not found", session_id=session_id)
            return None
        agent = self._session_agents.get(session_id)
        if agent is None:
            logger.warning(
                "No agent assigned for session - falling back to shared agent",
                session_id=session_id,
            )
            return None
        return agent

    async def _close_session_unlocked(self, session_id: str) -> None:
        """Close a session without acquiring the main lock (caller must hold lock)."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.is_closing = True
        session.closed_at = time.monotonic()
        # Recursively close children, respecting their lifecycle policies
        children = self._children.pop(session_id, [])
        for child_id in children:
            child_session = self._sessions.get(child_id)
            if child_session is not None and child_session.lifecycle_policy == "independent":
                continue
            await self._close_session_unlocked(child_id)
        self._session_agents.pop(session_id, None)
        self._sessions.pop(session_id, None)
        if self.store is not None:
            await self._mark_session_closed(session_id)
        # Remove from parent's children list
        if session.parent_session_id and session.parent_session_id in self._children:
            self._children[session.parent_session_id] = [
                cid for cid in self._children[session.parent_session_id] if cid != session_id
            ]

    @staticmethod
    def _should_checkpoint_on_close(data: SessionData | None) -> bool:
        """Check whether a session should be checkpointed before close.

        A session needs checkpoint-on-close when it has pending deferred calls
        that must be preserved for later resume.

        Args:
            data: The session data loaded from the store, or None.

        Returns:
            True if the session has pending deferred calls that require
            checkpointing before releasing resources.
        """
        return data is not None and bool(data.pending_deferred_calls)

    @staticmethod
    def _check_expired_calls(session_data: SessionData) -> list[PendingDeferredCall]:
        """Return pending calls whose timeout has elapsed.

        Args:
            session_data: The session data to check for expired calls.

        Returns:
            A list of ``PendingDeferredCall`` entries whose timeout has
            elapsed. Returns an empty list if none have expired.
        """
        now = datetime.now()
        expired: list[PendingDeferredCall] = []
        for call in session_data.pending_deferred_calls:
            if call.timeout is not None and (now - call.created_at) > call.timeout:
                expired.append(call)
        return expired

    async def _save_close_checkpoint(self, session_id: str, data: SessionData) -> bool:
        """Save session data with checkpointed status before close.

        Marks the session as ``"checkpointed"`` so it can be located by
        :meth:`resume_session` later. Returns ``True`` on success, ``False``
        if the storage write fails (caller should NOT release resources).

        Args:
            session_id: Session identifier (for logging).
            data: The session data to persist as checkpointed.

        Returns:
            True if the checkpoint was saved successfully, False on failure.
        """
        try:
            data = data.model_copy(update={"status": "checkpointed"})
            data.touch()
            if self.store is not None:
                await self.store.save(data)
            logger.info(
                "Session checkpointed before close",
                session_id=session_id,
                pending_call_count=len(data.pending_deferred_calls),
            )
            return True
        except Exception:
            logger.exception(
                "Failed to save checkpoint before close",
                session_id=session_id,
            )
            return False

    async def _mark_session_closed(self, session_id: str) -> None:
        """Mark a session as closed in the store instead of deleting it.

        This preserves session data across server restarts so that clients
        can resume sessions via ``session/resume`` or ``session/load`` after
        a server restart.

        Args:
            session_id: Session identifier to mark as closed.
        """
        assert self.store is not None
        data = await self.store.load(session_id)
        if data is None:
            logger.debug("Session not in store, skipping close mark", session_id=session_id)
            return
        data = data.model_copy(update={"status": "closed"})
        data.touch()
        await self.store.save(data)
        logger.debug("Session marked as closed in store", session_id=session_id)

    async def _close_session_run_turn(self, session_id: str) -> None:  # noqa: PLR0915
        """Close a session using the RunHandle lifecycle.

        Flow:
        1. Signal ``RunHandle.close()`` (sets ``_closing``, wakes idle loop).
        2. Mark ``session.closing = True``.
        3. Cancel the session ``CancelScope``.
        4. Acquire ``turn_lock`` (10 s timeout) — graceful turn completion.
        5. Await ``complete_event`` (10 s timeout) — graceful run completion.
        6. On timeout: call ``RunHandle.cancel()``.
        7. Clean up tracking dicts and agent context.

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return

            run_handle: RunHandle | None = None
            if session.current_run_id:
                run_handle = self._runs.get(session.current_run_id)
            if run_handle is not None:
                run_handle.close()

            session.closing = True
            session.closed_at = time.monotonic()

            scope = self._session_scopes.pop(session_id, None)
            if scope is not None:
                scope.cancel()

        acquired = False
        try:
            try:
                async with asyncio.timeout(10):
                    await session.turn_lock.acquire()
                acquired = True
            except TimeoutError:
                logger.warning(
                    "Timeout waiting for turn_lock during close_session (run-turn path)",
                    session_id=session_id,
                )

            if run_handle is not None and acquired:
                # Signal the idle/wake loop to exit so complete_event gets set.
                run_handle.close()
                try:
                    async with asyncio.timeout(2):
                        await run_handle.complete_event.wait()
                except TimeoutError:
                    logger.warning(
                        "Timeout waiting for run completion, cancelling",
                        session_id=session_id,
                    )
                    run_handle.cancel()
            elif run_handle is not None:
                run_handle.cancel()
        finally:
            if acquired:
                session.turn_lock.release()

        # Checkpoint-on-close: if pending deferred calls exist, save as
        # checkpointed before releasing resources. If checkpoint fails,
        # keep session in memory so it can be retried.
        _checkpointed = False
        if self.store is not None:
            _data = await self.store.load(session_id)
            if self._should_checkpoint_on_close(_data):
                assert _data is not None
                _checkpointed = await self._save_close_checkpoint(session_id, _data)
                if not _checkpointed:
                    logger.warning(
                        "Checkpoint failed, keeping session in memory",
                        session_id=session_id,
                    )
                    return

        async with self._lock:
            children = self._children.pop(session_id, [])
            if children:
                for child_id in children:
                    child_session = self._sessions.get(child_id)
                    if (
                        child_session is not None
                        and child_session.lifecycle_policy == "independent"
                    ):
                        continue
                    await self._close_session_unlocked(child_id)

            agent = self._session_agents.pop(session_id, None)
            self._sessions.pop(session_id, None)
            if self.store is not None and not _checkpointed:
                await self._mark_session_closed(session_id)
            if session.parent_session_id and session.parent_session_id in self._children:
                self._children[session.parent_session_id] = [
                    cid for cid in self._children[session.parent_session_id] if cid != session_id
                ]

        if agent is not None and session.is_per_session_agent:
            try:
                await agent.__aexit__(None, None, None)
            except Exception:
                logger.exception("Failed to exit agent context", session_id=session_id)
            finally:
                self._decrement_mcp_count(agent)

        logger.info("Closed session (run-turn path)", session_id=session_id)

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up resources.

        Uses the RunHandle lifecycle:
        1. Signal ``RunHandle.close()`` (sets ``_closing``, wakes idle loop).
        2. Mark ``session.closing = True``.
        3. Cancel the session ``CancelScope``.
        4. Acquire ``turn_lock`` (10 s timeout) — graceful turn completion.
        5. Await ``complete_event`` (10 s timeout) — graceful run completion.
        6. On timeout: call ``RunHandle.cancel()``.
        7. Clean up tracking dicts and agent context.

        Args:
            session_id: The session to close.
        """
        await self._close_session_run_turn(session_id)

    def get_session(self, session_id: str) -> SessionState | None:
        """Get a session by ID.

        Args:
            session_id: The session ID to look up.

        Returns:
            The session state, or None if not found.
        """
        return self._sessions.get(session_id)

    def get_children(self, session_id: str) -> list[str]:
        """Get child session IDs for a session.

        Args:
            session_id: The parent session ID.

        Returns:
            List of child session IDs.
        """
        return list(self._children.get(session_id, []))

    def get_parent(self, session_id: str) -> SessionState | None:
        """Get the parent session state for a session.

        Args:
            session_id: The child session ID.

        Returns:
            The parent session state, or None if not found.
        """
        session = self._sessions.get(session_id)
        if session is None or session.parent_session_id is None:
            return None
        return self._sessions.get(session.parent_session_id)

    def find_sessions_by_agent_name(self, agent_name: str) -> list[SessionState]:
        """Find all active sessions associated with a given agent name.

        Args:
            agent_name: The agent name to search for.

        Returns:
            List of session states matching the agent name, excluding closing sessions.
        """
        return [
            s for s in self._sessions.values() if s.agent_name == agent_name and not s.is_closing
        ]

    async def _consume_run(self, run_handle: RunHandle, initial_prompt: str) -> None:
        """Drive a RunHandle.start() async generator to completion.

        Events are published to the EventBus inside ``start()``, so this
        coroutine only needs to keep the generator alive until the first
        turn completes (StreamCompleteEvent or RunErrorEvent). After that,
        the generator is closed so that ``start()`` exits its idle/wake
        loop and ``complete_event`` is set.

        If ``start()`` raises an exception before yielding a terminal
        event, a ``RunErrorEvent`` and ``RunFailedEvent`` are published to
        the EventBus so that subscribers (e.g. background_output in
        BackgroundTaskCapability) are unblocked instead of waiting forever.

        Args:
            run_handle: The run handle whose ``start()`` to consume.
            initial_prompt: The first user prompt.
        """
        from agentpool.agents.events import RunErrorEvent, StreamCompleteEvent

        gen = run_handle.start(initial_prompt)
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                    break
        except Exception as exc:
            logger.exception(
                "RunHandle.start() raised for run_id=%s session_id=%s",
                run_handle.run_id,
                run_handle.session_id,
            )
            error_event = RunErrorEvent(
                message=f"{type(exc).__name__}: {exc}",
                run_id=run_handle.run_id,
                agent_name=run_handle.agent_type,
            )
            if self._event_bus is not None:
                await self._event_bus.publish(run_handle.session_id, error_event)
                await self._event_bus.publish(
                    run_handle.session_id,
                    RunFailedEvent(
                        run_id=run_handle.run_id,
                        session_id=run_handle.session_id,
                        exception=exc,
                    ),
                )
        finally:
            await gen.aclose()

    def _start_run_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
        content: str,
        *,
        deps: Any = None,
    ) -> RunHandle:
        """Create, register, and launch a RunHandle via the new path.

        Args:
            session: The session state.
            agent: The agent instance (native or ACP).
            session_id: The session identifier.
            content: The initial prompt text.
            deps: Optional dependencies to pass to the agent run context
                (e.g. delegation_depth from BackgroundTaskCapability).

        Returns:
            The newly created RunHandle.
        """
        event_bus = self._event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus, deps=deps)
        # Bridge agent.conversation (ChatMessage list) → list[ModelMessage]
        # so the new RunHandle has the full conversation history from prior
        # turns. Without this, each new RunHandle starts with empty
        # _message_history and the model loses all context.
        # Not all agent types have a conversation attribute (e.g. ACP agents),
        # so use getattr with a fallback.
        model_messages: list[ModelMessage] = []
        conversation = getattr(agent, "conversation", None)
        if conversation is not None:
            for chat_msg in conversation.get_history():
                model_messages.extend(chat_msg.messages)
        # Inject RetryPromptPart for any trailing unprocessed tool calls
        # (e.g. from a cancelled turn). Without this, PydanticAI rejects
        # the next user prompt with "unprocessed tool calls" error.
        model_messages = inject_cancelled_tool_results(model_messages)
        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
            _message_history=model_messages,
        )
        self._runs[run_handle.run_id] = run_handle
        session.current_run_id = run_handle.run_id
        task = asyncio.create_task(self._consume_run(run_handle, content))
        # Keep a strong reference to prevent GC from destroying the task.
        self._background_tasks.add(task)

        def _on_run_done(t: asyncio.Task[Any], rid: str = run_handle.run_id) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "Background run task failed for run_id=%s: %s",
                    rid,
                    t.exception(),
                )
            self._cleanup_run(rid)

        task.add_done_callback(_on_run_done)
        return run_handle

    async def receive_request(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Receive an incoming request for a session.

        Routes through the RunHandle path: idle sessions create a
        RunHandle, busy sessions call ``steer()`` / ``followup()``.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: ``"when_idle"`` to queue, ``"asap"`` to inject into active turn.
                Aliases: ``"steer"`` → ``"asap"``, ``"followup"`` → ``"when_idle"``.
            **kwargs: Additional arguments passed to the turn runner (e.g. input_provider).

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        session = self.get_session(session_id)
        if session is None:
            return None
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        # Extract deps from kwargs so they are passed to AgentRunContext
        # for the child agent run (e.g. delegation_depth from
        # BackgroundTaskCapability._task_async).
        deps = kwargs.pop("deps", None)
        agent = await self.get_or_create_session_agent(session_id, input_provider=input_provider)
        if agent is None:
            return None
        # RunHandle path (always)
        resolved = {"steer": "asap", "followup": "when_idle"}.get(priority, priority)
        # Convert content to string safely. Empty list (from ACP handler when
        # user sends only a slash command) must become "" not "[]".
        # Lists with content should be joined, not str()'d (which produces "['hello']").
        if isinstance(content, list):
            content_str = " ".join(str(c) for c in content) if content else ""
        elif not content:
            content_str = ""
        else:
            content_str = str(content)
        async with session._request_lock:
            if session.closing or session.is_closing:
                return None
            # Stale-run detection: if current_run_id points to a missing
            # or terminal run, clear it and start a new run.
            if session.current_run_id is not None:
                existing_run = self._runs.get(session.current_run_id)
                if existing_run is None or existing_run._status in (
                    RunStatus.failed,
                    RunStatus.completed,
                    RunStatus.done,
                ):
                    session.current_run_id = None
            if session.current_run_id is None:
                return self._start_run_handle(session, agent, session_id, content_str, deps=deps)
            run = self._runs.get(session.current_run_id) if session.current_run_id else None
            if run is not None:
                if resolved == "asap":
                    run.steer(content_str)
                else:
                    run.followup(content_str)
        return None

    def cancel_run_for_session(self, session_id: str) -> None:
        """Cancel the active run for a session.

        Args:
            session_id: The session whose run should be cancelled.
        """
        session = self.get_session(session_id)
        if session is None:
            return
        run_id = session.current_run_id
        if run_id is None:
            return
        run_handle = self._runs.get(run_id)
        if run_handle is None:
            return
        run_handle.cancel()

    def _cleanup_run(self, run_id: str) -> None:
        """Clean up a run after it completes.

        Removes the handle from _runs and signals completion.

        Args:
            run_id: The run ID to clean up.
        """
        run_handle = self._runs.pop(run_id, None)
        if run_handle is not None:
            run_handle.complete_event.set()
            # Clear current_run_id if it still points to this run.
            # This is a safety net — normally start() clears it, but if
            # the run died unexpectedly, current_run_id would be stale.
            session = self.get_session(run_handle.session_id)
            if session is not None and session.current_run_id == run_id:
                session.current_run_id = None

    def _count_mcp_processes(self) -> int:
        """Count active MCP processes across all per-session agents.

        Returns:
            The tracked MCP process count.
        """
        return self._mcp_process_count

    def _increment_mcp_count(self, _agent: BaseAgent[Any, Any]) -> None:
        """Increment MCP process count when a per-session agent is created.

        Args:
            _agent: The agent whose creation triggered the increment.
        """
        self._mcp_process_count += 1

    def _decrement_mcp_count(self, _agent: BaseAgent[Any, Any]) -> None:
        """Decrement MCP process count when a per-session agent is destroyed.

        Args:
            _agent: The agent whose destruction triggered the decrement.
        """
        self._mcp_process_count = max(0, self._mcp_process_count - 1)

    def list_pending_questions(self) -> list[Any]:
        """List all pending questions across sessions.

        Aggregates pending questions from each session's SessionState.

        Returns:
            A list of pending question objects.
        """
        result: list[Any] = []
        for session in self._sessions.values():
            result.extend(session.pending_questions.values())
        return result

    def cancel_all_pending_questions(self) -> list[str]:
        """Cancel all pending questions across all sessions.

        Iterates over every session, cancels each pending question's future,
        and returns the IDs of all cancelled questions.

        Returns:
            List of cancelled question IDs.
        """
        cancelled_ids: list[str] = []
        for session in self._sessions.values():
            for question_id, pending in list(session.pending_questions.items()):
                future = getattr(pending, "future", None)
                if future is not None and not future.done():
                    future.cancel()
                    cancelled_ids.append(question_id)
        return cancelled_ids

    def cancel_session_pending_questions(self, session_id: str) -> list[str]:
        """Cancel pending questions for a specific session.

        Args:
            session_id: The session whose pending questions should be cancelled.

        Returns:
            List of cancelled question IDs.
        """
        cancelled_ids: list[str] = []
        session = self._sessions.get(session_id)
        if session is None:
            return cancelled_ids
        for question_id, pending in list(session.pending_questions.items()):
            future = getattr(pending, "future", None)
            if future is not None and not future.done():
                future.cancel()
                cancelled_ids.append(question_id)
        return cancelled_ids

    def list_pending_permissions(self) -> list[PendingPermission]:
        """List all pending permissions across sessions.

        Returns:
            A list of pending permissions. Currently returns an empty list.
        """
        return []

    async def start_cleanup_task(self) -> None:
        """Start background task to periodically clean up expired sessions."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup_task(self) -> None:
        """Stop the cleanup background task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        """Periodically scan and close expired sessions.

        Runs every session_ttl_seconds / 2 (default: 30 minutes).
        A session is expired if last_active_at is older than session_ttl_seconds.
        """
        while True:
            try:
                await asyncio.sleep(self._session_ttl_seconds / 2)
                await self._cleanup_expired_sessions()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Session cleanup failed")

    async def _cleanup_expired_sessions(self) -> None:
        """Close all sessions that have exceeded TTL.

        Sessions with an active run are never expired — the run itself
        is proof of activity regardless of ``last_active_at`` age.
        """
        now = time.monotonic()
        expired_sessions: list[str] = []

        async with self._lock:
            for session_id, session in list(self._sessions.items()):
                if session.current_run_id is not None:
                    continue
                if now - session.last_active_at > self._session_ttl_seconds:
                    expired_sessions.append(session_id)

        for session_id in expired_sessions:
            logger.info("Closing expired session", session_id=session_id)
            try:
                if self._cleanup_callback is not None:
                    await self._cleanup_callback(session_id)
                else:
                    await self.close_session(session_id)
            except Exception:
                logger.exception(
                    "Failed to close expired session during cleanup",
                    session_id=session_id,
                )

    async def _start_cleanup_loop(self) -> None:
        """Periodically scan and expire deferred calls whose timeout has elapsed.

        Runs indefinitely in a background task. Checks every 60 seconds
        for pending deferred calls whose timeout has elapsed and removes
        them from the session data.
        """
        while True:
            try:
                await asyncio.sleep(60)
                if self.store is None:
                    continue
                async with self._lock:
                    for session_id in list(self._sessions.keys()):
                        data = await self.store.load(session_id)
                        if data is None:
                            continue
                        expired = self._check_expired_calls(data)
                        if expired:
                            remaining = [
                                c
                                for c in data.pending_deferred_calls
                                if c.tool_call_id not in {e.tool_call_id for e in expired}
                            ]
                            updated = data.model_copy(update={"pending_deferred_calls": remaining})
                            await self.store.save(updated)
                            logger.info(
                                "Removed expired deferred calls",
                                session_id=session_id,
                                count=len(expired),
                            )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Deferred call cleanup loop failed")


class SessionPool:
    """High-level session pool combining session and turn management.

    This is the main interface used by protocol handlers.
    """

    def __init__(
        self,
        pool: AgentPool[Any],
        store: SessionStore | None = None,
        enable_auto_resume: bool = True,
        enable_event_bus: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
        max_concurrent_runs: int | None = None,
        replay_buffer_size: int = 100,
    ) -> None:
        """Initialize the session pool.

        Args:
            pool: The agent pool to resolve agents from.
            store: Optional session store for persistence.
            enable_auto_resume: Whether to enable auto-resume loop.
            enable_event_bus: Whether to enable cross-turn event routing.
            max_auto_resume: Maximum auto-resume iterations.
            max_concurrent_runs: Maximum number of concurrent runs across all sessions.
            replay_buffer_size: Maximum number of events retained per session for replay.
        """
        self.pool = pool
        self.sessions = SessionController(
            pool,
            store=store,
            cleanup_callback=self.close_session,
            max_concurrent_runs=max_concurrent_runs,
        )
        self._event_bus = EventBus(
            session_controller=self.sessions,
            replay_buffer_size=replay_buffer_size,
        )
        self.sessions._event_bus = self._event_bus
        self._enable_auto_resume = enable_auto_resume
        self._enable_event_bus = enable_event_bus
        self._runs_lock: asyncio.Lock = asyncio.Lock()
        self._resume_locks: dict[str, asyncio.Lock] = {}
        self._resume_locks_lock = asyncio.Lock()
        self._message_cache: dict[str, list[ChatMessage[Any]]] = {}

    async def start(self) -> None:
        """Start the session pool and background tasks."""
        await self.sessions.start_cleanup_task()
        logger.info("SessionPool started")

    async def shutdown(self) -> None:
        """Shutdown the session pool and cancel background tasks."""
        await self.sessions.stop_cleanup_task()
        active_sessions = list(self.sessions._sessions.keys())
        for session_id in active_sessions:
            try:
                await self.close_session(session_id)
            except Exception:
                logger.exception(
                    "Failed to close session during shutdown",
                    session_id=session_id,
                )
        logger.info("SessionPool shut down")

    @property
    def event_bus(self) -> EventBus:
        """Get the event bus for cross-turn event routing."""
        return self._event_bus

    async def create_session(
        self,
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> SessionState:
        """Create or get a session.

        Args:
            session_id: Unique identifier for the session.
            agent_name: Name of the agent to associate with the session.
            parent_session_id: Optional parent session ID for hierarchical sessions.
            lifecycle_policy: Optional lifecycle policy override.
            **metadata: Arbitrary metadata to attach to the session.

        Returns:
            The session state.
        """
        if parent_session_id is not None and self.sessions.store is not None:
            parent_data = await self.sessions.store.load(parent_session_id)
            if parent_data is not None:
                metadata.setdefault("project_id", parent_data.project_id)
                metadata.setdefault("cwd", parent_data.cwd)
        state, _was_created = await self.sessions.get_or_create_session(
            session_id, agent_name, parent_session_id, lifecycle_policy, **metadata
        )
        return state

    async def create_team_from_config(
        self,
        team_name: str,
        team_config: TeamConfig,
    ) -> Team[Any] | TeamRun[Any, Any]:
        """Create a team from config using session-level agent resolution.

        For each member in the team config, resolves the agent via
        :meth:`SessionController.get_or_create_session_agent`, then
        constructs a :class:`Team` (parallel) or :class:`TeamRun`
        (sequential) using :meth:`TeamConfig.get_team`.

        Member names are stored on the resulting team nodes; actual
        session agents are created per-execution by
        :meth:`Team._resolve_scoped_team_nodes`.

        Args:
            team_name: Name for the created team.
            team_config: Team configuration from the manifest.

        Returns:
            A ``Team`` (parallel) or ``TeamRun`` (sequential) instance.

        Raises:
            ValueError: If a member name is not found in the manifest
                agents or teams sections.
        """
        from agentpool.messaging.messagenode import MessageNode
        from agentpool.utils.identifiers import generate_session_id

        member_names = [team_config.get_member_name(m) for m in team_config.members]

        nodes: list[MessageNode[Any, Any]] = []
        for member_name in member_names:
            cfg = self.pool.manifest.agents.get(member_name)
            if cfg is not None:
                member_session_id = generate_session_id()
                agent = await self.sessions.get_or_create_session_agent(
                    member_session_id,
                    agent_name=member_name,
                )
                nodes.append(agent)
            elif member_name in self.pool.manifest.teams:
                nested_config = self.pool.manifest.teams[member_name]
                nested_team = await self.create_team_from_config(member_name, nested_config)
                nodes.append(nested_team)
            else:
                msg = f"Team member {member_name!r} not found in manifest agents or teams"
                raise ValueError(msg)

        return team_config.get_team(nodes, team_name)

    async def _get_resume_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create per-session lock for resume serialization.

        Args:
            session_id: Session identifier.

        Returns:
            The per-session resume lock.
        """
        async with self._resume_locks_lock:
            lock = self._resume_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._resume_locks[session_id] = lock
            return lock

    @contextlib.asynccontextmanager
    async def _with_resume_lock(self, session_id: str) -> AsyncIterator[SessionState | None]:
        """Acquire per-session resume lock with state validation.

        Ensures only one resume runs per session at a time and that
        the session is in a resumable state (no active run, persisted
        status is ``"checkpointed"``).

        Args:
            session_id: Session to lock.

        Yields:
            The live ``SessionState``, or ``None`` if no live session exists.

        Raises:
            SessionBusyError: If the session has an active run or its
                persisted status is not ``"checkpointed"``.
        """
        resume_lock = await self._get_resume_lock(session_id)
        async with resume_lock:
            session = self.sessions.get_session(session_id)
            if session is not None and session.current_run_id is not None:
                raise SessionBusyError(session_id, session.current_run_id)

            if self.sessions.store is not None:
                current_data = await self.sessions.store.load(session_id)
                if current_data is not None and current_data.status != "checkpointed":
                    raise SessionBusyError(session_id, current_data.status)

            yield session

    async def _load_checkpoint_data(self, session_id: str) -> CheckpointData:
        """Load checkpoint data for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Checkpoint data.

        Raises:
            SessionNotFoundError: If no checkpoint exists for the session.
        """
        from agentpool.agents.native_agent.checkpoint import CheckpointManager

        storage = self.pool.storage
        if storage is None:
            raise SessionNotFoundError(session_id)

        checkpoint_mgr = CheckpointManager(storage)
        data = await checkpoint_mgr.load_checkpoint(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)
        return data

    async def _reconstruct_native_agent(
        self,
        session_id: str,
        agent_name: str,
    ) -> Agent[Any, Any]:
        """Reconstruct a native agent from config for session resume.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed native agent instance.

        Raises:
            SessionNotFoundError: If the agent config is not found.
        """
        from agentpool.models.agents import NativeAgentConfig
        from agentpool_config.context import ConfigContextManager

        cfg = self.pool.manifest.agents.get(agent_name)
        if cfg is None:
            raise SessionNotFoundError(session_id)

        if not isinstance(cfg, NativeAgentConfig):
            raise SessionNotFoundError(session_id)

        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        session = self.sessions.get_session(session_id)
        input_provider = session.input_provider if session else None

        with ConfigContextManager(self.pool._config_file_path):
            agent: Agent[Any, Any] = cfg.get_agent(
                input_provider=input_provider,
                pool=self.pool,
            )

        # Add pool-level providers (non-MCP only).
        # MCP tools are handled via McpConfigSnapshot → as_capability() →
        # MCPToolset, not through agent.tools.providers.
        if self.pool is not None:
            if self.pool.skills_instruction_provider:
                agent.tools.add_provider(self.pool.skills_instruction_provider)
            agent.tools.add_provider(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _reconstruct_acp_agent(
        self,
        session_id: str,
        agent_name: str,
    ) -> BaseAgent[Any, Any]:
        """Reconstruct an ACP agent from config for session resume.

        Args:
            session_id: Session identifier.
            agent_name: Name of the agent configuration to use.

        Returns:
            A reconstructed ACP agent with reopened subprocess.

        Raises:
            SessionNotFoundError: If the agent config is not found.
        """
        from agentpool_config.context import ConfigContextManager

        cfg = self.pool.manifest.agents.get(agent_name)
        if cfg is None:
            raise SessionNotFoundError(session_id)

        if cfg.name is None:
            cfg = cfg.model_copy(update={"name": agent_name})

        session = self.sessions.get_session(session_id)
        input_provider = session.input_provider if session else None

        with ConfigContextManager(self.pool._config_file_path):
            agent = cfg.get_agent(
                input_provider=input_provider,
                pool=self.pool,
            )

        # Add pool-level providers (non-MCP only).
        # MCP tools are handled via McpConfigSnapshot → as_capability() →
        # MCPToolset, not through agent.tools.providers.
        if self.pool is not None:
            if self.pool.skills_instruction_provider:
                agent.tools.add_provider(self.pool.skills_instruction_provider)
            agent.tools.add_provider(self.pool.skills_tools_provider)

        await agent.__aenter__()
        return agent

    async def _resume_native_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume a native agent from checkpoint with deferred results.

        Loads message_history from checkpoint, reconstructs the agent from its
        original config, and calls agent.run() with the restored history and
        deferred results.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data with message_history and pending_calls.
            results: DeferredToolResults for resolving pending deferred calls.

        Raises:
            SessionNotFoundError: If agent config is not found.
            RuntimeError: If agent.run() fails (pending_calls remain uncleared).
        """
        agent = await self._reconstruct_native_agent(
            session_data.session_id, session_data.agent_name
        )

        # Detect agent config drift between checkpoint and resume.
        # The hash check is advisory: if we can't compute the current hash
        # (e.g. agent has no tools attribute, or tools is a mock in tests),
        # we skip the comparison and proceed with resume.
        if session_data.agent_config_hash:
            try:
                from agentpool.agents.native_agent.checkpoint import (
                    compute_agent_config_hash,
                )

                agent_tools = await agent.tools.get_tools()
                current_hash = compute_agent_config_hash(agent_tools)
                if current_hash != session_data.agent_config_hash:
                    logger.warning(
                        "Agent config hash mismatch — tools may have changed since checkpoint",
                        session_id=session_data.session_id,
                        stored_hash=session_data.agent_config_hash,
                        current_hash=current_hash,
                    )
            except Exception:
                logger.debug(
                    "Could not compute agent config hash for drift check",
                    session_id=session_data.session_id,
                    exc_info=True,
                )

        try:
            message_history: list[Any] = list(checkpoint.message_history)
            # deferred_tool_results is forwarded to pydantic-ai Agent.run()
            # which accepts it natively; cast to Any since BaseAgent.run()
            # doesn't declare this kwarg in its signature.
            run_fn: Any = agent.run
            await run_fn(
                message_history=message_history,
                deferred_tool_results=results,
            )
        finally:
            await agent.__aexit__(None, None, None)

    async def _resume_acp_agent(
        self,
        session_data: SessionData,
        checkpoint: CheckpointData,
        results: Any,
    ) -> None:
        """Resume an ACP agent by reopening the subprocess and sending session/resume.

        Reopens the ACP subprocess and calls agent.run() to restart the
        session with restored state.

        Args:
            session_data: Persisted session data.
            checkpoint: Checkpoint data (used for metadata only; ACP agents
                manage their own message history).
            results: DeferredToolResults for resolving pending deferred calls.
        """
        agent = await self._reconstruct_acp_agent(session_data.session_id, session_data.agent_name)
        try:
            # ACP agents receive the resumed session context through run()
            run_fn: Any = agent.run
            await run_fn(
                message_history=list(checkpoint.message_history),
                deferred_tool_results=results,
            )
        finally:
            if hasattr(agent, "__aexit__"):
                await agent.__aexit__(None, None, None)

    async def resume_session(
        self,
        session_id: str,
        deferred_tool_results: Any,
        *,
        source: str = "resume_prompt",
    ) -> None:
        """Resume a paused session with resolved deferred tool results.

        Loads the persisted SessionData, validates that deferred_tool_results
        cover all pending_deferred_calls (raising CheckpointMismatchError if not),
        and resumes execution via the appropriate path:
        - Native agent: load checkpoint → reconstruct agent from config →
          agent.run(message_history=restored, deferred_tool_results=results)
        - ACP agent: load session data → reopen subprocess →
          agent.run(message_history=restored, deferred_tool_results=results)

        Per-session resume_lock ensures only one resume at a time.
        Emits SessionResumeEvent on success.

        Args:
            session_id: Session to resume.
            deferred_tool_results: Results for pending deferred tool calls
                (DeferredToolResults-compatible object with .calls dict).
            source: Identifier for the entity triggering the resume.

        Raises:
            SessionNotFoundError: If the session does not exist in storage.
            SessionBusyError: If the session has an active run.
            CheckpointMismatchError: If results don't cover all pending calls.
        """
        store = self.sessions.store
        if store is None:
            raise SessionNotFoundError(session_id)

        # Load persisted session data
        data = await store.load(session_id)
        if data is None:
            raise SessionNotFoundError(session_id)

        # Fast-path: check for active run in live sessions (before lock).
        # The authoritative check is inside _with_resume_lock, but this
        # early check avoids unnecessary store operations for busy sessions.
        session = self.sessions.get_session(session_id)
        if session is not None and session.current_run_id is not None:
            raise SessionBusyError(session_id, session.current_run_id)

        # Validate deferred_tool_results cover all pending_deferred_calls
        pending_call_ids: set[str] = {call.tool_call_id for call in data.pending_deferred_calls}
        provided_call_ids: set[str] = set(getattr(deferred_tool_results, "calls", {}).keys())

        missing = pending_call_ids - provided_call_ids
        extra = provided_call_ids - pending_call_ids
        if missing or extra:
            raise CheckpointMismatchError(
                session_id=session_id,
                expected=pending_call_ids,
                provided=provided_call_ids,
                missing=missing,
                extra=extra,
            )

        # Determine agent type
        agent_type = data.metadata.get("agent_type", "native")

        # Per-session resume lock with state validation (Decision 8, Task 19)
        async with self._with_resume_lock(session_id) as session:
            try:
                # Load checkpoint data
                checkpoint = await self._load_checkpoint_data(session_id)

                # Mark session as resuming
                data = data.model_copy(update={"status": "resuming"})
                await store.save(data)

                # Route to appropriate resume path
                if agent_type == "acp":
                    await self._resume_acp_agent(data, checkpoint, deferred_tool_results)
                else:
                    await self._resume_native_agent(data, checkpoint, deferred_tool_results)

                # Clear pending_deferred_calls ONLY after agent.run() succeeds (Decision 8)
                data = data.model_copy(
                    update={
                        "status": "active",
                        "pending_deferred_calls": [],
                    }
                )
                data.touch()
                await store.save(data)

                # Update live session if one exists
                if session is not None:
                    session.last_active_at = time.monotonic()

                # Emit SessionResumeEvent
                await self.event_bus.publish(
                    session_id,
                    SessionResumeEvent(
                        session_id=session_id,
                        resolved_call_count=len(pending_call_ids),
                        source=source,
                    ),
                )

                logger.info(
                    "Session resumed successfully",
                    session_id=session_id,
                    agent_type=agent_type,
                    resolved_calls=len(pending_call_ids),
                )

            except Exception:
                # On failure, keep status as checkpointed and do NOT clear pending calls
                data = data.model_copy(update={"status": "checkpointed"})
                data.touch()
                await store.save(data)
                raise

    async def close_session(self, session_id: str) -> None:
        """Close a session.

        Waits for any active run to complete before proceeding.
        Order: wait for run, session cleanup, event bus, then turn state.

        Args:
            session_id: The session to close.
        """
        session = self.sessions.get_session(session_id)
        run_handle: RunHandle | None = None
        if session is not None:
            async with session._request_lock:
                session.closing = True
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = self.sessions._runs.get(run_id)

            if run_handle is not None:
                # Signal the RunHandle to stop its idle/wake loop so that
                # start()'s finally block can set complete_event promptly.
                # Without this, a handle stuck in _idle_event.wait() will
                # never exit, causing close_session to hang until timeout.
                run_handle.close()
                # Unblock any background-task wait loop inside the run so
                # complete_event can be set promptly instead of waiting.
                if run_handle.run_ctx is not None:
                    run_handle.run_ctx.cancelled = True
                    # Snapshot values before setting to avoid dict mutation race.
                    for ev in list(run_handle.run_ctx.child_done_events.values()):
                        ev.set()
                    run_handle.run_ctx.child_done_events.clear()
                try:
                    await asyncio.wait_for(run_handle.complete_event.wait(), timeout=2.0)
                except TimeoutError:
                    self.cancel_run(run_handle.run_id)
                    await asyncio.sleep(0.1)

        await self.sessions.close_session(session_id)
        # EventBus and message cache cleanup may be interrupted by
        # CancelledError from garbage-collected async generator cleanup
        # (e.g., when a consumer broke from run_stream without closing
        # the generator). Suppress these spurious cancellations so
        # shutdown proceeds.
        try:
            await self.event_bus.close_session(session_id)
        except asyncio.CancelledError:
            logger.warning(
                "EventBus close_session interrupted by spurious cancellation",
                session_id=session_id,
            )

        self._message_cache.pop(session_id, None)

    async def _await_inflight_checkpoints(self) -> None:
        """Wait for any in-flight checkpoint operations to complete.

        During normal operation, checkpoint-on-close happens synchronously
        inside :meth:`close_session`, so there are no in-flight operations
        to await. This method is a future-proof hook for graceful teardown:
        if the checkpoint mechanism ever becomes asynchronous (e.g.,
        background flush), this method ensures the shutdown waits for
        completion.

        Called from :meth:`AgentPool.__aexit__` during pool shutdown.
        """
        # Currently no-op: all checkpoint operations complete synchronously
        # within SessionController.close_session() under its lock.
        logger.debug("No in-flight checkpoint operations to await")

    # ------------------------------------------------------------------
    # RunHandle delegation helpers
    # ------------------------------------------------------------------

    def _get_active_run_handle(self, session_id: str) -> RunHandle | None:
        """Get the active RunHandle for a session, if any.

        Returns:
            The RunHandle, or None if no active run exists.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.current_run_id is None:
            return None
        return self.sessions._runs.get(session.current_run_id)

    def _create_run_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
    ) -> RunHandle:
        """Create and register a RunHandle without a background task.

        Unlike :meth:`SessionController._start_run_handle`, this does
        NOT create an asyncio task to consume ``start()``. The caller
        is responsible for draining ``start()``.

        Returns:
            The newly created and registered RunHandle.
        """
        event_bus = self.event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus)
        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )
        self.sessions._runs[run_handle.run_id] = run_handle
        session.current_run_id = run_handle.run_id
        return run_handle

    async def _process_prompt_run_turn(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Handle process_prompt via the RunHandle path.

        If no active run exists, creates a RunHandle and drains
        ``start()`` to completion. If a run is active, steers the
        message into it.
        """
        session, _ = await self.sessions.get_or_create_session(session_id)
        if session.is_closing:
            return
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        agent = await self.sessions.get_or_create_session_agent(
            session_id, input_provider=input_provider
        )
        if agent is None:
            return
        content = " ".join(str(p) for p in prompts) if prompts else ""

        run_id = session.current_run_id
        if run_id is not None:
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None:
                run_handle.steer(content)
            return

        run_handle = self._create_run_handle(session, agent, session_id)
        gen = run_handle.start(content)
        try:
            async for _event in gen:
                if isinstance(_event, StreamCompleteEvent | RunErrorEvent):
                    break
        finally:
            await gen.aclose()
            session.current_run_id = None
            self.sessions._runs.pop(run_handle.run_id, None)

    # ------------------------------------------------------------------
    # SessionPool public methods
    # ------------------------------------------------------------------

    async def process_prompt(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Process a prompt through the RunHandle lifecycle.

        Main entry point for protocol handlers.
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            **kwargs: Additional arguments passed to the agent.
        """
        await self._process_prompt_run_turn(session_id, *prompts, **kwargs)

    async def receive_request(
        self,
        session_id: str,
        content: Any,
        priority: str = "when_idle",
        **kwargs: Any,
    ) -> RunHandle | None:
        """Route an incoming request for a session (fire-and-forget).

        Creates a background task that processes the prompt through
        the RunHandle lifecycle. Protocol handlers should subscribe to the
        EventBus *before* calling this method so no events are dropped.

        Idle sessions create a RunHandle, busy sessions call
        ``RunHandle.steer()`` or ``RunHandle.followup()``.

        Args:
            session_id: Target session.
            content: Message / prompt content.
            priority: "when_idle" to queue, "asap" to inject into active turn.
            **kwargs: Additional arguments passed to the turn runner.

        Returns:
            The RunHandle if a new run was started, otherwise None.
        """
        return await self.sessions.receive_request(session_id, content, priority=priority, **kwargs)

    @property
    def active_runs(self) -> list[RunHandle]:
        """Get all currently active (running) RunHandles."""
        return [rh for rh in self.sessions._runs.values() if rh.status == RunStatus.running]

    def get_run(self, run_id: str) -> RunHandle | None:
        """Get a RunHandle by ID.

        Args:
            run_id: The run ID to look up.

        Returns:
            The RunHandle, or None if not found.
        """
        return self.sessions._runs.get(run_id)

    def cancel_run(self, run_id: str) -> None:
        """Cancel a run by ID.

        Args:
            run_id: The run ID to cancel.

        Raises:
            ValueError: If no active run with the given ID exists.
        """
        run_handle = self.sessions._runs.get(run_id)
        if run_handle is None:
            raise ValueError("No active run found with ID: " + run_id)
        run_handle.cancel()

    async def run_stream(
        self,
        session_id: str,
        *prompts: str,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Process prompts and yield events.

        Convenience method for tests and standalone clients that want
        an async iterator over session events. Yields events directly
        from ``RunHandle.start()`` when no active run exists. If a run
        is already active, steers the message and falls back to EventBus.

        Args:
            session_id: The session to process the prompt for.
            *prompts: Prompts to process.
            scope: Subscription scope - "session" (exact match),
                "descendants" (self + children), or "subtree" (self + parent + siblings).
            **kwargs: Additional arguments passed to the turn runner
                (e.g. ``input_provider``).

        Yields:
            Events published to the EventBus for this session.
        """
        async for event in self._run_stream_run_turn(session_id, *prompts, scope=scope, **kwargs):
            yield event

    async def _run_stream_run_turn(
        self,
        session_id: str,
        *prompts: str,
        scope: str = "session",
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Handle run_stream via the RunHandle path.

        If no active run exists, creates a RunHandle and yields events
        directly from ``start()``. If a run is active, steers the
        message and yields from the EventBus subscription.
        """
        session, _ = await self.sessions.get_or_create_session(session_id)
        if session.is_closing:
            return
        # Extract input_provider from kwargs and set on session BEFORE
        # get_or_create_session_agent() so the agent is created with the
        # correct input_provider and the session state is consistent.
        input_provider = kwargs.pop("input_provider", None)
        if input_provider is not None:
            session.input_provider = input_provider
        agent = await self.sessions.get_or_create_session_agent(
            session_id, input_provider=input_provider
        )
        if agent is None:
            return
        content = " ".join(str(p) for p in prompts) if prompts else ""

        run_id = session.current_run_id
        if run_id is not None:
            # Active run — steer and use EventBus
            run_handle = self.sessions._runs.get(run_id)
            if run_handle is not None:
                run_handle.steer(content)
            stream = await self.event_bus.subscribe(session_id, scope=scope)
            try:
                while True:
                    try:
                        event = await stream.receive()
                    except anyio.EndOfStream:
                        break
                    yield event.event
                    raw_event = getattr(event, "event", event)
                    if isinstance(raw_event, StreamCompleteEvent | RunErrorEvent):
                        break
            finally:
                await self.event_bus.unsubscribe(session_id, stream)
            return

        # No active run — create RunHandle and yield from start().
        # Also subscribe to EventBus so that events published by tools
        # during turn execution (e.g. SpawnSessionStart from task() →
        # create_child_session()) are delivered to the consumer, not
        # just events yielded directly by start().
        run_handle = self._create_run_handle(session, agent, session_id)
        self.event_bus.clear_replay_buffer(session_id)
        bus_stream = await self.event_bus.subscribe(session_id, scope=scope)
        gen = run_handle.start(content)
        try:
            async for event in gen:
                # Drain any tool-published events from EventBus before
                # yielding the start() event. This ensures SpawnSessionStart
                # and similar events appear before the StreamCompleteEvent.
                with contextlib.suppress(anyio.WouldBlock):
                    while True:
                        envelope = bus_stream.receive_nowait()
                        yield envelope.event
                yield event
                if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                    break
        finally:
            await gen.aclose()
            await self.event_bus.unsubscribe(session_id, bus_stream)
            session.current_run_id = None
            self.sessions._runs.pop(run_handle.run_id, None)

    async def inject_prompt(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a message into a session.

        If the session has an active run, injects immediately via
        ``RunHandle.steer()``. Otherwise, returns False.

        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to inject into.
            message: The message to inject.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if injected into active turn, False if queued.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return False

    async def queue_prompt(self, session_id: str, *prompts: Any, **kwargs: Any) -> bool:
        """Queue prompts for a session.

        Similar to inject_prompt but for full prompts.
        Does NOT acquire session.turn_lock.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.
            **kwargs: Additional arguments passed to the agent run.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            message = prompts[0] if prompts else ""
            return run_handle.followup(str(message))
        return False

    async def steer(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Inject a steer message with agent-type-aware routing.

        Delegates to ``RunHandle.steer()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The steer message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.steer(message)
        return False

    async def followup(self, session_id: str, message: str, **kwargs: Any) -> bool:
        """Queue a follow-up message with agent-type-aware routing.

        Delegates to ``RunHandle.followup()`` when an active run exists.

        Args:
            session_id: Target session.
            message: The follow-up message to deliver.
            **kwargs: Additional arguments (ignored).

        Returns:
            True if delivered into active turn, False if queued for idle.
        """
        run_handle = self._get_active_run_handle(session_id)
        if run_handle is not None:
            return run_handle.followup(message)
        return False

    async def get_messages(
        self,
        session_id: str,
    ) -> list[ChatMessage[Any]]:
        """Get message history for a session.

        Results are cached per session_id (full message list) to avoid
        repeated storage queries. Cache is invalidated by append_message,
        truncate_messages, and copy_messages.

        Args:
            session_id: The session to retrieve messages for.

        Returns:
            List of messages ordered by timestamp (oldest first).

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        if session_id in self._message_cache:
            return list(self._message_cache[session_id])

        storage = self.pool.storage
        if storage is not None:
            messages = await storage.get_session_messages(session_id)
            self._message_cache[session_id] = list(messages)
            return messages

        return []

    async def append_message(
        self,
        session_id: str,
        message: ChatMessage[Any],
    ) -> str:
        """Append a message to a session's history.

        Args:
            session_id: The session to append to.
            message: The message to append.

        Returns:
            The ID of the appended message.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            await storage.log_message(message=message)

        self._message_cache.pop(session_id, None)
        return message.message_id

    async def copy_messages(
        self,
        source_session_id: str,
        target_session_id: str,
        *,
        up_to_message_id: str | None = None,
    ) -> str | None:
        """Copy messages from one session to another.

        Used by share_session (copy all) and revert_session (copy up to
        a specific message).

        Args:
            source_session_id: Session to copy from.
            target_session_id: Session to copy to.
            up_to_message_id: If set, only copy messages up to and
                including this message ID. If None, copy all messages.

        Returns:
            The ID of the fork point message (last copied message),
            or None if no messages were copied.

        Raises:
            KeyError: If either session does not exist.
        """
        if self.sessions.get_session(source_session_id) is None:
            raise KeyError(source_session_id)
        if self.sessions.get_session(target_session_id) is None:
            raise KeyError(target_session_id)

        storage = self.pool.storage
        if storage is not None:
            result = await storage.fork_conversation(
                source_session_id=source_session_id,
                new_session_id=target_session_id,
                fork_from_message_id=up_to_message_id,
            )
            self._message_cache.pop(target_session_id, None)
            return result

        return None

    async def truncate_messages(
        self,
        session_id: str,
        up_to_message_id: str,
    ) -> int:
        """Truncate messages after a specific message ID.

        Used by revert_session to remove messages after the revert point.

        Args:
            session_id: The session to truncate.
            up_to_message_id: Keep messages up to and including this ID,
                remove everything after.

        Returns:
            Number of messages removed.

        Raises:
            KeyError: If the session does not exist.
        """
        session = self.sessions.get_session(session_id)
        if session is None:
            raise KeyError(session_id)

        storage = self.pool.storage
        if storage is not None:
            removed = await storage.truncate_messages(session_id, up_to_message_id)
            self._message_cache.pop(session_id, None)
            return removed

        return 0
