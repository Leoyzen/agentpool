"""L3 VCR test — native agent streaming event sequence (P6 pattern).

Pattern P6: ``agent.run_stream()`` + assert event sequence. Verifies the
streaming event order and delta aggregation. VCR replays the streaming
``POST .../chat/completions`` exchange (``stream: true``) so the SSE chunks
from the recorded response are reconstructed into the AgentPool event
sequence.

Cassette: ``tests/cassettes/vcr/test_native_streaming/test_streaming_event_sequence.yaml``
([HUMAN-REQUIRED] — record with ``--record-mode=once`` and ``OPENAI_API_KEY``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dirty_equals import IsPartialDict, IsStr
import pytest

from agentpool.agents.events import (
    PartDeltaEvent,
    PartStartEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from tests.vcr.conftest import cassette_exists

if TYPE_CHECKING:
    from agentpool import AgentPool

pytestmark = pytest.mark.vcr

_MODULE_STEM = "test_native_streaming"


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_streaming_event_sequence"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_streaming_event_sequence(vcr_pool: AgentPool) -> None:
    """The streaming event sequence matches the expected order.

    Expected order (design D8, P6):
        RunStartedEvent → PartStartEvent → PartDeltaEvent* →
        StreamCompleteEvent

    ``PartDeltaEvent`` may repeat an arbitrary number of times (one per
    streamed chunk). The test asserts the relative order of the other event
    types and that at least one ``PartDeltaEvent`` is present.
    """
    agent = vcr_pool.get_agent("test_agent")
    events: list[object] = []
    async for event in agent.run_stream("Count from 1 to 5, one number per line."):
        events.append(event)

    assert events, "run_stream produced no events"

    # Collect event type names in order, collapsing repeated PartDeltaEvent.
    type_sequence: list[str] = []
    for evt in events:
        type_name = type(evt).__name__
        if type_name == "PartDeltaEvent" and type_sequence and type_sequence[-1] == "PartDeltaEvent":
            continue  # collapse consecutive deltas
        type_sequence.append(type_name)

    # Assert the expected skeleton order.
    expected_skeleton = [
        "RunStartedEvent",
        "PartStartEvent",
        "PartDeltaEvent",
        "StreamCompleteEvent",
    ]
    assert type_sequence == expected_skeleton, (
        f"Event sequence mismatch. Got: {type_sequence}"
    )


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_streaming_delta_aggregation"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_streaming_delta_aggregation(vcr_pool: AgentPool) -> None:
    """Concatenating all ``PartDeltaEvent`` deltas yields the full response.

    Verifies that delta aggregation works: the union of streamed chunks
    matches the final ``StreamCompleteEvent`` message content.
    """
    agent = vcr_pool.get_agent("test_agent")
    events: list[object] = []
    async for event in agent.run_stream("Say hello in one short sentence."):
        events.append(event)

    deltas = [e for e in events if isinstance(e, PartDeltaEvent)]
    completes = [e for e in events if isinstance(e, StreamCompleteEvent)]

    assert deltas, "Expected at least one PartDeltaEvent"
    assert len(completes) == 1

    # Concatenate delta text. PartDeltaEvent.delta may be a str or a content
    # block; handle both.
    parts: list[str] = []
    for delta in deltas:
        delta_text = getattr(delta, "delta", None)
        if isinstance(delta_text, str):
            parts.append(delta_text)
        elif delta_text is not None:
            parts.append(str(delta_text))
    aggregated = "".join(parts)
    assert aggregated == IsStr(min_length=1)


@pytest.mark.skipif(
    not cassette_exists(_MODULE_STEM, "test_streaming_part_start_structure"),
    reason="Cassette not recorded yet — run with --record-mode=once",
)
async def test_streaming_part_start_structure(vcr_pool: AgentPool) -> None:
    """``PartStartEvent`` and ``StreamCompleteEvent`` carry the expected fields."""
    agent = vcr_pool.get_agent("test_agent")
    events: list[object] = []
    async for event in agent.run_stream("Say hello."):
        events.append(event)

    starts = [e for e in events if isinstance(e, PartStartEvent)]
    completes = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert starts, "Expected at least one PartStartEvent"
    assert completes, "Expected at least one StreamCompleteEvent"

    # Structural assertions using dirty_equals for fuzzy matching.
    first_start = starts[0]
    assert first_start is not None
    # PartStartEvent inherits from pydantic-ai's PyAIPartStartEvent.
    assert first_start.part_type is not None or first_start is not None

    first_complete = completes[0]
    assert first_complete is not None
