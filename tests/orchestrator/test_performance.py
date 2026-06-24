"""Performance and stress tests for SessionPool orchestration.

Consolidated from:
- test_benchmark.py (latency, throughput, scaling benchmarks)
- test_stress.py (1000+ sessions, rapid cycles, queue overflow)
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
from agentpool.orchestrator.metrics import MetricsCollector
import anyio



# ============================================================================
# Fixtures
# ============================================================================



def _stream_empty(stream: anyio.abc.ObjectReceiveStream) -> bool:
    """Check if a memory receive stream has no buffered items."""
    try:
        stream.receive_nowait()
        return False
    except anyio.WouldBlock:
        return True
    except anyio.EndOfStream:
        return True

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
    state, _ = await pool.sessions.get_or_create_session(session_id)
    state.agent = agent  # type: ignore[assignment]
    pool.sessions._session_agents[session_id] = agent  # type: ignore[assignment]
    pool.pool.get_agent.return_value = agent  # type: ignore[attr-defined]


# ============================================================================
# Benchmark: session lifecycle
# ============================================================================


@pytest.mark.benchmark
async def test_benchmark_session_creation_latency(mock_pool: MagicMock) -> None:
    """Measure time to create and close sessions at varying scales."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    results: dict[str, dict[str, float]] = {}

    for count in (1, 10, 100, 500):
        # Measure creation
        start = time.perf_counter()
        sids = [f"bench-create-{i}" for i in range(count)]
        await asyncio.gather(*[session_pool.create_session(sid) for sid in sids])
        create_time = time.perf_counter() - start

        # Measure close
        start = time.perf_counter()
        await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])
        close_time = time.perf_counter() - start

        per_session_create = create_time / count * 1000  # ms
        per_session_close = close_time / count * 1000  # ms

        results[f"{count}_sessions"] = {
            "total_create_ms": create_time * 1000,
            "total_close_ms": close_time * 1000,
            "per_session_create_ms": per_session_create,
            "per_session_close_ms": per_session_close,
        }

        assert len(session_pool.sessions._sessions) == 0

    # Print benchmark results
    print("\n=== Session Lifecycle Benchmark ===")
    for label, metrics in results.items():
        print(
            f"{label}: create={metrics['per_session_create_ms']:.3f}ms/ea, "
            f"close={metrics['per_session_close_ms']:.3f}ms/ea"
        )

    # Sanity checks: should be reasonably fast
    assert results["1_sessions"]["per_session_create_ms"] < 50
    assert results["500_sessions"]["per_session_create_ms"] < 10

    await session_pool.shutdown()


