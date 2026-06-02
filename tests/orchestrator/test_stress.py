"""Stress tests for SessionPool orchestration (Group 6.1-6.4).

Validates system behavior under extreme load including:
- 1000+ concurrent sessions
- Rapid session create/close cycles
- EventBus bounded queue behavior at capacity
- TurnRunner injection queue overflow handling

All tests use mocked agents (no real LLM calls).
"""

from __future__ import annotations

import asyncio
import gc
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus, SessionController, SessionPool, TurnRunner


pytestmark = [pytest.mark.slow, pytest.mark.anyio]


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
    """Return a mocked BaseAgent that yields a single event instantly."""
    agent = MagicMock()

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
    """Return a mocked BaseAgent with a small per-event delay."""
    agent = MagicMock()

    async def _stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RunStartedEvent]:
        await asyncio.sleep(0.001)
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
# 6.1 Stress test: 1000+ concurrent sessions
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_1000_concurrent_sessions(mock_pool: MagicMock) -> None:
    """Create 1000 sessions concurrently and verify no resource leaks."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_count = 1000

    async def create_session(i: int) -> str:
        sid = f"sess-{i}"
        await session_pool.create_session(sid, agent_name="agent-a")
        return sid

    # Concurrent creation
    created = await asyncio.gather(*[create_session(i) for i in range(session_count)])
    assert len(created) == session_count
    assert len(session_pool.sessions._sessions) == session_count

    # Verify each session is individually accessible
    for sid in created:
        state = session_pool.sessions.get_session(sid)
        assert state is not None
        assert state.session_id == sid

    # Close all concurrently
    await asyncio.gather(*[session_pool.close_session(sid) for sid in created])
    assert len(session_pool.sessions._sessions) == 0

    # Verify event bus has no lingering subscribers
    counts = await session_pool.event_bus.get_subscriber_counts()
    assert counts == {}

    await session_pool.shutdown()


@pytest.mark.slow
async def test_1000_concurrent_sessions_with_agents(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """Create 1000 sessions with attached agents and run a turn on each."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_count = 1000

    # Create and attach agents
    for i in range(session_count):
        sid = f"sess-{i}"
        await _attach_agent(session_pool, sid, mock_agent)

    # Subscribe to all sessions
    queues: dict[str, asyncio.Queue] = {}
    for i in range(session_count):
        sid = f"sess-{i}"
        queues[sid] = await session_pool.event_bus.subscribe(sid)

    # Run a turn on each session concurrently
    async def run_turn(i: int) -> None:
        sid = f"sess-{i}"
        await session_pool.process_prompt(sid, "hello")

    await asyncio.gather(*[run_turn(i) for i in range(session_count)])

    # Verify each session received exactly one event
    for i in range(session_count):
        sid = f"sess-{i}"
        queue = queues[sid]
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event is not None
        assert isinstance(event, RunStartedEvent)

    # Close all
    await asyncio.gather(*[session_pool.close_session(f"sess-{i}") for i in range(session_count)])
    assert len(session_pool.sessions._sessions) == 0

    # Verify no leaked locks or injection state
    assert len(session_pool.turns._injection_locks) == 0
    assert len(session_pool.turns._post_turn_injections) == 0
    assert len(session_pool.turns._post_turn_prompts) == 0

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.2 Stress test: rapid session create/close cycles
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_rapid_create_close_cycles(mock_pool: MagicMock) -> None:
    """Repeatedly create and close sessions to verify stability."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    cycles = 200
    for i in range(cycles):
        sid = f"cycle-{i}"
        await session_pool.create_session(sid)
        assert session_pool.sessions.get_session(sid) is not None
        await session_pool.close_session(sid)
        assert session_pool.sessions.get_session(sid) is None

    # After all cycles, state should be clean
    assert len(session_pool.sessions._sessions) == 0
    counts = await session_pool.event_bus.get_subscriber_counts()
    assert counts == {}

    await session_pool.shutdown()


@pytest.mark.slow
async def test_rapid_create_close_cycles_with_turns(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """Create, run a turn, and close sessions in rapid succession."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    cycles = 100
    for i in range(cycles):
        sid = f"cycle-{i}"
        await _attach_agent(session_pool, sid, mock_agent)
        queue = await session_pool.event_bus.subscribe(sid)
        await session_pool.process_prompt(sid, "hello")
        event = await asyncio.wait_for(queue.get(), timeout=1.0)
        assert event is not None
        await session_pool.close_session(sid)
        assert session_pool.sessions.get_session(sid) is None

    assert len(session_pool.sessions._sessions) == 0
    await session_pool.shutdown()


