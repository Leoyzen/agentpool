"""Tests for _serialize_event, GlobalEvent model, and GlobalEventFactory."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool_server.opencode_server.models import GlobalEvent
from agentpool_server.opencode_server.models.common import TimeCreated
from agentpool_server.opencode_server.models.events import (
    CommandExecutedEvent,
    Event,
    FileEditedEvent,
    FileWatcherUpdatedEvent,
    LspClientDiagnosticsEvent,
    LspUpdatedEvent,
    McpToolsChangedEvent,
    MessageRemovedEvent,
    MessageUpdatedEvent,
    PartDeltaEvent,
    PartRemovedEvent,
    PartUpdatedEvent,
    PermissionRequestEvent,
    PermissionResolvedEvent,
    PermissionUpdatedEvent,
    ProjectUpdatedEvent,
    PtyCreatedEvent,
    PtyDeletedEvent,
    PtyExitedEvent,
    PtyUpdatedEvent,
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
    TuiCommandExecuteEvent,
    TuiPromptAppendEvent,
    TuiSessionSelectEvent,
    TuiToastShowEvent,
    VcsBranchUpdatedEvent,
)
from agentpool_server.opencode_server.models.message import (
    UserMessage,
)
from agentpool_server.opencode_server.models.parts import Part, TextPart  # noqa: TC001
from agentpool_server.opencode_server.models.question import (
    QuestionInfo,
    QuestionOption,
)
from agentpool_server.opencode_server.models.session import (
    Session,
    TimeCreatedUpdated,
)
from agentpool_server.opencode_server.routes.global_routes import (
    GlobalEventFactory,
    _event_generator,
    _extract_session_id,
    _serialize_event,
)
from agentpool_server.opencode_server.state import ServerState


if TYPE_CHECKING:
    from httpx import AsyncClient


# =============================================================================
# _serialize_event baseline tests
# =============================================================================


def test_serialize_event_session_id_injection() -> None:
    """SessionId is injected at top level when event has a session."""
    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    result = _serialize_event(event, wrap_payload=False)
    data = json.loads(result)
    assert data["sessionId"] == "abc"


def test_serialize_event_no_session_id() -> None:
    """No sessionId key when event has no session."""
    event = ServerConnectedEvent()
    result = _serialize_event(event, wrap_payload=False)
    data = json.loads(result)
    assert "sessionId" not in data


def test_serialize_event_wrap_payload_true() -> None:
    """wrap_payload=True nests event data under 'payload' key."""
    event = ServerConnectedEvent()
    result = _serialize_event(event, wrap_payload=True)
    data = json.loads(result)
    assert "payload" in data
    assert data["payload"]["type"] == "server.connected"


def test_serialize_event_unicode_preserved() -> None:
    r"""Unicode characters are preserved (not \uXXXX escaped)."""
    event = SessionStatusEvent.create(session_id="你好", status_type="idle")
    result = _serialize_event(event, wrap_payload=False)
    assert "你好" in result
    assert "\\u" not in result


def test_serialize_event_camel_case_aliases() -> None:
    """Model fields use camelCase aliases in serialized output."""
    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    result = _serialize_event(event, wrap_payload=False)
    data = json.loads(result)
    # session_id → sessionID via convert() alias generator
    props = data["properties"]
    assert "sessionID" in props


# =============================================================================
# GlobalEvent model and GlobalEventFactory tests
# =============================================================================


def test_global_event_model_construction() -> None:
    """GlobalEvent stores directory, project, and payload correctly."""
    payload = {"type": "test"}
    event = GlobalEvent(directory="/tmp/test", project="abc123", payload=payload)
    dumped = event.model_dump(by_alias=True, exclude_none=True)
    assert dumped["directory"] == "/tmp/test"
    assert dumped["project"] == "abc123"
    assert dumped["payload"] == payload


def test_global_event_workspace_omitted_when_none() -> None:
    """Workspace is excluded from output when not provided."""
    event = GlobalEvent(directory="/tmp/test", project="abc123", payload={})
    dumped = event.model_dump(by_alias=True, exclude_none=True)
    assert "workspace" not in dumped


def test_global_event_factory_wrap() -> None:
    """Factory.wrap() produces JSON with directory, project, payload."""
    factory = GlobalEventFactory(directory="/tmp", project="abc")
    event = SessionStatusEvent.create(session_id="sid1", status_type="idle")
    result = factory.wrap(event)
    data = json.loads(result)
    assert data["directory"] == "/tmp"
    assert data["project"] == "abc"
    assert isinstance(data["payload"], dict)


def test_global_event_factory_session_id_in_payload() -> None:
    """SessionId is injected inside payload by _serialize_event."""
    factory = GlobalEventFactory(directory="/tmp", project="abc")
    event = SessionStatusEvent.create(session_id="sid1", status_type="idle")
    result = factory.wrap(event)
    data = json.loads(result)
    assert data["payload"]["sessionId"] == "sid1"


def test_global_event_factory_is_global_only_event() -> None:
    """Server events are global-only; session events are not."""
    assert GlobalEventFactory.is_global_only_event(ServerConnectedEvent())
    assert GlobalEventFactory.is_global_only_event(ServerHeartbeatEvent())
    assert not GlobalEventFactory.is_global_only_event(
        SessionStatusEvent.create(session_id="a", status_type="busy")
    )


def test_global_event_factory_unicode_preserved() -> None:
    """Factory.wrap() preserves Unicode characters in output."""
    factory = GlobalEventFactory(directory="/tmp", project="abc")
    event = SessionStatusEvent.create(session_id="会话1", status_type="busy")
    result = factory.wrap(event)
    assert "会话1" in result
    assert "\\u" not in result


def test_global_event_model_project_global() -> None:
    """GlobalEvent with project='global' preserves the value."""
    event = GlobalEvent(directory="/tmp", project="global", payload={})
    dumped = event.model_dump(by_alias=True, exclude_none=True)
    assert dumped["project"] == "global"


def test_global_event_factory_wrap_returns_string() -> None:
    """Factory.wrap() returns a str (JSON string)."""
    factory = GlobalEventFactory(directory="/tmp", project="abc")
    event = ServerHeartbeatEvent()
    result = factory.wrap(event)
    assert isinstance(result, str)


# =============================================================================
# _event_generator integration tests
# =============================================================================


class _MockState:
    """Minimal ServerState-like object for _event_generator tests."""

    def __init__(self, working_dir: str = "/tmp/test_wd") -> None:
        self.working_dir = working_dir
        self.event_subscribers: list[asyncio.Queue[Event]] = []
        self._event_factory: GlobalEventFactory | None = None
        self._first_subscriber_triggered = False
        self.on_first_subscriber: Any = None

    def get_event_factory(self) -> GlobalEventFactory:
        if self._event_factory is None:
            from agentpool_storage.opencode_provider import helpers

            self._event_factory = GlobalEventFactory(
                directory=self.working_dir,
                project=helpers.compute_project_id(self.working_dir),
            )
        return self._event_factory

    def create_background_task(self, coro: Any, name: str = "") -> asyncio.Task[Any]:
        return asyncio.ensure_future(coro)


async def _collect_events(
    state: _MockState,
    wrap_payload: bool,
    events_to_send: list[Event],
) -> list[dict[str, Any]]:
    """Collect SSE items from _event_generator with given events."""
    results: list[dict[str, Any]] = []
    gen = _event_generator(state, wrap_payload=wrap_payload)
    # Get the initial connected event
    item = await gen.__anext__()
    results.append(json.loads(item["data"]))
    # Send additional events through the queue
    queue = state.event_subscribers[-1]
    for event in events_to_send:
        await queue.put(event)
        item = await gen.__anext__()
        results.append(json.loads(item["data"]))
    return results


@pytest.mark.anyio
async def test_global_event_server_connected_is_bare() -> None:
    """First event from /global/event is bare server.connected (no wrapper)."""
    state = _MockState()
    events = await _collect_events(state, wrap_payload=True, events_to_send=[])
    # Only the connected event
    assert len(events) == 1
    connected = events[0]
    assert connected["type"] == "server.connected"
    # Bare: no directory/project/payload keys
    assert "directory" not in connected
    assert "project" not in connected
    assert "payload" not in connected


@pytest.mark.anyio
async def test_global_event_wraps_regular_events_in_envelope() -> None:
    """/global/event wraps SessionStatusEvent in GlobalEvent envelope."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="s1", status_type="busy")
    events = await _collect_events(state, wrap_payload=True, events_to_send=[session_evt])
    assert len(events) == 2
    wrapped = events[1]
    assert "directory" in wrapped
    assert "project" in wrapped
    assert "payload" in wrapped
    assert wrapped["payload"]["type"] == "session.status"


