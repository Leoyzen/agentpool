"""Tests for SignalEmittingGraphRun adapter.

Validates that the wrapper correctly emits ``message_received``,
``message_sent``, ``connection_processed`` and ``message_forwarded``
signals at pydantic-graph step boundaries.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic_graph.graph_builder import EndMarker, GraphTask
from pydantic_graph.id_types import ForkStack, NodeID, TaskID
import pytest

from agentpool.messaging import ChatMessage
from agentpool.messaging.messagenode import MessageNode
from agentpool.messaging.signal_adapter import SignalEmittingGraphRun
from agentpool.talk import Talk


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockGraphRun:
    """Mock GraphRun that yields a configurable sequence of items."""

    def __init__(
        self,
        items: list[Sequence[GraphTask] | EndMarker[Any]],
    ) -> None:
        self._items = items
        self._index = 0

    def __aiter__(self) -> AsyncIterator[Sequence[GraphTask] | EndMarker[Any]]:
        return self

    async def __anext__(self) -> Sequence[GraphTask] | EndMarker[Any]:
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


class DummyMessageNode(MessageNode[Any, Any]):
    """Minimal concrete MessageNode for signal capture."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name)
        self.received: list[ChatMessage[Any]] = []
        self.sent: list[ChatMessage[Any]] = []

        # Wire up signal capture
        self.message_received.connect(self._on_received)
        self.message_sent.connect(self._on_sent)

    async def run(self, *prompts: Any, **kwargs: Any) -> ChatMessage[Any]:
        return ChatMessage(content="ok", role="assistant")

    async def get_stats(self) -> Any:
        return None

    async def _empty_iter(self) -> AsyncIterator[ChatMessage[Any]]:
        if False:
            yield ChatMessage(content="", role="assistant")
        return

    def run_iter(self, *prompts: Any, **kwargs: Any) -> AsyncIterator[ChatMessage[Any]]:
        return self._empty_iter()

    def get_context(
        self,
        data: Any = None,
        input_provider: Any = None,
    ) -> Any:
        return None

    def _on_received(self, message: ChatMessage[Any]) -> None:
        self.received.append(message)

    def _on_sent(self, message: ChatMessage[Any]) -> None:
        self.sent.append(message)


class DummyTalk(Talk[Any]):
    """Minimal Talk subclass that records signal emissions."""

    def __init__(self, source: MessageNode[Any, Any], target: MessageNode[Any, Any]) -> None:
        super().__init__(source=source, targets=[target])
        self.forwarded: list[ChatMessage[Any]] = []
        self.processed: list[Talk.ConnectionProcessed] = []

        self.message_forwarded.connect(self._on_forwarded)
        self.connection_processed.connect(self._on_processed)

    def _on_forwarded(self, message: ChatMessage[Any]) -> None:
        self.forwarded.append(message)

    def _on_processed(self, event: Talk.ConnectionProcessed) -> None:
        self.processed.append(event)