@pytest.mark.slow
async def test_rapid_create_close_memory_stable(mock_pool: MagicMock) -> None:
    """Memory usage should remain stable across many create/close cycles."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    gc.collect()
    initial_objects = len(gc.get_objects())

    cycles = 200
    for i in range(cycles):
        sid = f"cycle-{i}"
        await session_pool.create_session(sid)
        await session_pool.close_session(sid)

    gc.collect()
    final_objects = len(gc.get_objects())

    # Object count should not grow unboundedly (allow some tolerance)
    growth = final_objects - initial_objects
    assert growth <= cycles * 2, f"Object growth ({growth}) suggests leak"

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.3 Stress test: EventBus at capacity
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_event_bus_capacity_many_subscribers() -> None:
    """EventBus handles many subscribers with bounded queues under load."""
    event_bus = EventBus(max_queue_size=100)
    session_id = "stress-session"
    subscriber_count = 500

    # Create many subscribers
    queues = [await event_bus.subscribe(session_id) for _ in range(subscriber_count)]
    counts = await event_bus.get_subscriber_counts()
    assert counts[session_id] == subscriber_count

    # Publish more events than queue capacity
    events_to_publish = 300
    for i in range(events_to_publish):
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        )

    # Each queue should have at most max_queue_size items
    for queue in queues:
        assert queue.qsize() <= 100

    # Close session and verify cleanup
    await event_bus.close_session(session_id)
    counts = await event_bus.get_subscriber_counts()
    assert session_id not in counts


@pytest.mark.slow
async def test_event_bus_drop_oldest_under_load() -> None:
    """Under heavy load, EventBus drops oldest events correctly."""
    event_bus = EventBus(max_queue_size=10)
    session_id = "drop-session"

    queue = await event_bus.subscribe(session_id)

    # Publish 1000 events to a queue of size 10
    publish_count = 1000
    for i in range(publish_count):
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        )

    # Queue should be at max capacity
    assert queue.qsize() == 10

    # Drain and verify oldest events were dropped
    items: list[Any] = []
    while not queue.empty():
        items.append(queue.get_nowait())

    run_ids = [e.run_id for e in items if isinstance(e, RunStartedEvent)]
    # Oldest events (run-0 through run-989) should have been dropped
    assert "run-0" not in run_ids
    assert "run-500" not in run_ids
    # Most recent 10 should remain
    assert run_ids == [f"run-{publish_count - 10 + i}" for i in range(10)]

    await event_bus.close_session(session_id)


@pytest.mark.slow
async def test_event_bus_high_throughput_publish() -> None:
    """EventBus can sustain high publish rate without deadlocking."""
    event_bus = EventBus(max_queue_size=1000)
    session_id = "throughput-session"
    subscriber_count = 50

    queues = [await event_bus.subscribe(session_id) for _ in range(subscriber_count)]

    publish_count = 5000
    start = time.monotonic()

    for i in range(publish_count):
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        )

    elapsed = time.monotonic() - start
    # Should complete reasonably fast (< 5 seconds for 5000 publishes)
    assert elapsed < 5.0, f"Publish took too long: {elapsed:.2f}s"

    # All queues should have received events (up to capacity)
    for queue in queues:
        assert queue.qsize() > 0

    await event_bus.close_session(session_id)
    for q in queues:
        assert q.qsize() <= 1000


# ---------------------------------------------------------------------------
# 6.4 Stress test: TurnRunner queue overflow
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_turn_runner_injection_overflow(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """Rapidly inject many prompts into a session; verify no crash and work is processed."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    sid = "overflow-session"
    await _attach_agent(session_pool, sid, mock_agent)

    injection_count = 500

    # Rapidly inject prompts while no turn is active
    for i in range(injection_count):
        await session_pool.inject_prompt(sid, f"injected-{i}")

    # Now run a turn — auto-resume should process queued injections
    queue = await session_pool.event_bus.subscribe(sid)
    await session_pool.process_prompt(sid, "initial")

    # Collect all events (should be 1 per turn)
    events: list[Any] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            if ev is None:
                break
            events.append(ev)
        except TimeoutError:
            break

    # All queued injections are drained and processed in a single turn
    # (initial turn + one turn for all drained injections)
    assert len(events) == 2

    await session_pool.close_session(sid)
    await session_pool.shutdown()


@pytest.mark.slow
async def test_turn_runner_concurrent_injections(
    mock_pool: MagicMock,
    mock_agent_with_delay: MagicMock,
) -> None:
    """Many tasks inject into the same session concurrently."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    sid = "concurrent-inject"
    await _attach_agent(session_pool, sid, mock_agent_with_delay)

    injection_count = 100

    async def inject(i: int) -> None:
        await session_pool.inject_prompt(sid, f"msg-{i}")

    # Concurrent injections
    await asyncio.gather(*[inject(i) for i in range(injection_count)])

    # Run loop to process all queued work
    queue = await session_pool.event_bus.subscribe(sid)
    await session_pool.process_prompt(sid, "initial")

    # Collect events
    events: list[Any] = []
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        try:
            ev = await asyncio.wait_for(queue.get(), timeout=0.5)
            if ev is None:
                break
            events.append(ev)
        except TimeoutError:
            break

    # All queued injections are drained and processed in a single turn
    # (initial turn + one turn for all drained injections)
    assert len(events) == 2

    await session_pool.close_session(sid)
    await session_pool.shutdown()


@pytest.mark.slow
async def test_turn_runner_no_resource_leak_after_overflow(
    mock_pool: MagicMock,
    mock_agent: MagicMock,
) -> None:
    """After processing many injections, no locks or queues are leaked."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    sid = "leak-check"
    await _attach_agent(session_pool, sid, mock_agent)

    # Inject many prompts
    for i in range(200):
        await session_pool.inject_prompt(sid, f"msg-{i}")

    # Process all
    await session_pool.process_prompt(sid, "initial")

    # Allow auto-resume tasks to settle
    await asyncio.sleep(0.5)

    # Close session
    await session_pool.close_session(sid)

    # Verify cleanup
    assert sid not in session_pool.turns._post_turn_injections
    assert sid not in session_pool.turns._post_turn_prompts
    assert sid not in session_pool.turns._injection_locks
    assert session_pool.sessions.get_session(sid) is None

    await session_pool.shutdown()
