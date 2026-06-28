"""Integration tests for PR #64 round-3 review comment fixes.

Covers fixes for:
1. RunErrorEvent must set turn_failed=True so loop breaks (not continue to idle)
2. steer()/followup() must be inside _request_lock to prevent TOCTOU
3. child_done_events.items() wrapped with list() for concurrent safety
4. child_done_events.values() wrapped with list() for concurrent safety
5. No duplicate StreamCompleteEvent publish in _run_once()
6. No duplicate StreamCompleteEvent publish in _run_stream_once()
7. ACP adapter NOTE upgraded to TODO comment
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import (
    RunErrorEvent,
    StreamCompleteEvent,
)
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fix #1: RunErrorEvent sets turn_failed=True (not just break)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_error_event_sets_turn_failed_and_breaks_loop() -> None:
    """When turn.execute() yields RunErrorEvent, turn_failed must be True.

    Without setting turn_failed, the loop breaks from the inner async-for
    but then continues to the idle branch instead of breaking the outer
    while-loop. This causes a deadlock for clients waiting on complete_event.
    """
    agent = Agent(
        name="test-runevent-break",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-runevent-session",
            agent_name="test-runevent-break",
        )
        run_ctx = AgentRunContext(
            session_id="test-runevent-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-runevent-run",
            session_id="test-runevent-session",
            agent_type="test-runevent-break",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        class ErrorTurn:
            async def execute(self) -> Any:
                yield RunErrorEvent(
                    message="simulated error",
                    run_id="test-runevent-run",
                    agent_name="test-runevent-break",
                )

        agent.create_turn = MagicMock(return_value=ErrorTurn())  # type: ignore[method-assign]

        events: list[Any] = []
        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    events.append(event)
                    if isinstance(event, RunErrorEvent):
                        break
        except TimeoutError:
            pytest.fail(
                "start() hung after RunErrorEvent — loop continued to idle "
                "instead of breaking because turn_failed was not set"
            )
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        error_events = [e for e in events if isinstance(e, RunErrorEvent)]
        assert len(error_events) == 1

        # complete_event must be set (loop exited, not stuck in idle)
        assert run_handle.complete_event.is_set(), (
            "complete_event not set — loop is stuck in idle after RunErrorEvent"
        )


# ---------------------------------------------------------------------------
# Fix #2: steer()/followup() inside _request_lock (TOCTOU)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_steer_followup_inside_request_lock() -> None:
    """steer()/followup() must be called inside _request_lock.

    Without this, current_run_id can be cleared between the check and
    the steer()/followup() call, causing silent message drops.
    """
    from agentpool.orchestrator.core import SessionController

    mock_pool = MagicMock()
    mock_pool.main_agent = MagicMock()
    mock_pool.main_agent.name = "main-agent"
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    controller._event_bus = EventBus()

    mock_agent = MagicMock()
    mock_agent.AGENT_TYPE = "native"

    session_id = "sess-toctou"
    controller._sessions[session_id] = MagicMock()
    controller._sessions[session_id].session_id = session_id
    controller._sessions[session_id].current_run_id = "fake-run-id"
    controller._sessions[session_id].closing = False
    controller._sessions[session_id].is_closing = False
    controller._sessions[session_id]._request_lock = asyncio.Lock()
    controller._sessions[session_id].turn_lock = asyncio.Lock()
    controller._sessions[session_id].input_provider = None
    controller._sessions[session_id].is_per_session_agent = False
    controller._session_agents[session_id] = mock_agent

    # Track if steer is called while lock is held
    lock_was_held_during_steer = False
    fake_run = MagicMock()
    lock = controller._sessions[session_id]._request_lock

    def _check_lock_and_steer(content: str) -> None:
        nonlocal lock_was_held_during_steer
        lock_was_held_during_steer = lock.locked()

    fake_run.steer = _check_lock_and_steer
    fake_run.followup = MagicMock()
    controller._runs["fake-run-id"] = fake_run

    await controller.receive_request(session_id, "steer me", priority="asap")

    assert lock_was_held_during_steer, (
        "steer() was called outside _request_lock — TOCTOU race possible"
    )


# ---------------------------------------------------------------------------
# Fix #3+#4: child_done_events wrapped with list() for concurrent safety
# ---------------------------------------------------------------------------


def test_child_done_events_items_wrapped_with_list() -> None:
    """run.py source must wrap child_done_events.items() with list().

    Iterating directly over a dict that may be modified concurrently
    raises RuntimeError: dictionary changed size during iteration.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    # Check that items() is wrapped with list()
    assert "list(self.run_ctx.child_done_events.items())" in source, (
        "child_done_events.items() must be wrapped with list() for "
        "concurrent safety"
    )


