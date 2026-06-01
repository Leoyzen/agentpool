"""Integration tests for SessionPool (SessionPool Group 2.13).

Tests the full facade combining SessionController + TurnRunner,
including lifecycle, feature flags, and metrics collection.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import SessionPool
from agentpool.orchestrator.metrics import MetricsCollector


pytestmark = [pytest.mark.integration, pytest.mark.anyio]


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
def mock_agent() -> MagicMock:
    """Return a mocked BaseAgent that yields a single event."""
    agent = MagicMock()
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None
    agent.get_active_run_context.side_effect = lambda: agent._active_run_ctx

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent._run_stream_once = _stream
    return agent


@pytest.fixture
def mock_agent_with_delay() -> MagicMock:
    """Return a mocked BaseAgent with a small delay."""
    agent = MagicMock()
    agent._active_run_ctx = None
    agent._current_run_ctx = None
    agent._background_run_ctx = None
    agent.get_active_run_context.side_effect = lambda: agent._active_run_ctx

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        await asyncio.sleep(0.01)
        yield RunStartedEvent(session_id=kwargs.get("session_id", "default"), run_id="run-1")

    agent._run_stream_once = _stream
    return agent


async def _attach_agent(
    pool: SessionPool,
    session_id: str,
    agent: MagicMock,
) -> None:
    """Attach a mock agent to an existing session."""
    state = await pool.sessions.get_or_create_session(session_id)
    state.agent = agent
    pool.sessions._session_agents[session_id] = agent
    pool.pool.get_agent.return_value = agent  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Full lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_create_run_turn_close_lifecycle(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """create_session → process_prompt → close_session full cycle."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    # Create session
    state = await session_pool.create_session("sess-1", agent_name="agent-a")
    assert state.session_id == "sess-1"

    # Attach mock agent so turn can run
    await _attach_agent(session_pool, "sess-1", mock_agent)

    # Subscribe to events
    queue = await session_pool.event_bus.subscribe("sess-1")

    # Run a prompt
    await session_pool.process_prompt("sess-1", "hello")
    event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event is not None
    assert isinstance(event, RunStartedEvent)

    # Close session
    await session_pool.close_session("sess-1")
    assert session_pool.sessions.get_session("sess-1") is None

    # Sentinel should have been sent
    sentinel = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert sentinel is None

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_multiple_sessions_are_isolated(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """Events from different sessions do not cross over."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    await _attach_agent(session_pool, "sess-a", mock_agent)
    await _attach_agent(session_pool, "sess-b", mock_agent)

    q_a = await session_pool.event_bus.subscribe("sess-a")
    q_b = await session_pool.event_bus.subscribe("sess-b")

    await session_pool.process_prompt("sess-a", "hello-a")
    await session_pool.process_prompt("sess-b", "hello-b")

    ev_a = await asyncio.wait_for(q_a.get(), timeout=0.5)
    ev_b = await asyncio.wait_for(q_b.get(), timeout=0.5)

    assert ev_a is not None
    assert ev_b is not None
    assert isinstance(ev_a, RunStartedEvent)
    assert isinstance(ev_b, RunStartedEvent)

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_auto_resume_disabled_runs_single_turn(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """With enable_auto_resume=False, process_prompt only runs one turn."""
    session_pool = SessionPool(mock_pool, enable_auto_resume=False)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    await session_pool.process_prompt("sess-1", "hello")
    # Only one turn should have executed
    assert len(session_pool.turns._turn_timings) == 1

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_event_bus_disabled_still_creates_bus(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """enable_event_bus=False does not prevent the bus from being used
    by the facade itself (it still exists); rather the flag is checked
    by external consumers.  Here we just verify the pool functions."""
    session_pool = SessionPool(mock_pool, enable_event_bus=False)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    queue = await session_pool.event_bus.subscribe("sess-1")
    await session_pool.process_prompt("sess-1", "hello")
    event = await asyncio.wait_for(queue.get(), timeout=0.5)
    assert event is not None

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_auto_resume_enabled_processes_injections(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """With auto-resume on, queued injections are processed in the same loop."""
    session_pool = SessionPool(mock_pool, enable_auto_resume=True)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    # Queue an injection before running
    await session_pool.inject_prompt("sess-1", "injected-msg")
    await session_pool.process_prompt("sess-1", "hello")

    # Should have run initial turn + one for injection
    assert len(session_pool.turns._turn_timings) == 2

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# shutdown cleans up all sessions
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_shutdown_closes_all_sessions(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """shutdown iterates over all active sessions and closes them."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    for sid in ("sess-1", "sess-2", "sess-3"):
        await session_pool.create_session(sid)
        state = session_pool.sessions.get_session(sid)
        assert state is not None
        state.agent = mock_agent
        session_pool.sessions._session_agents[sid] = mock_agent

    assert len(session_pool.sessions._sessions) == 3
    await session_pool.shutdown()
    assert len(session_pool.sessions._sessions) == 0


@pytest.mark.anyio
async def test_shutdown_stops_cleanup_task(
    mock_pool: MagicMock,
) -> None:
    """shutdown stops the background TTL cleanup task."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    assert session_pool.sessions._cleanup_task is not None
    await session_pool.shutdown()
    assert session_pool.sessions._cleanup_task is None


# ---------------------------------------------------------------------------
# inject_prompt / queue_prompt facade
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_inject_prompt_facade_returns_bool(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """inject_prompt on the facade delegates and returns a bool."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    # Idle session -> queued
    result = await session_pool.inject_prompt("sess-1", "msg")
    assert result is False

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_queue_prompt_facade_returns_bool(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """queue_prompt on the facade delegates and returns a bool."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    # Idle session -> stored
    result = await session_pool.queue_prompt("sess-1", "msg")
    assert result is False

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# Metrics collection
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_metrics_collector_active_sessions(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """MetricsCollector reports the correct number of active sessions."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    await session_pool.create_session("sess-1")
    await session_pool.create_session("sess-2")

    collector = MetricsCollector(session_pool)
    metrics = await collector.get_metrics()
    assert metrics.active_sessions == 2

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_metrics_collector_active_turns(
    mock_pool: MagicMock,
    mock_agent_with_delay: MagicMock,
) -> None:
    """MetricsCollector reports active turns when a turn is in progress."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent_with_delay)

    collector = MetricsCollector(session_pool)

    # Start a slow turn in background
    task = asyncio.create_task(session_pool.process_prompt("sess-1", "hello"))
    await asyncio.sleep(0.005)  # let turn start

    metrics = await collector.get_metrics()
    assert metrics.active_turns >= 1

    await task
    await session_pool.shutdown()


@pytest.mark.anyio
async def test_metrics_collector_turn_latency(
    mock_pool: MagicMock,
    mock_agent_with_delay: MagicMock,
) -> None:
    """MetricsCollector averages turn latencies correctly."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent_with_delay)

    collector = MetricsCollector(session_pool)
    await session_pool.process_prompt("sess-1", "hello")

    metrics = await collector.get_metrics()
    assert metrics.turn_latency_ms > 0

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_metrics_collector_session_lifetime(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """MetricsCollector computes average session lifetime for closed sessions."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    collector = MetricsCollector(session_pool)
    await session_pool.create_session("sess-1")
    await session_pool.close_session("sess-1")

    metrics = await collector.get_metrics()
    assert metrics.session_lifetime_seconds >= 0

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_metrics_collector_auto_resume_counter(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """MetricsCollector tracks auto-resume occurrences."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    collector = MetricsCollector(session_pool)
    collector.record_auto_resume()
    collector.record_auto_resume()

    metrics = await collector.get_metrics()
    assert metrics.auto_resume_count == 2

    await session_pool.shutdown()


@pytest.mark.anyio
async def test_metrics_collector_event_bus_depth(
    mock_pool: MagicMock,
) -> None:
    """MetricsCollector includes subscriber counts from the event bus."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    await session_pool.event_bus.subscribe("sess-a")
    await session_pool.event_bus.subscribe("sess-a")
    await session_pool.event_bus.subscribe("sess-b")

    collector = MetricsCollector(session_pool)
    metrics = await collector.get_metrics()
    assert metrics.event_bus_queue_depth == {"sess-a": 2, "sess-b": 1}

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# close_session cleans up turn state
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_close_session_cleans_turn_state(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """close_session removes post-turn injections, prompts, and locks."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    await _attach_agent(session_pool, "sess-1", mock_agent)

    # Populate turn state
    await session_pool.turns.inject_prompt("sess-1", "msg")
    await session_pool.turns.queue_prompt("sess-1", "prompt")
    assert "sess-1" in session_pool.turns._post_turn_injections
    assert "sess-1" in session_pool.turns._post_turn_prompts
    assert "sess-1" in session_pool.turns._injection_locks

    await session_pool.close_session("sess-1")
    assert "sess-1" not in session_pool.turns._post_turn_injections
    assert "sess-1" not in session_pool.turns._post_turn_prompts
    assert "sess-1" not in session_pool.turns._injection_locks

    await session_pool.shutdown()
