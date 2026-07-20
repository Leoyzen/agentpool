"""Unit tests for SSE event parsing utilities."""

from __future__ import annotations

import json

import anyio
import pytest

from tests.helpers.sse_utils import parse_sse_events


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


def test_parse_sse_events_basic() -> None:
    """Parse a simple SSE response with one event."""
    body = "event: session.status\ndata: {\"status\": \"busy\"}\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "session.status"
    assert events[0]["data"] == {"status": "busy"}


def test_parse_sse_events_multiple() -> None:
    """Parse multiple events preserving order."""
    body = (
        'event: server.connected\ndata: {"id": "1"}\n\n'
        'event: session.status\ndata: {"status": "busy"}\n\n'
        'event: session.status\ndata: {"status": "idle"}\n\n'
    )
    events = parse_sse_events(body)
    assert len(events) == 3
    assert events[0]["event"] == "server.connected"
    assert events[1]["data"] == {"status": "busy"}
    assert events[2]["data"] == {"status": "idle"}


def test_parse_sse_events_multi_line_data() -> None:
    """Handle multi-line data fields by joining with newlines."""
    payload = {"text": "line1\nline2"}
    body = f"event: message.part.updated\ndata: {json.dumps(payload)}\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"]["text"] == "line1\nline2"


def test_parse_sse_events_no_data() -> None:
    """Handle events with event line but no data line."""
    body = "event: server.heartbeat\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "server.heartbeat"
    assert events[0]["data"] == {}


def test_parse_sse_events_empty_body() -> None:
    """Empty response body returns empty list."""
    assert parse_sse_events("") == []


def test_parse_sse_events_malformed_json() -> None:
    """Malformed JSON data is captured as _raw."""
    body = "event: test\ndata: {bad json}\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == {"_raw": "{bad json}"}


async def test_drain_sse_stream_chunked() -> None:
    """Drain SSE stream from a mock async response with chunked delivery."""

    class MockResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    lines = [
        'event: server.connected',
        'data: {"id": "1"}',
        '',
        'event: session.status',
        'data: {"status": "busy"}',
        '',
    ]
    from tests.helpers.sse_utils import drain_sse_stream

    events = await drain_sse_stream(MockResponse(lines))
    assert len(events) == 2
    assert events[0]["event"] == "server.connected"
    assert events[1]["data"] == {"status": "busy"}


async def test_drain_sse_stream_empty() -> None:
    """Empty stream returns empty list."""

    class MockResponse:
        async def aiter_lines(self):
            return
            yield  # make it an async generator

    from tests.helpers.sse_utils import drain_sse_stream

    events = await drain_sse_stream(MockResponse())
    assert events == []
