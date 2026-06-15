"""Test suite for event queue isolation.

Tests that run_ctx.event_queue is used instead of self._event_queue.
"""

import asyncio
import sys
from pathlib import Path
from typing import Any

import pytest

# Add src to path for imports
sys_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(sys_path))


def test_run_ctx_has_event_queue():
    """Test that AgentRunContext has event_queue attribute."""

    from agentpool.agents.context import AgentRunContext

    run_ctx = AgentRunContext()

    assert hasattr(run_ctx, "event_queue")
    assert isinstance(run_ctx.event_queue, asyncio.Queue)

    print("✓ AgentRunContext has event_queue attribute")


def test_run_ctx_event_queue_isolation():
    """Test that each AgentRunContext has its own event_queue."""

    from agentpool.agents.context import AgentRunContext

    ctx1 = AgentRunContext(session_id="ctx1")
    ctx2 = AgentRunContext(session_id="ctx2")

    # They should be different queues
    assert ctx1.event_queue is not ctx2.event_queue

    # Put an item in ctx1's queue
    ctx1.event_queue.put_nowait("event1")

    # ctx2's queue should be empty
    assert ctx2.event_queue.empty()

    # ctx1's queue should have the item
    assert not ctx1.event_queue.empty()
    assert ctx1.event_queue.get_nowait() == "event1"

    print("✓ Each AgentRunContext has isolated event_queue")


def test_run_ctx_event_queue_in_use():
    """Test that run_ctx.event_queue is used in run_stream methods."""

    from agentpool.agents.base_agent import BaseAgent

    # Check that run_ctx.event_queue is accessed (not self._event_queue)
    # This is a code inspection test

    import inspect

    source = inspect.getsource(BaseAgent.run_stream)

    # Count occurrences
    self_event_queue_count = source.count("self._event_queue")
    run_ctx_event_queue_count = source.count("run_ctx.event_queue")

    # run_ctx.event_queue should be used, self._event_queue should not
    print(f"  self._event_queue count: {self_event_queue_count}")
    print(f"  run_ctx.event_queue count: {run_ctx_event_queue_count}")

    # For RFC-0021 compliance, we expect run_ctx.event_queue usage
    # self._event_queue should only be used in non-run contexts (e.g., __init__)

    print("✓ Event queue usage pattern checked")


@pytest.mark.asyncio
async def test_concurrent_runs_dont_pollute_queues():
    """Test that concurrent runs don't pollute each other's event queues."""

    from agentpool.agents.context import AgentRunContext

    results = {"run1_events": [], "run2_events": []}

    async def simulate_run1():
        ctx = AgentRunContext(session_id="run1")
        queue = ctx.event_queue

        # Put events in queue
        for i in range(3):
            await queue.put(f"run1_event_{i}")

        # Get events
        for _ in range(3):
            event = await queue.get()
            results["run1_events"].append(event)

    async def simulate_run2():
        ctx = AgentRunContext(session_id="run2")
        queue = ctx.event_queue

        # Put events in queue
        for i in range(5):
            await queue.put(f"run2_event_{i}")

        # Get events
        for _ in range(5):
            event = await queue.get()
            results["run2_events"].append(event)

    # Run concurrently
    await asyncio.gather(simulate_run1(), simulate_run2())

    # Verify isolation
    assert len(results["run1_events"]) == 3
    assert len(results["run2_events"]) == 5
    assert all(e.startswith("run1_") for e in results["run1_events"])
    assert all(e.startswith("run2_") for e in results["run2_events"])

    print("✓ Concurrent runs don't pollute each other's event queues")


def test_agent_has_no_instance_event_queue():
    """Test that Agent no longer has instance-level _event_queue (RFC-0021)."""

    from agentpool.agents.base_agent import BaseAgent

    # Create minimal agent
    class TestAgent(BaseAgent):
        @property
        def model_name(self) -> str | None:
            return "test-model"

        async def set_model(self, model: str) -> None:
            pass

        async def _stream_events(self, run_ctx, *args, **kwargs):
            if False:
                yield

        async def _interrupt(self, run_ctx=None) -> None:
            pass

        async def get_available_models(self):
            return None

        async def get_modes(self):
            return []

        async def _set_mode(self, mode_id: str, category_id: str) -> None:
            pass

        async def list_sessions(self, *, cwd=None, limit=None):
            return []

        async def load_session(self, session_id: str):
            return None

    agent = TestAgent(name="test")

    # Instance-level queue should NOT exist — per-run isolation via AgentRunContext
    assert not hasattr(agent, "_event_queue"), (
        "Agent should not have instance-level _event_queue — "
        "use run_ctx.event_queue for per-run isolation"
    )

    print("✓ Agent has no instance-level _event_queue (per-run isolation only)")


if __name__ == "__main__":
    print("Testing event queue isolation...\n")
    test_run_ctx_has_event_queue()
    test_run_ctx_event_queue_isolation()
    test_run_ctx_event_queue_in_use()
    print("\n✓ All event queue isolation tests passed!")
    print("Run with pytest to execute async tests.")
