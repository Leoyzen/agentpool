"""Tests for StepUsageEvent handling in the OpenCode event processor.

Verifies that per-step token usage events produce StepFinishPart instances
with correct token counts, and that the final cumulative StepFinishPart
from finalize() is always emitted regardless of per-step emissions.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock

from pydantic_ai import RequestUsage
from pydantic_ai.usage import RunUsage
import pytest

from agentpool.agents.events import (
    PartDeltaEvent as AgentPoolPartDeltaEvent,
    PartStartEvent,
    StepUsageEvent,
    StreamCompleteEvent,
)
from agentpool_server.opencode_server.event_adapter import OpenCodeEventAdapter
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    PartDeltaEvent,
    PartUpdatedEvent,
)
from agentpool_server.opencode_server.models.parts import (
    StepFinishPart,
    TextPart,
)
from agentpool_server.opencode_server.stream_adapter import OpenCodeStreamAdapter


pytestmark = pytest.mark.integration


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def adapter_context() -> EventProcessorContext:
    """Create an event processor context for testing the adapter."""
    session_id = "test-session"
    assistant_msg_id = "msg-001"
    assistant_msg = MessageWithParts.assistant(
        message_id=assistant_msg_id,
        session_id=session_id,
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
        parent_id="msg-000",
    )
    state = Mock()
    state.messages = {}
    state.messages.setdefault(session_id, [])
    state.ensure_session = Mock()
    state.storage = Mock()
    state.storage.log_message = Mock()

    return EventProcessorContext(
        session_id=session_id,
        assistant_msg_id=assistant_msg_id,
        assistant_msg=assistant_msg,
        state=state,
        working_dir="/tmp",
    )


def _make_stream_adapter(adapter_context: EventProcessorContext) -> OpenCodeStreamAdapter:
    """Create a real OpenCodeStreamAdapter sharing the given context's state."""
    state_mock = Mock()
    state_mock.messages = {}
    state_mock.working_dir = "/tmp"
    return OpenCodeStreamAdapter(
        state=state_mock,
        session_id=adapter_context.session_id,
        assistant_msg_id=adapter_context.assistant_msg_id,
        assistant_msg=adapter_context.assistant_msg,
        working_dir="/tmp",
    )


async def _collect_events(async_gen: Any) -> list[Any]:
    """Collect all events from an async generator."""
    return [event async for event in async_gen]


# =============================================================================
# Task 5.5: StepUsageEvent produces StepFinishPart with correct tokens
# =============================================================================


@pytest.mark.asyncio
async def test_step_usage_produces_step_finish_part(
    adapter_context: EventProcessorContext,
) -> None:
    """StepUsageEvent should produce a StepFinishPart with correct token counts.

    Feeds a StepUsageEvent with RunUsage containing input, output, cache,
    and reasoning tokens, then asserts the emitted StepFinishPart carries
    those values (not hardcoded zeros).
    """
    adapter = OpenCodeEventAdapter(context=adapter_context)

    step_usage = RunUsage(
        input_tokens=50,
        output_tokens=30,
        cache_read_tokens=5,
        cache_write_tokens=3,
        details={"reasoning_tokens": 10},
    )
    cumulative_usage = RunUsage(
        input_tokens=50,
        output_tokens=30,
        cache_read_tokens=5,
        cache_write_tokens=3,
        details={"reasoning_tokens": 10},
    )
    event = StepUsageEvent(
        step_index=0,
        step_usage=step_usage,
        cumulative_usage=cumulative_usage,
    )

    events = await _collect_events(adapter.convert_event(event))

    part_updated = [e for e in events if isinstance(e, PartUpdatedEvent)]
    step_finish_parts = [e for e in part_updated if isinstance(e.properties.part, StepFinishPart)]
    assert len(step_finish_parts) == 1

    tokens = step_finish_parts[0].properties.part.tokens
    assert tokens.input == 50, f"Expected input=50, got {tokens.input}"
    assert tokens.output == 30, f"Expected output=30, got {tokens.output}"
    assert tokens.reasoning == 10, f"Expected reasoning=10, got {tokens.reasoning}"
    assert tokens.cache.read == 5, f"Expected cache.read=5, got {tokens.cache.read}"
    assert tokens.cache.write == 3, f"Expected cache.write=3, got {tokens.cache.write}"
    assert tokens.total == 50 + 30 + 10 + 5 + 3, f"Expected total=98, got {tokens.total}"

    # step_index must be set from the event
    assert step_finish_parts[0].properties.part.step_index == 0


