"""Regression tests for long-running sync message handling."""

from __future__ import annotations

import asyncio

from pydantic_ai import RequestUsage
import pytest

from agentpool_server.opencode_server.models import MessageRequest, TextPartInput
from agentpool_server.opencode_server.routes import message_routes


class _DelayedAdapter:
    """Test adapter that blocks until the test releases it."""

    gate: asyncio.Event
    started: asyncio.Event

    def __init__(self, **_: object) -> None:
        self.response_text = "Delayed reply"
        self.usage = RequestUsage(input_tokens=0, output_tokens=0)
        self.cost_info = None

    async def process_stream(self, stream):
        self.started.set()
        await self.gate.wait()
        if False:
            yield stream

    def finalize(self):
        return iter(())


@pytest.mark.asyncio
async def test_sync_message_does_not_use_route_timeout(
    async_client,
    server_state,
    event_capture,
    monkeypatch,
) -> None:
    """Long-silent sync turns should stay alive until the server-side work finishes."""
    response = await async_client.post("/session", json={"title": "Delayed Reply"})
    session_id = response.json()["id"]

    gate = asyncio.Event()
    started = asyncio.Event()
    _DelayedAdapter.gate = gate
    _DelayedAdapter.started = started

    async def silent_stream():
        await gate.wait()
        if False:
            yield None

    def fail_if_timeout_used(*args: object, **kwargs: object):
        msg = "sync /message must not wrap agent streams in a route-owned timeout"
        raise AssertionError(msg)

    monkeypatch.setattr(message_routes.asyncio, "timeout", fail_if_timeout_used)
    monkeypatch.setattr(message_routes, "OpenCodeStreamAdapter", _DelayedAdapter)
    server_state.agent.run_stream = lambda *args, **kwargs: silent_stream()

    request = MessageRequest(parts=[TextPartInput(text="hello")], agent="default")
    request_task = asyncio.create_task(
        async_client.post(f"/session/{session_id}/message", json=request.model_dump(mode="json"))
    )

    await asyncio.wait_for(started.wait(), timeout=1.0)
    await asyncio.sleep(0.05)

    assert not request_task.done()
    assert server_state.session_status[session_id].type == "busy"

    gate.set()
    result = await asyncio.wait_for(request_task, timeout=1.0)

    assert result.status_code == 200
    assert server_state.session_status[session_id].type == "idle"
    assert event_capture.get_events_by_type("session.error") == []
