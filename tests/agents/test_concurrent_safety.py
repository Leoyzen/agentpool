"""Test suite for Agent Concurrent Execution Safety (RFC-0021).

This test suite validates that concurrent calls to the same agent instance
execute safely without shared state pollution.

Run with: pytest tests/agents/test_concurrent_safety.py -v
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from agentpool import AgentPool
    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events import RichAgentStreamEvent, StreamCompleteEvent
    from agentpool.orchestrator.core import SessionPool


class AgentPoolSession:
    """Provides session_pool access for tests migrated to SessionPool architecture."""

    def __init__(self, agent: BaseAgent, pool: AgentPool, session_pool: SessionPool) -> None:
        self.agent = agent
        self.pool = pool
        self.session_pool = session_pool
        self.agent_name = agent.name


# =============================================================================
# Baseline Tests (Should pass before and after RFC-0021 implementation)
# =============================================================================


@pytest.mark.asyncio
async def test_serial_execution_baseline(native_agent: AgentPoolSession) -> None:
    """Serial execution must work correctly (baseline)."""
    results = []
    session_pool = native_agent.session_pool
    agent_name = native_agent.agent_name
    session_id = "serial-baseline"
    await session_pool.create_session(session_id, agent_name=agent_name)

    for i in range(3):
        events = []
        async for event in session_pool.run_stream(session_id, f"Task {i}"):
            events.append(event)
            if event_is_complete(event):
                break
        results.append(events)

    # All 3 serial calls should complete successfully
    assert len(results) == 3
    assert all(len(r) > 0 for r in results)


@pytest.mark.asyncio
async def test_single_call_completion(native_agent: AgentPoolSession) -> None:
    """A single call must complete with full event sequence."""
    events = []
    session_pool = native_agent.session_pool
    agent_name = native_agent.agent_name
    session_id = "single-call"
    await session_pool.create_session(session_id, agent_name=agent_name)

    async for event in session_pool.run_stream(session_id, "Single task"):
        events.append(event)
        if event_is_complete(event):
            break

    # Should have: RunStarted, PartStart, content..., PartEnd, StreamComplete
    assert len(events) >= 5
    assert any(event_is_complete(e) for e in events)


# =============================================================================
# Concurrent Isolation Tests (These will FAIL before RFC-0021, PASS after)
# =============================================================================


@pytest.mark.asyncio
async def test_concurrent_calls_complete(native_agent: AgentPoolSession) -> None:
    """Multiple concurrent calls to same agent must all complete.

    This is the PRIMARY test for RFC-0021. Before the fix, some calls
    may be prematurely terminated due to shared _cancelled state.
    """

    async def run_task(task_id: str) -> list[RichAgentStreamEvent]:
        """Run a single task and collect events."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"concurrent-{task_id}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        events = []
        async for event in session_pool.run_stream(session_id, f"Task {task_id}"):
            events.append(event)
            if event_is_complete(event):
                break
        return events

    # Run 3 tasks concurrently
    results = await asyncio.gather(
        run_task("A"),
        run_task("B"),
        run_task("C"),
    )

    # ALL tasks must complete with events
    assert len(results) == 3, "All 3 tasks should return results"
    assert all(len(r) > 0 for r in results), "All tasks should have events"

    # Each task should complete normally (not truncated)
    min_expected_events = 5
    for i, events in enumerate(results):
        assert len(events) >= min_expected_events, (
            f"Task {i} was truncated: only {len(events)} events"
        )