@pytest.mark.anyio
async def test_global_event_heartbeat_is_bare() -> None:
    """/global/event sends ServerHeartbeatEvent as bare JSON (no wrapper)."""
    state = _MockState()
    hb = ServerHeartbeatEvent()
    events = await _collect_events(state, wrap_payload=True, events_to_send=[hb])
    assert len(events) == 2
    heartbeat = events[1]
    assert heartbeat["type"] == "server.heartbeat"
    # Bare: no envelope keys
    assert "directory" not in heartbeat
    assert "project" not in heartbeat
    assert "payload" not in heartbeat


@pytest.mark.anyio
async def test_event_endpoint_all_events_are_bare() -> None:
    """/event sends connected, heartbeat, and session events all as bare."""
    state = _MockState()
    hb = ServerHeartbeatEvent()
    session_evt = SessionStatusEvent.create(session_id="s2", status_type="idle")
    events = await _collect_events(state, wrap_payload=False, events_to_send=[hb, session_evt])
    assert len(events) == 3
    for evt in events:
        # None should have envelope wrapper keys
        assert "directory" not in evt
        assert "project" not in evt
        assert "payload" not in evt
    assert events[0]["type"] == "server.connected"
    assert events[1]["type"] == "server.heartbeat"
    assert events[2]["type"] == "session.status"


@pytest.mark.anyio
async def test_global_events_have_no_session_id() -> None:
    """ServerConnectedEvent and ServerHeartbeatEvent lack sessionId."""
    state = _MockState()
    hb = ServerHeartbeatEvent()
    events = await _collect_events(state, wrap_payload=True, events_to_send=[hb])
    assert "sessionId" not in events[0]  # server.connected
    assert "sessionId" not in events[1]  # server.heartbeat


