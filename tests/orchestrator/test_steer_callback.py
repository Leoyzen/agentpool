"""Tests for steer_callback wiring in RunHandle.

Verifies that ``RunHandle.start()`` sets ``run_ctx.steer_callback`` to an
adapter that delegates to ``SessionState.steer_from_background_task()``,
enabling subagent ``complete_background_task()`` to inject messages into
the active turn or queue them for the next RunHandle.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.lifecycle.comm_channel import DirectChannel
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.orchestrator.core import SessionState
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
    agent.name = "test-agent"
    agent.conversation = MagicMock()
    event_bus = AsyncMock()
    # Use real SessionState so steer_from_background_task works properly
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
    delegates to steer and returns the message_id.
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
    is queued on run_ctx.queued_steer_messages.
    """  # noqa: D205
    run_ctx = AgentRunContext()
    handle = _make_handle(run_ctx=run_ctx)
    # Simulate active run so steer_from_background_task routes to the handle
    assert handle.session is not None
    handle.session.current_run_id = "test-run"

    gen = handle.start("hello")

    async def _consume() -> None:
        async for _ in gen:
            assert run_ctx.steer_callback is not None
            await run_ctx.steer_callback("any-session", "steer msg")
            # Message should be queued in queued_steer_messages (when
            # no active agent_run) or enqueued via agent_run.
            assert len(run_ctx.queued_steer_messages) > 0
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
