"""Global routes (health, events)."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from agentpool import log
from agentpool_server.opencode_server.dependencies import StateDep
from agentpool_server.opencode_server.models import GlobalEvent, HealthResponse
from agentpool_server.opencode_server.models.app import (
    DiagnosticResponse,
    DisposeResponse,
    UpgradeResponse,
)
from agentpool_server.opencode_server.models.events import (
    MessageUpdatedEvent,
    PartUpdatedEvent,
    ServerConnectedEvent,
    ServerHeartbeatEvent,
    SessionCreatedEvent,
    SessionErrorEvent,
    SessionIdProperties,
    SessionUpdatedEvent,
)
from agentpool_server.opencode_server.routes.routing import (
    RoutingCheckResponse,
    tui_event_filter,
)


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from agentpool_server.opencode_server.models import Event
    from agentpool_server.opencode_server.state import ServerState


logger = log.get_logger(__name__)
router = APIRouter(tags=["global"])

VERSION = "0.1.0"


@router.get("/global/health")
async def get_health() -> HealthResponse:
    """Get server health status."""
    return HealthResponse(healthy=True, version=VERSION)


@router.get("/global/diagnostic")
async def get_diagnostic(state: StateDep) -> DiagnosticResponse:
    """Get server diagnostic information.

    Returns directory, project, subscriber count, and server version.
    """
    if state.working_dir is None:
        return DiagnosticResponse(
            directory=None,
            project="",
            subscribers=len(state.event_subscribers),
            server_version=VERSION,
        )

    factory = state.get_event_factory()
    return DiagnosticResponse(
        directory=state.working_dir,
        project=factory._project,
        subscribers=len(state.event_subscribers),
        server_version=VERSION,
    )


@router.post("/global/dispose")
async def post_global_dispose() -> DisposeResponse:
    """Acknowledge OpenCode dispose requests without stopping the server."""
    return DisposeResponse(message="dispose acknowledged (no-op)")


@router.post("/global/upgrade")
async def post_global_upgrade() -> UpgradeResponse:
    """Acknowledge OpenCode upgrade requests without performing an upgrade."""
    return UpgradeResponse(message="upgrade not supported (stub)")


def _extract_session_id(event: Event) -> str | None:
    """Extract session ID from various event types.

    Uses a combination of:
    - ``isinstance(event.properties, SessionIdProperties)`` for the common
      case where session_id lives directly on the properties model.
    - Explicit match arms for special-path events where session_id is
      nested deeper (e.g., ``properties.info.id``,
      ``properties.info.session_id``, ``properties.part.session_id``).
    - ``SessionErrorEvent`` handled separately because its session_id is
      ``str | None`` (not ``str``), so its properties don't inherit from
      ``SessionIdProperties``.

    Unrecognized event types return None without warning, since many
    events genuinely have no session association.
    """
    # Special-path events: session_id is nested, not at properties.session_id
    match event:
        case SessionCreatedEvent(properties=props):
            session_id: str | None = props.info.id
        case SessionUpdatedEvent(properties=props):
            session_id = props.info.id
        case MessageUpdatedEvent(properties=props):
            session_id = props.info.session_id
        case PartUpdatedEvent(properties=props):
            session_id = props.part.session_id
        case SessionErrorEvent(properties=props):
            session_id = props.session_id
        case _:
            # Common path: properties inherit from SessionIdProperties
            if isinstance(event.properties, SessionIdProperties):
                return event.properties.session_id
            # No session association (server events, file events, pty events, etc.)
            return None

    return session_id


class GlobalEventFactory:
    """Creates GlobalEvent envelope JSON from Event instances.

    Stored on ServerState since directory/project don't change during
    the server's lifetime. Created lazily on first access.
    """

    def __init__(self, directory: str, project: str, workspace: str | None = None) -> None:
        """Initialize with directory and project routing metadata.

        Args:
            directory: Working directory for event routing
            project: Project identifier for event routing
            workspace: Workspace identifier for TUI workspace routing.
                When None, derives from project as f"wrk_{project[:12]}"
                to match the /experimental/workspace API id format.
        """
        self._directory = directory
        self._project = project
        self._workspace = workspace if workspace is not None else f"wrk_{project[:12]}"

    def wrap(self, event: Event) -> str:
        """Wrap an Event in a GlobalEvent envelope JSON string.

        Args:
            event: The event to wrap

        Returns:
            JSON string with directory, project, workspace, and payload keys.
        """
        payload = _event_to_dict(event)
        envelope: dict[str, Any] = {
            "directory": self._directory,
            "project": self._project,
            "workspace": self._workspace,
            "payload": payload,
        }
        return json.dumps(envelope, ensure_ascii=False)


def _event_to_dict(event: Event) -> dict[str, Any]:
    """Convert an Event to a dict with sessionId injected at top level.

    This is the dict-building half of serialization; the caller decides
    whether to wrap it in a payload envelope and when to call json.dumps.

    Injects sessionId (lowercase 'd') at the top level for subagent
    session tracking, separate from the alias-converted sessionID that
    appears inside properties.

    Args:
        event: The event to convert

    Returns:
        Dict with the event data and optional sessionId field.
    """
    event_data = event.model_dump(by_alias=True, exclude_none=True)
    session_id = _extract_session_id(event)
    if session_id is not None:
        event_data["sessionId"] = session_id
    return event_data


def _serialize_event(event: Event, wrap_payload: bool = False) -> str:
    r"""Serialize event, optionally wrapping in payload structure.

    Thin convenience wrapper around _event_to_dict + json.dumps.
    Uses ensure_ascii=False to preserve Unicode characters (Chinese, emoji, etc.)
    in the JSON output instead of escaping them as \uXXXX sequences.

    Args:
        event: The event to serialize
        wrap_payload: Whether to wrap in a {"payload": ...} structure

    Returns:
        JSON string of the serialized event data.
    """
    event_data = _event_to_dict(event)
    if wrap_payload:
        return json.dumps({"payload": event_data}, ensure_ascii=False)
    return json.dumps(event_data, ensure_ascii=False)


async def _event_generator(
    state: ServerState, *, wrap_payload: bool = False
) -> AsyncGenerator[dict[str, Any]]:
    """Generate SSE events for connected clients.

    Registers a subscriber queue, sends an initial connected event,
    then streams subsequent events from the broadcast system.

    When wrap_payload is True, session-scoped events are wrapped in a
    GlobalEvent envelope via the factory. Global server lifecycle events
    still use a top-level ``payload`` wrapper, but omit directory/project
    metadata to match OpenCode's `/global/event` contract.

    Subscriber lifecycle:
    1. Queue appended to state.event_subscribers
    2. If this is the first subscriber, triggers on_first_subscriber
       callback (e.g., for update check)
    3. Streams events until client disconnects
    4. Finally block removes queue from subscribers (suppresses
       ValueError if already removed by broadcast_event error handler)

    Args:
        state: The server state holding subscribers and event factory
        wrap_payload: Whether to wrap events in GlobalEvent envelopes
    """
    factory = state.get_event_factory() if wrap_payload else None
    queue: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.append(queue)
    subscriber_count = len(state.event_subscribers)
    logger.info("SSE: New client connected (total subscribers: %s)", subscriber_count)

    # Trigger first subscriber callback if this is the first connection.
    # Race condition analysis: This is safe because:
    # 1. The append (line above) and len check happen in the same async frame
    #    (no await between them), so no other coroutine can interleave.
    # 2. The _first_subscriber_triggered flag prevents double-firing even if
    #    a subscriber disconnects and reconnects rapidly.
    if (
        subscriber_count == 1
        and not state._first_subscriber_triggered
        and state.on_first_subscriber is not None
    ):
        state._first_subscriber_triggered = True
        state.create_background_task(state.on_first_subscriber(), name="on_first_subscriber")

    try:
        # Send initial connected event with payload wrapper on /global/event,
        # but without directory/project metadata.
        connected = ServerConnectedEvent()
        data = _serialize_event(connected, wrap_payload=wrap_payload)
        logger.info("SSE: Sending connected event", data=data)
        yield {"data": data}
        # Stream events
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
            except TimeoutError:
                # No events for 10s — send heartbeat to keep connection alive
                heartbeat = ServerHeartbeatEvent()
                data = _serialize_event(heartbeat, wrap_payload=wrap_payload)
                yield {"data": data}
                continue
            if factory is not None and not isinstance(
                event, ServerHeartbeatEvent | ServerConnectedEvent
            ):
                data = factory.wrap(event)
            elif wrap_payload:
                data = _serialize_event(event, wrap_payload=True)
            else:
                data = _serialize_event(event)
            logger.info("SSE: Sending event", event_type=event.type)
            yield {"data": data}
    finally:
        # Use safe removal: broadcast_event may have already removed this queue
        # due to error handling. Using discard-style pattern to avoid ValueError.
        with contextlib.suppress(ValueError):
            state.event_subscribers.remove(queue)
        # Cancel any pending questions when the SSE client disconnects.
        # This prevents agent_lock deadlock: when the agent is blocked waiting
        # for a question answer (Future.await) and the TUI disconnects, the
        # Future would never resolve, leaving agent_lock permanently held.
        # Cancelling the Future causes CancelledError in input_provider, which
        # returns ElicitResult(action="cancel"), leading to RunAbortedError,
        # which propagates through _process_message_locked's except handler
        # and releases agent_lock.
        cancelled = state.cancel_all_pending_questions()
        if cancelled:
            logger.info(
                "SSE: Cancelled pending questions on disconnect",
                question_ids=cancelled,
            )
        logger.info("SSE: Client disconnected", remaining_subscribers=len(state.event_subscribers))


@router.get("/global/event")
async def get_global_events(state: StateDep) -> EventSourceResponse:
    """Get global events as SSE stream (uses payload wrapper)."""
    return EventSourceResponse(
        _event_generator(state, wrap_payload=True),
        sep="\n",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/event")
async def get_events(state: StateDep) -> EventSourceResponse:
    """Get events as SSE stream (no payload wrapper)."""
    return EventSourceResponse(
        _event_generator(state, wrap_payload=False),
        sep="\n",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/global/routing-check", response_model=RoutingCheckResponse)
async def get_routing_check(
    state: StateDep,
    directory: str,
    workspace: str | None = None,
    current_workspace: str | None = None,
    project_directory: str | None = None,
) -> RoutingCheckResponse:
    """Check whether an event would pass the OpenCode TUI routing filter.

    Diagnostic endpoint that constructs a synthetic GlobalEvent with the
    given directory/workspace and runs it through the 4-rule TUI event
    routing filter. Returns whether the event would pass and why.

    Args:
        state: Server state (injected dependency).
        directory: The event's directory field.
        workspace: The event's workspace field (optional).
        current_workspace: The TUI's active workspace for rule 3 filtering.
        project_directory: The project directory to match against
            (defaults to state.working_dir).

    Returns:
        RoutingCheckResponse with would_pass and reason fields.
    """
    effective_project_dir = (
        project_directory if project_directory is not None else state.working_dir
    )
    event = GlobalEvent(directory=directory, workspace=workspace, payload={})
    would_pass, reason = tui_event_filter(
        event, effective_project_dir, current_workspace=current_workspace
    )
    return RoutingCheckResponse(would_pass=would_pass, reason=reason)
