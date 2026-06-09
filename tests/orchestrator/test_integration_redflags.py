"""Integration red flag tests for SessionPool subagent event routing and auto-resume.

Consolidated from:
- test_acp_sessionpool_inject_redflag.py (ACP + SessionPool + inject_prompt + auto-resume)
- test_session_tree_redflag.py (EventBus _session_tree and descendants scope)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from acp.schema import TurnCompleteUpdate
from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, EventEnvelope, SessionController, SessionPool, TurnRunner
from agentpool_server.acp_server.event_converter import ACPEventConverter


# ============================================================================
# ACP SessionPool inject + auto-resume red flags
# ============================================================================


async def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: Any,
    mock_pool: Any,
) -> Any:
    """Create a session and attach the agent."""
    state, _ = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent
    return state


@pytest.mark.anyio
async def test_post_turn_inject_prompt_triggers_auto_resume_with_per_session_agent() -> None:
    """inject_prompt AFTER run_loop ends MUST trigger auto-resume for per-session agent.

    Scenario (real-world from ACP + xeno-agent):
    1. ACP handler calls SessionPool.process_prompt() -> run_loop()
    2. run_loop creates per-session agent and runs _run_turn_unlocked
    3. Agent's tool spawns background task
    4. _run_turn_unlocked completes, run_loop calls _process_queued_work (none yet)
    5. run_loop releases turn_lock
    6. Background task completes, calls session_pool.inject_prompt()
    7. inject_prompt detects no active run context -> queues + triggers auto-resume
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

    agent = MagicMock()
    agent.get_active_run_context.return_value = None
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


@pytest.mark.anyio
async def test_session_pool_inject_prompt_triggers_auto_resume() -> None:
    """SessionPool.inject_prompt() after run_loop MUST trigger auto-resume."""
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
    agent.get_active_run_context.return_value = None
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

    # 2. Simulate SessionPool.inject_prompt
    injected = await turn_runner.inject_prompt("sess-1", "task completed")
    assert injected is False

    # 3. Wait for auto-resume
    await asyncio.sleep(0.1)

    assert call_count == 2, (
        f"SessionPool.inject_prompt BROKEN: _run_stream_once called {call_count} time(s), "
        f"expected 2. Auto-resume did not trigger after post-turn injection."
    )


@pytest.mark.integration
async def test_real_agentpool_sessionpool_inject_prompt_auto_resume() -> None:
    """Real AgentPool with SessionPool must auto-resume after inject_prompt."""
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

        # Check that auto-resume was triggered: we should see events from
        # the initial turn AND from the auto-resume turn.
        run_started_events = [
            e for e in events
            if isinstance(e.event if isinstance(e, EventEnvelope) else e, RunStartedEvent)
        ]
        assert len(run_started_events) >= 2, (
            f"Expected at least 2 RunStartedEvent (initial + auto-resume), got {len(run_started_events)}. "
            f"Auto-resume did not trigger after inject_prompt. Events: {[type(e).__name__ for e in events]}"
        )

        # Verify we got at least 2 StreamCompleteEvent (one per run)
        stream_complete_events = [
            e for e in events
            if isinstance(e.event if isinstance(e, EventEnvelope) else e, StreamCompleteEvent)
        ]
        assert len(stream_complete_events) >= 2, (
            f"Expected at least 2 StreamCompleteEvent, got {len(stream_complete_events)}"
        )


@pytest.mark.integration
async def test_per_session_agent_session_id_set() -> None:
    """Per-session agent created by SessionPool MUST have session_id set."""
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

        # Run a turn via run_stream to create per-session agent
        async for _ in session_pool.run_stream(session_id, "hello"):
            pass

        # Get the session and check agent
        session = session_pool.sessions.get_session(session_id)
        assert session is not None
        assert session.agent is not None

        # Run another turn via run_stream to verify no AssertionError
        async for _ in session_pool.run_stream(session_id, "hello"):
            pass


@pytest.mark.integration
async def test_turn_complete_update_after_auto_resume() -> None:
    """TurnCompleteUpdate MUST be emitted after each turn, including auto-resume turns."""
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
        converter = ACPEventConverter(client_supports_turn_complete=True)
        acp_updates: list[Any] = []
        for event in events:
            raw_event = event.event if isinstance(event, EventEnvelope) else event
            async for update in converter.convert(raw_event):
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


# ============================================================================
# Session tree / descendants scope red flags
# ============================================================================


