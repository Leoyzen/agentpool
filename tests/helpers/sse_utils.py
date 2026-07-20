"""SSE (Server-Sent Events) parsing utilities for test assertions.

Provides reusable helpers for parsing SSE-formatted response bodies into
structured event lists, usable across VCR and E2E test layers.

Usage:
    from tests.helpers.sse_utils import parse_sse_events, drain_sse_stream
"""

from __future__ import annotations

import codecs
import json
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _flush_event(
    event: str | None,
    data_lines: list[str],
) -> dict[str, object] | None:
    """Build an event dict from accumulated fields, or None if nothing to flush.

    Per the HTML5 SSE spec, if no ``event:`` field is present, the event type
    defaults to ``"message"``.
    """
    if event is None and not data_lines:
        return None
    data_str = "\n".join(data_lines)
    try:
        data: object = json.loads(data_str) if data_str else {}
    except json.JSONDecodeError:
        data = {"_raw": data_str}
    return {"event": event or "message", "data": data}


def parse_sse_events(response_body: str) -> list[dict[str, object]]:
    r"""Parse SSE-formatted text into a list of event dicts.

    Each event dict has ``event`` (str) and ``data`` (object) keys.
    Events are returned in the order they appear in the response body.

    Handles:
    - CRLF (``\r\n``) and LF (``\n``) line endings
    - Events with ``data:`` but no ``event:`` field (defaults to ``"message"``)
    - Multiple ``data:`` lines per event (concatenated with ``\n``)
    - SSE comment lines (``: ...``) — silently ignored
    - ``id:`` and ``retry:`` fields — silently ignored
    - Truncated streams (no trailing blank line) — last event is still emitted

    Args:
        response_body: Raw SSE response text.

    Returns:
        List of ``{"event": str, "data": object}`` dicts, preserving order.
    """
    results: list[dict[str, object]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    for raw_line in response_body.split("\n"):
        line = raw_line.rstrip("\r")
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "":
            flushed = _flush_event(current_event, current_data_lines)
            if flushed is not None:
                results.append(flushed)
            current_event = None
            current_data_lines = []
        # All other lines (id:, retry:, comments, whitespace) are silently ignored.

    # Flush any pending event at end of input (truncated stream).
    flushed = _flush_event(current_event, current_data_lines)
    if flushed is not None:
        results.append(flushed)

    return results


async def drain_sse_stream(response: object) -> list[dict[str, object]]:
    """Consume an SSE stream from an async HTTP response and return parsed events.

    Args:
        response: An httpx async response object with ``aiter_lines()`` or ``aiter_bytes()``.

    Returns:
        List of ``{"event": str, "data": object}`` dicts.
    """
    results: list[dict[str, object]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    async for raw_line in _iter_response_lines(response):
        line = raw_line.rstrip("\r")
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "":
            flushed = _flush_event(current_event, current_data_lines)
            if flushed is not None:
                results.append(flushed)
            current_event = None
            current_data_lines = []

    # Flush any pending event at end of stream.
    flushed = _flush_event(current_event, current_data_lines)
    if flushed is not None:
        results.append(flushed)

    return results


async def _iter_response_lines(response: object) -> AsyncIterator[str]:
    """Yield lines from an async HTTP response, supporting aiter_lines and aiter_bytes."""
    if hasattr(response, "aiter_lines"):
        async for line in response.aiter_lines():
            yield line
    elif hasattr(response, "aiter_bytes"):
        decoder = codecs.getincrementaldecoder("utf-8")()
        buffer = ""
        async for chunk in response.aiter_bytes():
            buffer += decoder.decode(chunk)
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line
        buffer += decoder.decode(b"", final=True)
        if buffer:
            yield buffer
    else:
        msg = f"Response object {type(response)} has no aiter_lines or aiter_bytes"
        raise TypeError(msg)
