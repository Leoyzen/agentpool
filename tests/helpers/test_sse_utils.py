"""Unit tests for SSE event parsing utilities."""

from __future__ import annotations

import json

import pytest

from tests.helpers.sse_utils import drain_sse_stream, parse_sse_events


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


# ---------------------------------------------------------------------------
# Basic parsing
# ---------------------------------------------------------------------------


def test_parse_sse_events_basic() -> None:
    """Parse a simple SSE response with one event."""
    body = 'event: session.status\ndata: {"status": "busy"}\n\n'
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


def test_parse_sse_events_empty_body() -> None:
    """Empty response body returns empty list."""
    assert parse_sse_events("") == []


def test_parse_sse_events_no_data() -> None:
    """Handle events with event line but no data line."""
    body = "event: server.heartbeat\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "server.heartbeat"
    assert events[0]["data"] == {}


def test_parse_sse_events_malformed_json() -> None:
    """Malformed JSON data is captured as _raw."""
    body = "event: test\ndata: {bad json}\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == {"_raw": "{bad json}"}


# ---------------------------------------------------------------------------
# CRLF line endings (C1)
# ---------------------------------------------------------------------------


def test_parse_sse_events_crlf_endings() -> None:
    """CRLF line endings are handled identically to LF."""
    body = 'event: session.status\r\ndata: {"status": "busy"}\r\n\r\n'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "session.status"
    assert events[0]["data"] == {"status": "busy"}


def test_parse_sse_events_crlf_multiple_events() -> None:
    """Multiple events with CRLF endings preserve order."""
    body = 'event: a\r\ndata: {"x": 1}\r\n\r\nevent: b\r\ndata: {"y": 2}\r\n\r\n'
    events = parse_sse_events(body)
    assert len(events) == 2
    assert events[0]["event"] == "a"
    assert events[1]["event"] == "b"


# ---------------------------------------------------------------------------
# data: without event: line (C3) — defaults to "message" per SSE spec
# ---------------------------------------------------------------------------


def test_parse_sse_events_data_only_defaults_to_message() -> None:
    """Events with data but no event field default to 'message' per HTML5 SSE spec."""
    body = 'data: {"status": "busy"}\n\n'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "message"
    assert events[0]["data"] == {"status": "busy"}


def test_parse_sse_events_data_only_non_json() -> None:
    """Non-JSON data without event field still defaults to 'message'."""
    body = "data: not json\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "message"
    assert events[0]["data"] == {"_raw": "not json"}


# ---------------------------------------------------------------------------
# Multiple data: lines (C4)
# ---------------------------------------------------------------------------


def test_parse_sse_events_multiple_data_lines() -> None:
    r"""Multiple data: lines for one event are joined with \\n per SSE spec."""
    body = "event: message\ndata: line1\ndata: line2\ndata: line3\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == {"_raw": "line1\nline2\nline3"}


def test_parse_sse_events_multiple_data_lines_json() -> None:
    """Multiple data: lines that form valid JSON when joined."""
    body = 'event: message\ndata: {"text":\ndata: "multi"}\n\n'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == {"text": "multi"}


# ---------------------------------------------------------------------------
# Truncated stream — no trailing blank line (I1)
# ---------------------------------------------------------------------------


def test_parse_sse_events_no_trailing_blank_line() -> None:
    """Event without terminating blank line is still captured."""
    body = 'event: session.status\ndata: {"status": "busy"}'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "session.status"
    assert events[0]["data"] == {"status": "busy"}


# ---------------------------------------------------------------------------
# id: and retry: fields (I2)
# ---------------------------------------------------------------------------


def test_parse_sse_events_ignores_id_and_retry_fields() -> None:
    """id: and retry: fields are ignored without breaking event parsing."""
    body = 'id: 42\nevent: session.status\nretry: 5000\ndata: {"status": "busy"}\n\n'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "session.status"
    assert events[0]["data"] == {"status": "busy"}


# ---------------------------------------------------------------------------
# SSE comments (I3)
# ---------------------------------------------------------------------------


def test_parse_sse_events_comment_lines_ignored() -> None:
    """SSE comment lines (starting with :) are ignored, including keepalive pings."""
    body = (
        ": keepalive ping\n"
        "event: session.status\n"
        ": another comment\n"
        'data: {"status": "busy"}\n'
        "\n"
        ": post-event ping\n"
    )
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["event"] == "session.status"
    assert events[0]["data"] == {"status": "busy"}


