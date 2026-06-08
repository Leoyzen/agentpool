"""SSE EventBus integration tests.

Validates that the SSE endpoint receives events from both the legacy
broadcast path and the EventBus path via a global subscription
(``scope="all"``).

Key behaviors tested:
- Legacy ``state.event_subscribers`` path still works
- EventBus events are forwarded to the SSE stream via global subscription
- Bridge-wrapped CustomEvent instances are deduplicated (skipped)
- Non-bridge CustomEvent instances are unwrapped
- Child session events reach the SSE stream
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any
from unittest.mock import Mock

import pytest

from agentpool.agents.events.events import (
    CustomEvent,
    PartStartEvent,
)
from agentpool.orchestrator.core import EventBus
from agentpool_server.opencode_server.models import (
    SessionStatus,
    SessionStatusEvent,
)
from agentpool_server.opencode_server.routes.global_routes import (
    GlobalEventFactory,
    _event_generator,
)


if TYPE_CHECKING:
    from agentpool_server.opencode_server.models.events import Event


# =============================================================================
# Mock state helpers
# =============================================================================


class _MockState:
    """Minimal ServerState-like object for _event_generator tests."""

    def __init__(
        self,
        working_dir: str = "/tmp/test_wd",
        session_controller: Any | None = None,
    ) -> None:
        self.working_dir = working_dir
        self.event_subscribers: list[asyncio.Queue[Event]] = []
        self._event_factory: GlobalEventFactory | None = None
        self._first_subscriber_triggered = False
        self.on_first_subscriber: Any = None
        self.session_controller = session_controller
        self._sse_event_counter = 0

        # Build a mock pool with session_pool / event_bus when controller is given.
        self.pool = Mock()
        if session_controller is not None:
            self.pool.session_pool = Mock()
            self.pool.session_pool.event_bus = EventBus()
        else:
            self.pool.session_pool = None

    def get_next_event_id(self) -> int:
        self._sse_event_counter += 1
        return self._sse_event_counter

    def get_event_factory(self) -> GlobalEventFactory:
        if self._event_factory is None:
            from agentpool_storage.opencode_provider import helpers

            directory = self.working_dir
            self._event_factory = GlobalEventFactory(
                directory=directory,
                project=helpers.compute_project_id(directory),
            )
        return self._event_factory

    def create_background_task(self, coro: Any, _name: str = "") -> asyncio.Task[Any]:
        return asyncio.ensure_future(coro)

    def cancel_all_pending_questions(self) -> list[str]:
        return []


# =============================================================================
# Helpers
# =============================================================================


async def _drain_one(gen: Any) -> dict[str, Any]:
    """Drain one item from the generator and parse JSON."""
    item = await gen.__anext__()
    return json.loads(item["data"])


# =============================================================================
# 1. Legacy path still works
# =============================================================================


@pytest.mark.anyio
async def test_legacy_path_without_session_controller() -> None:
    """Without session_controller, SSE still works via legacy queues."""
    state = _MockState(session_controller=None)
    event = SessionStatusEvent.create("sess-legacy", SessionStatus(type="busy"))

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))

    # Inject via legacy queue
    queue = state.event_subscribers[-1]
    await queue.put(event)
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "session.status"
    assert results[1]["sessionId"] == "sess-legacy"


# =============================================================================
# 2. EventBus events are forwarded to SSE
# =============================================================================


@pytest.mark.anyio
async def test_eventbus_events_forwarded_to_sse() -> None:
    """Events published exclusively to EventBus appear in the SSE stream."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish to EventBus BEFORE starting the generator.
    # The global subscription (scope="all") replays from ALL session buffers.
    event = SessionStatusEvent.create("sess-eb", SessionStatus(type="busy"))
    await event_bus.publish("sess-eb", event)

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))

    # [1] The pre-published event arrives via replay buffer.
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "session.status"
    assert results[1]["sessionId"] == "sess-eb"


# =============================================================================
# 3. Bridge-wrapped CustomEvent deduplication
# =============================================================================