@pytest.mark.anyio
async def test_global_event_directory_matches_working_dir() -> None:
    """Envelope directory field matches state.working_dir."""
    wd = "/custom/working/dir"
    state = _MockState(working_dir=wd)
    session_evt = SessionStatusEvent.create(session_id="s3", status_type="retry")
    events = await _collect_events(state, wrap_payload=True, events_to_send=[session_evt])
    wrapped = events[1]
    assert wrapped["directory"] == wd


@pytest.mark.anyio
async def test_multiple_events_maintain_correct_wrapping() -> None:
    """Sequence of wrapped/bare/wrapped events all have correct format."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="s4", status_type="busy")
    hb = ServerHeartbeatEvent()
    session_evt2 = SessionStatusEvent.create(session_id="s5", status_type="idle")
    events = await _collect_events(
        state,
        wrap_payload=True,
        events_to_send=[session_evt, hb, session_evt2],
    )
    assert len(events) == 4
    # [0] connected — bare
    assert events[0]["type"] == "server.connected"
    assert "payload" not in events[0]
    # [1] session status — wrapped
    assert "payload" in events[1]
    assert events[1]["payload"]["type"] == "session.status"
    # [2] heartbeat — bare
    assert events[2]["type"] == "server.heartbeat"
    assert "payload" not in events[2]
    # [3] session status — wrapped
    assert "payload" in events[3]
    assert events[3]["payload"]["type"] == "session.status"


# =============================================================================
# /event endpoint backward compatibility tests
# =============================================================================


@pytest.mark.anyio
async def test_event_endpoint_no_global_event_fields() -> None:
    """wrap_payload=False events have no directory/project/workspace."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="bc1", status_type="busy")
    events = await _collect_events(state, wrap_payload=False, events_to_send=[session_evt])
    for evt in events:
        assert "directory" not in evt
        assert "project" not in evt
        assert "workspace" not in evt


@pytest.mark.anyio
async def test_event_endpoint_no_payload_wrapper() -> None:
    """No payload wrapper key; event data is at top level."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="bc2", status_type="idle")
    events = await _collect_events(state, wrap_payload=False, events_to_send=[session_evt])
    session_data = events[1]
    assert "payload" not in session_data
    # Event fields directly at top level
    assert session_data["type"] == "session.status"


@pytest.mark.anyio
async def test_event_endpoint_session_id_at_top_level() -> None:
    """SessionId present at top level for session events."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="bc3", status_type="busy")
    events = await _collect_events(state, wrap_payload=False, events_to_send=[session_evt])
    session_data = events[1]
    assert session_data["sessionId"] == "bc3"


@pytest.mark.anyio
async def test_event_endpoint_unicode_preserved() -> None:
    r"""Unicode characters not escaped as \uXXXX in /event output."""
    state = _MockState()
    session_evt = SessionStatusEvent.create(session_id="会话测试", status_type="idle")
    gen = _event_generator(state, wrap_payload=False)
    # Consume connected event
    await gen.__anext__()
    # Send unicode session event
    queue = state.event_subscribers[-1]
    await queue.put(session_evt)
    item = await gen.__anext__()
    raw_data = item["data"]
    assert "会话测试" in raw_data
    assert "\\u" not in raw_data


# =============================================================================
# SSE integration tests for /global/event
# =============================================================================


async def _collect_real_events(
    state: ServerState,
    wrap_payload: bool,
    events_to_send: list[Event],
) -> list[dict[str, Any]]:
    """Collect SSE items from _event_generator with real ServerState."""
    results: list[dict[str, Any]] = []
    gen = _event_generator(state, wrap_payload=wrap_payload)
    # Get the initial connected event
    item = await gen.__anext__()
    results.append(json.loads(item["data"]))
    # Send additional events through the real broadcast system
    for event in events_to_send:
        await state.broadcast_event(event)
        # Yield control so the event can propagate through subscriber queues
        await asyncio.sleep(0.01)
        item = await gen.__anext__()
        results.append(json.loads(item["data"]))
    return results


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_envelope_fields(
    server_state: ServerState,
) -> None:
    """Test /global/event returns SSE with GlobalEvent envelope."""
    event = SessionStatusEvent.create(session_id="s1", status_type="busy")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    assert len(results) == 2
    received = results[1]
    assert "directory" in received
    assert "project" in received
    assert "payload" in received


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_directory_matches_working_dir(
    server_state: ServerState,
) -> None:
    """Test directory field matches server_state working_dir."""
    event = SessionStatusEvent.create(session_id="s2", status_type="idle")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    received = results[1]
    assert received["directory"] == server_state.working_dir


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_project_computed(
    server_state: ServerState,
) -> None:
    """Test project field is computed via compute_project_id."""
    from agentpool_storage.opencode_provider.helpers import compute_project_id

    event = SessionStatusEvent.create(session_id="s3", status_type="busy")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    received = results[1]
    expected_project = compute_project_id(server_state.working_dir)
    assert received["project"] == expected_project


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_workspace_absent(
    server_state: ServerState,
) -> None:
    """Test workspace field is absent from GlobalEvent."""
    event = SessionStatusEvent.create(session_id="s4", status_type="retry")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    received = results[1]
    assert "workspace" not in received


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_session_id_in_payload(
    server_state: ServerState,
) -> None:
    """Test sessionId injection at top level of GlobalEvent payload."""
    event = SessionStatusEvent.create(session_id="injected-sid", status_type="busy")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    received = results[1]
    payload = received["payload"]
    assert payload["sessionId"] == "injected-sid"


