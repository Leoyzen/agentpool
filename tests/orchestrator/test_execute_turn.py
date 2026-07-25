"""Tests for _execute_turn() RunErrorEvent handling.

Verifies that:
- ``_execute_turn()`` yields both ``RunErrorEvent`` and the trailing
  ``StreamCompleteEvent`` when the turn produces that sequence (does NOT
  break on ``RunErrorEvent``).
- ``_execute_turn()`` has a defensive guard: if more than 3 events follow
  ``RunErrorEvent`` without a ``StreamCompleteEvent``, it breaks and logs
  a warning.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    PartDeltaEvent,
    RunErrorEvent,
    StreamCompleteEvent,
)
from agentpool.lifecycle.comm_channel import DirectChannel
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.orchestrator.core import SessionState
from agentpool.orchestrator.run import RunHandle


pytestmark = pytest.mark.unit


class _SequenceTurn:
    """Turn that yields a pre-configured sequence of events."""

    def __init__(self, events: list[Any]) -> None:
        self._events = events
        self._final_message: Any = None
        self._message_history: list[Any] = []

    async def execute(self) -> Any:
        for event in self._events:
            yield event


def _make_handle(
    *,
    turn_events: list[Any],
    event_bus: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with a turn that yields the given events.

    Args:
        turn_events: Events the turn's ``execute()`` will yield.
        event_bus: EventBus or ``None``. Defaults to ``None`` (standalone).
    """
    agent = MagicMock()
    agent.name = "test-agent"
    agent.conversation = MessageHistory()
    agent.create_turn = MagicMock(return_value=_SequenceTurn(turn_events))

    session = SessionState(
        session_id="test-session",
        agent_name="test-agent",
    )
    session._comm_channel = DirectChannel(MemoryJournal())

    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="test",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=AgentRunContext(),
    )


async def _consume_start(gen: Any) -> list[Any]:
    """Consume run_handle.start() generator and return all events."""
    events: list[Any] = []
    try:
        async for event in gen:
            events = [*events, event]
    except (GeneratorExit, asyncio.CancelledError):
        pass
    return events


# ---------------------------------------------------------------------------
# Task 7.3: RunErrorEvent followed by StreamCompleteEvent
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_execute_turn_yields_both_run_error_and_stream_complete() -> None:
    """When turn.execute() yields [RunErrorEvent, StreamCompleteEvent],
    _execute_turn() must yield both events without breaking on RunErrorEvent.
    """  # noqa: D205
    error_event = RunErrorEvent(
        message="simulated error",
        run_id="test-run",
        agent_name="test-agent",
    )
    complete_event = StreamCompleteEvent(
        message=ChatMessage(content="done", role="assistant"),
        cancelled=True,
    )
    handle = _make_handle(turn_events=[error_event, complete_event])

    gen = handle.start("test prompt")
    events = await _consume_start(gen)

    # Both RunErrorEvent and StreamCompleteEvent must be yielded.
    error_events = [e for e in events if isinstance(e, RunErrorEvent)]
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(error_events) == 1, f"Expected 1 RunErrorEvent, got {len(error_events)}"
    assert len(complete_events) == 1, f"Expected 1 StreamCompleteEvent, got {len(complete_events)}"

    # RunErrorEvent must come before StreamCompleteEvent.
    error_idx = events.index(error_events[0])
    complete_idx = events.index(complete_events[0])
    assert error_idx < complete_idx, "RunErrorEvent must precede StreamCompleteEvent"

    # Turn failed flag must be set.
    assert handle._current_turn_failed is True

    # Generator must have terminated (complete_event set).
    assert handle.complete_event.is_set()


# ---------------------------------------------------------------------------
# Task 7.4: Defensive guard — RunErrorEvent without StreamCompleteEvent
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_execute_turn_breaks_after_3_events_without_stream_complete() -> None:
    """When turn.execute() yields RunErrorEvent followed by 4+ events without
    StreamCompleteEvent, _execute_turn() breaks after 3 events and logs a warning.
    """  # noqa: D205
    error_event = RunErrorEvent(
        message="simulated error",
        run_id="test-run",
        agent_name="test-agent",
    )
    # 4 PartDeltaEvents after RunErrorEvent — no StreamCompleteEvent.
    delta_events = [PartDeltaEvent(index=i, delta=f"delta-{i}") for i in range(4)]
    all_events = [error_event, *delta_events]
    handle = _make_handle(turn_events=all_events)

    gen = handle.start("test prompt")
    events = await _consume_start(gen)

    # RunErrorEvent must be yielded.
    error_events = [e for e in events if isinstance(e, RunErrorEvent)]
    assert len(error_events) == 1

    # No StreamCompleteEvent should be present.
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 0

    # The defensive guard should have broken after 3 events past RunErrorEvent.
    # We expect: RunStartedEvent (from _execute_turn), RunErrorEvent, then at
    # most 3 PartDeltaEvents before the guard breaks.
    delta_events_received = [e for e in events if isinstance(e, PartDeltaEvent)]
    assert len(delta_events_received) <= 3, (
        f"Expected at most 3 PartDeltaEvents after RunErrorEvent, got {len(delta_events_received)}"
    )

    # Turn failed flag must be set.
    assert handle._current_turn_failed is True

    # Generator must have terminated (complete_event set).
    assert handle.complete_event.is_set()


@pytest.mark.unit
async def test_execute_turn_continues_after_run_error_with_one_delta_then_complete() -> None:
    """When turn.execute() yields [RunErrorEvent, PartDeltaEvent, StreamCompleteEvent],
    _execute_turn() yields all 3 events (guard allows up to 3 events after error).
    """  # noqa: D205
    error_event = RunErrorEvent(
        message="simulated error",
        run_id="test-run",
        agent_name="test-agent",
    )
    delta_event = PartDeltaEvent(index=0, delta="partial output")
    complete_event = StreamCompleteEvent(
        message=ChatMessage(content="done", role="assistant"),
    )
    handle = _make_handle(
        turn_events=[error_event, delta_event, complete_event],
    )

    gen = handle.start("test prompt")
    events = await _consume_start(gen)

    # All 3 turn events should be yielded (plus RunStartedEvent from _execute_turn).
    error_events = [e for e in events if isinstance(e, RunErrorEvent)]
    delta_events = [e for e in events if isinstance(e, PartDeltaEvent)]
    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]

    assert len(error_events) == 1
    assert len(delta_events) == 1
    assert len(complete_events) == 1

    # Order must be preserved.
    error_idx = events.index(error_events[0])
    delta_idx = events.index(delta_events[0])
    complete_idx = events.index(complete_events[0])
    assert error_idx < delta_idx < complete_idx

    assert handle._current_turn_failed is True
    assert handle.complete_event.is_set()