def _make_task(node_id: str, inputs: Any = None) -> GraphTask:
    return GraphTask(
        node_id=NodeID(node_id),
        inputs=inputs,
        fork_stack=ForkStack(()),
        task_id=TaskID(f"task:{node_id}"),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_message_received_before_step():
    """message_received is emitted when a GraphTask is first yielded."""
    node_a = DummyMessageNode("node_a")
    run = MockGraphRun([
        [_make_task("node_a", inputs="hello")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={NodeID("node_a"): node_a},
    )

    # Consume the adapter
    _ = [event async for event in adapter]

    assert len(node_a.received) == 1
    assert node_a.received[0].content == "hello"
    assert node_a.received[0].role == "user"


@pytest.mark.anyio
async def test_message_sent_after_step():
    """message_sent is emitted on the next yield after a task was seen."""
    node_a = DummyMessageNode("node_a")
    run = MockGraphRun([
        [_make_task("node_a", inputs="hello")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={NodeID("node_a"): node_a},
    )

    async for _ in adapter:
        pass

    assert len(node_a.sent) == 1
    assert node_a.sent[0].role == "assistant"


@pytest.mark.anyio
async def test_two_step_chain_signals():
    """A 2-step chain emits received/sent in the correct order."""
    node_a = DummyMessageNode("node_a")
    node_b = DummyMessageNode("node_b")

    run = MockGraphRun([
        [_make_task("node_a", inputs="step_a_input")],
        [_make_task("node_b", inputs="step_b_input")],
        EndMarker("final"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={
            NodeID("node_a"): node_a,
            NodeID("node_b"): node_b,
        },
    )

    async for _ in adapter:
        pass

    # node_a: received when its task was yielded, sent when step_b was yielded
    assert len(node_a.received) == 1
    assert len(node_a.sent) == 1

    # node_b: received when its task was yielded, sent when EndMarker was yielded
    assert len(node_b.received) == 1
    assert len(node_b.sent) == 1

    # Verify chronological order via a unified timeline
    timeline: list[tuple[str, str, str]] = []
    timeline.extend(("node_a", "received", msg.content) for msg in node_a.received)
    timeline.extend(("node_a", "sent", msg.content) for msg in node_a.sent)
    timeline.extend(("node_b", "received", msg.content) for msg in node_b.received)
    timeline.extend(("node_b", "sent", msg.content) for msg in node_b.sent)

    # Sort by the order they were appended (each list is in order)
    # The expected sequence is:
    # 1. node_a received (yield [task_a])
    # 2. node_a sent (yield [task_b])
    # 3. node_b received (yield [task_b])
    # 4. node_b sent (yield EndMarker)
    expected = [
        ("node_a", "received", "step_a_input"),
        ("node_a", "sent", "step_a_input"),
        ("node_b", "received", "step_b_input"),
        ("node_b", "sent", "step_b_input"),
    ]
    assert timeline == expected


@pytest.mark.anyio
async def test_connection_processed_on_edge():
    """connection_processed is emitted when an edge traversal is detected."""
    node_a = DummyMessageNode("node_a")
    node_b = DummyMessageNode("node_b")
    talk_ab = DummyTalk(source=node_a, target=node_b)

    run = MockGraphRun([
        [_make_task("node_a", inputs="hello")],
        [_make_task("node_b", inputs="world")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={
            NodeID("node_a"): node_a,
            NodeID("node_b"): node_b,
        },
        talk_mapping={
            (NodeID("node_a"), NodeID("node_b")): talk_ab,
        },
    )

    async for _ in adapter:
        pass

    assert len(talk_ab.processed) == 1
    event = talk_ab.processed[0]
    assert event.source == node_a
    assert event.targets == [node_b]
    assert event.message.content == "hello"
    assert event.connection_type == "run"
    assert not event.queued


@pytest.mark.anyio
async def test_message_forwarded_on_edge():
    """message_forwarded is emitted alongside connection_processed."""
    node_a = DummyMessageNode("node_a")
    node_b = DummyMessageNode("node_b")
    talk_ab = DummyTalk(source=node_a, target=node_b)

    run = MockGraphRun([
        [_make_task("node_a", inputs="hello")],
        [_make_task("node_b", inputs="world")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={
            NodeID("node_a"): node_a,
            NodeID("node_b"): node_b,
        },
        talk_mapping={
            (NodeID("node_a"), NodeID("node_b")): talk_ab,
        },
    )

    async for _ in adapter:
        pass

    assert len(talk_ab.forwarded) == 1
    assert talk_ab.forwarded[0].content == "hello"


@pytest.mark.anyio
async def test_session_id_injected():
    """ChatMessage payloads carry the configured session_id."""
    node_a = DummyMessageNode("node_a")
    run = MockGraphRun([
        [_make_task("node_a", inputs="hello")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={NodeID("node_a"): node_a},
        session_id="sess-123",
    )

    async for _ in adapter:
        pass

    assert node_a.received[0].session_id == "sess-123"
    assert node_a.sent[0].session_id == "sess-123"


@pytest.mark.anyio
async def test_unmapped_node_id_skipped_gracefully():
    """Nodes not present in node_mapping are silently skipped."""
    node_a = DummyMessageNode("node_a")
    run = MockGraphRun([
        [_make_task("unknown_node", inputs="hello")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={NodeID("node_a"): node_a},
    )

    # Should not raise
    async for _ in adapter:
        pass

    assert len(node_a.received) == 0
    assert len(node_a.sent) == 0


@pytest.mark.anyio
async def test_parallel_execution_signals():
    """Parallel tasks emit received/sent for each branch."""
    node_a = DummyMessageNode("node_a")
    node_b = DummyMessageNode("node_b")

    run = MockGraphRun([
        [_make_task("node_a", inputs="a"), _make_task("node_b", inputs="b")],
        EndMarker("done"),
    ])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={
            NodeID("node_a"): node_a,
            NodeID("node_b"): node_b,
        },
    )

    async for _ in adapter:
        pass

    assert len(node_a.received) == 1
    assert len(node_a.sent) == 1
    assert len(node_b.received) == 1
    assert len(node_b.sent) == 1


@pytest.mark.anyio
async def test_is_completed_property():
    """is_completed becomes True after EndMarker is yielded."""
    run = MockGraphRun([EndMarker("done")])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={},
    )

    assert not adapter.is_completed
    async for _ in adapter:
        pass
    assert adapter.is_completed


@pytest.mark.anyio
async def test_graph_run_property():
    """graph_run exposes the underlying GraphRun instance."""
    run = MockGraphRun([EndMarker("done")])

    adapter = SignalEmittingGraphRun(
        graph_run=run,  # type: ignore[arg-type]
        node_mapping={},
    )

    assert adapter.graph_run is run
