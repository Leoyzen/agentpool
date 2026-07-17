"""Unit tests for ACPClientAdapter.

Tests the adapter that bridges the blocking ``ACPAgentAPI.prompt()`` to the
non-blocking ``ACPClientProtocol`` interface expected by ``ACPTurn``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from acp.schema import (
    AgentMessageChunk,
    PromptResponse,
    TextContentBlock,
    TurnCompleteUpdate,
)
from agentpool.agents.acp_agent.adapter import ACPClientAdapter


# ---------------------------------------------------------------------------
# Mock ACPAgentAPI
# ---------------------------------------------------------------------------


class MockACPAgentAPI:
    """Mock ACPAgentAPI for testing ACPClientAdapter."""

    def __init__(
        self,
        *,
        response: PromptResponse | None = None,
        error: Exception | None = None,
        delay: float = 0.0,
    ) -> None:
        self._response = response or PromptResponse(stop_reason="end_turn")
        self._error = error
        self._delay = delay
        self.prompt_calls: list[tuple[str, list[Any]]] = []

    async def prompt(self, session_id: str, content: list[Any]) -> PromptResponse:
        self.prompt_calls.append((session_id, content))
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return self._response


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _text_update(text: str) -> AgentMessageChunk:
    return AgentMessageChunk(content=TextContentBlock(text=text))


def _make_queue(updates: list[Any] | None = None) -> asyncio.Queue[Any]:
    """Create a queue pre-loaded with updates."""
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    for u in updates or []:
        queue.put_nowait(u)
    return queue


# ---------------------------------------------------------------------------
# Happy path tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_prompt_launches_background_task_and_returns_none() -> None:
    """Given a MockACPAgentAPI, prompt() returns None immediately and launches a task."""
    api = MockACPAgentAPI()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    result = await adapter.prompt("session-1", [TextContentBlock(text="hello")])

    assert result is None
    assert adapter._prompt_task is not None
    assert not adapter._prompt_task.done()

    # Wait for the background task to complete
    await adapter._prompt_task
    assert len(api.prompt_calls) == 1
    assert api.prompt_calls[0][0] == "session-1"


@pytest.mark.unit
async def test_stream_events_yields_items_in_order() -> None:
    """Given a queue with updates, stream_events() yields them in order."""
    updates = [_text_update("first"), _text_update("second"), _text_update("third")]
    api = MockACPAgentAPI()
    queue = _make_queue(updates)
    adapter = ACPClientAdapter(api, queue)

    await adapter.prompt("session-1", [])
    # Allow background task to complete
    await asyncio.sleep(0.01)

    yielded = [item async for item in adapter.stream_events()]

    assert len(yielded) == 3
    assert yielded[0] is updates[0]
    assert yielded[1] is updates[1]
    assert yielded[2] is updates[2]


@pytest.mark.unit
async def test_stop_reason_returns_correct_value_after_completion() -> None:
    """Given completed streaming, stop_reason returns the PromptResponse's stop_reason."""
    api = MockACPAgentAPI(response=PromptResponse(stop_reason="max_tokens"))
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    await adapter.prompt("session-1", [])
    await asyncio.sleep(0.01)
    async for _item in adapter.stream_events():
        pass

    assert adapter.stop_reason == "max_tokens"


@pytest.mark.unit
async def test_get_messages_returns_collected_updates() -> None:
    """Given completed streaming, get_messages() returns all yielded updates."""
    updates = [_text_update("a"), _text_update("b"), TurnCompleteUpdate()]
    api = MockACPAgentAPI()
    queue = _make_queue(updates)
    adapter = ACPClientAdapter(api, queue)

    await adapter.prompt("session-1", [])
    await asyncio.sleep(0.01)
    async for _item in adapter.stream_events():
        pass

    messages = await adapter.get_messages("session-1")
    assert len(messages) == 3
    assert messages[0] is updates[0]
    assert messages[1] is updates[1]
    assert messages[2] is updates[2]


# ---------------------------------------------------------------------------
# Failure path tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_concurrent_prompt_raises_runtime_error() -> None:
    """Given a prompt already in progress, second prompt() raises RuntimeError."""
    api = MockACPAgentAPI(delay=0.1)
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    await adapter.prompt("session-1", [])

    with pytest.raises(RuntimeError, match="Prompt already in progress"):
        await adapter.prompt("session-1", [])

    # Cleanup
    await asyncio.sleep(0.2)


@pytest.mark.unit
async def test_stop_reason_before_completion_raises_runtime_error() -> None:
    """Given streaming not complete, stop_reason raises RuntimeError."""
    api = MockACPAgentAPI(delay=0.1)
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    # Before prompt
    with pytest.raises(RuntimeError, match="stop_reason not available"):
        _ = adapter.stop_reason

    await adapter.prompt("session-1", [])

    # After prompt but before completion
    with pytest.raises(RuntimeError, match="stop_reason not available"):
        _ = adapter.stop_reason

    await asyncio.sleep(0.2)


@pytest.mark.unit
async def test_background_task_error_propagates_through_stream_events() -> None:
    """Given api.prompt() raises, stream_events() propagates the error."""
    error = RuntimeError("API connection lost")
    api = MockACPAgentAPI(error=error)
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    await adapter.prompt("session-1", [])

    # Background task will fail; stream_events should propagate the error
    with pytest.raises(RuntimeError, match="API connection lost"):
        async for _item in adapter.stream_events():
            pass


@pytest.mark.unit
async def test_stream_events_without_prompt_raises_runtime_error() -> None:
    """Given no prompt() called, stream_events() raises RuntimeError."""
    api = MockACPAgentAPI()
    queue: asyncio.Queue[Any] = asyncio.Queue(maxsize=1000)
    adapter = ACPClientAdapter(api, queue)

    with pytest.raises(RuntimeError, match="No prompt in progress"):
        async for _item in adapter.stream_events():
            pass