# ---------------------------------------------------------------------------
# Non-dict JSON data (I4)
# ---------------------------------------------------------------------------


def test_parse_sse_events_data_is_list() -> None:
    """JSON data that is a list is preserved as-is."""
    body = "event: test\ndata: [1, 2, 3]\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == [1, 2, 3]


def test_parse_sse_events_data_is_string() -> None:
    """JSON data that is a string primitive is preserved."""
    body = 'event: done\ndata: "finished"\n\n'
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == "finished"


def test_parse_sse_events_data_is_integer() -> None:
    """JSON data that is an integer is preserved."""
    body = "event: count\ndata: 42\n\n"
    events = parse_sse_events(body)
    assert len(events) == 1
    assert events[0]["data"] == 42


# ---------------------------------------------------------------------------
# Multiple blank lines (I5)
# ---------------------------------------------------------------------------


def test_parse_sse_events_multiple_blank_lines() -> None:
    """Multiple blank lines between events do not create phantom events."""
    body = 'event: a\ndata: {"x": 1}\n\n\n\n\nevent: b\ndata: {"y": 2}\n\n'
    events = parse_sse_events(body)
    assert len(events) == 2
    assert events[0]["event"] == "a"
    assert events[1]["event"] == "b"


# ---------------------------------------------------------------------------
# drain_sse_stream async tests
# ---------------------------------------------------------------------------


async def test_drain_sse_stream_chunked() -> None:
    """Drain SSE stream from a mock async response with chunked delivery."""

    class MockResponse:
        def __init__(self, lines: list[str]) -> None:
            self._lines = lines

        async def aiter_lines(self):
            for line in self._lines:
                yield line

    lines = [
        "event: server.connected",
        'data: {"id": "1"}',
        "",
        "event: session.status",
        'data: {"status": "busy"}',
        "",
    ]
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

    events = await drain_sse_stream(MockResponse())
    assert events == []


# ---------------------------------------------------------------------------
# aiter_bytes fallback path (C2)
# ---------------------------------------------------------------------------


async def test_drain_sse_stream_aiter_bytes_basic() -> None:
    """Fallback to aiter_bytes() when aiter_lines() is unavailable."""

    class BytesResponse:
        def __init__(self, data: bytes) -> None:
            self._data = data

        async def aiter_bytes(self):
            yield self._data

    body = b'event: server.connected\ndata: {"id": "1"}\n\n'
    events = await drain_sse_stream(BytesResponse(body))
    assert len(events) == 1
    assert events[0]["event"] == "server.connected"


async def test_drain_sse_stream_aiter_bytes_utf8_split() -> None:
    """Multi-byte UTF-8 character split across chunk boundaries."""
    payload = {"text": "héllo"}
    full_body = f"event: message\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
    split_point = full_body.index(b"\xc3")  # inside the é character

    class ChunkedResponse:
        async def aiter_bytes(self):
            yield full_body[:split_point]
            yield full_body[split_point:]

    events = await drain_sse_stream(ChunkedResponse())
    assert len(events) == 1
    assert events[0]["data"]["text"] == "héllo"


async def test_drain_sse_stream_aiter_bytes_multi_chunk() -> None:
    """A single SSE line split across many small chunks."""
    full = b'event: session.status\ndata: {"status": "busy"}\n\n'

    class TinyChunks:
        async def aiter_bytes(self):
            for i in range(0, len(full), 3):
                yield full[i : i + 3]

    events = await drain_sse_stream(TinyChunks())
    assert len(events) == 1
    assert events[0]["data"] == {"status": "busy"}


async def test_drain_sse_stream_aiter_bytes_no_trailing_newline() -> None:
    """Remaining buffer after last newline is flushed."""

    class NoTrailingNewline:
        async def aiter_bytes(self):
            yield b'event: test\ndata: {"a": 1}\n\nevent: test2\ndata: {"b": 2}'

    events = await drain_sse_stream(NoTrailingNewline())
    assert len(events) == 2
    assert events[1]["data"] == {"b": 2}


# ---------------------------------------------------------------------------
# Unsupported response (I6)
# ---------------------------------------------------------------------------


async def test_drain_sse_stream_unsupported_response_raises() -> None:
    """Response without aiter_lines/aiter_bytes raises TypeError."""
    with pytest.raises(TypeError, match="no aiter_lines or aiter_bytes"):
        await drain_sse_stream(object())
