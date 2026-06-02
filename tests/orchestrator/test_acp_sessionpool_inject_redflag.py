"""Red flag tests for ACP + SessionPool + per-session agent + post-turn inject_prompt + auto-resume.

These tests reproduce the issue where background task completion inject_prompt
does not trigger auto-resume in ACP SessionPool mode.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from acp.schema import TurnCompleteUpdate
from agentpool import Agent, AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionController, TurnRunner
from agentpool_server.acp_server.event_converter import ACPEventConverter


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------


async def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: Any,
    mock_pool: Any,
) -> Any:
    """Create a session and attach the agent."""
    state = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent
    return state


# -----------------------------------------------------------------------------
# Red Flag Test 1: Post-turn inject_prompt triggers auto-resume with per-session agent
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_post_turn_inject_prompt_triggers_auto_resume_with_per_session_agent() -> None:
    """inject_prompt AFTER run_loop ends MUST trigger auto-resume for per-session agent.

    This is a **red flag test** — if it fails, ACP SessionPool auto-resume is broken.

    Scenario (real-world from ACP + xeno-agent):
    1. ACP handler calls SessionPool.process_prompt() → run_loop()
    2. run_loop creates per-session agent and runs _run_turn_unlocked
    3. Agent's tool spawns background task
    4. _run_turn_unlocked completes, run_loop calls _process_queued_work (none yet)
    5. run_loop releases turn_lock
    6. Background task completes, calls session_pool.inject_prompt()
    7. inject_prompt detects no active run context → queues + triggers auto-resume
    8. _trigger_auto_resume acquires turn_lock, runs queued work

    Expected: _run_stream_once called TWICE (initial + auto-resume).
    """
    call_count = 0
    received_prompts: list[tuple[Any, ...]] = []

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        nonlocal call_count
        call_count += 1
        received_prompts.append(prompts)
        yield RunStartedEvent(session_id="sess-1", run_id=f"run-{call_count}")
        yield StreamCompleteEvent(
            message=ChatMessage(content=f"done-{call_count}", role="assistant"),
        )

    # Use MagicMock to simulate a per-session agent
    agent = MagicMock()
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None
    agent.get_active_run_context.side_effect = lambda: agent._active_run_ctx
    agent._run_stream_once = _fake_stream

    mock_pool = MagicMock()
    mock_pool.main_agent = agent
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    turn_runner = TurnRunner(session_controller=controller, enable_auto_resume=True)

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # 1. Initial turn completes via run_loop
    await turn_runner.run_loop("sess-1", "initial")
    assert call_count == 1, f"Expected 1 call after run_loop, got {call_count}"

    # 2. Post-turn injection (simulates background task completion)
    injected = await turn_runner.inject_prompt("sess-1", "bg-task completed")
    assert injected is False  # Queued, not injected into active turn

    # 3. Wait for auto-resume to fire and complete
    await asyncio.sleep(0.1)

    # RED FLAG: auto-resume should have triggered a second turn
    assert call_count == 2, (
        f"post-turn inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2 (initial + auto-resume). "
        f"_trigger_auto_resume did not process queued injection."
    )
    assert received_prompts[1] == ("bg-task completed",), (
        f"Auto-resume should process injected prompt, got {received_prompts[1]}"
    )


# -----------------------------------------------------------------------------
# Red Flag Test 2: SessionPool-level inject_prompt triggers auto-resume
# -----------------------------------------------------------------------------


@pytest.mark.anyio
async def test_session_pool_inject_prompt_triggers_auto_resume() -> None:
    """SessionPool.inject_prompt() after run_loop MUST trigger auto-resume.

    This tests the exact code path used by xeno-agent's BackgroundTaskProvider:
    ```python
    session_pool.inject_prompt(task.parent_session_id, notice)
    ```

    Scenario:
    1. SessionPool.process_prompt() → run_loop() completes
    2. Background task completion calls SessionPool.inject_prompt()
    3. TurnRunner.inject_prompt queues + triggers auto-resume
    4. Auto-resume processes the queued injection

    Expected: _run_stream_once called TWICE.
    """
    call_count = 0

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        nonlocal call_count
        call_count += 1
        yield RunStartedEvent(session_id="sess-1", run_id=f"run-{call_count}")
        yield StreamCompleteEvent(
            message=ChatMessage(content=f"done-{call_count}", role="assistant"),
        )

    agent = MagicMock()
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None
    agent.get_active_run_context.side_effect = lambda: agent._active_run_ctx
    agent._run_stream_once = _fake_stream

    mock_pool = MagicMock()
    mock_pool.main_agent = agent
    mock_pool.manifest = MagicMock()
    mock_pool.manifest.agents = {}

    controller = SessionController(pool=mock_pool)
    turn_runner = TurnRunner(session_controller=controller, enable_auto_resume=True)

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # 1. Run loop completes
    await turn_runner.run_loop("sess-1", "initial")
    assert call_count == 1

    # 2. Simulate SessionPool.inject_prompt (what BackgroundTaskProvider calls)
    injected = await turn_runner.inject_prompt("sess-1", "task completed")
    assert injected is False

    # 3. Wait for auto-resume
    await asyncio.sleep(0.1)

    assert call_count == 2, (
        f"SessionPool.inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2. Auto-resume did not trigger after post-turn injection."
    )


# -----------------------------------------------------------------------------
# Red Flag Test 3: Real AgentPool + SessionPool + inject_prompt + auto-resume
# -----------------------------------------------------------------------------


@pytest.mark.integration
async def test_real_agentpool_sessionpool_inject_prompt_auto_resume() -> None:
    """Real AgentPool with SessionPool must auto-resume after inject_prompt.

    This tests the full stack with real AgentPool, SessionPool, and TestModel.
    """
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest, enable_session_pool=True) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        session_id = "test-session"
        await session_pool.create_session(session_id, agent_name="test_agent")

        # Subscribe to EventBus to consume events
        event_queue = await session_pool.event_bus.subscribe(session_id)
        events: list[Any] = []

        async def _consume_events() -> None:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                if event is None:
                    break
                events.append(event)

        consumer_task = asyncio.create_task(_consume_events())

        # 1. Process initial prompt via run_loop
        await session_pool.process_prompt(session_id, "hello")

        # 2. Post-turn inject (simulates background task completion)
        injected = await session_pool.inject_prompt(session_id, "bg done")
        assert injected is False  # Should be queued, not injected into active turn

        # 3. Wait for auto-resume to process the injection
        await asyncio.sleep(0.2)

        # Cancel consumer
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task

        # Check that we got events from BOTH turns
        run_started_events = [e for e in events if isinstance(e, RunStartedEvent)]
        assert len(run_started_events) == 2, (
            f"Expected 2 RunStartedEvent (initial + auto-resume), got {len(run_started_events)}. "
            f"Auto-resume did not trigger after inject_prompt. Events: {[type(e).__name__ for e in events]}"
        )


# -----------------------------------------------------------------------------
# Red Flag Test 4: Per-session agent session_id is set correctly
# -----------------------------------------------------------------------------


@pytest.mark.integration
async def test_per_session_agent_session_id_set() -> None:
    """Per-session agent created by SessionPool MUST have session_id set.

    REGRESSION: Previously, per-session agent's session_id was None,
    causing AssertionError in NativeAgent._stream_events.
    """
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest, enable_session_pool=True) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        session_id = "test-session"
        await session_pool.create_session(session_id, agent_name="test_agent")

        # Run a turn to create per-session agent
        await session_pool.process_prompt(session_id, "hello")

        # Get the session and check agent
        session = session_pool.sessions.get_session(session_id)
        assert session is not None
        assert session.agent is not None
        assert session.agent.session_id == session_id, (
            f"Per-session agent session_id mismatch: "
            f"expected {session_id!r}, got {session.agent.session_id!r}"
        )

        # Run a turn to verify no AssertionError
        await session_pool.process_prompt(session_id, "hello")


# -----------------------------------------------------------------------------
# Red Flag Test 5: TurnCompleteUpdate is emitted after auto-resume
# -----------------------------------------------------------------------------


@pytest.mark.integration
async def test_turn_complete_update_after_auto_resume() -> None:
    """TurnCompleteUpdate MUST be emitted after each turn, including auto-resume turns.

    This tests the draft RFD PR #644 implementation (turn-complete signal).
    See: https://github.com/agentclientprotocol/agent-client-protocol/pull/644
    """
    agent_config = NativeAgentConfig(
        name="test_agent",
        model="test",
        system_prompt="You are a test agent",
    )
    manifest = AgentsManifest(agents={"test_agent": agent_config})

    async with AgentPool(manifest, enable_session_pool=True) as pool:
        session_pool = pool.session_pool
        assert session_pool is not None

        session_id = "test-session"
        await session_pool.create_session(session_id, agent_name="test_agent")

        # Subscribe to EventBus to consume events
        event_queue = await session_pool.event_bus.subscribe(session_id)
        events: list[Any] = []

        async def _consume_events() -> None:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                if event is None:
                    break
                events.append(event)

        consumer_task = asyncio.create_task(_consume_events())

        # 1. Process initial prompt via run_loop
        await session_pool.process_prompt(session_id, "hello")

        # 2. Post-turn inject (simulates background task completion)
        injected = await session_pool.inject_prompt(session_id, "bg done")
        assert injected is False  # Should be queued, not injected into active turn

        # 3. Wait for auto-resume to process the injection
        await asyncio.sleep(0.2)

        # Cancel consumer
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task

        # Convert events to ACP updates using the same converter as the handler
        converter = ACPEventConverter()
        acp_updates: list[Any] = []
        for event in events:
            async for update in converter.convert(event):
                acp_updates.append(update)

        # Check that TurnCompleteUpdate is emitted for BOTH turns
        turn_complete_updates = [u for u in acp_updates if isinstance(u, TurnCompleteUpdate)]
        assert len(turn_complete_updates) == 2, (
            f"Expected 2 TurnCompleteUpdate (initial + auto-resume), got {len(turn_complete_updates)}. "
            f"Updates: {[type(u).__name__ for u in acp_updates]}"
        )
        # All should have stop_reason="end_turn"
        for tc in turn_complete_updates:
            assert tc.stop_reason == "end_turn"
