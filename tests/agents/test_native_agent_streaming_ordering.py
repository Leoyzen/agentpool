"""Test that native agent streaming emits events in correct order.

Verifies: RunStartedEvent -> (intermediate events) -> StreamCompleteEvent.
"""

from __future__ import annotations

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent


TEST_RESPONSE = "I am a test response"


@pytest.fixture
def ordering_agent() -> Agent[None]:
    """Agent with instant TestModel for event ordering testing."""
    model = TestModel(custom_output_text=TEST_RESPONSE)
    return Agent(name="ordering-test-agent", model=model)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_streaming_event_ordering(ordering_agent: Agent[None]) -> None:
    """First event must be RunStartedEvent, last must be StreamCompleteEvent."""
    events = []

    async for event in ordering_agent.run_stream("Hello"):
        events.append(event)

    assert len(events) >= 2, "Expected at least RunStartedEvent and StreamCompleteEvent"

    first_event = events[0]
    last_event = events[-1]

    assert isinstance(first_event, RunStartedEvent), (
        f"First event must be RunStartedEvent, got {type(first_event).__name__}"
    )
    assert isinstance(last_event, StreamCompleteEvent), (
        f"Last event must be StreamCompleteEvent, got {type(last_event).__name__}"
    )
    assert last_event.message is not None, "StreamCompleteEvent.message must not be None"
    assert last_event.message.content == TEST_RESPONSE

    # Ensure StreamCompleteEvent is strictly the final event
    stream_complete_count = sum(1 for e in events if isinstance(e, StreamCompleteEvent))
    assert stream_complete_count == 1, (
        f"Expected exactly one StreamCompleteEvent, got {stream_complete_count}"
    )