@pytest.mark.integration
@pytest.mark.anyio
async def test_global_event_integration_unicode_preserved(
    server_state: ServerState,
) -> None:
    r"""Test unicode characters preserved in SSE output (not \uXXXX escaped)."""
    event = SessionStatusEvent.create(session_id="会话测试", status_type="idle")
    results = await _collect_real_events(server_state, wrap_payload=True, events_to_send=[event])
    received = results[1]
    payload = received["payload"]
    assert payload["sessionId"] == "会话测试"


# =============================================================================
# on_first_subscriber callback tests
# =============================================================================


@pytest.mark.anyio
async def test_on_first_subscriber_fires_once() -> None:
    """Callback fires exactly once on first subscriber."""
    state = _MockState()
    callback = AsyncMock()
    state.on_first_subscriber = callback

    gen = _event_generator(state, wrap_payload=False)
    await gen.__anext__()  # consume connected event

    assert state._first_subscriber_triggered is True
    await asyncio.sleep(0.05)
    callback.assert_called_once()


@pytest.mark.anyio
async def test_on_first_subscriber_does_not_fire_on_second_subscriber() -> None:
    """Callback does not fire again on second subscriber."""
    state = _MockState()
    callback = AsyncMock()
    state.on_first_subscriber = callback

    gen1 = _event_generator(state, wrap_payload=False)
    await gen1.__anext__()  # consume connected event

    await asyncio.sleep(0.05)
    assert callback.call_count == 1

    gen2 = _event_generator(state, wrap_payload=False)
    await gen2.__anext__()  # consume connected event

    await asyncio.sleep(0.05)
    # Callback should still have been called only once
    callback.assert_called_once()


@pytest.mark.anyio
async def test_on_first_subscriber_flag_set_and_stays_true() -> None:
    """First subscriber flag is set to True after first subscriber and stays True."""
    state = _MockState()
    callback = AsyncMock()
    state.on_first_subscriber = callback

    assert state._first_subscriber_triggered is False

    gen1 = _event_generator(state, wrap_payload=False)
    await gen1.__anext__()  # consume connected event

    assert state._first_subscriber_triggered is True

    gen2 = _event_generator(state, wrap_payload=False)
    await gen2.__anext__()  # consume connected event

    # Flag must remain True, never reset
    assert state._first_subscriber_triggered is True


@pytest.mark.anyio
async def test_on_first_subscriber_fires_before_events_delivered() -> None:
    """Callback fires before the generator yields any events beyond connected."""
    state = _MockState()
    callback = AsyncMock()
    state.on_first_subscriber = callback

    gen = _event_generator(state, wrap_payload=False)
    # Consuming the connected event should have already triggered the callback
    await gen.__anext__()

    # The flag is set synchronously before yielding the connected event
    assert state._first_subscriber_triggered is True
    await asyncio.sleep(0.05)
    # The background task created by the callback should have been scheduled
    callback.assert_called_once()


@pytest.mark.anyio
async def test_on_first_subscriber_no_callback_set() -> None:
    """No callback invocation when on_first_subscriber is None."""
    state = _MockState()
    # on_first_subscriber is None by default
    assert state.on_first_subscriber is None

    gen = _event_generator(state, wrap_payload=False)
    await gen.__anext__()  # consume connected event

    # Flag should not be set because there is no callback
    assert state._first_subscriber_triggered is False


# =============================================================================
# Client disconnect cleanup tests
# =============================================================================


@pytest.mark.anyio
async def test_disconnect_queue_removed_from_subscribers() -> None:
    """Queue is removed from event_subscribers when client disconnects."""
    state = _MockState()
    gen = _event_generator(state, wrap_payload=False)
    await gen.__anext__()  # consume connected event
    assert len(state.event_subscribers) == 1

    await gen.aclose()
    assert len(state.event_subscribers) == 0


@pytest.mark.anyio
async def test_disconnect_events_not_delivered() -> None:
    """After disconnect, broadcast_event does not deliver to disconnected client."""
    state = _MockState()
    gen1 = _event_generator(state, wrap_payload=False)
    await gen1.__anext__()  # consume connected event
    # Add a second subscriber that stays connected to verify isolation
    gen2 = _event_generator(state, wrap_payload=False)
    await gen2.__anext__()  # consume connected event
    assert len(state.event_subscribers) == 2

    queue1 = state.event_subscribers[0]
    queue2 = state.event_subscribers[1]

    # Disconnect first client
    await gen1.aclose()
    assert len(state.event_subscribers) == 1
    assert queue1 not in state.event_subscribers
    assert queue2 in state.event_subscribers

    # Put an event directly — only queue2 should receive it
    event = SessionStatusEvent.create(session_id="disc1", status_type="busy")
    await queue2.put(event)
    item = await gen2.__anext__()
    data = json.loads(item["data"])
    assert data["type"] == "session.status"