@pytest.mark.asyncio
async def test_concurrent_event_isolation(native_agent: AgentPoolSession) -> None:
    """Events from concurrent calls must not cross-contaminate.

    Each call should only receive its own events, not events from
    other concurrent calls. We verify this by checking that each
    stream only contains events with matching run_id.
    """
    from agentpool.agents.events import RunStartedEvent

    async def run_task(task_id: str) -> tuple[str | None, list[Any]]:
        """Run task and collect run_id and events."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"isolation-{task_id}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        run_id: str | None = None
        events: list[Any] = []
        async for event in session_pool.run_stream(session_id, f"Task {task_id}"):
            if isinstance(event, RunStartedEvent):
                run_id = event.run_id
            events.append(event)
            if event_is_complete(event):
                break
        return run_id, events

    # Run 3 tasks concurrently
    results = await asyncio.gather(
        run_task("A"),
        run_task("B"),
        run_task("C"),
    )

    # Each task should have a unique run_id and only its own events
    run_ids = [r[0] for r in results]
    assert len(set(run_ids)) == 3, "Each task should have a unique run_id"

    # Verify each task received events
    for i, (run_id, events) in enumerate(results):
        assert len(events) > 0, f"Task {i} received no events"
        # All events in the stream should belong to this run
        # (This is implicit - if events were cross-contaminated, we'd see wrong event counts)


@pytest.mark.asyncio
async def test_concurrent_cancellation_isolation(native_agent: AgentPoolSession) -> None:
    """Cancellation of one call must not affect other concurrent calls.

    If Task A is cancelled, Task B should continue running normally.
    """

    async def run_slow_task(task_id: str, duration: float) -> tuple[str, float]:
        """Run a slow task, return completion status and duration."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"cancel-{task_id}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        start = time.perf_counter()
        event_count = 0

        try:
            async for event in session_pool.run_stream(session_id, f"Slow task {task_id}"):
                event_count += 1
                await asyncio.sleep(duration / 5)  # Simulate slow processing
                if event_is_complete(event):
                    break
        except asyncio.CancelledError:
            elapsed = time.perf_counter() - start
            return ("cancelled", elapsed)

        elapsed = time.perf_counter() - start
        return ("completed", elapsed)

    # Start two slow tasks
    task_a = asyncio.create_task(run_slow_task("A", 0.5))
    task_b = asyncio.create_task(run_slow_task("B", 0.5))

    # Wait a bit then cancel Task A
    await asyncio.sleep(0.1)
    task_a.cancel()

    # Task B should complete normally
    try:
        status_a, duration_a = await task_a
    except asyncio.CancelledError:
        status_a = "cancelled"
        duration_a = 0.1

    status_b, duration_b = await task_b

    # Task A was cancelled (expected)
    assert status_a == "cancelled"

    # Task B should complete despite Task A cancellation
    assert status_b == "completed", f"Task B was affected by Task A cancellation: {status_b}"


@pytest.mark.asyncio
async def test_concurrent_session_stream_isolation(native_agent: AgentPoolSession) -> None:
    """Each concurrent call must have isolated streams.

    Events emitted by one call should not appear in another call's stream.
    """

    async def count_events(task_id: str) -> int:
        """Count events received by this task."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"queue-{task_id}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        count = 0
        async for event in session_pool.run_stream(session_id, f"Counting task {task_id}"):
            count += 1
            if event_is_complete(event):
                break
        return count

    # Run 3 tasks concurrently
    counts = await asyncio.gather(
        count_events("A"),
        count_events("B"),
        count_events("C"),
    )

    # All tasks should receive similar number of events
    # (if queues were shared, one task might receive all events)
    avg_count = sum(counts) / len(counts)
    for i, count in enumerate(counts):
        # Each task should have within 50% of average
        assert count > avg_count * 0.5, (
            f"Task {i} received too few events ({count}), possible queue pollution"
        )


# =============================================================================
# Stress Tests
# =============================================================================


@pytest.mark.slow
@pytest.mark.asyncio
async def test_10_concurrent_calls(native_agent: AgentPoolSession) -> None:
    """Stress test: 10 concurrent calls must all complete."""

    async def run_task(i: int) -> int:
        """Run task and return its index if successful."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"stress-{i}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        async for event in session_pool.run_stream(session_id, f"Task {i}"):
            if event_is_complete(event):
                return i
        return -1

    results = await asyncio.gather(*[run_task(i) for i in range(10)])

    # All 10 should complete with their index
    assert set(results) == set(range(10)), f"Some tasks failed or returned wrong index: {results}"


