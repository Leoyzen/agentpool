"""Tests for _serialize_event, GlobalEvent model, and GlobalEventFactory."""

from __future__ import annotations

import json

import pytest

from agentpool_server.opencode_server.models import GlobalEvent
from agentpool_server.opencode_server.models.events import (
    ServerConnectedEvent,
    ServerHeartbeatEvent,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.routes.global_routes import (
    GlobalEventFactory,
    _serialize_event,
)


# =============================================================================
# _serialize_event baseline tests
# =============================================================================


def test_serialize_event_session_id_injection() -> None:
    """sessionId is injected at top level when event has a session."""
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
    """Unicode characters are preserved (not \\uXXXX escaped)."""
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
    """workspace is excluded from output when not provided."""
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
    """sessionId is injected inside payload by _serialize_event."""
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