@pytest.mark.anyio
async def test_bridge_wrapped_events_are_deduplicated() -> None:
    """Bridge-wrapped CustomEvent (source=opencode_event_bridge) is skipped."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish a bridge-wrapped event to EventBus.
    wrapped = CustomEvent(
        event_data=SessionStatusEvent.create("sess-dedup", SessionStatus(type="busy")),
        event_type="opencode:session.status",
        source="opencode_event_bridge",
    )
    await event_bus.publish("sess-dedup", wrapped)

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))

    # The wrapped event should be skipped.  Next drain times out → heartbeat.
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "server.heartbeat"


# =============================================================================
# 4. Non-bridge CustomEvent unwrapping
# =============================================================================


@pytest.mark.anyio
async def test_non_bridge_custom_event_unwrapped() -> None:
    """Non-bridge CustomEvent instances have their event_data unwrapped."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish a non-bridge CustomEvent.
    inner = SessionStatusEvent.create("sess-unwrap", SessionStatus(type="busy"))
    wrapped = CustomEvent(
        event_data=inner,
        event_type="my_custom_event",
        source="some_tool",
    )
    await event_bus.publish("sess-unwrap", wrapped)

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))
    # [1] unwrapped event
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "session.status"
    assert results[1]["sessionId"] == "sess-unwrap"


# =============================================================================
# 5. Child session events via global subscription
# =============================================================================


@pytest.mark.anyio
async def test_child_session_events_visible_on_sse() -> None:
    """Events from child sessions reach the SSE stream via scope="all"."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))

    # Publish parent and child events to EventBus.
    parent_event = SessionStatusEvent.create("parent-sess", SessionStatus(type="busy"))
    child_event = SessionStatusEvent.create("child-sess", SessionStatus(type="idle"))
    await event_bus.publish("parent-sess", parent_event)
    await event_bus.publish("child-sess", child_event)

    # [1] parent event
    results.append(await _drain_one(gen))
    # [2] child event (scope="all" receives everything)
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "session.status"
    assert results[1]["sessionId"] == "parent-sess"
    assert results[2]["type"] == "session.status"
    assert results[2]["sessionId"] == "child-sess"


# =============================================================================
# 6. Mixed legacy + EventBus events
# =============================================================================


@pytest.mark.anyio
async def test_mixed_legacy_and_eventbus_events() -> None:
    """Events from both paths are interleaved correctly."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]

    # [0] server.connected
    await _drain_one(gen)

    # Send legacy event
    legacy_queue = state.event_subscribers[-1]
    legacy_event = SessionStatusEvent.create("mixed-legacy", SessionStatus(type="busy"))
    await legacy_queue.put(legacy_event)
    result = await _drain_one(gen)
    assert result["sessionId"] == "mixed-legacy"

    # Send EventBus event
    eb_event = SessionStatusEvent.create("mixed-eb", SessionStatus(type="idle"))
    await event_bus.publish("mixed-eb", eb_event)
    result = await _drain_one(gen)
    assert result["sessionId"] == "mixed-eb"

    # Send another legacy event
    legacy_event2 = SessionStatusEvent.create("mixed-legacy2", SessionStatus(type="busy"))
    await legacy_queue.put(legacy_event2)
    result = await _drain_one(gen)
    assert result["sessionId"] == "mixed-legacy2"


# =============================================================================
# 7. GlobalEvent envelope wrapping with EventBus events
# =============================================================================


@pytest.mark.anyio
async def test_eventbus_events_wrapped_in_global_event() -> None:
    """EventBus events on /global/event are wrapped in GlobalEvent envelopes."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(working_dir="/wrap/eb", session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    event = SessionStatusEvent.create("sess-wrap", SessionStatus(type="busy"))
    await event_bus.publish("sess-wrap", event)

    gen = _event_generator(state, wrap_payload=True)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected (payload wrapped, no directory)
    results.append(await _drain_one(gen))
    # [1] EventBus event (GlobalEvent wrapped)
    results.append(await _drain_one(gen))

    assert results[0]["payload"]["type"] == "server.connected"
    assert "directory" not in results[0]

    wrapped = results[1]
    assert "directory" in wrapped
    assert wrapped["directory"] == "/wrap/eb"
    assert "project" in wrapped
    assert wrapped["payload"]["type"] == "session.status"
    assert wrapped["payload"]["sessionId"] == "sess-wrap"


# =============================================================================
# 8. Cleanup unsubscribes from EventBus
# =============================================================================


@pytest.mark.anyio
async def test_cleanup_unsubscribes_eventbus() -> None:
    """When the generator exits, it unsubscribes from EventBus."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    await _drain_one(gen)  # server.connected

    # Verify the global subscription exists.
    counts_before = await event_bus.get_subscriber_counts()
    assert "__global_sse__" in counts_before
    assert counts_before["__global_sse__"] >= 1

    # Close the generator (simulates client disconnect).
    await gen.aclose()

    # After cleanup, global subscriber should be removed.
    counts_after = await event_bus.get_subscriber_counts()
    assert counts_after.get("__global_sse__", 0) == 0


