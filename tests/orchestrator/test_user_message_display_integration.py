"""Integration tests for user message display bugs in the OpenCode TUI.

Reproduces three bugs identified in PR #289/#298:
- Bug 1 (FIXED): ``handle_enqueued_messages()`` generates UUID message_ids
  when FIFO queue is empty → spurious messages.
- Bug 2 (FIXED): ``message_routes.py`` persists to storage AND EventProcessor
  creates message → duplicates.
- Bug 3 (NOT FIXED): ``_route_message()`` steer path emits
  ``UserMessageInsertedEvent`` at routing time (wrong timing), causing
  messages to appear at wrong position in TUI.

These tests use a **real AgentPool** with **FunctionModel** (deterministic,
no real model calls) and the **full event pipeline** (EventBus → consumer).
They are designed to **FAIL** on the current code to reproduce the bugs,
so we can verify the fix.

The key challenge is timing: with ``TestModel``, turns complete instantly,
so steers arrive after the turn finishes.  To reproduce the mid-turn steer
scenario, we use a ``FunctionModel`` that blocks on an ``asyncio.Event``
until the test releases it, keeping the turn active while the steer is sent.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from pydantic_ai.models.function import AgentInfo, FunctionModel
import pytest

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig
from agentpool.agents.events.events import (
    StreamCompleteEvent,
    UserMessageInsertedEvent,
)
from agentpool.lifecycle.types import DeliveryMode
from agentpool.orchestrator.event_bus import EventBus, EventEnvelope


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.session_pool import SessionPool


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_timestamp_id(message_id: str) -> bool:
    """Check if message_id is timestamp-encoded (from ``ascending()``).

    Timestamp-encoded IDs start with ``msg_`` followed by a hex timestamp.
    Random UUIDs do NOT start with ``msg_``.
    """
    return message_id.startswith("msg_")


async def _drain_events(
    queue: asyncio.Queue[EventEnvelope],
    *,
    timeout: float = 10.0,
    until: type | None = None,
) -> list[Any]:
    """Drain events from an EventBus subscription queue.

    Args:
        queue: The asyncio.Queue from ``event_bus.subscribe()``.
        timeout: Maximum wall-clock time to wait.
        until: If set, stop after receiving an event of this type.

    Returns:
        List of unwrapped events (``envelope.event``).
    """
    events: list[Any] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            async with asyncio.timeout(remaining):
                envelope = await queue.get()
        except TimeoutError:
            break
        event = envelope.event if isinstance(envelope, EventEnvelope) else envelope
        events.append(event)
        if until is not None and isinstance(event, until):
            break
    return events


async def _drain_remaining(
    queue: asyncio.Queue[EventEnvelope],
    *,
    timeout: float = 1.0,
) -> list[Any]:
    """Drain any remaining events that are immediately available.

    After the main drain, this picks up late-arriving events (e.g. the
    ``UserMessageInsertedEvent`` emitted after ``StreamCompleteEvent`` due
    to Bug 3).
    """
    events: list[Any] = []
    try:
        async with asyncio.timeout(timeout):
            while True:
                envelope = await queue.get()
                event = envelope.event if isinstance(envelope, EventEnvelope) else envelope
                events.append(event)
    except TimeoutError:
        pass
    return events


def _make_blocking_model(
    release_event: asyncio.Event,
    *,
    response_text: str = "Done processing",
) -> FunctionModel:
    """Create a FunctionModel that blocks until ``release_event`` is set.

    The stream function awaits the event before yielding text chunks.
    This keeps the agent turn active (model call in-flight) so steers can
    be injected mid-turn.
    """

    async def _stream_fn(messages: list[Any], info: AgentInfo) -> Any:
        """Async generator that blocks, then yields the response text."""
        await release_event.wait()
        yield response_text

    return FunctionModel(stream_function=_stream_fn)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def integration_pool() -> AsyncIterator[AgentPool]:
    """Real AgentPool with TestModel for basic integration testing.

    Creates a pool with a single ``conductor`` agent using TestModel.
    The pool has a real SessionPool with EventBus enabled.
    """
    config = AgentsManifest(
        agents={
            "conductor": NativeAgentConfig(
                name="conductor",
                model="test",
                system_prompt="You are a conductor agent.",
            ),
        },
    )
    async with AgentPool(config) as pool:
        yield pool


@pytest.fixture
async def blocking_pool() -> AsyncIterator[tuple[AgentPool, asyncio.Event, Any]]:
    """AgentPool with a FunctionModel that blocks on an asyncio.Event.

    Returns a tuple of (pool, release_event, setup_session).  The
    ``setup_session`` coroutine creates a session and sets the blocking
    model on that session's agent, so the turn blocks until
    ``release_event.set()`` is called.

    This is essential for reproducing the timing-dependent Bug 3 where
    ``_route_message()`` emits ``UserMessageInsertedEvent`` at the wrong
    time relative to ``StreamCompleteEvent``.
    """
    release_event = asyncio.Event()

    config = AgentsManifest(
        agents={
            "conductor": NativeAgentConfig(
                name="conductor",
                model="test",
                system_prompt="You are a conductor agent.",
            ),
        },
    )
    async with AgentPool(config) as pool:
        sp = pool.session_pool
        assert sp is not None

        async def setup_session(session_id: str) -> None:
            """Create a session and swap in the blocking model on its agent."""
            await sp.sessions.get_or_create_session(session_id, agent_name="conductor")
            agent = await sp.sessions.get_or_create_session_agent(
                session_id, agent_name="conductor"
            )
            agent._model = _make_blocking_model(release_event)

        yield pool, release_event, setup_session


@pytest.fixture
def session_pool(integration_pool: AgentPool) -> SessionPool:
    """Return the SessionPool from the integration pool."""
    sp = integration_pool.session_pool
    assert sp is not None, "SessionPool must be initialized"
    return sp


@pytest.fixture
def event_bus(session_pool: SessionPool) -> EventBus:
    """Return the EventBus from the SessionPool."""
    return session_pool.event_bus


# ---------------------------------------------------------------------------
# Scenario 1: Initial prompt display
# ---------------------------------------------------------------------------


async def test_scenario_1_initial_prompt_display(
    integration_pool: AgentPool,
    session_pool: SessionPool,
    event_bus: EventBus,
) -> None:
    """Initial prompt produces exactly one UserMessageInsertedEvent.

    Verifies:
    - Exactly 1 ``UserMessageInsertedEvent`` with ``delivery="initial"``
    - Content is non-empty
    - ``message_id`` is timestamp-encoded (starts with ``msg_``)
    """
    session_id = "test-s1-initial"
    await session_pool.sessions.get_or_create_session(session_id, agent_name="conductor")

    # Subscribe BEFORE sending the message
    queue = await event_bus.subscribe(session_id, scope="session")

    # Send initial prompt
    await session_pool.send_message(session_id, "Hello conductor", mode=DeliveryMode.QUEUE)

    # Drain events until StreamCompleteEvent
    events = await _drain_events(queue, timeout=10.0, until=StreamCompleteEvent)

    # Filter for UserMessageInsertedEvent
    user_msg_events = [e for e in events if isinstance(e, UserMessageInsertedEvent)]

    # Assert exactly 1 UserMessageInsertedEvent
    assert len(user_msg_events) == 1, (
        f"Expected exactly 1 UserMessageInsertedEvent, got {len(user_msg_events)}. "
        f"Event types: {[type(e).__name__ for e in events]}"
    )

    event = user_msg_events[0]
    assert event.delivery == "initial", (
        f"Expected delivery='initial', got delivery='{event.delivery}'"
    )
    assert event.content, f"Content should be non-empty: {event.content!r}"
    assert _is_timestamp_id(event.message_id), (
        f"Expected timestamp-encoded message_id (msg_...), got: {event.message_id!r}"
    )


# ---------------------------------------------------------------------------
# Scenario 2: Steer mid-turn display
# ---------------------------------------------------------------------------


async def test_scenario_2_steer_mid_turn_display(
    blocking_pool: tuple[AgentPool, asyncio.Event, Any],
) -> None:
    """Steer mid-turn produces exactly one UserMessageInsertedEvent before StreamCompleteEvent.

    Uses a blocking model so the turn is still active when the steer is sent.

    Verifies:
    - Exactly 1 ``UserMessageInsertedEvent`` for the steer (not 2, not 0)
    - The steer event arrives BEFORE ``StreamCompleteEvent``
    - ``message_id`` is timestamp-encoded (starts with ``msg_``)
    - Content is non-empty
    - ``source`` is ``"enqueued"`` (from ``EnqueuedMessagesEvent`` mapping),
      NOT ``"internal"`` or ``"protocol"`` (from ``_route_message()``)

    Bug 3: ``_route_message()`` emits the event at routing time with
    ``source="protocol"``, but ``handle_enqueued_messages()`` (which should
    emit with ``source="enqueued"``) never fires because
    ``EnqueuedMessagesEvent`` is not captured by ``NativeTurn.execute()``.
    The fix should: (1) capture ``EnqueuedMessagesEvent``, (2) remove the
    ``_route_message()`` emission for steers.
    """
    pool, release_event, setup_session = blocking_pool
    sp = pool.session_pool
    assert sp is not None
    bus = sp.event_bus

    session_id = "test-s2-steer"
    await setup_session(session_id)

    # Subscribe BEFORE sending anything
    queue = await bus.subscribe(session_id, scope="session")

    # Start a turn (model blocks on release_event)
    await sp.send_message(session_id, "Start working", mode=DeliveryMode.QUEUE)

    # Give the turn a moment to start (model is now blocking)
    await asyncio.sleep(0.3)

    # Steer mid-turn (turn is still active because model is blocking)
    await sp.send_message(session_id, "Actually, do this instead", mode=DeliveryMode.STEER)

    # Give the steer a moment to be processed
    await asyncio.sleep(0.1)

    # Release the blocking model so the turn can complete
    release_event.set()

    # Drain events until StreamCompleteEvent
    events = await _drain_events(queue, timeout=10.0, until=StreamCompleteEvent)

    # Also drain any remaining events (late-arriving UserMessageInsertedEvent)
    remaining = await _drain_remaining(queue, timeout=1.0)
    events.extend(remaining)

    # Filter for UserMessageInsertedEvent
    user_msg_events = [e for e in events if isinstance(e, UserMessageInsertedEvent)]
    steer_events = [e for e in user_msg_events if e.delivery == "steer"]

    # Assert exactly 1 steer UserMessageInsertedEvent
    assert len(steer_events) == 1, (
        f"Expected exactly 1 steer UserMessageInsertedEvent, got {len(steer_events)}. "
        f"All user msg events: {[(e.delivery, e.source, e.message_id) for e in user_msg_events]}. "
        f"All event types: {[type(e).__name__ for e in events]}"
    )

    steer_event = steer_events[0]
    assert steer_event.content, f"Steer event content should be non-empty: {steer_event.content!r}"
    assert _is_timestamp_id(steer_event.message_id), (
        f"Expected timestamp-encoded message_id (msg_...), got: {steer_event.message_id!r}"
    )
    # Bug 3: source should be "enqueued" (from EnqueuedMessagesEvent mapping
    # at model-processing time), not "protocol" (from _route_message() at
    # routing time).  Currently fails because handle_enqueued_messages()
    # never fires — EnqueuedMessagesEvent is not captured by NativeTurn.
    assert steer_event.source == "enqueued", (
        f"Expected source='enqueued' (from EnqueuedMessagesEvent mapping), "
        f"got source={steer_event.source!r}. "
        f"Bug 3: _route_message() emits at routing time with source='protocol', "
        f"but handle_enqueued_messages() never fires."
    )

    # Verify steer event arrives BEFORE StreamCompleteEvent
    steer_idx = events.index(steer_event)
    complete_indices = [i for i, e in enumerate(events) if isinstance(e, StreamCompleteEvent)]
    assert len(complete_indices) > 0, "No StreamCompleteEvent received"
    complete_idx = complete_indices[0]
    assert steer_idx < complete_idx, (
        f"Steer event (index {steer_idx}) should arrive BEFORE "
        f"StreamCompleteEvent (index {complete_idx}). "
        f"Event types: {[type(e).__name__ for e in events]}"
    )


# ---------------------------------------------------------------------------
# Scenario 3: Team member steer (the main bug)
# ---------------------------------------------------------------------------


async def test_scenario_3_team_member_steer(
    blocking_pool: tuple[AgentPool, asyncio.Event, Any],
) -> None:
    """Team member steer via ``steer_from_background_task()``.

    This is the scenario the user is seeing: a team member (e.g., critic)
    sends a result to the lead (conductor) via steer. The lead should see
    the team member's message at the correct position (before the lead's
    response to it).

    Uses a blocking model so the turn is still active when the steer is sent.

    Verifies:
    - Exactly 1 ``UserMessageInsertedEvent`` with correct content
    - Correct timing (before StreamCompleteEvent)
    - Correct message_id (timestamp-encoded, not random UUID)
    - Content is non-empty

    Bug 3: ``steer_from_background_task()`` emits ``UserMessageInsertedEvent``
    at call time, but the steer may not be processed until later.  The event
    arrives AFTER ``StreamCompleteEvent`` (wrong position in TUI).
    """
    pool, release_event, setup_session = blocking_pool
    sp = pool.session_pool
    assert sp is not None
    bus = sp.event_bus

    session_id = "test-s3-team-steer"
    await setup_session(session_id)

    # Subscribe BEFORE sending anything
    queue = await bus.subscribe(session_id, scope="session")

    # Start a turn on the lead agent (model blocks on release_event)
    await sp.send_message(session_id, "Coordinate the team", mode=DeliveryMode.QUEUE)

    # Give the turn a moment to start (model is now blocking)
    await asyncio.sleep(0.3)

    # Simulate team member sending a result via steer_from_background_task
    team_message = "Critic completed review: looks good"
    await sp.steer_from_background_task(session_id, team_message)

    # Give the steer a moment to be processed
    await asyncio.sleep(0.1)

    # Release the blocking model so the turn can complete
    release_event.set()

    # Drain events until StreamCompleteEvent
    events = await _drain_events(queue, timeout=10.0, until=StreamCompleteEvent)

    # Also drain any remaining events (late-arriving UserMessageInsertedEvent)
    remaining = await _drain_remaining(queue, timeout=1.0)
    events.extend(remaining)

    # Filter for UserMessageInsertedEvent
    user_msg_events = [e for e in events if isinstance(e, UserMessageInsertedEvent)]
    steer_events = [e for e in user_msg_events if e.delivery == "steer"]

    # Assert exactly 1 steer UserMessageInsertedEvent
    assert len(steer_events) == 1, (
        f"Expected exactly 1 steer UserMessageInsertedEvent from background task, "
        f"got {len(steer_events)}. "
        f"All user msg events: "
        f"{[(e.delivery, e.source, e.message_id, e.content) for e in user_msg_events]}. "
        f"All event types: {[type(e).__name__ for e in events]}"
    )

    steer_event = steer_events[0]
    assert steer_event.content, f"Steer event content should be non-empty: {steer_event.content!r}"
    assert _is_timestamp_id(steer_event.message_id), (
        f"Expected timestamp-encoded message_id (msg_...), got: {steer_event.message_id!r}"
    )
    # Bug 3: source should be "enqueued" (from EnqueuedMessagesEvent mapping
    # at model-processing time), not "internal" (from
    # steer_from_background_task() at call time).  Currently fails because
    # handle_enqueued_messages() never fires.
    assert steer_event.source == "enqueued", (
        f"Expected source='enqueued' (from EnqueuedMessagesEvent mapping), "
        f"got source={steer_event.source!r}. "
        f"Bug 3: steer_from_background_task() emits at call time with "
        f"source='internal', but handle_enqueued_messages() never fires."
    )

    # Verify steer event arrives BEFORE StreamCompleteEvent
    # This is the key assertion for Bug 3: the steer event should arrive
    # BEFORE the turn completes, not after.
    steer_idx = events.index(steer_event)
    complete_indices = [i for i, e in enumerate(events) if isinstance(e, StreamCompleteEvent)]
    assert len(complete_indices) > 0, "No StreamCompleteEvent received"
    complete_idx = complete_indices[0]
    assert steer_idx < complete_idx, (
        f"Team member steer event (index {steer_idx}) should arrive BEFORE "
        f"StreamCompleteEvent (index {complete_idx}). "
        f"This is Bug 3: the steer event arrives after the turn completes, "
        f"causing the message to appear at the wrong position in the TUI. "
        f"Event types: {[type(e).__name__ for e in events]}"
    )


# ---------------------------------------------------------------------------
# Scenario 4: Background task steer
# ---------------------------------------------------------------------------


async def test_scenario_4_background_task_steer(
    blocking_pool: tuple[AgentPool, asyncio.Event, Any],
) -> None:
    """``steer_from_background_task()`` produces exactly one UserMessageInsertedEvent.

    Uses a blocking model so the turn is still active when the steer is sent.

    Verifies:
    - Exactly 1 ``UserMessageInsertedEvent`` (not 2 from manual emission + steer())
    - ``message_id`` is timestamp-encoded (starts with ``msg_``)
    - Content is non-empty
    - Event arrives before StreamCompleteEvent

    ``steer_from_background_task()`` emits its own ``UserMessageInsertedEvent``
    and then calls ``run.steer(emit_user_message=False)``. If ``_route_message()``
    is also called (e.g., via ``send_message(STEER)``), it would emit a second
    event. This test verifies only one event is produced.
    """
    pool, release_event, setup_session = blocking_pool
    sp = pool.session_pool
    assert sp is not None
    bus = sp.event_bus

    session_id = "test-s4-bg-steer"
    await setup_session(session_id)

    # Subscribe BEFORE sending anything
    queue = await bus.subscribe(session_id, scope="session")

    # Start a turn (model blocks on release_event)
    await sp.send_message(session_id, "Start processing", mode=DeliveryMode.QUEUE)

    # Give the turn a moment to start (model is now blocking)
    await asyncio.sleep(0.3)

    # Steer from background task
    bg_message = "Background task completed with result: 42"
    await sp.steer_from_background_task(session_id, bg_message)

    # Give the steer a moment to be processed
    await asyncio.sleep(0.1)

    # Release the blocking model so the turn can complete
    release_event.set()

    # Drain events until StreamCompleteEvent
    events = await _drain_events(queue, timeout=10.0, until=StreamCompleteEvent)

    # Also drain any remaining events
    remaining = await _drain_remaining(queue, timeout=1.0)
    events.extend(remaining)

    # Filter for UserMessageInsertedEvent
    user_msg_events = [e for e in events if isinstance(e, UserMessageInsertedEvent)]

    # Count steer events (should be exactly 1, not 2)
    steer_events = [e for e in user_msg_events if e.delivery == "steer"]
    assert len(steer_events) == 1, (
        f"Expected exactly 1 steer UserMessageInsertedEvent, got {len(steer_events)}. "
        f"This suggests duplicate emission from both "
        f"steer_from_background_task() and _route_message(). "
        f"All user msg events: {[(e.delivery, e.source, e.message_id) for e in user_msg_events]}"
    )

    event = steer_events[0]
    assert event.content, f"Content should be non-empty: {event.content!r}"
    assert _is_timestamp_id(event.message_id), (
        f"Expected timestamp-encoded message_id (msg_...), got: {event.message_id!r}"
    )
    # Bug 3: source should be "enqueued" (from EnqueuedMessagesEvent mapping),
    # not "internal" (from steer_from_background_task()).
    assert event.source == "enqueued", (
        f"Expected source='enqueued' (from EnqueuedMessagesEvent mapping), "
        f"got source={event.source!r}. "
        f"Bug 3: steer_from_background_task() emits at call time with "
        f"source='internal', but handle_enqueued_messages() never fires."
    )

    # Verify steer event arrives BEFORE StreamCompleteEvent
    steer_idx = events.index(event)
    complete_indices = [i for i, e in enumerate(events) if isinstance(e, StreamCompleteEvent)]
    if len(complete_indices) > 0:
        complete_idx = complete_indices[0]
        assert steer_idx < complete_idx, (
            f"Background task steer event (index {steer_idx}) should arrive BEFORE "
            f"StreamCompleteEvent (index {complete_idx}). "
            f"Event types: {[type(e).__name__ for e in events]}"
        )


# ---------------------------------------------------------------------------
# Scenario 5: Multiple rapid steers
# ---------------------------------------------------------------------------


async def test_scenario_5_multiple_rapid_steers(
    blocking_pool: tuple[AgentPool, asyncio.Event, Any],
) -> None:
    """Multiple rapid steers produce unique UserMessageInsertedEvents.

    Uses a blocking model so the turn is still active when steers are sent.

    Sends 3 steer messages in rapid succession and verifies:
    - 3 ``UserMessageInsertedEvent`` events, each with unique timestamp-encoded
      ``message_id``
    - Each with non-empty content
    - All arrive before the final ``StreamCompleteEvent``
    """
    pool, release_event, setup_session = blocking_pool
    sp = pool.session_pool
    assert sp is not None
    bus = sp.event_bus

    session_id = "test-s5-multi-steer"
    await setup_session(session_id)

    # Subscribe BEFORE sending anything
    queue = await bus.subscribe(session_id, scope="session")

    # Start a turn (model blocks on release_event)
    await sp.send_message(session_id, "Start working on multiple tasks", mode=DeliveryMode.QUEUE)

    # Give the turn a moment to start (model is now blocking)
    await asyncio.sleep(0.3)

    # Send 3 rapid steers
    steer_messages = [
        "Steer message 1: check this",
        "Steer message 2: also check that",
        "Steer message 3: final check",
    ]
    for msg in steer_messages:
        await sp.steer_from_background_task(session_id, msg)
        await asyncio.sleep(0.05)  # Small delay to ensure ordering

    # Give steers a moment to be processed
    await asyncio.sleep(0.1)

    # Release the blocking model so the turn can complete
    release_event.set()

    # Drain events until StreamCompleteEvent
    events = await _drain_events(queue, timeout=10.0, until=StreamCompleteEvent)

    # Also drain any remaining events
    remaining = await _drain_remaining(queue, timeout=1.0)
    events.extend(remaining)

    # Filter for steer UserMessageInsertedEvent
    steer_events = [
        e for e in events if isinstance(e, UserMessageInsertedEvent) and e.delivery == "steer"
    ]

    # Assert exactly 3 steer events
    all_user_msgs = [
        (e.delivery, e.message_id) for e in events if isinstance(e, UserMessageInsertedEvent)
    ]
    assert len(steer_events) == 3, (
        f"Expected 3 steer UserMessageInsertedEvent events, got {len(steer_events)}. "
        f"All user msg events: {all_user_msgs}. "
        f"All event types: {[type(e).__name__ for e in events]}"
    )

    # Each should have a unique timestamp-encoded message_id
    message_ids = [e.message_id for e in steer_events]
    assert len(set(message_ids)) == 3, (
        f"Expected 3 unique message_ids, got duplicates: {message_ids}"
    )
    for mid in message_ids:
        assert _is_timestamp_id(mid), (
            f"Expected timestamp-encoded message_id (msg_...), got: {mid!r}"
        )

    # Each should have non-empty content
    for i, event in enumerate(steer_events):
        assert event.content, f"Steer event {i} content should be non-empty: {event.content!r}"
        # Bug 3: source should be "enqueued" (from EnqueuedMessagesEvent
        # mapping), not "internal" (from steer_from_background_task()).
        assert event.source == "enqueued", (
            f"Steer event {i}: Expected source='enqueued' (from "
            f"EnqueuedMessagesEvent mapping), got source={event.source!r}. "
            f"Bug 3: steer_from_background_task() emits at call time with "
            f"source='internal', but handle_enqueued_messages() never fires."
        )

    # All steer events should arrive before StreamCompleteEvent
    complete_indices = [i for i, e in enumerate(events) if isinstance(e, StreamCompleteEvent)]
    assert len(complete_indices) > 0, "No StreamCompleteEvent received"
    complete_idx = complete_indices[0]
    for i, steer_event in enumerate(steer_events):
        steer_idx = events.index(steer_event)
        assert steer_idx < complete_idx, (
            f"Steer event {i} (index {steer_idx}) should arrive BEFORE "
            f"StreamCompleteEvent (index {complete_idx})"
        )
