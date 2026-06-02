"""Red flag tests for SessionPool subagent event routing.

These tests verify the root cause: EventBus._session_tree is never populated,
breaking scope="descendants" subscriptions used by ACPProtocolHandler.

Run with: uv run pytest tests/orchestrator/test_session_tree_redflag.py -xvs
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from agentpool.orchestrator.core import EventBus, SessionController, SessionPool


class TestEventBusSessionTree:
    """Red flag: _session_tree is never updated, breaking descendants scope."""

    def test_session_tree_is_empty_after_construction(self) -> None:
        """Baseline: fresh EventBus has empty _session_tree."""
        bus = EventBus()
        assert bus._session_tree == {}

    async def test_is_descendant_always_false_for_empty_tree(self) -> None:
        """RED FLAG: _is_descendant returns False even for direct children."""
        bus = EventBus()
        # Even though we conceptually know parent -> child,
        # _session_tree has no record of it
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
        assert received == event

    async def test_publish_delivers_to_exact_session(self) -> None:
        """Green: exact session scope works (baseline)."""
        bus = EventBus()
        queue = await bus.subscribe("same-sid", scope="session")

        event = {"type": "test", "data": "hello"}
        await bus.publish("same-sid", event)

        assert not queue.empty(), "Exact session scope should work"
        received = await queue.get()
        assert received == event


class TestSessionControllerChildrenVsEventBus:
    """Red flag: SessionController._children and EventBus._session_tree diverge."""

    async def test_children_tracking_works(self) -> None:
        """SessionController correctly tracks parent-child relationships."""
        mock_pool = MagicMock()
        mock_pool.main_agent.name = "test-agent"
        mock_pool.manifest.agents = {}

        controller = SessionController(mock_pool)

        # Create parent session
        parent = await controller.get_or_create_session("parent-sid")
        assert parent.session_id == "parent-sid"

        # Create child session
        child = await controller.get_or_create_session(
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

    async def test_is_descendant_always_false_for_empty_tree(self) -> None:
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

    async def test_should_receive_descendants_always_false(self) -> None:
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
        """FIXED: Full ACP handler scenario - subagent events reach parent.

        This test simulates what ACPProtocolHandler._event_consumer_loop does:
        1. Parent session subscribes with scope="descendants"
        2. Subagent (child session) runs and produces events
        3. Events are delivered to parent subscriber via SessionController
        """
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


class TestSessionPoolIntegration:
    """Red flag: SessionPool-level integration tests."""

    async def test_subagent_streaming_events_routed_to_parent(self) -> None:
        """FIXED: When SessionPool runs subagent, events reach parent.

        This is a higher-level test that mimics BackgroundTaskProvider._task_async
        behavior with SessionPool enabled.
        """
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
        # 1. create_child_session is called (updates SessionController._children)
        # 2. subagent runs and emits events via StreamEventEmitter
        # 3. StreamEventEmitter publishes to EventBus with child session_id

        # We simulate step 1 & 3:
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
        assert received["data"] == "hello"


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

        # The injection is queued, auto-resume is triggered
        # But when auto-resume runs and produces events, those events
        # would also be subject to the same _session_tree issue if this
        # were a child session


# ============================================================================
# Diagnostic helpers for manual testing
# ============================================================================

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
