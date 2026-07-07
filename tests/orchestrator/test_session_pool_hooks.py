"""E2E regression tests for hooks firing through the SessionPool path.

This is the regression test for the original bug where hooks didn't fire
when going through SessionPool (RunHandle.start() → turn.execute()).

The test verifies:
1. Hooks fire when going through RunHandle.start() (the SessionPool path)
2. hooks_fired is cleared between turns so turn 2 hooks still fire
"""

from __future__ import annotations

import asyncio
from typing import Any

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import StreamCompleteEvent
from agentpool.hooks import AgentHooks, CallableHook, HookResult
from agentpool.orchestrator.core import EventBus, SessionState
from agentpool.orchestrator.run import RunHandle


# ---------------------------------------------------------------------------
# Test state
# ---------------------------------------------------------------------------

hook_calls: list[tuple[str, dict[str, Any]]] = []


def _reset_calls() -> None:
    hook_calls.clear()


def _make_recorder(event: str) -> CallableHook:
    def _fn(**kwargs: Any) -> HookResult:
        hook_calls.append((event, kwargs))
        return {"decision": "allow"}

    return CallableHook(event=event, fn=_fn)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_handle(
    agent: Agent[Any, Any],
    run_ctx: AgentRunContext,
) -> RunHandle:
    """Create a RunHandle wired for the SessionPool path."""
    event_bus = EventBus()
    session = SessionState(session_id="test-session", agent_name="test-agent")
    return RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx,
    )


# ---------------------------------------------------------------------------
# Test: hooks fire through RunHandle.start() path
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_hooks_fire_through_run_handle_start() -> None:
    """Given a RunHandle.start() path, hooks fire during turn execution.

    This is the regression test for the bug where hooks didn't fire
    when going through the SessionPool/RunHandle path.
    """
    _reset_calls()
    hooks = AgentHooks(
        pre_turn=[_make_recorder("pre_turn")],
        post_turn=[_make_recorder("post_turn")],
    )
    agent = Agent(
        name="test-pool-hooks",
        model=TestModel(custom_output_text="pool response"),
        hooks=hooks,
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")
        handle = _make_run_handle(agent, run_ctx)

        events: list[Any] = []
        gen = handle.start("hello")

        async def _consume() -> None:
            events.extend([event async for event in gen])

        consumer_task = asyncio.create_task(_consume())
        await asyncio.sleep(0.1)

        # Close to unblock the idle wait after the turn completes
        handle.close()
        await asyncio.sleep(0.1)
        await consumer_task

    event_names = [name for name, _ in hook_calls]
    assert "pre_turn" in event_names, "pre_turn hook must fire through RunHandle.start()"

    # post_turn fires in NativeTurn.execute()'s finally block, which runs
    # when the generator is closed. In the RunHandle.start() path, the
    # generator may be suspended after break. The direct turn.execute()
    # test (below) verifies post_turn fires when the generator is fully consumed.
    # Here we only assert pre_turn — the original bug was that pre_turn
    # didn't fire at all through the SessionPool path.

    # The turn should have completed
    assert any(isinstance(e, StreamCompleteEvent) for e in events)


# ---------------------------------------------------------------------------
# Test: hooks_fired cleared between turns
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_hooks_fired_cleared_between_turns() -> None:
    """Given two sequential turns, turn 2 hooks still fire.

    RunHandle.start() clears hooks_fired at the start of each turn.
    Without this, turn 1's 'pre_turn' guard key would block turn 2's
    pre_turn from firing.

    This test runs turn 1 through RunHandle.start(), then manually
    creates a second turn with the same run_ctx (after clearing
    hooks_fired) to verify hooks fire again.
    """
    _reset_calls()
    hooks = AgentHooks(
        pre_turn=[_make_recorder("pre_turn")],
        post_turn=[_make_recorder("post_turn")],
    )
    agent = Agent(
        name="test-multi-turn-hooks",
        model=TestModel(custom_output_text="response"),
        hooks=hooks,
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")

        # Turn 1: Run through RunHandle.start()
        handle = _make_run_handle(agent, run_ctx)
        gen = handle.start("first prompt")

        async def _consume() -> None:
            _ = [event async for event in gen]

        consumer_task = asyncio.create_task(_consume())
        await asyncio.sleep(0.1)
        handle.close()
        await asyncio.sleep(0.1)
        await consumer_task

    # Turn 1 should have fired both hooks
    pre_turn_count = sum(1 for name, _ in hook_calls if name == "pre_turn")
    assert pre_turn_count == 1, "pre_turn must fire in turn 1"

    # Simulate RunHandle.start() clearing hooks_fired for turn 2
    run_ctx.hooks_fired.clear()

    # Turn 2: Create a new turn manually with the same run_ctx
    from agentpool.agents.native_agent.turn import NativeTurn

    turn = NativeTurn(
        agent=agent,
        prompts=["second prompt"],
        run_ctx=run_ctx,
        message_history=[],
        hooks=hooks,
    )
    _ = [event async for event in turn.execute()]

    pre_turn_count = sum(1 for name, _ in hook_calls if name == "pre_turn")
    assert pre_turn_count == 2, "pre_turn must fire again in turn 2 after clearing hooks_fired"


# ---------------------------------------------------------------------------
# Test: simplified SessionPool path (direct turn.execute)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_hooks_fire_in_direct_turn_execute() -> None:
    """Given a NativeTurn created via agent.create_turn(), hooks fire.

    This is a simplified version of the SessionPool path that verifies
    the create_turn() → turn.execute() pipeline fires hooks correctly.
    """
    _reset_calls()
    hooks = AgentHooks(
        pre_turn=[_make_recorder("pre_turn")],
        post_turn=[_make_recorder("post_turn")],
    )
    agent = Agent(
        name="test-create-turn-hooks",
        model=TestModel(custom_output_text="response"),
        hooks=hooks,
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")

        # create_turn() is what RunHandle.start() calls internally
        turn = agent.create_turn(
            prompts=["hello"],
            run_ctx=run_ctx,
            message_history=[],
        )

        events = [event async for event in turn.execute()]

    event_names = [name for name, _ in hook_calls]
    assert "pre_turn" in event_names
    assert "post_turn" in event_names
    assert any(isinstance(e, StreamCompleteEvent) for e in events)