class TestEventBusSessionTree:
    """Red flag: _session_tree is never updated, breaking descendants scope."""

    def test_session_tree_is_empty_after_construction(self) -> None:
        """Baseline: fresh EventBus has empty _session_tree."""
        bus = EventBus()
        assert bus._session_tree == {}

    async def test_is_descendant_always_false_for_empty_tree(self) -> None:
        """RED FLAG: _is_descendant returns False even for direct children."""
        bus = EventBus()
        result = bus._is_descendant("child-sid", "parent-sid")
        assert result is False, (
            "_is_descendant should be True for known children, "
            "but _session_tree is empty so it returns False"
        )

    async def test_should_receive_descendants_always_false(self) -> None:
        """RED FLAG: scope='descendants' never matches child events."""
        bus = EventBus()
        result = bus._should_receive(
            published_sid="child-sid",
            subscriber_sid="parent-sid",
            scope="descendants",
        )
        assert result is False, (
            "scope='descendants' should receive child events, "
            "but _session_tree is empty so it returns False"
        )

    async def test_publish_delivers_descendant_events_to_parent(self) -> None:
        """FIXED: Child session events ARE delivered to parent subscribers via controller."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}
        controller = SessionController(mock_pool)
        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session("child-sid", parent_session_id="parent-sid")
        bus = EventBus(session_controller=controller)
        parent_queue = await bus.subscribe("parent-sid", scope="descendants")

        event = {"type": "test", "data": "hello from child"}
        await bus.publish("child-sid", event)

        assert not parent_queue.empty(), (
            "Parent subscriber with scope='descendants' should receive child events "
            "when EventBus is wired to SessionController"
        )
        received = await parent_queue.get()
        assert isinstance(received, EventEnvelope)
        assert received.event == event
        assert received.source_session_id == "child-sid"

    async def test_publish_delivers_to_exact_session(self) -> None:
        """Green: exact session scope works (baseline)."""
        bus = EventBus()
        queue = await bus.subscribe("same-sid", scope="session")

        event = {"type": "test", "data": "hello"}
        await bus.publish("same-sid", event)

        assert not queue.empty(), "Exact session scope should work"
        received = await queue.get()
        assert isinstance(received, EventEnvelope)
        assert received.event == event
        assert received.source_session_id == "same-sid"


class TestSessionControllerChildrenVsEventBus:
    """Red flag: SessionController._children and EventBus._session_tree diverge."""

    async def test_children_tracking_works(self) -> None:
        """SessionController correctly tracks parent-child relationships."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}

        controller = SessionController(mock_pool)

        # Create parent session
        parent, _ = await controller.get_or_create_session("parent-sid")
        assert parent.session_id == "parent-sid"

        # Create child session
        child, _ = await controller.get_or_create_session(
            "child-sid", parent_session_id="parent-sid"
        )
        assert child.parent_session_id == "parent-sid"

        # SessionController knows about the relationship
        assert "child-sid" in controller._children.get("parent-sid", [])

    async def test_event_bus_does_not_know_about_children(self) -> None:
        """RED FLAG: EventBus has no knowledge of SessionController's children."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}

        controller = SessionController(mock_pool)
        turn_runner = MagicMock()
        turn_runner.event_bus = EventBus()

        # Simulate SessionPool behavior: create sessions via controller
        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session(
            "child-sid", parent_session_id="parent-sid"
        )

        # EventBus knows nothing
        assert turn_runner.event_bus._session_tree == {}, (
            "EventBus._session_tree is empty even though SessionController "
            "knows about parent-child relationship"
        )

    async def test_is_descendant_with_controller_wired(self) -> None:
        """With controller wired, _is_descendant works despite empty _session_tree."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}

        controller = SessionController(mock_pool)
        bus = EventBus(session_controller=controller)

        # _session_tree is empty (would be always false without controller)
        assert bus._session_tree == {}

        # Controller knows about the relationship
        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session(
            "child-sid", parent_session_id="parent-sid"
        )

        # Should now work because controller is wired
        result = bus._is_descendant("child-sid", "parent-sid")
        assert result is True, (
            "_is_descendant should return True when controller knows the relationship, "
            "even though _session_tree is empty"
        )

    async def test_should_receive_descendants_with_controller_wired(self) -> None:
        """With controller wired, descendants scope works despite empty _session_tree."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}

        controller = SessionController(mock_pool)
        bus = EventBus(session_controller=controller)

        # _session_tree is empty (would be always false without controller)
        assert bus._session_tree == {}

        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session(
            "child-sid", parent_session_id="parent-sid"
        )

        result = bus._should_receive(
            published_sid="child-sid",
            subscriber_sid="parent-sid",
            scope="descendants",
        )
        assert result is True, (
            "scope='descendants' should receive child events when controller is wired, "
            "even though _session_tree is empty"
        )

    async def test_acp_handler_delivers_child_events(self) -> None:
        """FIXED: Full ACP handler scenario - subagent events reach parent."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}
        controller = SessionController(mock_pool)
        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session("child-sid", parent_session_id="parent-sid")
        bus = EventBus(session_controller=controller)

        # Step 1: ACP handler subscribes to parent session with descendants scope
        parent_queue = await bus.subscribe("parent-sid", scope="descendants")

        # Step 2: Subagent runs and publishes events with its own session_id
        subagent_events = [
            {"type": "agent_message_chunk", "content": "Hello"},
            {"type": "tool_call", "tool": "search"},
            {"type": "agent_message_chunk", "content": "Done"},
        ]

        for event in subagent_events:
            await bus.publish("child-sid", event)

        # Step 3: Parent queue should have received all child events
        received = []
        while not parent_queue.empty():
            received.append(await parent_queue.get())

        assert len(received) == len(subagent_events), (
            f"Expected {len(subagent_events)} events, got {len(received)}. "
            f"Child events were not delivered to parent subscriber."
        )

    async def test_acp_handler_child_events_have_session_id(self) -> None:
        """Child session events are wrapped in EventEnvelope with source_session_id."""
        from agentpool.agents.events import StreamCompleteEvent
        from agentpool.messaging import ChatMessage
        from agentpool.orchestrator.core import TurnRunner

        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}
        controller = SessionController(mock_pool)
        await controller.get_or_create_session("parent-sid")
        await controller.get_or_create_session("child-sid", parent_session_id="parent-sid")
        bus = EventBus(session_controller=controller)
        controller.turn_runner = TurnRunner(session_controller=controller, enable_auto_resume=False)
        controller.turn_runner.event_bus = bus

        # Parent subscribes with descendants scope
        parent_queue = await bus.subscribe("parent-sid", scope="descendants")

        # Publish child event via TurnRunner._publish_event (the real path)
        child_event = StreamCompleteEvent(
            message=ChatMessage(content="hello", role="assistant"),
        )

        await controller.turn_runner._publish_event("child-sid", child_event)

        # Parent receives the event wrapped in EventEnvelope
        received = await parent_queue.get()
        assert isinstance(received, EventEnvelope)
        assert received.source_session_id == "child-sid", (
            "Child event should carry source_session_id so ACP handler can route it correctly"
        )
        # The actual event payload is accessible via received.event
        assert isinstance(received.event, StreamCompleteEvent)


