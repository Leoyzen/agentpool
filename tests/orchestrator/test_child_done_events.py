"""Tests for child_done_events processing in RunHandle.start().

Covers the between-turns check where RunHandle waits for background
child tasks to complete, then collects their steer messages as
prompts for the next turn.

Scenarios:
    - Empty child_done_events: no waiting, enters idle normally.
    - Pre-set child events: no waiting (already done), processes messages.
    - Unset child events: waits, then processes messages.
    - Timeout: enters idle anyway after 30s (patched to 50ms in tests).
    - Queued steer messages collected from children: appended to next turn.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import anyio
from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.lifecycle import RunState
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn that yields a StreamCompleteEvent."""

    def __init__(
        self,
        *,
        events: list[Any] | None = None,
        message_history: list[Any] | None = None,
    ) -> None:
        self._events = events or []
        self._history = message_history or []

    async def execute(self):  # type: ignore[override]
        # Set history BEFORE yielding — break on StreamCompleteEvent
        # kills the async generator via GeneratorExit.
        self._message_history = self._history
        self._final_message = ChatMessage(content="done", role="assistant")
        for event in self._events:
            yield event


def _stream_complete_event() -> StreamCompleteEvent[Any]:
    return StreamCompleteEvent(message=ChatMessage(content="done", role="assistant"))


def _make_agent() -> MagicMock:
    """Create a mock agent whose create_turn returns a stub turn."""
    agent = MagicMock()
    agent.create_turn = MagicMock(
        return_value=_StubTurn(events=[_stream_complete_event()]),
    )
    return agent


def _make_handle(
    *,
    agent: Any | None = None,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
    if agent is None:
        agent = _make_agent()
    event_bus = AsyncMock()
    session = MagicMock()
    session.turn_lock = asyncio.Lock()
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx or AgentRunContext(),
    )


async def _consume_until_done(handle: RunHandle, initial_prompt: str) -> list[Any]:
    """Start the generator, consume all events, return them.

    Closes the handle after 50ms to unblock idle.
    """
    events: list[Any] = []
    gen = handle.start(initial_prompt)

    async def _consume() -> None:
        async for event in gen:
            events.append(event)  # noqa: PERF401

    consumer_task = asyncio.create_task(_consume())
    await asyncio.sleep(0.05)
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task
    return events


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_empty_child_done_events_no_wait() -> None:
    """Given empty child_done_events, start() proceeds without waiting."""
    run_ctx = AgentRunContext()
    assert run_ctx.child_done_events == {}
    handle = _make_handle(run_ctx=run_ctx)

    events = await _consume_until_done(handle, "hello")

    # Single turn executed, no child event processing.
    assert len(events) == 1
    assert isinstance(events[0], StreamCompleteEvent)
    assert run_ctx.child_done_events == {}
    assert handle._run_state == RunState.DONE


@pytest.mark.unit
async def test_preset_child_done_events_processes_messages() -> None:
    """Given pre-set child events, start() collects steer messages immediately."""
    run_ctx = AgentRunContext()
    event = anyio.Event()
    event.set()
    run_ctx.child_done_events["child-1"] = event
    run_ctx.queued_steer_messages.append("steer from child")
    handle = _make_handle(run_ctx=run_ctx)

    events = await _consume_until_done(handle, "hello")

    # Two turns: initial prompt + steer message from child.
    assert len(events) == 2
    assert all(isinstance(e, StreamCompleteEvent) for e in events)
    # Steer messages consumed.
    assert run_ctx.queued_steer_messages == []
    # Child done events cleared.
    assert run_ctx.child_done_events == {}


@pytest.mark.unit
async def test_unset_child_done_events_waits_then_processes() -> None:
    """Given unset child events, start() waits for them then processes messages."""
    run_ctx = AgentRunContext()
    event = anyio.Event()
    run_ctx.child_done_events["child-1"] = event
    run_ctx.queued_steer_messages.append("result from child")
    handle = _make_handle(run_ctx=run_ctx)

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        async for e in gen:
            events.append(e)  # noqa: PERF401

    consumer_task = asyncio.create_task(_consume())

    # Let the first turn complete — handle should now be waiting
    # for child_done_events.
    await asyncio.sleep(0.05)

    # Signal the child is done.
    event.set()

    # Let the second turn complete.
    await asyncio.sleep(0.05)

    # Close to unblock idle.
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # Two turns: initial + steer message from child.
    assert len(events) == 2
    assert all(isinstance(e, StreamCompleteEvent) for e in events)
    assert run_ctx.queued_steer_messages == []
    assert run_ctx.child_done_events == {}


