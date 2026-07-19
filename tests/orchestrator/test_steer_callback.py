"""Tests for steer_callback wiring in RunHandle.

Verifies that ``RunHandle.start()`` sets ``run_ctx.steer_callback`` to an
adapter that delegates to ``RunHandle.steer()``, enabling subagent
``complete_background_task()`` to inject messages into the active turn.

Also covers ``AgentRunContext.complete_background_task()`` system
notification emission (task 3.2).
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import anyio
import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import SystemNotificationEvent
from agentpool.orchestrator.run import RunHandle

from .test_run_handle import _stream_complete_event, _StubTurn


pytestmark = pytest.mark.unit


def _make_handle(
    *,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked deps and a stub turn."""
    turn = _StubTurn(
        events=[_stream_complete_event()],
        message_history=[],
    )
    agent = MagicMock()
    agent.create_turn = MagicMock(return_value=turn)
    event_bus = AsyncMock()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="test",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx or AgentRunContext(),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_callback_is_set_after_start() -> None:
    """Given a RunHandle with steer_callback=None, after start() begins
    run_ctx.steer_callback is set.
    """  # noqa: D205
    run_ctx = AgentRunContext()
    handle = _make_handle(run_ctx=run_ctx)

    assert run_ctx.steer_callback is None

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            assert run_ctx.steer_callback is not None
            break

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_steer_callback_delegates_to_handle_steer() -> None:
    """Given steer_callback is set, calling it with (session_id, message)
    delegates to RunHandle.steer(message) and returns the message_id.
    """  # noqa: D205
    run_ctx = AgentRunContext()
    handle = _make_handle(run_ctx=run_ctx)

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            assert run_ctx.steer_callback is not None
            result = await run_ctx.steer_callback("any-session", "steer me")
            assert result is not None
            break

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_steer_callback_queues_message_when_running() -> None:
    """Given steer_callback is called during a running turn, the message
    is queued on the handle.
    """  # noqa: D205
    run_ctx = AgentRunContext()
    handle = _make_handle(run_ctx=run_ctx)

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            assert run_ctx.steer_callback is not None
            await run_ctx.steer_callback("any-session", "steer msg")
            # Message should be queued in queued_steer_messages or
            # _message_queue depending on handle state.
            assert len(run_ctx.queued_steer_messages) > 0 or len(handle._message_queue) > 0
            break

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


@pytest.mark.unit
async def test_steer_callback_is_wrapper_method() -> None:
    """The steer_callback is set to RunHandle._steer_callback_wrapper."""
    run_ctx = AgentRunContext()
    handle = _make_handle(run_ctx=run_ctx)

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            assert run_ctx.steer_callback == handle._steer_callback_wrapper
            break

    consumer = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)

    handle.close()
    await asyncio.sleep(0.05)
    await consumer


# ---------------------------------------------------------------------------
# complete_background_task() — SystemNotificationEvent emission (task 3.2)
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_complete_background_task_emits_system_notification() -> None:
    """complete_background_task() emits a SystemNotificationEvent with
    source="background_task" and ref_session_id=child_session_id.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="parent-sess", event_bus=bus)
    steer_cb = AsyncMock(return_value="mid-1")
    run_ctx.steer_callback = steer_cb
    done_event = anyio.Event()
    run_ctx.child_done_events["child-sess-1"] = done_event

    await run_ctx.complete_background_task("child-sess-1", "task finished")

    # steer_callback was called first.
    steer_cb.assert_awaited_once_with("parent-sess", "task finished")

    # Then a SystemNotificationEvent was published.
    bus.publish.assert_awaited_once()
    publish_call = bus.publish.await_args
    assert publish_call.args[0] == "parent-sess"
    event = publish_call.args[1]
    assert isinstance(event, SystemNotificationEvent)
    assert event.source == "background_task"
    assert event.text == "task finished"
    assert event.ref_session_id == "child-sess-1"
    assert event.level == "info"

    # The child_done_event was set.
    assert done_event.is_set()
    assert "child-sess-1" not in run_ctx.child_done_events


@pytest.mark.unit
async def test_complete_background_task_emits_notification_even_if_steer_raises() -> None:
    """If steer_callback raises, complete_background_task still emits the
    SystemNotificationEvent (steer failure does not block notification).
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="parent-sess", event_bus=bus)
    steer_cb = AsyncMock(side_effect=RuntimeError("steer failed"))
    run_ctx.steer_callback = steer_cb
    done_event = anyio.Event()
    run_ctx.child_done_events["child-sess-2"] = done_event

    # Should not raise despite steer_callback failure.
    await run_ctx.complete_background_task("child-sess-2", "done msg")

    # Notification was still emitted.
    bus.publish.assert_awaited_once()
    event = bus.publish.await_args.args[1]
    assert isinstance(event, SystemNotificationEvent)
    assert event.source == "background_task"
    assert event.text == "done msg"

    # done_event was still set.
    assert done_event.is_set()


@pytest.mark.unit
async def test_complete_background_task_emits_notification_without_steer_callback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When steer_callback is None, a warning is logged but the
    SystemNotificationEvent is still emitted.
    """
    bus = AsyncMock()
    run_ctx = AgentRunContext(session_id="parent-sess", event_bus=bus)
    assert run_ctx.steer_callback is None
    done_event = anyio.Event()
    run_ctx.child_done_events["child-sess-3"] = done_event

    await run_ctx.complete_background_task("child-sess-3", "orphan done")

    # Notification was still emitted.
    bus.publish.assert_awaited_once()
    event = bus.publish.await_args.args[1]
    assert isinstance(event, SystemNotificationEvent)
    assert event.source == "background_task"

    # done_event was still set.
    assert done_event.is_set()
