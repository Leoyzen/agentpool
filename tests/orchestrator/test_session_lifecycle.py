"""Tests for SessionPool session lifecycle, close semantics, and error propagation.

Consolidated from:
- test_session_pool.py (SessionLifecyclePolicy, SessionState parent/child, EventBus scopes)
- test_close_session.py (close_session wait/cancel/race semantics)
- test_error_propagation.py (RunFailedEvent via TurnRunner and receive_request)
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import anyio

import pytest

from agentpool.agents.events import RunFailedEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.messaging.messages import ChatMessage
from agentpool.orchestrator.core import (
    EventBus,
    SessionController,
    SessionLifecyclePolicy,
    SessionPool,
    SessionState,
    TurnRunner,
)
from agentpool.orchestrator.run import RunHandle

if TYPE_CHECKING:
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
        # Yield at least one event so TurnRunner doesn't hang
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


@pytest.fixture
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner with auto-resume disabled."""
    return TurnRunner(session_controller=controller, enable_auto_resume=False)


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
    def test_default_is_cascade(self) -> None:
        assert SessionLifecyclePolicy.default() == "cascade"

    def test_valid_policies(self) -> None:
        assert SessionLifecyclePolicy.is_valid("independent")
        assert SessionLifecyclePolicy.is_valid("cascade")
        assert SessionLifecyclePolicy.is_valid("bound")
        assert not SessionLifecyclePolicy.is_valid("invalid")


class TestSessionStateParentChild:
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
    @pytest.mark.anyio
    async def test_creates_child_session(self) -> None:
        ctrl = SessionController(pool=MagicMock())
        parent, _ = await ctrl.get_or_create_session("parent1")
        child, _ = await ctrl.get_or_create_session(
            "child1", parent_session_id="parent1"
        )
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
    @pytest.mark.anyio
    async def test_session_scope_receives_own_events(self) -> None:
        bus = EventBus()
        queue = await bus.subscribe("s1", scope="session")
        await bus.publish("s1", "event1")
        envelope = await asyncio.wait_for(queue.receive(), timeout=1.0)
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
            await asyncio.wait_for(queue.receive(), timeout=0.5)

    @pytest.mark.anyio
    async def test_descendants_scope_receives_child_events(self) -> None:
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1"], "s1.1": []}
        queue = await bus.subscribe("s1", scope="descendants")
        await bus.publish("s1.1", "event1")
        envelope = await asyncio.wait_for(queue.receive(), timeout=1.0)
        assert envelope is not None
        assert envelope.event == "event1"

    @pytest.mark.anyio
    async def test_subtree_scope_receives_sibling_events(self) -> None:
        bus = EventBus()
        bus._session_tree = {"s1": ["s1.1", "s1.2"], "s1.1": [], "s1.2": []}
        queue = await bus.subscribe("s1.1", scope="subtree")
        await bus.publish("s1.2", "event1")
        envelope = await asyncio.wait_for(queue.receive(), timeout=1.0)
        assert envelope is not None
        assert envelope.event == "event1"


# ============================================================================
# Close session semantics
# ============================================================================