@pytest.mark.benchmark
async def test_benchmark_session_lifecycle_memory(mock_pool: MagicMock) -> None:
    """Verify session creation/close does not leak memory under sustained load."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    iterations = 10
    batch_size = 100

    times: list[float] = []
    for _ in range(iterations):
        sids = [f"mem-{i}" for i in range(batch_size)]
        start = time.perf_counter()
        await asyncio.gather(*[session_pool.create_session(sid) for sid in sids])
        await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])
        times.append(time.perf_counter() - start)

    avg_time = sum(times) / len(times)
    print("\n=== Session Lifecycle Memory Benchmark ===")
    print(f"Average batch ({batch_size} sessions): {avg_time * 1000:.2f}ms")

    # Time should be stable (last 3 iterations within 50% of first 3)
    first_avg = sum(times[:3]) / 3
    last_avg = sum(times[-3:]) / 3
    assert last_avg < first_avg * 1.5, f"Time grew from {first_avg:.3f}s to {last_avg:.3f}s"

    await session_pool.shutdown()


# ============================================================================
# Benchmark: turn latency
# ============================================================================


@pytest.mark.benchmark
async def test_benchmark_turn_latency_under_load(
    mock_pool: MagicMock,
    mock_agent_with_delay: MagicMock,
) -> None:
    """Measure turn latency with increasing concurrent session load."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()
    collector = MetricsCollector(session_pool)

    results: dict[str, dict[str, float]] = {}

    for session_count in (1, 10, 50, 100):
        # Setup sessions with agents
        sids = [f"latency-{i}" for i in range(session_count)]
        for sid in sids:
            await _attach_agent(session_pool, sid, mock_agent_with_delay)

        # Subscribe to events
        queues = {sid: await session_pool.event_bus.subscribe(sid) for sid in sids}

        # Run turns concurrently
        start = time.perf_counter()
        await asyncio.gather(
            *[session_pool.process_prompt(sid, "hello") for sid in sids]
        )
        total_time = time.perf_counter() - start

        # Collect events to ensure completion
        for sid in sids:
            await asyncio.wait_for(queues[sid].get(), timeout=5.0)

        metrics = await collector.get_metrics()
        avg_latency = metrics.turn_latency_ms

        results[f"{session_count}_sessions"] = {
            "total_time_ms": total_time * 1000,
            "avg_turn_latency_ms": avg_latency,
            "throughput_turns_per_sec": session_count / total_time,
        }

        # Cleanup
        await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])

    print("\n=== Turn Latency Benchmark ===")
    for label, metrics in results.items():
        print(
            f"{label}: total={metrics['total_time_ms']:.1f}ms, "
            f"avg_latency={metrics['avg_turn_latency_ms']:.2f}ms, "
            f"throughput={metrics['throughput_turns_per_sec']:.1f} turns/s"
        )

    # Sanity: 100 sessions should complete in under 5 seconds
    assert results["100_sessions"]["total_time_ms"] < 5000

    await session_pool.shutdown()


@pytest.mark.benchmark
async def test_benchmark_turn_latency_serial_vs_concurrent(
    mock_pool: MagicMock,
    mock_agent_with_delay: MagicMock,
) -> None:
    """Concurrent turns should be faster than serial for multiple sessions."""
    session_pool = SessionPool(mock_pool)
    await session_pool.start()

    session_count = 20
    sids = [f"cmp-{i}" for i in range(session_count)]
    for sid in sids:
        await _attach_agent(session_pool, sid, mock_agent_with_delay)
        _ = await session_pool.event_bus.subscribe(sid)

    # Serial execution
    serial_start = time.monotonic()
    for sid in sids:
        await session_pool.process_prompt(sid, "hello")
    serial_time = time.monotonic() - serial_start

    # Reset for concurrent test
    await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])
    for sid in sids:
        await session_pool.create_session(sid)
        await _attach_agent(session_pool, sid, mock_agent_with_delay)

    # Concurrent execution
    concurrent_start = time.monotonic()
    await asyncio.gather(
        *[session_pool.process_prompt(sid, "hello") for sid in sids]
    )
    concurrent_time = time.monotonic() - concurrent_start

    speedup = serial_time / concurrent_time
    print("\n=== Serial vs Concurrent ===")
    print(f"Serial: {serial_time * 1000:.1f}ms, Concurrent: {concurrent_time * 1000:.1f}ms")
    print(f"Speedup: {speedup:.2f}x")

    assert speedup > 1.5, f"Concurrent not faster: {speedup:.2f}x"

    await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])
    await session_pool.shutdown()


# ============================================================================
# Benchmark: event throughput
# ============================================================================


@pytest.mark.benchmark
async def test_benchmark_event_throughput_single_subscriber() -> None:
    """Measure raw event publish throughput with one subscriber."""
    event_bus = EventBus(max_queue_size=10000)
    session_id = "throughput-1"
    queue = await event_bus.subscribe(session_id)

    event_count = 10000
    start = time.perf_counter()

    for i in range(event_count):
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        )

    publish_time = time.perf_counter() - start
    throughput = event_count / publish_time

    print("\n=== Event Throughput (1 subscriber) ===")
    print(f"Published {event_count} events in {publish_time * 1000:.1f}ms")
    print(f"Throughput: {throughput:.0f} events/second")

    assert throughput > 1000, f"Throughput too low: {throughput:.0f} events/s"

    # Verify all events reached subscriber
    assert queue.qsize() == event_count
    await event_bus.close_session(session_id)


