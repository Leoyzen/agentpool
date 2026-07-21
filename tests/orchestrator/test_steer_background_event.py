"""Unit tests for ``UserMessageInsertedEvent`` publication from ``steer_from_background_task()``.

Verifies that the SYNC method publishes the event via
``asyncio.create_task()`` fire-and-forget, handles ``RuntimeError``
for no-running-loop scenarios, and uses the correct ``delivery`` and
``source`` fields.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import pytest

from agentpool.agents.events import UserMessageInsertedEvent
from agentpool.orchestrator.core import SessionState


pytestmark = pytest.mark.unit


class _EventRecorder:
    """Records events published to a mock EventBus."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, session_id: str, event: Any) -> None:
        self.published.append((session_id, event))


def _make_session(
    session_id: str = "bg-sess-1",
    event_bus: Any = None,
) -> SessionState:
    """Create a SessionState with an optional EventBus reference."""
    session = SessionState(session_id=session_id, agent_name="test-agent")
    session._event_bus = event_bus
    return session


# ---------------------------------------------------------------------------
# Tests: SYNC method verification
# ---------------------------------------------------------------------------


def test_steer_from_background_task_is_synchronous() -> None:
    """Given steer_from_background_task, it is NOT a coroutine function."""
    assert not inspect.iscoroutinefunction(SessionState.steer_from_background_task)


# ---------------------------------------------------------------------------
# Tests: event publication with active event loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_steer_from_background_task_publishes_event() -> None:
    """Given an active event loop and EventBus, the event is published."""
    recorder = _EventRecorder()
    session = _make_session(event_bus=recorder)
    # Simulate an active run so the steer callback path is taken.
    session.set_current_run_id("active-run-bg")

    # Register a steer callback so the method takes the active-run path.
    callback_calls: list[str] = []
    session._active_steer_callback = lambda msg: callback_calls.append(msg) or "cb-mid"

    result = session.steer_from_background_task("background result")

    # Steer callback was called.
    assert callback_calls == ["background result"]
    assert result == "cb-mid"

    # Allow the fire-and-forget task to complete.
    await asyncio.sleep(0.01)

    assert len(recorder.published) == 1
    sid, event = recorder.published[0]
    assert sid == "bg-sess-1"
    assert isinstance(event, UserMessageInsertedEvent)
    assert event.delivery == "steer"
    assert event.source == "background_task"
    assert event.content == "background result"
    assert event.message_id  # Non-empty auto-generated.


@pytest.mark.anyio
async def test_steer_from_background_task_no_active_run_enqueues() -> None:
    """Given no active run, the message is enqueued to feedback_queue."""
    recorder = _EventRecorder()
    session = _make_session(event_bus=recorder)

    result = session.steer_from_background_task("queued result")

    # No callback was set, so message goes to feedback_queue.
    assert result is not None
    assert not session.feedback_queue.empty()

    # Allow fire-and-forget task to complete.
    await asyncio.sleep(0.01)

    assert len(recorder.published) == 1
    _, event = recorder.published[0]
    assert event.delivery == "steer"
    assert event.source == "background_task"


# ---------------------------------------------------------------------------
# Tests: EventBus=None guard
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_steer_from_background_task_no_event_bus_skips_emission() -> None:
    """Given EventBus=None, no event is published and steer proceeds."""
    session = _make_session(event_bus=None)

    result = session.steer_from_background_task("no bus")

    # Steer still proceeds (message enqueued).
    assert result is not None
    assert not session.feedback_queue.empty()

    # No event published — no crash.
    await asyncio.sleep(0.01)


# ---------------------------------------------------------------------------
# Tests: RuntimeError handling (no running event loop)
# ---------------------------------------------------------------------------


def test_steer_from_background_task_no_running_loop_does_not_crash() -> None:
    """Given no running event loop, emission is skipped and steer proceeds.

    This test runs outside of anyio/asyncio event loop to verify the
    RuntimeError path.
    """
    recorder = _EventRecorder()
    session = _make_session(event_bus=recorder)

    # No active run — message goes to feedback_queue.
    # Should not raise RuntimeError.
    result = session.steer_from_background_task("no loop")

    assert result is not None
    assert not session.feedback_queue.empty()
    # No event published (no running loop).
    assert len(recorder.published) == 0


# ---------------------------------------------------------------------------
# Tests: event bus publish failure does not break steer
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_steer_from_background_task_publish_failure_does_not_break_steer() -> None:
    """Given EventBus.publish() raises, steer still proceeds."""

    class _FailingBus:
        async def publish(self, session_id: str, event: Any) -> None:
            raise RuntimeError("publish failed")

    session = _make_session(event_bus=_FailingBus())

    result = session.steer_from_background_task("will fail")

    # Steer still proceeds.
    assert result is not None
    assert not session.feedback_queue.empty()

    # Allow fire-and-forget task to complete (exception caught internally).
    await asyncio.sleep(0.01)