@pytest.mark.anyio
async def test_close_session_waits_for_run_to_complete(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session waits for the active run to finish before proceeding."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-1", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-1", agent, mock_pool)

    # Start a run via receive_request so a RunHandle is created
    await session_pool.sessions.receive_request("sess-1", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Session should have an active run
    session = session_pool.sessions.get_session("sess-1")
    assert session is not None
    assert session.current_run_id is not None
    run_handle = session_pool.sessions._runs.get(session.current_run_id)
    assert run_handle is not None

    # close_session should wait for the run to complete
    close_task = asyncio.create_task(session_pool.close_session("sess-1"))

    # Give close_session time to start waiting
    await asyncio.sleep(0.05)
    assert not close_task.done(), "close_session should be waiting for run"

    # Let the stream finish
    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)

    # Session should be closed
    assert session_pool.sessions.get_session("sess-1") is None


@pytest.mark.anyio
async def test_close_session_sets_closing_before_wait(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session sets session.closing=True before waiting for the run."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-2", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-2", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-2", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    close_task = asyncio.create_task(session_pool.close_session("sess-2"))
    await asyncio.sleep(0.05)

    # Session should still exist (close_session is waiting)
    session = session_pool.sessions.get_session("sess-2")
    assert session is not None
    assert session.closing is True

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)


@pytest.mark.anyio
async def test_close_session_cancels_on_timeout(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """If run doesn't complete within timeout, close_session cancels it."""
    stream_started = asyncio.Event()

    async def very_slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await asyncio.sleep(60)
        yield RunStartedEvent(session_id="sess-3", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = very_slow_stream

    await _setup_session(session_pool.sessions, "sess-3", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-3", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Patch close_session's timeout to be very short for testing

    async def fast_close(session_id: str) -> None:
        session = session_pool.sessions.get_session(session_id)
        run_handle: RunHandle | None = None
        if session is not None:
            async with session._request_lock:
                session.closing = True
                run_id = session.current_run_id
                if run_id is not None:
                    run_handle = session_pool.sessions._runs.get(run_id)

            if run_handle is not None:
                try:
                    await asyncio.wait_for(
                        run_handle.complete_event.wait(), timeout=0.1
                    )
                except (asyncio.TimeoutError, anyio.EndOfStream):
                    session_pool.cancel_run(run_handle.run_id)
                    # Give cancellation a moment to propagate and release turn_lock
                    await asyncio.sleep(0.1)

        await session_pool.sessions.close_session(session_id)
        await session_pool.event_bus.close_session(session_id)
        has_turn_state = (
            session_id in session_pool.turns._post_turn_injections
            or session_id in session_pool.turns._post_turn_prompts
            or session_id in session_pool.turns._injection_locks
        )
        if has_turn_state:
            lock = await session_pool.turns._get_injection_lock(session_id)
            async with lock:
                session_pool.turns._post_turn_injections.pop(session_id, None)
                session_pool.turns._post_turn_prompts.pop(session_id, None)
                session_pool.turns._injection_locks.pop(session_id, None)

    session_pool.close_session = fast_close  # type: ignore[method-assign]

    # Patch cancel_run to verify it's called
    cancelled_runs: list[str] = []
    original_cancel = session_pool.cancel_run

    def _spy_cancel(run_id: str) -> None:
        cancelled_runs.append(run_id)
        original_cancel(run_id)

    session_pool.cancel_run = _spy_cancel  # type: ignore[method-assign]

    close_task = asyncio.create_task(session_pool.close_session("sess-3"))
    await asyncio.wait_for(close_task, timeout=2.0)

    assert len(cancelled_runs) == 1


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
async def test_close_session_run_completes_before_wait(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session is fast when run already completed."""
    agent = MockAgent()

    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id="sess-5", run_id="run-1")

    agent._stream_impl = quick_stream
    await _setup_session(session_pool.sessions, "sess-5", agent, mock_pool)

    # Run via receive_request
    await session_pool.sessions.receive_request("sess-5", "hello", priority="when_idle")
    await asyncio.sleep(0.1)  # Let it complete

    # close_session should proceed without waiting
    await session_pool.close_session("sess-5")
    assert session_pool.sessions.get_session("sess-5") is None


@pytest.mark.anyio
async def test_receive_request_rejected_after_close_starts(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """receive_request rejects new requests once close_session sets closing=True."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-6", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-6", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-6", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Start closing (but don't let it finish yet)
    close_task = asyncio.create_task(session_pool.close_session("sess-6"))
    await asyncio.sleep(0.05)

    # Try to send a new request - should be rejected
    await session_pool.receive_request("sess-6", "late message")

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)


@pytest.mark.anyio
async def test_process_prompt_rejected_after_close_starts(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """process_prompt rejects new requests once close_session sets closing=True."""
    stream_started = asyncio.Event()
    stream_continue = asyncio.Event()

    async def slow_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        stream_started.set()
        await stream_continue.wait()
        yield RunStartedEvent(session_id="sess-7", run_id="run-1")

    agent = MockAgent()
    agent._stream_impl = slow_stream

    await _setup_session(session_pool.sessions, "sess-7", agent, mock_pool)

    await session_pool.sessions.receive_request("sess-7", "hello", priority="when_idle")
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    close_task = asyncio.create_task(session_pool.close_session("sess-7"))
    await asyncio.sleep(0.05)

    # process_prompt delegates to receive_request, which should reject
    await session_pool.process_prompt("sess-7", "late message")

    stream_continue.set()
    await asyncio.wait_for(close_task, timeout=2.0)


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


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_run_failed_event_published_on_turn_exception(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """When _run_stream_once raises, RunFailedEvent is published to EventBus."""
    agent = MockAgent()

    async def broken_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        raise RuntimeError("native agent boom")

    agent._stream_impl = broken_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # Manually create a RunHandle so the exception handler can publish via it
    run_handle = controller._create_run("sess-1", "hello")
    controller._runs[run_handle.run_id] = run_handle
    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = run_handle.run_id

    # Subscribe to EventBus before running
    event_queue = await turn_runner.event_bus.subscribe("sess-1")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                event = await asyncio.wait_for(event_queue.receive(), timeout=0.5)
                events.append(event)
        except (asyncio.TimeoutError, anyio.EndOfStream):
            pass

    consumer = asyncio.create_task(_consume())

    # run_turn should NOT swallow the exception
    with pytest.raises(RuntimeError, match="native agent boom"):
        await turn_runner.run_turn("sess-1", "hello")

    # Give the EventBus a moment to deliver
    await asyncio.sleep(0.05)
    await turn_runner.event_bus.publish("sess-1", None)
    await consumer

    failed_events = [e for e in events if isinstance(getattr(e, 'event', e), RunFailedEvent)]
    assert len(failed_events) == 1, (
        f"Expected 1 RunFailedEvent, got {len(failed_events)} "
        f"(total events: {len(events)})"
    )
    failed = failed_events[0].event
    assert failed.session_id == "sess-1"
    assert isinstance(failed.exception, RuntimeError)
    assert str(failed.exception) == "native agent boom"
    assert failed.run_id is not None


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_run_failed_event_includes_run_id(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """RunFailedEvent carries the same run_id as the active run."""
    agent = MockAgent()

    async def broken_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        raise ValueError("boom")

    agent._stream_impl = broken_stream

    await _setup_session(controller, "sess-1", agent, mock_pool)

    # Manually create a RunHandle so we can track the run_id
    run_handle = controller._create_run("sess-1", "hello")
    controller._runs[run_handle.run_id] = run_handle
    session = controller.get_session("sess-1")
    assert session is not None
    session.current_run_id = run_handle.run_id

    event_queue = await turn_runner.event_bus.subscribe("sess-1")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                event = await asyncio.wait_for(event_queue.receive(), timeout=0.5)
                events.append(event)
        except (asyncio.TimeoutError, anyio.EndOfStream):
            pass

    consumer = asyncio.create_task(_consume())

    with pytest.raises(ValueError, match="boom"):
        await turn_runner.run_turn("sess-1", "hello")

    await asyncio.sleep(0.05)
    await turn_runner.event_bus.publish("sess-1", None)
    await consumer

    failed_events = [e for e in events if isinstance(getattr(e, 'event', e), RunFailedEvent)]
    assert len(failed_events) == 1
    assert failed_events[0].event.run_id == run_handle.run_id


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_run_failed_event_published_via_receive_request(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """When receive_request's background task fails, RunFailedEvent is published."""
    tr = TurnRunner(session_controller=controller, enable_auto_resume=False)
    controller._turn_runner = tr

    agent = MockAgent()

    async def broken_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        raise RuntimeError("receive_request boom")

    agent._stream_impl = broken_stream

    await _setup_session(controller, "sess-2", agent, mock_pool)

    event_queue = await tr.event_bus.subscribe("sess-2")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                event = await asyncio.wait_for(event_queue.receive(), timeout=1.0)
                events.append(event)
        except (asyncio.TimeoutError, anyio.EndOfStream):
            pass

    consumer = asyncio.create_task(_consume())

    # receive_request starts a background task; wait for it to finish
    await controller.receive_request("sess-2", "hello", priority="when_idle")
    await asyncio.sleep(0.1)

    await tr.event_bus.publish("sess-2", None)
    await consumer

    failed_events = [e for e in events if isinstance(getattr(e, 'event', e), RunFailedEvent)]
    assert len(failed_events) == 1, (
        f"Expected 1 RunFailedEvent via receive_request, got {len(failed_events)}"
    )
    failed = failed_events[0].event
    assert failed.session_id == "sess-2"
    assert isinstance(failed.exception, RuntimeError)
    assert str(failed.exception) == "receive_request boom"


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_process_prompt_uses_legacy_path(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """process_prompt uses the legacy blocking path for backward compatibility."""
    agent = MockAgent()

    async def ok_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id="sess-3", run_id="run-1")

    agent._stream_impl = ok_stream
    await _setup_session(session_pool.sessions, "sess-3", agent, mock_pool)

    # process_prompt should block until completion using legacy path
    await session_pool.process_prompt("sess-3", "hello")

    # If we get here without error, the legacy path worked
    assert session_pool.sessions.get_session("sess-3") is not None


@pytest.mark.deprecated
@pytest.mark.anyio
async def test_process_prompt_fallback_with_kwargs(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """process_prompt with kwargs falls back to the legacy direct path."""
    agent = MockAgent()

    async def ok_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id="sess-4", run_id="run-1")

    agent._stream_impl = ok_stream
    await _setup_session(session_pool.sessions, "sess-4", agent, mock_pool)

    # When kwargs are passed, it should go through the legacy path
    await session_pool.process_prompt("sess-4", "hello", extra_kwarg=True)
    # Should complete without error


# ============================================================================
# close_session background task unblock
# ============================================================================


@pytest.mark.anyio
async def test_close_session_unblocks_background_task_wait(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session sets cancelled + child_done_events so the run finishes promptly.

    Given: A run with a pending child done event (child_done_events["test"] unset).
    When:  close_session() is called during the wait.
    Then:  close_session returns in < 5s (not 30s) and StreamCompleteEvent(cancelled=True) is published.
    """
    stream_started = asyncio.Event()

    async def stream_with_bg_wait(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[StreamCompleteEvent]:
        stream_started.set()
        # Simulate a pending child done event
        run_ctx.child_done_events["test"] = anyio.Event()
        # Wait for child done event (or be unblocked by close_session)
        await asyncio.wait_for(
            run_ctx.child_done_events["test"].wait(), timeout=10.0
        )
        yield StreamCompleteEvent(
            message=ChatMessage(content="done", role="assistant"),
            cancelled=run_ctx.cancelled,
            session_id="sess-bg-1",
        )

    agent = MockAgent()
    agent._stream_impl = stream_with_bg_wait

    await _setup_session(session_pool.sessions, "sess-bg-1", agent, mock_pool)

    # Subscribe to event bus before starting the run
    event_queue = await session_pool.event_bus.subscribe("sess-bg-1")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                envelope = await asyncio.wait_for(
                    event_queue.receive(), timeout=2.0
                )
                events.append(envelope)
        except (asyncio.TimeoutError, anyio.EndOfStream):
            pass

    consumer = asyncio.create_task(_consume())

    # Start a run
    await session_pool.sessions.receive_request(
        "sess-bg-1", "hello", priority="when_idle"
    )
    await asyncio.wait_for(stream_started.wait(), timeout=1.0)

    # Call close_session — should unblock the child done event wait
    # This should complete in well under 30s because we set all child_done_events
    await asyncio.wait_for(session_pool.close_session("sess-bg-1"), timeout=5.0)

    # Wait for consumer to finish (gets EndOfStream from event_bus.close_session)
    await asyncio.wait_for(consumer, timeout=3.0)

    # Assert StreamCompleteEvent(cancelled=True) was published
    complete_events = [
        e
        for e in events
        if isinstance(getattr(e, "event", e), StreamCompleteEvent)
    ]
    assert len(complete_events) >= 1, (
        f"Expected at least 1 StreamCompleteEvent, got {len(complete_events)} "
        f"(total events: {len(events)})"
    )
    assert complete_events[0].event.cancelled is True


@pytest.mark.anyio
async def test_close_session_no_pending_background_tasks(
    session_pool: SessionPool,
    mock_pool: MagicMock,
) -> None:
    """close_session works normally when no child done events are pending.

    Given: A run that completes with no pending child done events.
    When:  close_session() is called after the run completes.
    Then:  close_session returns promptly and the session is closed (no regression).
    """
    agent = MockAgent()

    async def quick_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[StreamCompleteEvent]:
        yield StreamCompleteEvent(
            message=ChatMessage(content="done", role="assistant"),
            cancelled=False,
            session_id="sess-bg-2",
        )

    agent._stream_impl = quick_stream
    await _setup_session(session_pool.sessions, "sess-bg-2", agent, mock_pool)

    # Start a run and let it complete
    await session_pool.sessions.receive_request(
        "sess-bg-2", "hello", priority="when_idle"
    )
    await asyncio.sleep(0.1)  # Let it complete

    # close_session should proceed without waiting (no pending child done events)
    await asyncio.wait_for(session_pool.close_session("sess-bg-2"), timeout=5.0)

    # Session should be closed
    assert session_pool.sessions.get_session("sess-bg-2") is None
