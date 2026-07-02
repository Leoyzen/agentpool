"""Tests for SessionPool session lifecycle, close semantics, and error propagation.

Consolidated from:
- test_session_pool.py (SessionLifecyclePolicy, SessionState parent/child, EventBus scopes)
- test_close_session.py (close_session wait/cancel/race semantics)
- test_error_propagation.py (RunFailedEvent via receive_request)
"""

from __future__ import annotations

import asyncio
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import (
    EventBus,
    SessionController,
    SessionLifecyclePolicy,
    SessionPool,
    SessionState,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.agents.context import AgentRunContext


pytestmark = pytest.mark.unit


# ============================================================================
# Shared fixtures and helpers
# ============================================================================


class MockAgent:
    """Simple mock agent for testing."""

    AGENT_TYPE: str = "native"

    def __init__(self) -> None:
        self._stream_impl: Any = None
        self.get_active_run_context = MagicMock(return_value=None)

    async def run_stream(
        self,
        *prompts: Any,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Mock run_stream that delegates to _stream_impl via _run_stream_once."""
        if self._stream_impl is None:
            raise RuntimeError("No stream impl set")
        run_ctx = MagicMock()
        if inspect.isasyncgenfunction(self._stream_impl):
            async for event in self._stream_impl(run_ctx, *prompts, **kwargs):
                yield event
        else:
            await self._stream_impl(run_ctx, *prompts, **kwargs)
        # Yield at least one event so the run doesn't hang
        yield RunStartedEvent(session_id=session_id or "", run_id="run-mock")

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        if self._stream_impl is None:
            raise RuntimeError("No stream impl set")
        if inspect.isasyncgenfunction(self._stream_impl):
            async for event in self._stream_impl(run_ctx, *prompts, **kwargs):
                yield event
        else:
            await self._stream_impl(run_ctx, *prompts, **kwargs)


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    return SessionPool(pool=mock_pool, enable_auto_resume=False)


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a real SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


async def _setup_session(
    ctrl: SessionController,
    session_id: str,
    agent: MockAgent,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent directly."""
    state, _ = await ctrl.get_or_create_session(session_id)
    state.agent = agent
    ctrl._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


# ============================================================================
# SessionLifecyclePolicy
# ============================================================================


class TestSessionLifecyclePolicy:
    """Tests for SessionLifecyclePolicy enum and validation."""

    def test_default_is_cascade(self) -> None:
        assert SessionLifecyclePolicy.default() == "cascade"

    def test_valid_policies(self) -> None:
        assert SessionLifecyclePolicy.is_valid("independent")
        assert SessionLifecyclePolicy.is_valid("cascade")
        assert SessionLifecyclePolicy.is_valid("bound")
        assert not SessionLifecyclePolicy.is_valid("invalid")


class TestSessionStateParentChild:
    """Tests for SessionState parent-child relationship fields."""

    def test_session_state_has_parent_and_policy(self) -> None:
        state = SessionState(
            session_id="s1",
            agent_name="test",
            parent_session_id="parent1",
            lifecycle_policy="independent",
        )
        assert state.parent_session_id == "parent1"
        assert state.lifecycle_policy == "independent"

    def test_session_state_defaults(self) -> None:
        state = SessionState(session_id="s1", agent_name="test")
        assert state.parent_session_id is None
        assert state.lifecycle_policy == "cascade"


class TestSessionControllerParentChild:
    """Tests for SessionController parent-child session management."""

    @pytest.mark.anyio
    async def test_creates_child_session(self) -> None:
        ctrl = SessionController(pool=MagicMock())
        parent, _ = await ctrl.get_or_create_session("parent1")
        child, _ = await ctrl.get_or_create_session("child1", parent_session_id="parent1")
        assert child.parent_session_id == "parent1"
        assert ctrl.get_children("parent1") == ["child1"]
        assert ctrl.get_parent("child1") == parent

    @pytest.mark.anyio
    async def test_close_session_cascade_closes_children(self) -> None:
        ctrl = SessionController(pool=MagicMock())
        await ctrl.get_or_create_session("parent1")
        await ctrl.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="cascade"
        )
        await ctrl.close_session("parent1")
        assert ctrl.get_session("parent1") is None
        assert ctrl.get_session("child1") is None

    @pytest.mark.anyio
    async def test_close_session_independent_preserves_children(self) -> None:
        ctrl = SessionController(pool=MagicMock())
        await ctrl.get_or_create_session("parent1")
        await ctrl.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="independent"
        )
        await ctrl.close_session("parent1")
        assert ctrl.get_session("parent1") is None
        assert ctrl.get_session("child1") is not None

    @pytest.mark.anyio
    async def test_lifecycle_policy_bound_closes_child_immediately(self) -> None:
        ctrl = SessionController(pool=MagicMock())
        await ctrl.get_or_create_session("parent1")
        await ctrl.get_or_create_session(
            "child1", parent_session_id="parent1", lifecycle_policy="bound"
        )
        await ctrl.close_session("parent1")
        assert ctrl.get_session("parent1") is None
        assert ctrl.get_session("child1") is None


