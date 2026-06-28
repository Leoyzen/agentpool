"""Integration tests for PR #64 round-4 review comment fixes.

Covers fixes for:
1. run_ctx.current_task must be set in start() so cancel() can interrupt turns
2. ACPTurn must use self._run_ctx.run_id (not generate new uuid4) and not
   yield redundant RunStartedEvent
3. NativeTurn must not yield redundant RunStartedEvent (RunHandle.start()
   already publishes it)
4. (Not adopted) session.closing = True already sets is_closing via property
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import (
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fix #1: run_ctx.current_task set in start()
# ---------------------------------------------------------------------------


def test_start_sets_current_task() -> None:
    """RunHandle.start() must set run_ctx.current_task.

    Without this, cancel() in _interrupt() gets None for current_task
    and cannot interrupt the running turn.
    """
    import agentpool.orchestrator.run as run_module

    source = inspect.getsource(run_module.RunHandle.start)
    assert "current_task" in source, (
        "run_ctx.current_task must be set in start() so cancel() can "
        "interrupt the running turn"
    )
    assert "asyncio.current_task()" in source, (
        "current_task must be set to asyncio.current_task()"
    )


@pytest.mark.asyncio
async def test_current_task_set_during_start_execution() -> None:
    """Verify run_ctx.current_task is populated during start() execution."""
    agent = Agent(
        name="test-current-task",
        model=TestModel(custom_output_text="ok"),
    )
    async with agent:
        event_bus = EventBus()
        session = SessionState(
            session_id="test-current-task-session",
            agent_name="test-current-task",
        )
        run_ctx = AgentRunContext(
            session_id="test-current-task-session",
            event_bus=event_bus,
        )
        run_handle = RunHandle(
            run_id="test-current-task-run",
            session_id="test-current-task-session",
            agent_type="test-current-task",
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
        )

        captured_tasks: list[Any] = []

        class CapturingTurn:
            async def execute(self) -> Any:
                # Capture current_task from run_ctx during turn execution
                captured_tasks.append(run_ctx.current_task)
                yield StreamCompleteEvent(message=MagicMock())

        agent.create_turn = MagicMock(return_value=CapturingTurn())  # type: ignore[method-assign]

        gen = run_handle.start("test")
        try:
            async with asyncio.timeout(5):
                async for event in gen:
                    if isinstance(event, StreamCompleteEvent):
                        break
        finally:
            with contextlib.suppress(Exception):
                await gen.aclose()

        assert len(captured_tasks) == 1
        assert captured_tasks[0] is not None, (
            "run_ctx.current_task was not set during start() execution"
        )
        assert captured_tasks[0] is asyncio.current_task(), (
            "run_ctx.current_task should be the current asyncio task"
        )


# ---------------------------------------------------------------------------
# Fix #2: ACPTurn uses self._run_ctx.run_id, no redundant RunStartedEvent
# ---------------------------------------------------------------------------


def test_acp_turn_uses_run_ctx_run_id() -> None:
    """ACPTurn.execute() must use self._run_ctx.run_id, not generate uuid4."""
    import agentpool.agents.acp_agent.turn as turn_module

    source = inspect.getsource(turn_module.ACPTurn.execute)
    assert "self._run_ctx.run_id" in source, (
        "ACPTurn must use self._run_ctx.run_id for consistency with RunHandle"
    )
    assert "str(uuid4())" not in source or "message_id" in source, (
        "ACPTurn.execute() must not generate a new run_id via uuid4()"
    )


def test_acp_turn_no_redundant_run_started_event() -> None:
    """ACPTurn.execute() must not yield RunStartedEvent.

    RunHandle.start() already publishes RunStartedEvent before calling
    turn.execute(). Yielding it again causes duplicate events.
    """
    import agentpool.agents.acp_agent.turn as turn_module

    source = inspect.getsource(turn_module.ACPTurn.execute)
    # Check that RunStartedEvent is not yielded in the execute method body
    import re

    yield_matches = re.findall(r"yield\s+RunStartedEvent", source)
    assert len(yield_matches) == 0, (
        f"ACPTurn.execute() still yields RunStartedEvent {len(yield_matches)} "
        "time(s) — RunHandle.start() already publishes it"
    )


# ---------------------------------------------------------------------------
# Fix #3: NativeTurn no redundant RunStartedEvent
# ---------------------------------------------------------------------------


def test_native_turn_no_redundant_run_started_event() -> None:
    """NativeTurn.execute() must not yield RunStartedEvent.

    RunHandle.start() already publishes RunStartedEvent before calling
    turn.execute(). Yielding it again causes duplicate events.
    """
    import agentpool.agents.native_agent.turn as turn_module

    source = inspect.getsource(turn_module.NativeTurn.execute)
    import re

    yield_matches = re.findall(r"yield\s+RunStartedEvent", source)
    assert len(yield_matches) == 0, (
        f"NativeTurn.execute() still yields RunStartedEvent {len(yield_matches)} "
        "time(s) — RunHandle.start() already publishes it"
    )


# ---------------------------------------------------------------------------
# Fix #4 (not adopted): closing is a property alias for is_closing
# ---------------------------------------------------------------------------


def test_closing_property_sets_is_closing() -> None:
    """session.closing = True already sets session.is_closing = True.

    The `closing` property is an alias for `is_closing` — its setter
    writes to `self.is_closing`. So setting `session.closing = True`
    is equivalent to setting `session.is_closing = True`.
    """
    session = SessionState(
        session_id="test-property",
        agent_name="test",
    )
    assert session.is_closing is False
    assert session.closing is False

    session.closing = True
    assert session.is_closing is True, (
        "Setting session.closing = True should also set session.is_closing = True "
        "via the property setter"
    )
    assert session.closing is True