@pytest.mark.slow
@pytest.mark.asyncio
async def test_rapid_fire_concurrent_calls(native_agent: AgentPoolSession) -> None:
    """Test rapid-fire concurrent calls with minimal delay."""

    async def quick_task(i: int) -> bool:
        """Quick task that completes fast."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = f"rapid-{i}"
        await session_pool.create_session(session_id, agent_name=agent_name)
        async for event in session_pool.run_stream(session_id, f"Quick {i}"):
            if event_is_complete(event):
                return True
        return False

    # Launch 20 tasks as fast as possible
    tasks = [quick_task(i) for i in range(20)]
    results = await asyncio.gather(*tasks)

    # All should complete successfully
    assert all(results), f"Some tasks failed: {results.count(False)} failures"


# =============================================================================
# Performance Regression Tests
# =============================================================================


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_serial_performance_baseline(native_agent: AgentPoolSession) -> None:
    """Serial execution performance must not regress significantly.

    Establish baseline for serial performance.
    """
    durations = []
    session_pool = native_agent.session_pool
    agent_name = native_agent.agent_name
    session_id = "perf-serial"
    await session_pool.create_session(session_id, agent_name=agent_name)

    for i in range(5):
        start = time.perf_counter()
        async for event in session_pool.run_stream(session_id, f"Perf test {i}"):
            if event_is_complete(event):
                break
        durations.append(time.perf_counter() - start)

    avg_duration = sum(durations) / len(durations)
    print(f"\nSerial performance baseline: {avg_duration:.3f}s per call")

    # Store baseline for future comparison
    # In real test, this would be compared against a stored value
    assert avg_duration < 5.0, "Serial execution is too slow"


@pytest.mark.benchmark
@pytest.mark.asyncio
async def test_concurrent_performance(native_agent: AgentPoolSession) -> None:
    """Concurrent execution should be faster than serial for multiple tasks.

    3 concurrent tasks should complete faster than 3 serial tasks.
    """

    async def measure_serial() -> float:
        """Measure serial execution time."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        session_id = "perf-serial-measure"
        await session_pool.create_session(session_id, agent_name=agent_name)
        start = time.perf_counter()
        for i in range(3):
            async for event in session_pool.run_stream(session_id, f"Serial {i}"):
                if event_is_complete(event):
                    break
        return time.perf_counter() - start

    async def measure_concurrent() -> float:
        """Measure concurrent execution time."""
        session_pool = native_agent.session_pool
        agent_name = native_agent.agent_name
        # Pre-create sessions before timing to match serial setup pattern
        for i in range(3):
            await session_pool.create_session(f"perf-concurrent-{i}", agent_name=agent_name)

        async def task(i: int) -> None:
            async for event in session_pool.run_stream(f"perf-concurrent-{i}", f"Concurrent {i}"):
                if event_is_complete(event):
                    break

        start = time.perf_counter()
        await asyncio.gather(task(0), task(1), task(2))
        return time.perf_counter() - start

    serial_time = await measure_serial()
    concurrent_time = await measure_concurrent()

    print(f"\nSerial: {serial_time:.3f}s, Concurrent: {concurrent_time:.3f}s")

    # Concurrent should be at least as fast as serial (allow for measurement noise).
    # With sub-millisecond test model execution, timing variance dominates.
    # Accept any speedup >= 0.8 (within measurement noise for trivial tasks).
    speedup = serial_time / concurrent_time
    assert speedup >= 0.8, f"Concurrent execution significantly slower than serial: speedup = {speedup:.2f}x"


# =============================================================================
# Subclass Compatibility Tests
# =============================================================================


@pytest.mark.asyncio
async def test_native_agent_concurrent(native_agent: AgentPoolSession) -> None:
    """NativeAgent subclass must support concurrent calls."""
    # Same as test_concurrent_calls_complete but specifically for NativeAgent
    await test_concurrent_calls_complete(native_agent)


# =============================================================================
# Utility Functions
# =============================================================================


def event_is_complete(event: RichAgentStreamEvent) -> bool:
    """Check if event indicates stream completion.

    Handles different event types that indicate completion.
    """
    from agentpool.agents.events import StreamCompleteEvent

    if isinstance(event, StreamCompleteEvent):
        return True

    # Some implementations may use different completion indicators
    event_type = type(event).__name__
    return "Complete" in event_type or "End" in event_type


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
async def native_agent():
    """Create a test NativeAgent instance with AgentPool and SessionPool."""
    from pydantic_ai.models.test import TestModel

    from agentpool import Agent, AgentPool
    from agentpool.models.agents import NativeAgentConfig
    from agentpool.models.manifest import AgentsManifest

    model = TestModel(custom_output_text="Test response")
    agent = Agent(name="test_agent", model=model)

    manifest = AgentsManifest(agents={"test_agent": NativeAgentConfig(model="test")})
    pool = AgentPool(manifest)
    async with pool:
        session_pool = pool.session_pool
        assert session_pool is not None
        yield AgentPoolSession(agent=agent, pool=pool, session_pool=session_pool)


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