def test_child_done_events_values_wrapped_with_list() -> None:
    """run.py source must wrap child_done_events.values() with list().

    Iterating directly over a dict that may be modified concurrently
    raises RuntimeError: dictionary changed size during iteration.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    assert "list(self.run_ctx.child_done_events.values())" in source, (
        "child_done_events.values() must be wrapped with list() for "
        "concurrent safety"
    )


# ---------------------------------------------------------------------------
# Fix #5+#6: No duplicate StreamCompleteEvent in _run_once/_run_stream_once
# ---------------------------------------------------------------------------


def test_no_duplicate_stream_complete_in_run_once() -> None:
    """_execute_node must not publish StreamCompleteEvent after turn.execute().

    NativeTurn.execute() already yields StreamCompleteEvent as its terminal
    event. Publishing it again results in duplicate events on the EventBus.
    """
    import agentpool.agents.native_agent.agent as agent_module

    source = inspect.getsource(agent_module.Agent._execute_node)
    # After the fix, there should be no explicit StreamCompleteEvent publish.
    # Check for the pattern of publishing StreamCompleteEvent (not just the word
    # in docstrings or comments).
    import re

    publish_matches = re.findall(
        r"await\s+event_bus\.publish\s*\([^)]*StreamCompleteEvent", source
    )
    assert len(publish_matches) == 0, (
        f"_execute_node still publishes StreamCompleteEvent {len(publish_matches)} "
        "time(s) — duplicate publish should be removed since turn.execute() "
        "already yields it"
    )


def test_no_duplicate_stream_complete_in_run_stream_once() -> None:
    """_stream_events must not publish StreamCompleteEvent after turn.execute().

    NativeTurn.execute() already yields StreamCompleteEvent as its terminal
    event. Publishing it again results in duplicate events on the EventBus.
    """
    import agentpool.agents.native_agent.agent as agent_module

    source = inspect.getsource(agent_module.Agent._stream_events)
    import re

    publish_matches = re.findall(
        r"await\s+event_bus\.publish\s*\([^)]*StreamCompleteEvent", source
    )
    assert len(publish_matches) == 0, (
        f"_stream_events still publishes StreamCompleteEvent {len(publish_matches)} "
        "time(s) — duplicate publish should be removed since turn.execute() "
        "already yields it"
    )


# ---------------------------------------------------------------------------
# Fix #7: ACP adapter NOTE upgraded to TODO
# ---------------------------------------------------------------------------


def test_acp_adapter_has_todo_comment() -> None:
    """ACP agent adapter gap must be documented with TODO, not just NOTE.

    The TODO comment must describe the required infrastructure
    (async futures / notification registry) to prevent runtime crashes.
    """
    import agentpool.agents.acp_agent.acp_agent as acp_module

    source = inspect.getsource(acp_module.ACPAgent.create_turn)
    assert "TODO" in source, (
        "ACP adapter gap must be documented with TODO comment, not just NOTE"
    )
    assert "AttributeError" in source or "adapter" in source.lower(), (
        "TODO comment must describe the gap and required infrastructure"
    )
