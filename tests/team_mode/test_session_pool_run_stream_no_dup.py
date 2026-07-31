"""Regression test: ``session_pool.run_stream()`` must yield each event exactly once.

Symptom
-------
The opencode client showed duplicated model output (every text token twice),
visible immediately after a slash/skill command because the slash executor
consumes ``session_pool.run_stream()``.

Root cause (fixed here)
-----------------------
``SessionPool._run_stream_run_turn()`` previously drained events from BOTH
``run_handle.start()`` (which publishes every event to the EventBus via
``ProtocolChannel.publish``) AND a direct EventBus subscription, so each
``PartDeltaEvent`` was yielded twice.  The fix drives ``start()`` as a
background side-effect (discarding yields, exactly like
``SessionController._consume_run``) and consumes events from the EventBus
subscription only — a single delivery path.

This test asserts the concatenated text ``PartDelta``/``PartStart`` content
matches the model's stream exactly ONCE, which failed on the buggy code.
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import TextPart, TextPartDelta
from pydantic_ai.models.function import FunctionModel
import pytest

from agentpool.agents.events.events import PartDeltaEvent, PartStartEvent, StreamCompleteEvent


pytestmark = pytest.mark.integration


def _make_streaming_text_model(chunks: list[str]) -> FunctionModel:
    """Build a FunctionModel that streams text chunks in order.

    The stream yields each chunk as a ``TextPartDelta`` followed by an empty
    tail to terminate the part, mirroring pydantic-ai's streaming contract.
    """
    from pydantic_ai.messages import ModelResponse, TextPart

    async def fn(messages: list[Any], info: Any) -> ModelResponse:
        return ModelResponse(parts=[TextPart(content="".join(chunks))])

    async def stream_fn(messages: list[Any], info: Any) -> Any:
        for c in chunks:
            yield c
        yield ""

    return FunctionModel(function=fn, stream_function=stream_fn)


async def _collect_text(events: list[Any]) -> str:
    """Concatenate text from PartStartEvent initial content + PartDeltaEvents."""
    parts: list[str] = []
    for e in events:
        if isinstance(e, PartStartEvent) and isinstance(e.part, TextPart):
            parts.append(e.part.content or "")
        elif isinstance(e, PartDeltaEvent):
            delta = e.delta
            if isinstance(delta, TextPartDelta):
                parts.append(delta.content_delta or "")
    return "".join(parts)


async def test_run_stream_yields_each_text_delta_once(team_mode_pool: Any) -> None:
    """Given a real AgentPool + FunctionModel streaming known text chunks.

    When: ``session_pool.run_stream()`` consumes the full turn.
    Then: the concatenated text content equals the source model text exactly
        once — i.e. no duplicated deltas from the double EventBus/start()
        drain.
    """
    expected = ["段", "一 | ", "二 |", " 三done"]  # deterministic byte-distinct chunks
    full_text = "".join(expected)

    session_id = "test-run-stream-dedup"
    await team_mode_pool.session_pool.create_session(
        session_id,
        agent_name="coordinator",
        team_role="lead",
        team_member_name="coordinator",
    )
    agent = await team_mode_pool.session_pool.sessions.get_or_create_session_agent(
        session_id,
    )
    await agent.set_model(_make_streaming_text_model(expected))

    events: list[Any] = []
    complete_received = False
    async for event in team_mode_pool.session_pool.run_stream(
        session_id,
        "stream markers",
    ):
        events.append(event)
        if isinstance(event, StreamCompleteEvent):
            complete_received = True

    assert complete_received, "Expected StreamCompleteEvent"
    got = await _collect_text(events)
    assert got == full_text, (
        "run_stream() delivered duplicated text deltas. "
        f"expected exactly: {full_text!r}\n"
        f"got: {got!r}\n"
        f"deltas: {[e.delta if isinstance(e, PartDeltaEvent) else None for e in events]}"
    )