# =============================================================================
# 9. RichAgentStreamEvent filtering
# =============================================================================


@pytest.mark.anyio
async def test_rich_agent_stream_event_filtered() -> None:
    """RichAgentStreamEvent (e.g. PartStartEvent) is filtered, not yielded."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish a RichAgentStreamEvent to EventBus.
    rich_event = PartStartEvent.text(index=0, content="hello")
    await event_bus.publish("sess-rich", rich_event)

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))

    # The RichAgentStreamEvent lacks a 'type' attribute → filtered.
    # Next drain times out → heartbeat.
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["type"] == "server.heartbeat"


# =============================================================================
# 10. Replay buffer event ordering
# =============================================================================


@pytest.mark.anyio
async def test_replay_buffer_events_in_order() -> None:
    """Events published before generator start are replayed in correct order."""
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish multiple events BEFORE starting the generator.
    event1 = SessionStatusEvent.create("sess-order-1", SessionStatus(type="busy"))
    event2 = SessionStatusEvent.create("sess-order-2", SessionStatus(type="idle"))
    event3 = SessionStatusEvent.create("sess-order-3", SessionStatus(type="busy"))
    await event_bus.publish("sess-order", event1)
    await event_bus.publish("sess-order", event2)
    await event_bus.publish("sess-order", event3)

    gen = _event_generator(state, wrap_payload=False)  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # [0] server.connected
    results.append(await _drain_one(gen))
    # [1-3] replayed events in FIFO order
    results.append(await _drain_one(gen))
    results.append(await _drain_one(gen))
    results.append(await _drain_one(gen))

    assert results[0]["type"] == "server.connected"
    assert results[1]["sessionId"] == "sess-order-1"
    assert results[2]["sessionId"] == "sess-order-2"
    assert results[3]["sessionId"] == "sess-order-3"
    assert results[1]["properties"]["status"]["type"] == "busy"
    assert results[2]["properties"]["status"]["type"] == "idle"
    assert results[3]["properties"]["status"]["type"] == "busy"


# =============================================================================
# 11. Reconnect receives replay buffer events with last_event_id filtering
# =============================================================================


@pytest.mark.anyio
async def test_sse_reconnect_receives_replay() -> None:
    """Reconnect with last_event_id receives only events after that ID.

    Events published before generator start are replayed from the EventBus
    buffer. The last_event_id parameter filters out events the client
    already received.
    """
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish 3 events before starting the generator.
    event1 = SessionStatusEvent.create("sess-replay-1", SessionStatus(type="busy"))
    event2 = SessionStatusEvent.create("sess-replay-2", SessionStatus(type="idle"))
    event3 = SessionStatusEvent.create("sess-replay-3", SessionStatus(type="busy"))
    await event_bus.publish("sess-replay", event1)
    await event_bus.publish("sess-replay", event2)
    await event_bus.publish("sess-replay", event3)

    # Reconnect with last_event_id="1" — client already saw server.connected (id=1).
    # Replay events get ids 2, 3, 4.  With last_id=1, all 3 replay events pass.
    gen = _event_generator(state, wrap_payload=False, last_event_id="1")  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    results.append(await _drain_one(gen))
    results.append(await _drain_one(gen))
    results.append(await _drain_one(gen))

    # server.connected (id=1) is filtered by last_event_id="1".
    # All 3 replayed events are yielded (ids 2, 3, 4 > 1).
    assert len(results) == 3
    assert results[0]["sessionId"] == "sess-replay-1"
    assert results[1]["sessionId"] == "sess-replay-2"
    assert results[2]["sessionId"] == "sess-replay-3"


# =============================================================================
# 12. Event ordering correct after reconnect
# =============================================================================


@pytest.mark.anyio
async def test_sse_reconnect_event_ordering() -> None:
    """Events are replayed in correct order after reconnect.

    Publishes events in a known sequence, reconnects with last_event_id,
    and asserts the replayed events maintain FIFO ordering.
    """
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    # Publish events in a specific order.
    events = [
        SessionStatusEvent.create("sess-ord-1", SessionStatus(type="busy")),
        SessionStatusEvent.create("sess-ord-2", SessionStatus(type="idle")),
        SessionStatusEvent.create("sess-ord-3", SessionStatus(type="busy")),
        SessionStatusEvent.create("sess-ord-4", SessionStatus(type="idle")),
    ]
    for evt in events:
        await event_bus.publish("sess-ord", evt)

    # Reconnect with last_event_id="2" — filter out server.connected (1)
    # and the first replay event (2).
    gen = _event_generator(state, wrap_payload=False, last_event_id="2")  # type: ignore[arg-type]
    results: list[dict[str, Any]] = []

    # Drain the 2 replay events that pass the filter (ids 3 and 4).
    results.append(await _drain_one(gen))
    results.append(await _drain_one(gen))

    assert len(results) == 2
    assert results[0]["sessionId"] == "sess-ord-2"
    assert results[1]["sessionId"] == "sess-ord-3"
    assert results[0]["properties"]["status"]["type"] == "idle"
    assert results[1]["properties"]["status"]["type"] == "busy"


# =============================================================================
# 13. Deduplication with last_event_id
# =============================================================================


@pytest.mark.anyio
async def test_sse_dedup_last_event_id() -> None:
    """Only events after last_event_id are received on reconnect.

    Publishes 3 events, reconnects with last_event_id="3", and asserts
    only the last published event (which gets id=4) is replayed.
    """
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    event1 = SessionStatusEvent.create("sess-dedup-1", SessionStatus(type="busy"))
    event2 = SessionStatusEvent.create("sess-dedup-2", SessionStatus(type="idle"))
    event3 = SessionStatusEvent.create("sess-dedup-3", SessionStatus(type="busy"))
    await event_bus.publish("sess-dedup", event1)
    await event_bus.publish("sess-dedup", event2)
    await event_bus.publish("sess-dedup", event3)

    # Reconnect with last_event_id="3".
    # server.connected gets id=1 (filtered).
    # event1 gets id=2 (filtered).
    # event2 gets id=3 (filtered).
    # event3 gets id=4 (yielded).
    gen = _event_generator(state, wrap_payload=False, last_event_id="3")  # type: ignore[arg-type]

    result = await _drain_one(gen)
    assert result["sessionId"] == "sess-dedup-3"
    assert result["properties"]["status"]["type"] == "busy"


# =============================================================================
# 14. No duplicate events when last_event_id equals last replay event
# =============================================================================


@pytest.mark.anyio
async def test_sse_reconnect_no_duplicate_events() -> None:
    """When last_event_id covers all replayed events, none are duplicated.

    Publishes events, reconnects with last_event_id beyond all replay IDs,
    and asserts only the heartbeat is received (no replay events).
    """
    controller = Mock()
    controller.cancel_all_pending_questions = Mock(return_value=[])

    state = _MockState(session_controller=controller)
    event_bus = state.pool.session_pool.event_bus

    event1 = SessionStatusEvent.create("sess-nodup-1", SessionStatus(type="busy"))
    event2 = SessionStatusEvent.create("sess-nodup-2", SessionStatus(type="idle"))
    await event_bus.publish("sess-nodup", event1)
    await event_bus.publish("sess-nodup", event2)

    # Reconnect with last_event_id="4".
    # server.connected gets id=1 (filtered).
    # event1 gets id=2 (filtered).
    # event2 gets id=3 (filtered).
    # No replay events pass the filter → next drain times out → heartbeat.
    gen = _event_generator(state, wrap_payload=False, last_event_id="4")  # type: ignore[arg-type]

    result = await _drain_one(gen)
    assert result["type"] == "server.heartbeat"