# =============================================================================
# Task 5.6: Final StepFinishPart still emitted after per-step emissions
# =============================================================================


@pytest.mark.asyncio
async def test_final_step_finish_still_emitted(
    adapter_context: EventProcessorContext,
) -> None:
    """Both per-step and final StepFinishPart must be emitted.

    Feeds StepUsageEvent events then calls finalize(). Asserts BOTH
    per-step and final StepFinishPart are emitted.  Also verifies
    backward compat: no StepUsageEvent → finalize() emits single
    StepFinishPart.
    """
    # --- Case 1: Per-step events + finalize() ---
    adapter = _make_stream_adapter(adapter_context)

    step_usage_0 = RunUsage(
        input_tokens=50,
        output_tokens=30,
        cache_read_tokens=5,
        cache_write_tokens=3,
        details={"reasoning_tokens": 10},
    )
    step_event_0 = StepUsageEvent(
        step_index=0,
        step_usage=step_usage_0,
        cumulative_usage=step_usage_0,
    )
    per_step_events = list(await _collect_events(adapter.convert_event(step_event_0)))

    per_step_finish = [
        e
        for e in per_step_events
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(per_step_finish) == 1
    assert per_step_finish[0].properties.part.step_index == 0

    # finalize() must still emit a final StepFinishPart
    final_events = list(adapter.finalize())
    final_finish = [
        e
        for e in final_events
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(final_finish) == 1, (
        "finalize() must emit a final StepFinishPart even after per-step emissions"
    )
    assert final_finish[0].properties.part.step_index is None, (
        "Final StepFinishPart from finalize() should have step_index=None"
    )

    # --- Case 2: No StepUsageEvent → finalize() emits single StepFinishPart ---
    adapter_context_2 = EventProcessorContext(
        session_id="test-session-2",
        assistant_msg_id="msg-002",
        assistant_msg=MessageWithParts.assistant(
            message_id="msg-002",
            session_id="test-session-2",
            time=MessageTime(created=0),
            agent_name="test-agent",
            model_id="test-model",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
            parent_id="msg-000",
        ),
        state=Mock(),
        working_dir="/tmp",
    )
    adapter2 = _make_stream_adapter(adapter_context_2)

    final_events_2 = list(adapter2.finalize())
    final_finish_2 = [
        e
        for e in final_events_2
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(final_finish_2) == 1, (
        "finalize() should emit exactly one StepFinishPart when no per-step events"
    )
    # Tokens should be zeros (no usage data)
    assert final_finish_2[0].properties.part.tokens.input == 0
    assert final_finish_2[0].properties.part.tokens.output == 0


# =============================================================================
# Task 5.7: StepFinishPart positioning in event sequence
# =============================================================================


@pytest.mark.asyncio
async def test_step_finish_positioning(
    adapter_context: EventProcessorContext,
) -> None:
    """StepFinishPart instances should appear after content and before next step.

    Feeds: PartStartEvent → PartDeltaEvent → StepUsageEvent(0) →
    PartStartEvent → PartDeltaEvent → StepUsageEvent(1) → StreamCompleteEvent.
    Asserts StepFinishPart instances appear at the right positions and
    the final one appears LAST.
    """
    adapter = OpenCodeEventAdapter(context=adapter_context)

    all_events: list[Any] = []

    # Step 0: text start + delta + usage
    all_events.extend(
        await _collect_events(adapter.convert_event(PartStartEvent.text(index=0, content="Hello")))
    )
    all_events.extend(
        await _collect_events(
            adapter.convert_event(AgentPoolPartDeltaEvent.text(index=0, content=" world"))
        )
    )
    step_usage_0 = RunUsage(
        input_tokens=10,
        output_tokens=5,
        details={"reasoning_tokens": 2},
    )
    all_events.extend(
        await _collect_events(
            adapter.convert_event(
                StepUsageEvent(
                    step_index=0,
                    step_usage=step_usage_0,
                    cumulative_usage=step_usage_0,
                )
            )
        )
    )

    # Step 1: text start + delta + usage
    all_events.extend(
        await _collect_events(adapter.convert_event(PartStartEvent.text(index=1, content="Second")))
    )
    all_events.extend(
        await _collect_events(
            adapter.convert_event(AgentPoolPartDeltaEvent.text(index=1, content=" part"))
        )
    )
    step_usage_1 = RunUsage(
        input_tokens=20,
        output_tokens=10,
        details={"reasoning_tokens": 3},
    )
    all_events.extend(
        await _collect_events(
            adapter.convert_event(
                StepUsageEvent(
                    step_index=1,
                    step_usage=step_usage_1,
                    cumulative_usage=step_usage_1,
                )
            )
        )
    )

    # Stream complete
    msg = Mock()
    msg.content = "Done"
    msg.usage = RequestUsage(input_tokens=30, output_tokens=15)
    msg.cost_info = None
    msg.model_name = None
    msg.provider_name = None
    all_events.extend(
        await _collect_events(adapter.convert_event(StreamCompleteEvent(message=msg)))
    )

    # Extract StepFinishPart positions
    step_finish_indices = [
        i
        for i, e in enumerate(all_events)
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]

    # Should have 3 StepFinishParts: per-step(0), per-step(1), final
    assert len(step_finish_indices) == 3, (
        f"Expected 3 StepFinishParts, got {len(step_finish_indices)}: indices={step_finish_indices}"
    )

    # Per-step finishes should have step_index 0 and 1
    assert all_events[step_finish_indices[0]].properties.part.step_index == 0
    assert all_events[step_finish_indices[1]].properties.part.step_index == 1

    # Final finish (from StreamCompleteEvent) should have step_index=None
    assert all_events[step_finish_indices[2]].properties.part.step_index is None

    # Per-step finishes should appear AFTER content deltas and BEFORE next step's start
    delta_events = [i for i, e in enumerate(all_events) if isinstance(e, PartDeltaEvent)]
    assert len(delta_events) >= 2

    # Step 0 finish should be after the first delta event
    assert step_finish_indices[0] > delta_events[0], (
        "Step 0 finish should appear after first content delta"
    )

    # Final finish must be the LAST StepFinishPart in the sequence
    # (a SessionStatusEvent may follow, so we check relative to other
    # StepFinishParts, not absolute last position)
    assert step_finish_indices[-1] > step_finish_indices[1], (
        "Final StepFinishPart must appear after per-step StepFinishParts"
    )
    # Final finish must appear after all content deltas
    assert step_finish_indices[-1] > delta_events[-1], (
        "Final StepFinishPart must appear after all content deltas"
    )

    # Step 0 finish should be before step 1's PartStartEvent
    # (PartStartEvent for step 1 produces PartUpdatedEvent for TextPart)
    text_part_updated_indices = [
        i
        for i, e in enumerate(all_events)
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, TextPart)
    ]
    assert len(text_part_updated_indices) >= 2
    assert step_finish_indices[0] < text_part_updated_indices[1], (
        "Step 0 finish should appear before step 1's text start"
    )


# =============================================================================
# Task 5.8: Edge cases for _step_finish_emitted flag
# =============================================================================


@pytest.mark.asyncio
async def test_step_finish_emitted_flag_edge_cases(
    adapter_context: EventProcessorContext,
) -> None:
    """Edge cases for the _step_finish_emitted flag.

    (a) No events → finalize() emits one StepFinishPart with zeros.
    (b) Only StreamCompleteEvent → one from stream complete, finalize()
        doesn't duplicate.
    (c) Per-step + error → finalize() still emits final StepFinishPart.
    """
    # --- (a) No events → finalize() emits one with zeros ---
    ctx_a = EventProcessorContext(
        session_id="test-a",
        assistant_msg_id="msg-a",
        assistant_msg=MessageWithParts.assistant(
            message_id="msg-a",
            session_id="test-a",
            time=MessageTime(created=0),
            agent_name="test-agent",
            model_id="test-model",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
            parent_id="msg-000",
        ),
        state=Mock(),
        working_dir="/tmp",
    )
    adapter_a = _make_stream_adapter(ctx_a)
    final_a = list(adapter_a.finalize())
    finish_a = [
        e
        for e in final_a
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(finish_a) == 1, "(a) No events → finalize() should emit one StepFinishPart"
    assert finish_a[0].properties.part.tokens.input == 0
    assert finish_a[0].properties.part.tokens.output == 0

    # --- (b) Only StreamCompleteEvent → one from stream complete, no duplicate ---
    ctx_b = EventProcessorContext(
        session_id="test-b",
        assistant_msg_id="msg-b",
        assistant_msg=MessageWithParts.assistant(
            message_id="msg-b",
            session_id="test-b",
            time=MessageTime(created=0),
            agent_name="test-agent",
            model_id="test-model",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
            parent_id="msg-000",
        ),
        state=Mock(),
        working_dir="/tmp",
    )
    adapter_b = _make_stream_adapter(ctx_b)

    msg = Mock()
    msg.content = "Done"
    msg.usage = RequestUsage(input_tokens=100, output_tokens=50)
    msg.cost_info = None
    msg.model_name = None
    msg.provider_name = None
    stream_events = await _collect_events(adapter_b.convert_event(StreamCompleteEvent(message=msg)))
    stream_finish = [
        e
        for e in stream_events
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(stream_finish) == 1, "(b) StreamCompleteEvent should emit one StepFinishPart"

    final_b = list(adapter_b.finalize())
    final_finish_b = [
        e
        for e in final_b
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(final_finish_b) == 0, (
        "(b) finalize() should NOT duplicate StepFinishPart after StreamCompleteEvent"
    )

    # --- (c) Per-step + error → finalize() still emits ---
    ctx_c = EventProcessorContext(
        session_id="test-c",
        assistant_msg_id="msg-c",
        assistant_msg=MessageWithParts.assistant(
            message_id="msg-c",
            session_id="test-c",
            time=MessageTime(created=0),
            agent_name="test-agent",
            model_id="test-model",
            provider_id="agentpool",
            path=MessagePath(cwd="/tmp", root="/tmp"),
            parent_id="msg-000",
        ),
        state=Mock(),
        working_dir="/tmp",
    )
    adapter_c = _make_stream_adapter(ctx_c)

    step_usage = RunUsage(
        input_tokens=50,
        output_tokens=30,
        details={"reasoning_tokens": 10},
    )
    step_event = StepUsageEvent(
        step_index=0,
        step_usage=step_usage,
        cumulative_usage=step_usage,
    )
    per_step_events_c = await _collect_events(adapter_c.convert_event(step_event))
    per_step_finish_c = [
        e
        for e in per_step_events_c
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(per_step_finish_c) == 1, "(c) Per-step should emit one StepFinishPart"
    assert per_step_finish_c[0].properties.part.step_index == 0

    # Simulate an error — no StreamCompleteEvent, just go to finalize()
    final_c = list(adapter_c.finalize())
    final_finish_c = [
        e
        for e in final_c
        if isinstance(e, PartUpdatedEvent) and isinstance(e.properties.part, StepFinishPart)
    ]
    assert len(final_finish_c) == 1, (
        "(c) finalize() must still emit final StepFinishPart after per-step + error"
    )
    assert final_finish_c[0].properties.part.step_index is None, (
        "(c) Final StepFinishPart should have step_index=None"
    )