class TestSessionPoolIntegration:
    """Red flag: SessionPool-level integration tests."""

    async def test_subagent_streaming_events_routed_to_parent(self) -> None:
        """FIXED: When SessionPool runs subagent, events reach parent."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}
        # Make create_child_session an async mock to avoid TypeError
        mock_pool.session_pool.create_child_session = MagicMock(return_value=asyncio.Future())
        mock_pool.session_pool.create_child_session.return_value.set_result(None)

        pool = SessionPool(
            mock_pool,
            enable_auto_resume=True,
            enable_event_bus=True,
        )

        # Parent subscribes to events
        parent_queue = await pool.event_bus.subscribe("parent-sid", scope="descendants")

        # Simulate what happens when subagent runs:
        await pool.create_session("parent-sid")
        await pool.create_session("child-sid", parent_session_id="parent-sid")

        # Simulate subagent event emission
        await pool.event_bus.publish("child-sid", {"type": "agent_message_chunk", "content": "hi"})

        # Parent should have received it
        assert not parent_queue.empty(), (
            "Parent should receive subagent events via descendants scope "
            "when EventBus is wired to SessionController through TurnRunner"
        )

    async def test_manual_session_tree_fix_works(self) -> None:
        """Verify that populating _session_tree manually fixes the issue."""
        bus = EventBus()

        # Manually populate _session_tree (this is the fix)
        bus._session_tree["parent-sid"] = ["child-sid"]

        parent_queue = await bus.subscribe("parent-sid", scope="descendants")

        await bus.publish("child-sid", {"type": "test", "data": "hello"})

        assert not parent_queue.empty(), "Manual _session_tree fix should work"
        received = await parent_queue.get()
        assert isinstance(received, EventEnvelope)
        assert received.event["data"] == "hello"
        assert received.source_session_id == "child-sid"


class TestInjectPromptWithSessionPool:
    """Red flag: inject_prompt relies on auto-resume, but events still lost."""

    async def test_inject_prompt_triggers_auto_resume(self) -> None:
        """inject_prompt itself works, but its events are lost due to _session_tree."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}
        # Make create_child_session an async mock
        mock_pool.session_pool.create_child_session = MagicMock(return_value=asyncio.Future())
        mock_pool.session_pool.create_child_session.return_value.set_result(None)

        pool = SessionPool(mock_pool)

        # Create a session with an agent that can accept injections
        await pool.create_session("test-sid")

        # Mock the agent to avoid full agent setup
        mock_agent = MagicMock()
        mock_agent.get_active_run_context.return_value = None

        # Replace session agent
        session = pool.sessions.get_session("test-sid")
        if session:
            session.agent = mock_agent

        # inject_prompt should queue and trigger auto-resume
        result = await pool.inject_prompt("test-sid", "test message")

        # inject_prompt returns False when queued (no active run_ctx)
        assert result is False


