"""Unit tests for ACPEventConverter double-terminal guard.

Verifies that when RunErrorEvent is followed by StreamCompleteEvent,
only one TurnCompleteUpdate is emitted (from RunErrorEvent), not two.
Also verifies the guard resets between turns.
"""

from __future__ import annotations

from typing import Any

import pytest

from acp.schema import TurnCompleteUpdate, UsageUpdate
from agentpool.agents.events.events import (
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.messaging.messages import ChatMessage
from agentpool_server.acp_server.event_converter import ACPEventConverter


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def _collect(converter: ACPEventConverter, event: Any) -> list[Any]:
    """Collect all notifications yielded by converter.convert(event)."""
    return [update async for update in converter.convert(event)]


def _turn_complete_updates(notifs: list[Any]) -> list[TurnCompleteUpdate]:
    """Filter notifications to only TurnCompleteUpdate instances."""
    return [n for n in notifs if isinstance(n, TurnCompleteUpdate)]


def _make_stream_complete_event(cancelled: bool = False) -> StreamCompleteEvent[str]:
    """Create a minimal StreamCompleteEvent for testing."""
    msg = ChatMessage(content="test", role="assistant")
    return StreamCompleteEvent(message=msg, cancelled=cancelled)


# ---------------------------------------------------------------------------
# Test 1: RunErrorEvent + StreamCompleteEvent → single TurnCompleteUpdate
# ---------------------------------------------------------------------------


async def test_run_error_then_stream_complete_emits_single_turn_complete() -> None:
    """RunErrorEvent followed by StreamCompleteEvent emits only one TurnCompleteUpdate.

    The guard flag prevents the StreamCompleteEvent from emitting a second
    TurnCompleteUpdate after RunErrorEvent already emitted one.
    """
    converter = ACPEventConverter(client_supports_turn_complete=True)

    # Step 1: Process RunErrorEvent — should emit TurnCompleteUpdate(stop_reason="refusal")
    error_event = RunErrorEvent(message="Something went wrong")
    error_notifs = await _collect(converter, error_event)
    error_turn_completes = _turn_complete_updates(error_notifs)
    assert len(error_turn_completes) == 1
    assert error_turn_completes[0].stop_reason == "refusal"

    # Guard flag should now be True
    assert converter._run_error_emitted is True

    # Step 2: Process StreamCompleteEvent — should NOT emit TurnCompleteUpdate
    stream_event = _make_stream_complete_event(cancelled=True)
    stream_notifs = await _collect(converter, stream_event)
    stream_turn_completes = _turn_complete_updates(stream_notifs)
    assert len(stream_turn_completes) == 0, (
        "StreamCompleteEvent should not emit TurnCompleteUpdate after RunErrorEvent"
    )

    # Guard flag should be reset after StreamCompleteEvent
    assert converter._run_error_emitted is False


# ---------------------------------------------------------------------------
# Test 2: Normal StreamCompleteEvent (no preceding RunErrorEvent) → emits TurnCompleteUpdate
# ---------------------------------------------------------------------------


async def test_normal_stream_complete_emits_turn_complete() -> None:
    """StreamCompleteEvent without preceding RunErrorEvent emits TurnCompleteUpdate."""
    converter = ACPEventConverter(client_supports_turn_complete=True)

    stream_event = _make_stream_complete_event(cancelled=False)
    notifs = await _collect(converter, stream_event)
    turn_completes = _turn_complete_updates(notifs)

    assert len(turn_completes) == 1
    assert turn_completes[0].stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Test 3: Guard resets on RunStartedEvent (turn boundary)
# ---------------------------------------------------------------------------


async def test_guard_resets_on_run_started_event() -> None:
    """RunStartedEvent resets the guard flag for a new turn.

    This ensures that if a RunErrorEvent was emitted in turn 1 but no
    StreamCompleteEvent followed, the guard does not persist into turn 2.
    """
    converter = ACPEventConverter(client_supports_turn_complete=True)

    # Set the guard as if RunErrorEvent was processed
    error_event = RunErrorEvent(message="Error in turn 1")
    await _collect(converter, error_event)
    assert converter._run_error_emitted is True

    # Process RunStartedEvent for turn 2 — should reset the guard
    run_started = RunStartedEvent(run_id="run-2")
    await _collect(converter, run_started)
    assert converter._run_error_emitted is False

    # Now a normal StreamCompleteEvent should emit TurnCompleteUpdate
    stream_event = _make_stream_complete_event(cancelled=False)
    notifs = await _collect(converter, stream_event)
    turn_completes = _turn_complete_updates(notifs)
    assert len(turn_completes) == 1
    assert turn_completes[0].stop_reason == "end_turn"


# ---------------------------------------------------------------------------
# Test 4: New converter instance has guard = False
# ---------------------------------------------------------------------------


async def test_new_converter_has_guard_false() -> None:
    """A freshly created ACPEventConverter has _run_error_emitted = False."""
    converter = ACPEventConverter(client_supports_turn_complete=True)
    assert converter._run_error_emitted is False


# ---------------------------------------------------------------------------
# Test 5: UsageUpdate is still emitted even when guard is active
# ---------------------------------------------------------------------------


async def test_usage_update_still_emitted_when_guard_active() -> None:
    """StreamCompleteEvent still yields UsageUpdate even when TurnCompleteUpdate is skipped."""
    converter = ACPEventConverter(client_supports_turn_complete=True)

    # Process RunErrorEvent first
    error_event = RunErrorEvent(message="Error")
    await _collect(converter, error_event)

    # Process StreamCompleteEvent — should still emit UsageUpdate
    stream_event = _make_stream_complete_event(cancelled=True)
    notifs = await _collect(converter, stream_event)
    usage_updates = [n for n in notifs if isinstance(n, UsageUpdate)]
    assert len(usage_updates) == 1, "UsageUpdate should still be emitted"