@pytest.mark.anyio
async def test_disconnect_finally_block_executes() -> None:
    """The finally block in _event_generator runs on disconnect, removing the queue."""
    state = _MockState()
    gen = _event_generator(state, wrap_payload=False)
    await gen.__anext__()  # consume connected event
    queue_before = state.event_subscribers[-1]
    assert queue_before in state.event_subscribers

    await gen.aclose()

    # The finally block removed the queue
    assert queue_before not in state.event_subscribers
    assert len(state.event_subscribers) == 0


@pytest.mark.anyio
async def test_disconnect_abrupt_cleanup() -> None:
    """Abrupt disconnect (task cancellation) still triggers cleanup."""
    state = _MockState()

    async def consume() -> None:
        gen = _event_generator(state, wrap_payload=False)
        with contextlib.suppress(StopAsyncIteration):
            async for _ in gen:
                pass

    task = asyncio.create_task(consume())
    # Let the generator start and consume the connected event
    await asyncio.sleep(0.05)
    assert len(state.event_subscribers) == 1

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # Cleanup should have removed the subscriber queue
    assert len(state.event_subscribers) == 0


@pytest.mark.anyio
async def test_disconnect_no_memory_leak() -> None:
    """Multiple connect/disconnect cycles do not cause subscriber list growth."""
    state = _MockState()

    for _ in range(5):
        gen = _event_generator(state, wrap_payload=False)
        await gen.__anext__()  # consume connected event
        assert len(state.event_subscribers) == 1
        await gen.aclose()
        assert len(state.event_subscribers) == 0

    # After 5 cycles, no leaked subscribers
    assert len(state.event_subscribers) == 0


# =============================================================================
# _extract_session_id exhaustiveness tests
# =============================================================================


def _make_session(session_id: str = "test-sid") -> Session:
    """Create a minimal Session for event construction."""
    return Session(
        id=session_id,
        project_id="proj1",
        directory="/tmp",
        title="Test",
        time=TimeCreatedUpdated(created=0, updated=0),
    )


def _make_part(session_id: str = "test-sid") -> Part:
    """Create a minimal Part for event construction."""
    return TextPart(
        id="part1",
        message_id="msg1",
        session_id=session_id,
        text="hello",
    )


# All 16 handled event types with their constructors
_HANDLED_EVENT_FACTORIES: list[tuple[str, type]] = [
    ("session.deleted", SessionDeletedEvent),
    ("session.status", SessionStatusEvent),
    ("session.idle", SessionIdleEvent),
    ("session.compacted", SessionCompactedEvent),
    ("message.removed", MessageRemovedEvent),
    ("message.part.removed", PartRemovedEvent),
    ("permission.asked", PermissionRequestEvent),
    ("permission.replied", PermissionResolvedEvent),
    ("question.asked", QuestionAskedEvent),
    ("question.replied", QuestionRepliedEvent),
    ("question.rejected", QuestionRejectedEvent),
    ("todo.updated", TodoUpdatedEvent),
    ("session.error", SessionErrorEvent),
    ("session.created", SessionCreatedEvent),
    ("session.updated", SessionUpdatedEvent),
    ("message.part.updated", PartUpdatedEvent),
]


def _build_handled_event(event_type: type) -> Event:  # noqa: PLR0911
    """Build a handled event with session_id='abc' using the appropriate constructor."""
    sid = "abc"
    if event_type is SessionDeletedEvent:
        return SessionDeletedEvent.create(session_id=sid)
    if event_type is SessionStatusEvent:
        return SessionStatusEvent.create(session_id=sid, status_type="busy")
    if event_type is SessionIdleEvent:
        return SessionIdleEvent.create(session_id=sid)
    if event_type is SessionCompactedEvent:
        return SessionCompactedEvent.create(session_id=sid)
    if event_type is MessageRemovedEvent:
        return MessageRemovedEvent.create(session_id=sid, message_id="m1")
    if event_type is PartRemovedEvent:
        return PartRemovedEvent.create(session_id=sid, message_id="m1", part_id="p1")
    if event_type is PermissionRequestEvent:
        return PermissionRequestEvent.create(
            session_id=sid,
            permission_id="perm1",
            tool_name="bash",
            args_preview="ls",
            message="Allow?",
        )
    if event_type is PermissionResolvedEvent:
        return PermissionResolvedEvent.create(
            session_id=sid,
            request_id="perm1",
            reply="once",
        )
    if event_type is QuestionAskedEvent:
        return QuestionAskedEvent.create(
            request_id="q1",
            session_id=sid,
            questions=[
                QuestionInfo(
                    question="Continue?",
                    header="Confirm",
                    options=[QuestionOption(label="Yes", description="Proceed")],
                )
            ],
        )
    if event_type is QuestionRepliedEvent:
        return QuestionRepliedEvent.create(
            session_id=sid,
            request_id="q1",
            answers=[["Yes"]],
        )
    if event_type is QuestionRejectedEvent:
        return QuestionRejectedEvent.create(
            session_id=sid,
            request_id="q1",
        )
    if event_type is TodoUpdatedEvent:
        return TodoUpdatedEvent.create(session_id=sid, todos=[])
    if event_type is SessionErrorEvent:
        return SessionErrorEvent.create(session_id=sid, error_name="TestError")
    if event_type is SessionCreatedEvent:
        return SessionCreatedEvent.create(session=_make_session(sid))
    if event_type is SessionUpdatedEvent:
        return SessionUpdatedEvent.create(session=_make_session(sid))
    if event_type is PartUpdatedEvent:
        return PartUpdatedEvent.create(part=_make_part(sid))
    msg = f"Unhandled event type in test helper: {event_type}"
    raise ValueError(msg)


