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
from agentpool_server.opencode_server.models.app import DiagnosticResponse
from agentpool_server.opencode_server.models.events import (
    CommandExecutedEvent,
    MessageRemovedEvent,
    MessageUpdatedEvent,
    PartDeltaEvent,
    PartRemovedEvent,
    PartUpdatedEvent,
    PermissionRequestEvent,
    PermissionResolvedEvent,
    PermissionUpdatedEvent,
    QuestionAskedEvent,
    QuestionRejectedEvent,
    QuestionRepliedEvent,
    ServerConnectedEvent,
    ServerHeartbeatEvent,
    SessionCompactedEvent,
    SessionCreatedEvent,
    SessionDeletedEvent,
    SessionDiffEvent,
    SessionErrorEvent,
    SessionIdleEvent,
    SessionStatusEvent,
    SessionUpdatedEvent,
    TodoUpdatedEvent,
    TuiSessionSelectEvent,
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


def _extract_session_id(event: Event) -> str | None:  # noqa: PLR0911
    """Extract session ID from various event types.

    Uses pattern matching to access session_id from four different
    property structures:
    - properties.session_id (most events)
    - properties.info.id (SessionCreated/Updated events)
    - properties.info.session_id (MessageUpdatedEvent)
    - properties.part.session_id (PartUpdatedEvent)

    Unrecognized event types trigger a warning log and return None,
    since some events genuinely have no session association.
    """
    match event:
        # Events with properties.session_id directly
        case SessionDeletedEvent(properties=props):
            return props.session_id
        case SessionStatusEvent(properties=props):
            return props.session_id
        case SessionIdleEvent(properties=props):
            return props.session_id
        case SessionCompactedEvent(properties=props):
            return props.session_id
        case MessageRemovedEvent(properties=props):
            return props.session_id
        case PartRemovedEvent(properties=props):
            return props.session_id
        case PermissionRequestEvent(properties=props):
            return props.session_id
        case PermissionResolvedEvent(properties=props):
            return props.session_id
        case QuestionAskedEvent(properties=props):
            return props.session_id
        case QuestionRepliedEvent(properties=props):
            return props.session_id
        case QuestionRejectedEvent(properties=props):
            return props.session_id
        case TodoUpdatedEvent(properties=props):
            return props.session_id
        case SessionErrorEvent(properties=props):
            return props.session_id
        case SessionDiffEvent(properties=props):
            return props.session_id
        case PartDeltaEvent(properties=props):
            return props.session_id
        case PermissionUpdatedEvent(properties=props):
            return props.session_id
        case CommandExecutedEvent(properties=props):
            return props.session_id
        case TuiSessionSelectEvent(properties=props):
            return props.session_id

        # Events with properties.info.id (Session has id field)
        case SessionCreatedEvent(properties=props):
            return props.info.id
        case SessionUpdatedEvent(properties=props):
            return props.info.id

        # Events with properties.info.session_id (MessageInfo has session_id field)
        case MessageUpdatedEvent(properties=props):
            return props.info.session_id

        # Events with properties.part.session_id (Part has session_id field)
        case PartUpdatedEvent(properties=props):
            return props.part.session_id

        # Events without session_id return None
        case _:
            logger.warning("Unhandled event type in _extract_session_id: %s", type(event).__name__)
            return None


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
            workspace: Workspace identifier for TUI workspace routing
        """
        self._directory = directory
        self._project = project
        self._workspace = workspace

    def wrap(self, event: Event) -> str:
        """Wrap an Event in a GlobalEvent envelope JSON string.

        Args:
            event: The event to wrap

        Returns:
            JSON string with directory, project, workspace, and payload keys.
        """
        payload_json = _serialize_event(event, wrap_payload=False)
        payload = json.loads(payload_json)
        envelope: dict[str, Any] = {
            "directory": self._directory,
            "project": self._project,
            "payload": payload,
        }
        if self._workspace is not None:
            envelope["workspace"] = self._workspace
        return json.dumps(envelope, ensure_ascii=False)

    @staticmethod
    def is_global_only_event(event: Event) -> bool:
        """Check if event is server-scoped (no session routing needed)."""
        return isinstance(event, ServerHeartbeatEvent)


def _serialize_event(event: Event, wrap_payload: bool = False) -> str:
    r"""Serialize event, optionally wrapping in payload structure.

    Injects sessionId at the top level of the serialized data (lowercase 'd')
    for subagent session tracking, separate from the alias-converted
    sessionID that appears inside properties.

    Uses ensure_ascii=False to preserve Unicode characters (Chinese, emoji, etc.)
    in the JSON output instead of escaping them as \uXXXX sequences.

    Args:
        event: The event to serialize
        wrap_payload: Whether to wrap in a {"payload": ...} structure

    Returns:
        JSON string of the serialized event data.
    """
    event_data = event.model_dump(by_alias=True, exclude_none=True)

    # Add sessionId at top level if available (for subagent session tracking)
    session_id = _extract_session_id(event)
    if session_id is not None:
        event_data["sessionId"] = session_id

    if wrap_payload:
        return json.dumps({"payload": event_data}, ensure_ascii=False)
    return json.dumps(event_data, ensure_ascii=False)


async def _event_generator(
    state: ServerState, *, wrap_payload: bool = False
) -> AsyncGenerator[dict[str, Any]]:
    """Generate SSE events for connected clients.

    Registers a subscriber queue, sends an initial connected event,
    then streams subsequent events from the broadcast system.

    When wrap_payload is True, events are wrapped in a GlobalEvent envelope
    via the factory; heartbeat events are always sent bare.

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
        # Send initial connected event (wrapped when payload wrapping is enabled)
        connected = ServerConnectedEvent()
        data = (
            factory.wrap(connected)
            if factory is not None
            else _serialize_event(connected, wrap_payload=False)
        )
        logger.info("SSE: Sending connected event", data=data)
        yield {"data": data}
        # Stream events
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=10.0)
            except TimeoutError:
                # No events for 10s — send heartbeat to keep connection alive
                heartbeat = ServerHeartbeatEvent()
                data = _serialize_event(heartbeat, wrap_payload=False)
                yield {"data": data}
                continue
            if factory and not GlobalEventFactory.is_global_only_event(event):
                data = factory.wrap(event)
            else:
                data = _serialize_event(event, wrap_payload=False)
            logger.info("SSE: Sending event", event_type=event.type)
            yield {"data": data}
    finally:
        # Use safe removal: broadcast_event may have already removed this queue
        # due to error handling. Using discard-style pattern to avoid ValueError.
        with contextlib.suppress(ValueError):
            state.event_subscribers.remove(queue)
        logger.info("SSE: Client disconnected", remaining_subscribers=len(state.event_subscribers))


@router.get("/global/event")
async def get_global_events(state: StateDep) -> EventSourceResponse:
    """Get global events as SSE stream (uses payload wrapper)."""
    return EventSourceResponse(_event_generator(state, wrap_payload=True), sep="\n")


@router.get("/event")
async def get_events(state: StateDep) -> EventSourceResponse:
    """Get events as SSE stream (no payload wrapper)."""
    return EventSourceResponse(_event_generator(state, wrap_payload=False), sep="\n")


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
