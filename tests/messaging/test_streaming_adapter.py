"""Tests for the Graph.iter() streaming adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from pydantic_graph.graph_builder import EndMarker, ErrorMarker, GraphTask
from pydantic_graph.id_types import ForkStack, NodeID, TaskID

from agentpool.agents.events import (
    PartStartEvent,
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    SubAgentEvent,
)
from agentpool.messaging.messages import ChatMessage
from agentpool.messaging.streaming_adapter import (
    GraphStreamingAdapter,
    StepEventCollector,
    adapt_graph_run,
)


class MockGraphRun:
    """Mock GraphRun that yields a configurable sequence of items."""

    def __init__(
        self,
        items: list[Sequence[GraphTask] | EndMarker[Any] | ErrorMarker],
        *,
        delay: float = 0.0,
    ) -> None:
        self._items = items
        self._index = 0
        self._delay = delay

    def __aiter__(self) -> AsyncIterator[Sequence[GraphTask] | EndMarker[Any] | ErrorMarker]:
        return self

    async def __anext__(self) -> Sequence[GraphTask] | EndMarker[Any] | ErrorMarker:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        if self._delay:
            await asyncio.sleep(self._delay)
        return item


def _make_task(node_id: str, task_id: int) -> GraphTask:
    """Create a minimal GraphTask for testing."""
    return GraphTask(
        node_id=NodeID(node_id),
        inputs=None,
        fork_stack=ForkStack(()),
        task_id=TaskID(f"task:{task_id}"),
    )


@pytest.mark.anyio
async def test_run_started_event():
    """Adapter always yields RunStartedEvent first."""
    run = MockGraphRun([EndMarker("done")])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )

    events = [e async for e in adapter]

    assert len(events) >= 1
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].session_id == "sess-1"
    assert events[0].agent_name == "test-agent"


@pytest.mark.anyio
async def test_graph_task_to_part_start():
    """GraphTask yields map to PartStartEvent."""
    run = MockGraphRun([
        [_make_task("step_a", 0)],
        [_make_task("step_b", 1)],
        EndMarker("done"),
    ])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )

    events = [e async for e in adapter]

    part_starts = [e for e in events if isinstance(e, PartStartEvent)]
    assert len(part_starts) == 2
    assert part_starts[0].index == 0
    assert part_starts[1].index == 0  # index resets per yield


@pytest.mark.anyio
async def test_end_marker_to_stream_complete():
    """EndMarker yields map to StreamCompleteEvent."""
    run = MockGraphRun([EndMarker("final result")])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
        message_id="msg-1",
    )

    events = [e async for e in adapter]

    complete_events = [e for e in events if isinstance(e, StreamCompleteEvent)]
    assert len(complete_events) == 1
    assert complete_events[0].message.content == "final result"
    assert complete_events[0].message.session_id == "sess-1"
    assert complete_events[0].message.name == "test-agent"
    assert complete_events[0].message.message_id == "msg-1"


@pytest.mark.anyio
async def test_error_marker_raises():
    """ErrorMarker yields RunErrorEvent and re-raises the exception."""
    original_error = ValueError("boom")
    run = MockGraphRun([ErrorMarker(original_error)])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )

    events: list[Any] = []
    with pytest.raises(ValueError, match="boom"):
        async for event in adapter:
            events.append(event)

    error_events = [e for e in events if isinstance(e, RunErrorEvent)]
    assert len(error_events) == 1
    assert error_events[0].message == "boom"
    assert error_events[0].agent_name == "test-agent"


@pytest.mark.anyio
async def test_step_event_collector_flat():
    """StepEventCollector emits events directly when depth is 0."""
    run = MockGraphRun([EndMarker("done")], delay=0.1)
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )
    collector = adapter.create_collector("my_step", depth=0)

    async def emit_while_running() -> None:
        await asyncio.sleep(0.02)
        await collector.emit_text_delta(0, "hello")
        await collector.emit_text_delta(1, " world")

    asyncio.create_task(emit_while_running())
    events = [e async for e in adapter]

    deltas = [e for e in events if hasattr(e, "delta")]
    assert len(deltas) == 2


@pytest.mark.anyio
async def test_step_event_collector_nested():
    """StepEventCollector wraps events in SubAgentEvent when depth > 0."""
    run = MockGraphRun([EndMarker("done")], delay=0.1)
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )
    collector = adapter.create_collector("sub_agent", depth=1)

    async def emit_while_running() -> None:
        await asyncio.sleep(0.02)
        await collector.emit_text_delta(0, "nested text")

    asyncio.create_task(emit_while_running())
    events = [e async for e in adapter]

    subagent_events = [e for e in events if isinstance(e, SubAgentEvent)]
    assert len(subagent_events) == 1
    assert subagent_events[0].source_name == "sub_agent"
    assert subagent_events[0].depth == 1


@pytest.mark.anyio
async def test_adapt_graph_run_convenience():
    """adapt_graph_run() yields the same events as the adapter class."""
    run = MockGraphRun([
        [_make_task("step_1", 0)],
        EndMarker("result"),
    ])

    events = [e async for e in adapt_graph_run(
        run,
        session_id="sess-2",
        agent_name="conv-agent",
    )]

    assert any(isinstance(e, RunStartedEvent) for e in events)
    assert any(isinstance(e, PartStartEvent) for e in events)
    assert any(isinstance(e, StreamCompleteEvent) for e in events)


@pytest.mark.anyio
async def test_event_ordering():
    """Events are yielded in the order they are produced."""
    sync_queue: asyncio.Queue[str] = asyncio.Queue()

    class CoordinatedMockGraphRun:
        """Mock that waits for collector before yielding step_2."""

        def __init__(self) -> None:
            self._items = [
                [_make_task("step_1", 0)],
                [_make_task("step_2", 1)],
                EndMarker("done"),
            ]
            self._index = 0

        def __aiter__(self) -> AsyncIterator[Any]:
            return self

        async def __anext__(self) -> Any:
            if self._index >= len(self._items):
                raise StopAsyncIteration
            item = self._items[self._index]
            self._index += 1
            if self._index == 2:
                # Wait for collector to emit before yielding step_2
                await sync_queue.get()
            return item

    run = CoordinatedMockGraphRun()
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )
    collector = adapter.create_collector("step_1", depth=0)

    async def emit_after_step_1() -> None:
        await asyncio.sleep(0.02)
        await collector.emit_text_delta(0, "chunk")
        await sync_queue.put("done")

    asyncio.create_task(emit_after_step_1())
    events = [e async for e in adapter]

    kinds = [type(e).__name__ for e in events]
    assert kinds[0] == "RunStartedEvent"
    assert kinds[-1] == "StreamCompleteEvent"
    # First PartStart should appear before any delta
    part_start_indices = [i for i, e in enumerate(events) if isinstance(e, PartStartEvent)]
    delta_indices = [i for i, e in enumerate(events) if hasattr(e, "delta")]
    assert len(delta_indices) > 0, "Expected at least one delta event"
    assert part_start_indices[0] < delta_indices[0]


@pytest.mark.anyio
async def test_user_msg_parent_id():
    """StreamCompleteEvent carries parent_id from user_msg."""
    user_msg = ChatMessage(content="hello", role="user", message_id="user-1")
    run = MockGraphRun([EndMarker("done")])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
        user_msg=user_msg,
    )

    events = [e async for e in adapter]
    complete = [e for e in events if isinstance(e, StreamCompleteEvent)][0]
    assert complete.message.parent_id == "user-1"


@pytest.mark.anyio
async def test_cancellation():
    """Adapter cancels cleanly when consumer breaks early."""
    run = MockGraphRun([
        [_make_task("step_a", 0)],
        [_make_task("step_b", 1)],
        EndMarker("done"),
    ])
    adapter = GraphStreamingAdapter(
        run,
        session_id="sess-1",
        agent_name="test-agent",
    )

    events: list[Any] = []
    async for event in adapter:
        events.append(event)
        if len(events) >= 2:
            break

    # Should have received RunStartedEvent + PartStartEvent before breaking
    assert len(events) == 2
