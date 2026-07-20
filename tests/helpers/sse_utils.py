"""SSE (Server-Sent Events) parsing utilities for test assertions.

Provides reusable helpers for parsing SSE-formatted response bodies into
structured event lists, usable across VCR and E2E test layers.

Usage:
    from tests.helpers.sse_utils import parse_sse_events, drain_sse_stream
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def parse_sse_events(response_body: str) -> list[dict[str, object]]:
    r"""Parse SSE-formatted text into a list of event dicts.

    Each event dict has ``event`` (str) and ``data`` (dict) keys.
    Events are returned in the order they appear in the response body.

    Args:
        response_body: Raw SSE response text with ``event: <type>\\ndata: <json>\\n\\n`` format.

    Returns:
        List of ``{"event": str, "data": dict}`` dicts, preserving order.
    """
    results: list[dict[str, object]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    for line in response_body.split("\n"):
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "" and current_event is not None:
            data_str = "\n".join(current_data_lines)
            try:
                data: dict[str, object] = json.loads(data_str) if data_str else {}
            except json.JSONDecodeError:
                data = {"_raw": data_str}
            results.append({"event": current_event, "data": data})
            current_event = None
            current_data_lines = []

    return results


async def drain_sse_stream(response: object) -> list[dict[str, object]]:
    """Consume an SSE stream from an async HTTP response and return parsed events.

    Args:
        response: An httpx async response object with ``aiter_lines()`` or ``aiter_bytes()``.

    Returns:
        List of ``{"event": str, "data": dict}`` dicts.
    """
    results: list[dict[str, object]] = []
    current_event: str | None = None
    current_data_lines: list[str] = []

    async for line in _iter_response_lines(response):
        if line.startswith("event: "):
            current_event = line[len("event: ") :]
        elif line.startswith("data: "):
            current_data_lines.append(line[len("data: ") :])
        elif line == "" and current_event is not None:
            data_str = "\n".join(current_data_lines)
            try:
                data: dict[str, object] = json.loads(data_str) if data_str else {}
            except json.JSONDecodeError:
                data = {"_raw": data_str}
            results.append({"event": current_event, "data": data})
            current_event = None
            current_data_lines = []

    return results


async def _iter_response_lines(response: object) -> AsyncIterator[str]:
    """Yield lines from an async HTTP response, supporting aiter_lines and aiter_bytes."""
    if hasattr(response, "aiter_lines"):
        async for line in response.aiter_lines():
            yield line
    elif hasattr(response, "aiter_bytes"):
        buffer = ""
        async for chunk in response.aiter_bytes():
            buffer += chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                yield line
        if buffer:
            yield buffer
    else:
        msg = f"Response object {type(response)} has no aiter_lines or aiter_bytes"
        raise TypeError(msg)
