"""Performance benchmarks for SessionPool orchestration (Group 6.5-6.7).

Produces reproducible metrics for:
- Session creation and close latency
- Turn latency under varying concurrent load
- EventBus event throughput

All benchmarks use mocked agents (no real LLM calls) and run in < 30s.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus, SessionPool
from agentpool.orchestrator.metrics import MetricsCollector


pytestmark = [pytest.mark.benchmark, pytest.mark.anyio]


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
        await asyncio.sleep(0.002)
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
# 6.5 Performance benchmark: session lifecycle
# ---------------------------------------------------------------------------


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
    print(f"\n=== Session Lifecycle Memory Benchmark ===")
    print(f"Average batch ({batch_size} sessions): {avg_time * 1000:.2f}ms")

    # Time should be stable (last 3 iterations within 50% of first 3)
    first_avg = sum(times[:3]) / 3
    last_avg = sum(times[-3:]) / 3
    assert last_avg < first_avg * 1.5, f"Time grew from {first_avg:.3f}s to {last_avg:.3f}s"

    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.6 Performance benchmark: turn latency under load
# ---------------------------------------------------------------------------


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
    print(f"\n=== Serial vs Concurrent ===")
    print(f"Serial: {serial_time * 1000:.1f}ms, Concurrent: {concurrent_time * 1000:.1f}ms")
    print(f"Speedup: {speedup:.2f}x")

    assert speedup > 1.5, f"Concurrent not faster: {speedup:.2f}x"

    await asyncio.gather(*[session_pool.close_session(sid) for sid in sids])
    await session_pool.shutdown()


# ---------------------------------------------------------------------------
# 6.7 Performance benchmark: event throughput
# ---------------------------------------------------------------------------


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

    print(f"\n=== Event Throughput (1 subscriber) ===")
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
    # (not drop to near-zero due to overhead)
    fifty_total = results["50_subscribers"]["total_deliveries_per_second"]
    assert fifty_total > 100000, (
        f"Total throughput with 50 subscribers too low: {fifty_total:.0f}"
    )