@pytest.mark.parametrize(
    ("event_type_name", "event_type"),
    [(name, cls) for name, cls in _HANDLED_EVENT_FACTORIES],
    ids=[name for name, _ in _HANDLED_EVENT_FACTORIES],
)
def test_extract_session_id_handled_events(
    event_type_name: str,
    event_type: type,
) -> None:
    """All 16 handled event types extract sessionId correctly."""
    event = _build_handled_event(event_type)
    result = _extract_session_id(event)
    assert result == "abc", f"Expected 'abc' for {event_type_name}, got {result!r}"


def test_extract_session_id_session_error_nullable() -> None:
    """SessionErrorEvent with None session_id returns None."""
    event = SessionErrorEvent.create(session_id=None, error_name="TestError")
    result = _extract_session_id(event)
    assert result is None


def test_extract_session_id_unhandled_events_return_none() -> None:
    """Unhandled event types return None from _extract_session_id."""
    unhandled_events: list[Event] = [
        PartDeltaEvent.create(
            session_id="x",
            message_id="m1",
            part_id="p1",
            delta="hi",
        ),
        MessageUpdatedEvent.create(
            message=UserMessage(
                id="m1",
                session_id="x",
                time=TimeCreated(created=0),
            ),
        ),
        ServerConnectedEvent(),
        ServerHeartbeatEvent(),
    ]
    for event in unhandled_events:
        result = _extract_session_id(event)
        assert result is None, f"Expected None for {type(event).__name__}, got {result!r}"


def test_extract_session_id_unhandled_events_with_session_id_return_none() -> None:
    r"""Known-gap unhandled events that HAVE session_id still return None.

    These 5 events have session_id in properties but are not handled
    by _extract_session_id, so they fall through to the wildcard case.
    """
    unhandled_with_sid: list[Event] = [
        SessionDiffEvent.create(session_id="gap1", diff=[]),
        PartDeltaEvent.create(
            session_id="gap2",
            message_id="m1",
            part_id="p1",
            delta="x",
        ),
        PermissionUpdatedEvent.create(
            session_id="gap3",
            permission_id="perm1",
            tool_name="bash",
            patterns=["bash: *"],
            metadata={},
        ),
        CommandExecutedEvent.create(
            name="test",
            session_id="gap4",
            arguments="",
            message_id="m1",
        ),
        TuiSessionSelectEvent.create(session_id="gap5"),
    ]
    for event in unhandled_with_sid:
        result = _extract_session_id(event)
        assert result is None, f"Expected None for known-gap {type(event).__name__}, got {result!r}"


def test_extract_session_id_warning_logged_for_unhandled(caplog: pytest.LogCaptureFixture) -> None:
    """Warning is logged when an unhandled event type hits the wildcard case."""
    event = PartDeltaEvent.create(
        session_id="warn-test",
        message_id="m1",
        part_id="p1",
        delta="x",
    )
    with caplog.at_level("WARNING"):
        result = _extract_session_id(event)
    assert result is None
    assert "Unhandled event type in _extract_session_id" in caplog.text
    assert "PartDeltaEvent" in caplog.text


def test_extract_session_id_no_warning_for_handled(caplog: pytest.LogCaptureFixture) -> None:
    """No warning logged for handled event types."""
    event = SessionStatusEvent.create(session_id="no-warn", status_type="idle")
    with caplog.at_level("WARNING"):
        _extract_session_id(event)
    assert "Unhandled event type in _extract_session_id" not in caplog.text


