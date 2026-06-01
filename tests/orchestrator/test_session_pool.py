"""Tests for SessionPool unified session lifecycle and EventBus scoped subscriptions."""

from __future__ import annotations

import asyncio

import pytest

from agentpool.orchestrator.core import (
    EventBus,
    SessionController,
    SessionLifecyclePolicy,
    SessionPool,
    SessionState,
)


class MockAgent:
    def __init__(self):
        self.name = "test_agent"


class MockPool:
    def __init__(self):
        self.main_agent = MockAgent()


class TestSessionLifecyclePolicy:
    def test_default_is_cascade(self):
        assert SessionLifecyclePolicy.default() == "cascade"

    def test_valid_policies(self):
        assert SessionLifecyclePolicy.is_valid("independent")
        assert SessionLifecyclePolicy.is_valid("cascade")
        assert SessionLifecyclePolicy.is_valid("bound")
        assert not SessionLifecyclePolicy.is_valid("invalid")


class TestSessionStateParentChild:
    def test_session_state_has_parent_and_policy(self):
        state = SessionState(
            session_id="s1",
            agent_name="test",
            parent_session_id="parent1",
            lifecycle_policy="independent",
        )
        assert state.parent_session_id == "parent1"
        assert state.lifecycle_policy == "independent"

    def test_session_state_defaults(self):
        state = SessionState(session_id="s1", agent_name="test")
        assert state.parent_session_id is None
        assert state.lifecycle_policy == "cascade"


class TestSessionControllerParentChild:
    @pytest.mark.anyio
    async def test_creates_child_session(self):
        # Create a mock pool
        controller = SessionController(pool=MockPool())
        parent = await controller.get_or_create_session("parent1")
        child = await controller.get_or_create_session(
            "child1", parent_session_id="parent1"
        )
        assert child.parent_session_id == "parent1"
        assert controller.get_children("parent1") == ["child1"]
        assert controller.get_parent("child1") == parent

    @pytest.mark.anyio
    async def test_close_session_cascade_closes_children(self):
        controller = SessionController(pool=MockPool())
        await controller.get_or_create_session("parent1")
        await controller.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="cascade"
        )
        await controller.close_session("parent1")
        assert controller.get_session("parent1") is None
        assert controller.get_session("child1") is None

    @pytest.mark.anyio
    async def test_close_session_independent_preserves_children(self):
        controller = SessionController(pool=MockPool())
        await controller.get_or_create_session("parent1")
        await controller.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="independent"
        )
        await controller.close_session("parent1")
        assert controller.get_session("parent1") is None
        assert controller.get_session("child1") is not None

    @pytest.mark.anyio
    async def test_lifecycle_policy_cascade_closes_children(self):
        controller = SessionController(pool=MockPool())
        await controller.get_or_create_session("parent1")
        await controller.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="cascade"
        )
        await controller.close_session("parent1")
        assert controller.get_session("parent1") is None
        assert controller.get_session("child1") is None

    @pytest.mark.anyio
    async def test_lifecycle_policy_independent_preserves_children(self):
        controller = SessionController(pool=MockPool())
        await controller.get_or_create_session("parent1")
        await controller.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="independent"
        )
        await controller.close_session("parent1")
        assert controller.get_session("parent1") is None
        assert controller.get_session("child1") is not None

    @pytest.mark.anyio
    async def test_lifecycle_policy_bound_closes_child_immediately(self):
        controller = SessionController(pool=MockPool())
        await controller.get_or_create_session("parent1")
        await controller.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="bound"
        )
        await controller.close_session("parent1")
        assert controller.get_session("parent1") is None
        assert controller.get_session("child1") is None


class TestEventBusScopedSubscription:
    @pytest.mark.anyio
    async def test_session_scope_receives_own_events(self):
        bus = EventBus()
        queue = await bus.subscribe("s1", scope="session")
        await bus.publish("s1", "event1")
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == "event1"

    @pytest.mark.anyio
    async def test_session_scope_excludes_child_events(self):
        bus = EventBus()
        # Manually set up tree: s1 -> s1.1
        bus._session_tree = {"s1": ["s1.1"], "s1.1": []}
        queue = await bus.subscribe("s1", scope="session")
        await bus.publish("s1.1", "event1")
        # Should NOT receive - queue should be empty
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.5)

    @pytest.mark.anyio
    async def test_descendants_scope_receives_child_events(self):
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1"], "s1.1": []}
        queue = await bus.subscribe("s1", scope="descendants")
        await bus.publish("s1.1", "event1")
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == "event1"

    @pytest.mark.anyio
    async def test_subtree_scope_receives_sibling_events(self):
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1", "s1.2"], "s1.1": [], "s1.2": []}
        queue = await bus.subscribe("s1.1", scope="subtree")
        await bus.publish("s1.2", "event1")
        assert await asyncio.wait_for(queue.get(), timeout=1.0) == "event1"