@pytest.mark.benchmark
async def test_benchmark_event_throughput_many_subscribers() -> None:
    """Measure event throughput with many subscribers."""
    event_bus = EventBus(max_queue_size=1000)
    session_id = "throughput-n"
    subscriber_count = 100

    queues = [await event_bus.subscribe(session_id) for _ in range(subscriber_count)]

    event_count = 1000
    start = time.perf_counter()

    for i in range(event_count):
        await event_bus.publish(
            session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
        )

    publish_time = time.perf_counter() - start
    total_events_delivered = event_count * subscriber_count
    throughput = total_events_delivered / publish_time

    print(f"\n=== Event Throughput ({subscriber_count} subscribers) ===")
    print(f"Published {event_count} events to {subscriber_count} subscribers")
    print(f"Total deliveries: {total_events_delivered}")
    print(f"Time: {publish_time * 1000:.1f}ms")
    print(f"Effective throughput: {throughput:.0f} events/second")

    # Verify each subscriber received events
    for queue in queues:
        assert queue.qsize() > 0

    await event_bus.close_session(session_id)


@pytest.mark.benchmark
async def test_benchmark_event_throughput_scaling() -> None:
    """Measure how throughput scales with subscriber count."""
    event_bus = EventBus(max_queue_size=500)
    session_id = "scale"
    event_count = 500

    results: dict[str, dict[str, float]] = {}

    for subscriber_count in (1, 10, 50):
        queues = [await event_bus.subscribe(session_id) for _ in range(subscriber_count)]

        start = time.perf_counter()
        for i in range(event_count):
            await event_bus.publish(
                session_id, RunStartedEvent(session_id=session_id, run_id=f"run-{i}")
            )
        elapsed = time.perf_counter() - start

        total_deliveries = event_count * subscriber_count
        results[f"{subscriber_count}_subscribers"] = {
            "publish_time_ms": elapsed * 1000,
            "events_per_second": event_count / elapsed,
            "total_deliveries_per_second": total_deliveries / elapsed,
        }

        # Drain and unsubscribe for next iteration
        await event_bus.close_session(session_id)
        for q in queues:
            while not q.empty():
                q.get_nowait()

    print("\n=== Event Throughput Scaling ===")
    for label, metrics in results.items():
        print(
            f"{label}: {metrics['events_per_second']:.0f} publishes/s, "
            f"{metrics['total_deliveries_per_second']:.0f} total deliveries/s"
        )

    # With many subscribers, total deliveries should remain healthy
    fifty_total = results["50_subscribers"]["total_deliveries_per_second"]
    assert fifty_total > 100000, (
        f"Total throughput with 50 subscribers too low: {fifty_total:.0f}"
    )


# ============================================================================
# Stress: 1000+ concurrent sessions
# ============================================================================


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
        event = await asyncio.wait_for(queue.receive(), timeout=1.0)
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


# ============================================================================
# Stress: rapid create/close cycles
# ============================================================================


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
        event = await asyncio.wait_for(queue.receive(), timeout=1.0)
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


# ============================================================================
# Stress: EventBus at capacity
# ============================================================================


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
    while not _stream_empty(queue):
        items.append(queue.receive_nowait())

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


# ============================================================================
# Stress: TurnRunner queue overflow
# ============================================================================


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
            ev = await asyncio.wait_for(queue.receive(), timeout=0.5)
            if ev is None:
                break
            events.append(ev)
        except TimeoutError:
            break

    # All queued injections are drained and processed in a single turn
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
            ev = await asyncio.wait_for(queue.receive(), timeout=0.5)
            if ev is None:
                break
            events.append(ev)
        except TimeoutError:
            break

    # All queued injections are drained and processed in a single turn
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