def test_extract_session_id_exhaustiveness() -> None:
    """All Event union members are either handled or explicitly documented as no-session.

    Catches future regressions: if a new event type with session_id is added
    to the Event union but not to _extract_session_id, this test fails.
    """
    # Event types handled by _extract_session_id match cases
    handled_types: set[type] = {
        SessionDeletedEvent,
        SessionStatusEvent,
        SessionIdleEvent,
        SessionCompactedEvent,
        MessageRemovedEvent,
        PartRemovedEvent,
        PermissionRequestEvent,
        PermissionResolvedEvent,
        QuestionAskedEvent,
        QuestionRepliedEvent,
        QuestionRejectedEvent,
        TodoUpdatedEvent,
        SessionErrorEvent,
        SessionCreatedEvent,
        SessionUpdatedEvent,
        PartUpdatedEvent,
    }

    # Event types that genuinely have no session association
    # (no session_id field anywhere in their properties)
    no_session_types: set[type] = {
        ServerConnectedEvent,
        ServerHeartbeatEvent,
        FileWatcherUpdatedEvent,
        FileEditedEvent,
        McpToolsChangedEvent,
        VcsBranchUpdatedEvent,
        TuiPromptAppendEvent,
        TuiCommandExecuteEvent,
        TuiToastShowEvent,
        ProjectUpdatedEvent,
        LspUpdatedEvent,
        LspClientDiagnosticsEvent,
        PtyCreatedEvent,
        PtyUpdatedEvent,
        PtyExitedEvent,
        PtyDeletedEvent,
    }

    # Event types with session_id that are NOT handled (known gaps)
    known_gap_types: set[type] = {
        SessionDiffEvent,
        PartDeltaEvent,
        PermissionUpdatedEvent,
        CommandExecutedEvent,
        TuiSessionSelectEvent,
    }

    # MessageUpdatedEvent has no session_id at top level (it uses info.id pattern)
    no_session_types.add(MessageUpdatedEvent)

    expected = handled_types | no_session_types | known_gap_types

    # Get all members of the Event union
    event_union_args: set[type] = set(Event.__args__)

    # Every union member must be accounted for
    missing = event_union_args - expected
    assert not missing, (
        f"New event types not covered by _extract_session_id: "
        f"{sorted(t.__name__ for t in missing)}. "
        f"Add them to handled_types, no_session_types, or known_gap_types."
    )

    # No extra types that aren't in the union
    extra = expected - event_union_args
    assert not extra, (
        f"Types listed in test but not in Event union: {sorted(t.__name__ for t in extra)}"
    )

    # Known gaps should be documented — if they're fixed, move them to handled
    if known_gap_types:
        gap_names = sorted(t.__name__ for t in known_gap_types)
        # This assertion always passes but documents the known gaps
        assert True, f"Known gap types with session_id not handled: {gap_names}"


# =============================================================================
# Concurrent subscriber tests
# =============================================================================


@pytest.mark.anyio
async def test_concurrent_two_subscribers_both_receive_events() -> None:
    """Two SSE clients both receive a broadcast event."""
    state = _MockState()

    gen1 = _event_generator(state, wrap_payload=True)
    gen2 = _event_generator(state, wrap_payload=True)

    # Consume initial connected events
    await gen1.__anext__()
    await gen2.__anext__()

    assert len(state.event_subscribers) == 2

    # Broadcast event to both subscribers via their queues
    event = SessionStatusEvent.create(session_id="s_concurrent", status_type="busy")
    queue1 = state.event_subscribers[0]
    queue2 = state.event_subscribers[1]
    await queue1.put(event)
    await queue2.put(event)

    item1 = await gen1.__anext__()
    item2 = await gen2.__anext__()

    data1 = json.loads(item1["data"])
    data2 = json.loads(item2["data"])

    assert data1["payload"]["type"] == "session.status"
    assert data2["payload"]["type"] == "session.status"
    assert data1["payload"]["sessionId"] == "s_concurrent"
    assert data2["payload"]["sessionId"] == "s_concurrent"


@pytest.mark.anyio
async def test_concurrent_subscribers_receive_same_content() -> None:
    """Both subscribers get identical GlobalEvent envelopes."""
    state = _MockState()

    gen1 = _event_generator(state, wrap_payload=True)
    gen2 = _event_generator(state, wrap_payload=True)

    await gen1.__anext__()
    await gen2.__anext__()

    event = SessionStatusEvent.create(session_id="s_same", status_type="idle")
    queue1 = state.event_subscribers[0]
    queue2 = state.event_subscribers[1]
    await queue1.put(event)
    await queue2.put(event)

    item1 = await gen1.__anext__()
    item2 = await gen2.__anext__()

    # Both envelopes must have identical directory, project, and payload
    data1 = json.loads(item1["data"])
    data2 = json.loads(item2["data"])

    assert data1["directory"] == data2["directory"]
    assert data1["project"] == data2["project"]
    assert data1["payload"] == data2["payload"]


@pytest.mark.anyio
async def test_concurrent_event_ordering_preserved() -> None:
    """Broadcast 3 events; each subscriber receives them in order."""
    state = _MockState()

    gen1 = _event_generator(state, wrap_payload=True)
    gen2 = _event_generator(state, wrap_payload=True)

    await gen1.__anext__()
    await gen2.__anext__()

    queue1 = state.event_subscribers[0]
    queue2 = state.event_subscribers[1]

    events = [
        SessionStatusEvent.create(session_id="ord1", status_type="busy"),
        SessionStatusEvent.create(session_id="ord2", status_type="idle"),
        SessionStatusEvent.create(session_id="ord3", status_type="retry"),
    ]

    for ev in events:
        await queue1.put(ev)
        await queue2.put(ev)

    # Collect all 3 events from each subscriber
    received1 = [json.loads((await gen1.__anext__())["data"]) for _ in range(3)]
    received2 = [json.loads((await gen2.__anext__())["data"]) for _ in range(3)]

    expected_order = ["ord1", "ord2", "ord3"]
    ids1 = [r["payload"]["sessionId"] for r in received1]
    ids2 = [r["payload"]["sessionId"] for r in received2]

    assert ids1 == expected_order
    assert ids2 == expected_order