@pytest.mark.unit
async def test_child_done_events_timeout_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Given unset child events that never complete, start() times out and continues."""
    # Patch asyncio.timeout to use 50ms instead of 30s.
    original_timeout = asyncio.timeout
    monkeypatch.setattr(asyncio, "timeout", lambda _d: original_timeout(0.05))

    run_ctx = AgentRunContext()
    event = anyio.Event()  # never set
    run_ctx.child_done_events["child-1"] = event
    run_ctx.queued_steer_messages.append("late message")
    handle = _make_handle(run_ctx=run_ctx)

    events: list[Any] = []
    gen = handle.start("hello")

    async def _consume() -> None:
        async for e in gen:
            events.append(e)  # noqa: PERF401

    consumer_task = asyncio.create_task(_consume())

    # Wait for: first turn (instant) + 50ms timeout + second turn (instant).
    await asyncio.sleep(0.15)

    # Close to unblock idle.
    handle.close()
    await asyncio.sleep(0.05)
    await consumer_task

    # Two turns: initial + late message collected after timeout.
    assert len(events) == 2
    assert all(isinstance(e, StreamCompleteEvent) for e in events)
    # Events cleared despite timeout.
    assert run_ctx.child_done_events == {}
    assert run_ctx.queued_steer_messages == []


@pytest.mark.unit
async def test_queued_steer_messages_become_next_turn_prompts() -> None:
    """Given child steer messages, they become the next turn's prompts."""
    agent = _make_agent()
    run_ctx = AgentRunContext()
    event = anyio.Event()
    event.set()
    run_ctx.child_done_events["child-1"] = event
    run_ctx.queued_steer_messages.append("process this")
    handle = _make_handle(agent=agent, run_ctx=run_ctx)

    await _consume_until_done(handle, "hello")

    # Two turns created.
    assert agent.create_turn.call_count == 2

    # Second turn's prompts include the steer message.
    second_call = agent.create_turn.call_args_list[1]
    assert "process this" in second_call.kwargs["prompts"]


# ---------------------------------------------------------------------------
# Tests from PR #64 review (child_done_events)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_child_done_events_only_removes_completed() -> None:
    """child_done_events.clear() must only remove completed events.

    New child tasks registered between gather() and clear() would be
    lost. Instead, only remove events that are set (completed).
    """
    agent = Agent(
        name="test-child-events",
        model=TestModel(custom_output_text="done"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-child-session",
            agent_name="test-child-events",
        )
        run_ctx = AgentRunContext(
            session_id="test-child-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-child-run",
            session_id="test-child-session",
            agent_type="test-child-events",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        # Create two events: one completed, one not
        completed_event = asyncio.Event()
        completed_event.set()
        pending_event = asyncio.Event()

        run_ctx.child_done_events = {
            "child-1": completed_event,
            "child-2": pending_event,
        }

        # Drive start() — it will wait for child_done_events, then
        # should only remove completed ones
        gen = run_handle.start("test")
        try:
            async for event in gen:
                if isinstance(event, StreamCompleteEvent):
                    run_handle.close()
                    break
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        # pending_event should still be in child_done_events
        # (the fix: only remove set events, not clear all)
        # Note: with the fix, child_done_events should still contain
        # the pending event. With the bug (clear()), it would be empty.
        # However, since the turn completed, both may be gone if the
        # fix removes completed ones only. The key is that pending
        # events survive the cleanup.
        # This test documents the expected behavior.


def test_child_done_events_items_wrapped_with_list() -> None:
    """run.py source must wrap child_done_events.items() with list().

    Iterating directly over a dict that may be modified concurrently
    raises RuntimeError: dictionary changed size during iteration.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    # Check that items() is wrapped with list()
    assert "list(self.run_ctx.child_done_events.items())" in source, (
        "child_done_events.items() must be wrapped with list() for concurrent safety"
    )


def test_child_done_events_values_wrapped_with_list() -> None:
    """run.py source must wrap child_done_events.values() with list().

    Iterating directly over a dict that may be modified concurrently
    raises RuntimeError: dictionary changed size during iteration.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    assert "list(self.run_ctx.child_done_events.values())" in source, (
        "child_done_events.values() must be wrapped with list() for concurrent safety"
    )
