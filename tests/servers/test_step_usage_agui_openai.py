"""Tests for StepUsageEvent handling in AG-UI and OpenAI API converters.

Verifies that StepUsageEvent is properly handled by:
1. AG-UI adapter (emitted as CustomEvent with usage data)
2. OpenAI API streaming helper (emitted as chunk with usage field)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_ai import RunUsage
import pytest

from agentpool.agents.events.events import StepUsageEvent


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = pytest.mark.integration


# =============================================================================
# AG-UI adapter test
# =============================================================================


class _FakeAgent:
    """Minimal agent stub that yields a fixed sequence of events.

    This is NOT a mock of the adapter -- the real BaseAgentAGUIAdapter
    is used. Only the agent is a lightweight stub.
    """

    def __init__(self, events: list[Any]) -> None:
        self._events = events

    async def run_stream(
        self,
        prompt: str,
        *,
        store_history: bool = False,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_agui_handles_step_usage_event() -> None:
    """Feed StepUsageEvent to AG-UI adapter, assert no exception and CustomEvent emitted."""
    from ag_ui.core import CustomEvent, RunAgentInput

    from agentpool_server.agui_server.base_agent_adapter import BaseAgentAGUIAdapter

    step_usage = RunUsage(input_tokens=100, output_tokens=50)
    cumulative = RunUsage(input_tokens=200, output_tokens=100)

    agent = _FakeAgent(
        events=[
            StepUsageEvent(
                step_index=0,
                step_usage=step_usage,
                cumulative_usage=cumulative,
            ),
        ]
    )

    run_input = RunAgentInput.model_validate({
        "thread_id": "t1",
        "run_id": "r1",
        "messages": [],
        "state": {},
        "tools": [],
        "context": [],
        "forwardedProps": {},
    })

    adapter = BaseAgentAGUIAdapter(agent=agent, run_input=run_input)
    events = [event async for event in adapter.run_stream()]

    # Should not raise; should produce at least one CustomEvent with name="step_usage"
    custom_events = [e for e in events if isinstance(e, CustomEvent)]
    step_usage_events = [e for e in custom_events if e.name == "step_usage"]
    assert len(step_usage_events) == 1, (
        f"Expected 1 step_usage CustomEvent, got {len(step_usage_events)}. "
        f"Custom events: {[(e.name, e.value) for e in custom_events]}"
    )

    value = step_usage_events[0].value
    assert value["step_index"] == 0
    assert value["step_usage"]["input_tokens"] == 100
    assert value["step_usage"]["output_tokens"] == 50
    assert value["step_usage"]["total_tokens"] == 150
    assert value["cumulative_usage"]["input_tokens"] == 200
    assert value["cumulative_usage"]["output_tokens"] == 100
    assert value["cumulative_usage"]["total_tokens"] == 300


# =============================================================================
# OpenAI API streaming helper test
# =============================================================================


_DONE = "[DONE]"


async def _collect_stream_chunks(
    events: AsyncIterator[Any],
    request: Any,
) -> list[dict[str, Any]]:
    """Collect all SSE chunks from stream_response as parsed dicts."""
    import anyenv

    from agentpool_server.openai_api_server.completions.helpers import stream_response

    chunks: list[dict[str, Any]] = []
    async for sse_data in stream_response(events, request):
        stripped = sse_data.strip()
        if not stripped.startswith("data: "):
            continue
        payload = stripped[6:]
        if payload == _DONE:
            continue
        chunks.append(anyenv.load_json(payload, return_type=dict))
    return chunks


@pytest.mark.asyncio
async def test_openai_api_handles_step_usage_event() -> None:
    """Feed StepUsageEvent to OpenAI API helper, assert usage in output."""
    from agentpool_server.openai_api_server.completions.models import ChatCompletionRequest

    step_usage = RunUsage(input_tokens=120, output_tokens=80)
    cumulative = RunUsage(input_tokens=240, output_tokens=160)

    async def event_stream() -> AsyncIterator[Any]:
        yield StepUsageEvent(
            step_index=0,
            step_usage=step_usage,
            cumulative_usage=cumulative,
        )

    request = ChatCompletionRequest(model="test-model", messages=[])
    chunks = await _collect_stream_chunks(event_stream(), request)

    # Find chunks with usage field
    usage_chunks = [c for c in chunks if "usage" in c]
    assert len(usage_chunks) == 1, (
        f"Expected 1 chunk with usage, got {len(usage_chunks)}. All chunks: {chunks}"
    )

    usage = usage_chunks[0]["usage"]
    assert usage["prompt_tokens"] == 120
    assert usage["completion_tokens"] == 80
    assert usage["total_tokens"] == 200

    # The choices should be empty for usage-only chunks
    assert usage_chunks[0]["choices"] == []