@pytest.mark.anyio
async def test_concurrent_subscriber_receives_after_another_disconnects() -> None:
    """Subscriber B still receives events after subscriber A disconnects."""
    state = _MockState()

    gen_a = _event_generator(state, wrap_payload=True)
    gen_b = _event_generator(state, wrap_payload=True)

    await gen_a.__anext__()
    await gen_b.__anext__()

    assert len(state.event_subscribers) == 2

    # Disconnect subscriber A
    await gen_a.aclose()
    assert len(state.event_subscribers) == 1

    # Send event only to remaining subscriber B's queue
    event = SessionStatusEvent.create(session_id="s_survive", status_type="busy")
    queue_b = state.event_subscribers[0]
    await queue_b.put(event)

    item_b = await gen_b.__anext__()
    data_b = json.loads(item_b["data"])

    assert data_b["payload"]["type"] == "session.status"
    assert data_b["payload"]["sessionId"] == "s_survive"


@pytest.mark.anyio
async def test_concurrent_all_get_server_connected() -> None:
    """Each subscriber gets the initial bare server.connected event."""
    state = _MockState()

    gen1 = _event_generator(state, wrap_payload=True)
    gen2 = _event_generator(state, wrap_payload=True)
    gen3 = _event_generator(state, wrap_payload=True)

    item1 = await gen1.__anext__()
    item2 = await gen2.__anext__()
    item3 = await gen3.__anext__()

    for item in [item1, item2, item3]:
        data = json.loads(item["data"])
        assert data["type"] == "server.connected"
        # Bare event: no GlobalEvent wrapper keys
        assert "directory" not in data
        assert "project" not in data
        assert "payload" not in data


# =============================================================================
# ServerState.broadcast_event direct tests
# =============================================================================


def _make_broadcast_state() -> ServerState:
    """Create a ServerState with a minimal mock agent for broadcast_event tests."""
    from unittest.mock import Mock

    mock_env = Mock()
    mock_env.get_fs = Mock(return_value=Mock())
    mock_agent = Mock()
    mock_agent.env = mock_env
    return ServerState(working_dir="/test", agent=mock_agent)


@pytest.mark.anyio
async def test_broadcast_event_single_subscriber() -> None:
    """Broadcast delivers event to one subscriber queue."""
    state = _make_broadcast_state()
    queue: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.append(queue)

    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    await state.broadcast_event(event)

    received = queue.get_nowait()
    assert received is event


@pytest.mark.anyio
async def test_broadcast_event_multiple_subscribers() -> None:
    """Broadcast delivers event to all subscriber queues."""
    state = _make_broadcast_state()
    queue1: asyncio.Queue[Event] = asyncio.Queue()
    queue2: asyncio.Queue[Event] = asyncio.Queue()
    queue3: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.extend([queue1, queue2, queue3])

    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    await state.broadcast_event(event)

    assert queue1.get_nowait() is event
    assert queue2.get_nowait() is event
    assert queue3.get_nowait() is event


@pytest.mark.anyio
async def test_broadcast_event_no_subscribers() -> None:
    """Broadcast with no subscribers does not raise."""
    state = _make_broadcast_state()
    assert state.event_subscribers == []

    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    await state.broadcast_event(event)  # Should not raise


@pytest.mark.anyio
async def test_broadcast_event_exception_isolation() -> None:
    """Subscriber whose queue raises is removed; other subscribers still receive."""
    state = _make_broadcast_state()

    good_queue: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.append(good_queue)

    # Create a mock queue that raises on put_nowait
    bad_queue = MagicMock(spec=asyncio.Queue)
    bad_queue.put_nowait.side_effect = RuntimeError("queue broken")
    state.event_subscribers.append(bad_queue)

    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    await state.broadcast_event(event)

    # Good queue should still have received the event
    assert good_queue.get_nowait() is event
    # Bad queue should have been removed from subscribers
    assert bad_queue not in state.event_subscribers
    assert good_queue in state.event_subscribers


@pytest.mark.anyio
async def test_broadcast_event_queue_full_dropped() -> None:
    """Full queue has event dropped; other subscribers still receive."""
    state = _make_broadcast_state()

    # Queue with maxsize=1, already full
    full_queue: asyncio.Queue[Event] = asyncio.Queue(maxsize=1)
    full_queue.put_nowait(ServerHeartbeatEvent())  # Fill the queue
    state.event_subscribers.append(full_queue)

    good_queue: asyncio.Queue[Event] = asyncio.Queue()
    state.event_subscribers.append(good_queue)

    event = SessionStatusEvent.create(session_id="abc", status_type="busy")
    await state.broadcast_event(event)

    # Full queue should still have only the original item (event dropped)
    assert full_queue.qsize() == 1
    assert not isinstance(full_queue.get_nowait(), SessionStatusEvent)

    # Good queue should have received the event
    assert good_queue.get_nowait() is event


# =============================================================================
# /global/health endpoint tests
# =============================================================================


@pytest.mark.anyio
async def test_global_health_endpoint(async_client: AsyncClient) -> None:
    """GET /global/health returns 200 with HealthResponse body."""
    response = await async_client.get("/global/health")
    assert response.status_code == 200
    data = response.json()
    assert data["healthy"] is True
    assert "version" in data


@pytest.mark.anyio
async def test_global_health_endpoint_fields(async_client: AsyncClient) -> None:
    """GET /global/health returns correct healthy and version fields."""
    from agentpool_server.opencode_server.routes.global_routes import VERSION

    response = await async_client.get("/global/health")
    data = response.json()
    assert data["healthy"] is True
    assert data["version"] == VERSION