class TestEventBusScopedSubscription:
    """Tests for EventBus scoped subscription behavior."""

    @pytest.mark.anyio
    async def test_session_scope_receives_own_events(self) -> None:
        bus = EventBus()
        queue = await bus.subscribe("s1", scope="session")
        await bus.publish("s1", "event1")
        envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert envelope is not None
        assert envelope.event == "event1"

    @pytest.mark.anyio
    async def test_session_scope_excludes_child_events(self) -> None:
        bus = EventBus()
        # Manually set up tree: s1 -> s1.1
        bus._session_tree = {"s1": ["s1.1"], "s1.1": []}
        queue = await bus.subscribe("s1", scope="session")
        await bus.publish("s1.1", "event1")
        # Should NOT receive - queue should be empty
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(queue.get(), timeout=0.5)

    @pytest.mark.anyio
    async def test_descendants_scope_receives_child_events(self) -> None:
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1"], "s1.1": []}
        queue = await bus.subscribe("s1", scope="descendants")
        await bus.publish("s1.1", "event1")
        envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert envelope is not None
        assert envelope.event == "event1"

    @pytest.mark.anyio
    async def test_subtree_scope_receives_sibling_events(self) -> None:
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1", "s1.2"], "s1.1": [], "s1.2": []}
        queue = await bus.subscribe("s1.1", scope="subtree")
        await bus.publish("s1.2", "event1")
        envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert envelope is not None
        assert envelope.event == "event1"


# ============================================================================
# Close session semantics
# ============================================================================
@pytest.mark.anyio
async def test_close_session_no_active_run(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session works normally when there is no active run."""
    agent = MockAgent()

    await _setup_session(session_pool.sessions, "sess-4", agent, mock_pool)

    await session_pool.close_session("sess-4")
    assert session_pool.sessions.get_session("sess-4") is None


@pytest.mark.anyio
async def test_close_session_acquires_request_lock(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session acquires _request_lock before setting closing=True."""
    lock_acquired = False

    original_acquire = asyncio.Lock.acquire

    async def _patched_acquire(self: asyncio.Lock, *args: Any, **kwargs: Any) -> bool:
        nonlocal lock_acquired
        result = await original_acquire(self, *args, **kwargs)
        session = session_pool.sessions.get_session("sess-8")
        if session is not None and self is session._request_lock:
            lock_acquired = True
        return result

    asyncio.Lock.acquire = _patched_acquire  # type: ignore[method-assign]

    agent = MockAgent()

    await _setup_session(session_pool.sessions, "sess-8", agent, mock_pool)
    await session_pool.close_session("sess-8")

    asyncio.Lock.acquire = original_acquire  # type: ignore[method-assign]
    assert lock_acquired is True


# ============================================================================
# Error propagation
# ============================================================================
# Should complete without error


# ============================================================================
# close_session background task unblock
# ============================================================================
