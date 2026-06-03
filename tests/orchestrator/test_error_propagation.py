"""Tests for error propagation via EventBus.

Tests that RunFailedEvent is published when runs fail,
for both native-agent (TurnRunner) and receive_request paths.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import inspect
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.events import RunFailedEvent, RunStartedEvent
from agentpool.orchestrator.core import SessionController, SessionPool, TurnRunner

if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


pytestmark = pytest.mark.unit


class MockAgent:
    """Simple mock agent for testing."""

    def __init__(self) -> None:
        self._stream_impl: Any = None
        self.get_active_run_context = MagicMock(return_value=None)

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


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


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
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a real SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner with auto-resume disabled."""
    return TurnRunner(session_controller=controller, enable_auto_resume=False)


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    return SessionPool(pool=mock_pool, enable_auto_resume=False)


async def _setup_session(
    controller: SessionController,
    session_id: str,
    agent: MockAgent,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent directly."""
    state = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


# ---------------------------------------------------------------------------
# TurnRunner path (_run_turn_unlocked)
# ---------------------------------------------------------------------------


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
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is None:
                    break
                events.append(event)
        except TimeoutError:
            pass

    consumer = asyncio.create_task(_consume())

    # run_turn should NOT swallow the exception
    with pytest.raises(RuntimeError, match="native agent boom"):
        await turn_runner.run_turn("sess-1", "hello")

    # Give the EventBus a moment to deliver
    await asyncio.sleep(0.05)
    await turn_runner.event_bus.publish("sess-1", None)
    await consumer

    failed_events = [e for e in events if isinstance(e, RunFailedEvent)]
    assert len(failed_events) == 1, (
        f"Expected 1 RunFailedEvent, got {len(failed_events)} "
        f"(total events: {len(events)})"
    )
    assert failed_events[0].session_id == "sess-1"
    assert isinstance(failed_events[0].exception, RuntimeError)
    assert str(failed_events[0].exception) == "native agent boom"
    assert failed_events[0].run_id is not None


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
                event = await asyncio.wait_for(event_queue.get(), timeout=0.5)
                if event is None:
                    break
                events.append(event)
        except TimeoutError:
            pass

    consumer = asyncio.create_task(_consume())

    with pytest.raises(ValueError, match="boom"):
        await turn_runner.run_turn("sess-1", "hello")

    await asyncio.sleep(0.05)
    await turn_runner.event_bus.publish("sess-1", None)
    await consumer

    failed_events = [e for e in events if isinstance(e, RunFailedEvent)]
    assert len(failed_events) == 1
    assert failed_events[0].run_id == run_handle.run_id


# ---------------------------------------------------------------------------
# receive_request path (SessionController)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_failed_event_published_via_receive_request(
    controller: SessionController,
    mock_pool: MagicMock,
) -> None:
    """When receive_request's background task fails, RunFailedEvent is published."""
    turn_runner = TurnRunner(session_controller=controller, enable_auto_resume=False)
    controller._turn_runner = turn_runner

    agent = MockAgent()

    async def broken_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        raise RuntimeError("receive_request boom")

    agent._stream_impl = broken_stream

    await _setup_session(controller, "sess-2", agent, mock_pool)

    event_queue = await turn_runner.event_bus.subscribe("sess-2")
    events: list[Any] = []

    async def _consume() -> None:
        try:
            while True:
                event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                if event is None:
                    break
                events.append(event)
        except TimeoutError:
            pass

    consumer = asyncio.create_task(_consume())

    # receive_request starts a background task; wait for it to finish
    await controller.receive_request("sess-2", "hello", priority="when_idle")
    await asyncio.sleep(0.1)

    await turn_runner.event_bus.publish("sess-2", None)
    await consumer

    failed_events = [e for e in events if isinstance(e, RunFailedEvent)]
    assert len(failed_events) == 1, (
        f"Expected 1 RunFailedEvent via receive_request, got {len(failed_events)}"
    )
    assert failed_events[0].session_id == "sess-2"
    assert isinstance(failed_events[0].exception, RuntimeError)
    assert str(failed_events[0].exception) == "receive_request boom"


# ---------------------------------------------------------------------------
# SessionPool.process_prompt delegation
# ---------------------------------------------------------------------------


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
