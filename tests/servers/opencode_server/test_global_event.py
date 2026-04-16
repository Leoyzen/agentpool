"""Tests for _serialize_event, GlobalEvent model, and GlobalEventFactory."""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from agentpool_server.opencode_server.models import GlobalEvent
from agentpool_server.opencode_server.models.events import (  # noqa: TC001
    Event,
    ServerConnectedEvent,
    ServerHeartbeatEvent,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.routes.global_routes import (
    GlobalEventFactory,
    _event_generator,
    _serialize_event,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.state import ServerState


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