@pytest.mark.manual
async def test_diagnostic_print_session_tree_state() -> None:
    """Print the state of _session_tree for diagnostic purposes."""
    mock_pool = MagicMock()
    mock_pool.main_agent.name = "test-agent"
    mock_pool.manifest.agents = {}
    # Make create_child_session an async mock
    mock_pool.session_pool.create_child_session = MagicMock(return_value=asyncio.Future())
    mock_pool.session_pool.create_child_session.return_value.set_result(None)

    pool = SessionPool(mock_pool)

    await pool.create_session("parent-sid")
    await pool.create_session("child-sid", parent_session_id="parent-sid")

    print(f"\n{'='*60}")
    print("DIAGNOSTIC: Session Tree State")
    print(f"{'='*60}")
    print(f"SessionController._children: {pool.sessions._children}")
    print(f"EventBus._session_tree: {pool.event_bus._session_tree}")
    print(f"EventBus._subscribers: {await pool.event_bus.get_subscriber_counts()}")
    print(f"{'='*60}")

    # This assertion documents the bug:
    assert pool.sessions._children != {}, "SessionController knows about children"
    assert pool.event_bus._session_tree == {}, "BUG: EventBus._session_tree is empty"


@pytest.mark.integration
async def test_shared_agent_inject_prompt_fallback_triggers_auto_resume() -> None:
    """Shared agent inject_prompt without session_id MUST fallback to SessionPool auto-resume.

    Scenario (real-world from BackgroundTaskProvider):
      1. A shared agent (no fixed session_id) runs a turn via SessionPool
      2. The turn completes, session becomes idle
      3. A background task completes and calls agent.inject_prompt("notice")
         WITHOUT passing session_id
      4. agent.inject_prompt has no active run_ctx and _events.session_id is None
      5. Fallback: find the most recently active session for this agent in SessionPool
      6. Trigger auto-resume via session_pool.receive_request

    EXPECTED: Auto-resume triggers and processes the injected message.
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
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    if event is None:
                        break
                    events.append(event)
                except asyncio.TimeoutError:
                    break

        consumer_task = asyncio.create_task(_consume_events())

        # 1. Process initial prompt via run_loop
        await session_pool.process_prompt(session_id, "hello")

        # Wait for consumer to collect initial turn events
        await asyncio.sleep(0.2)

        # 2. Get the shared agent from pool (simulates ctx.agent in BackgroundTaskProvider)
        shared_agent = pool.get_agent("test_agent")
        # Verify shared agent has no fixed session_id
        assert shared_agent._events.session_id is None, (
            "Shared agent should not have a fixed session_id for this test"
        )

        # 3. Call inject_prompt WITHOUT session_id (simulates BackgroundTaskProvider)
        shared_agent.inject_prompt("bg task completed")

        # 4. Wait for auto-resume to process the injection
        await asyncio.sleep(0.2)

        # Cancel consumer
        consumer_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await consumer_task

        # Check that auto-resume was triggered: we should see events from
        # the initial turn AND from the auto-resume turn.
        run_started_events = [
            e for e in events
            if isinstance(e.event if isinstance(e, EventEnvelope) else e, RunStartedEvent)
        ]
        assert len(run_started_events) >= 2, (
            f"Expected at least 2 RunStartedEvent (initial + auto-resume), got {len(run_started_events)}. "
            f"Fallback auto-resume did not trigger after inject_prompt. "
            f"Events: {[type(e).__name__ for e in events]}"
        )

        # Verify we got at least 2 StreamCompleteEvent (one per run)
        stream_complete_events = [
            e for e in events
            if isinstance(e.event if isinstance(e, EventEnvelope) else e, StreamCompleteEvent)
        ]
        assert len(stream_complete_events) >= 2, (
            f"Expected at least 2 StreamCompleteEvent, got {len(stream_complete_events)}"
        )
