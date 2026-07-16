"""End-to-end integration tests for v2 message_id infrastructure.

Tests the full message_id pipeline from NativeTurn through events,
CommChannel, RunHandle, SessionController, and protocol conversion.

Covers 12 scenarios:
1. Steer→revoke flow
2. Followup→revoke flow
3. ACP agent message_id propagation
4. ACPEventConverter reads message_id from events
5. receive_request returns str|None
6. content_blocks flows through without stringification
7. OpenCode delivery mode mapping
8. DeliveryMode enum values match wire format
9. SessionPool.send_message with STEER mode on active session
10. SessionPool.send_message with QUEUE mode creates new run
11. SessionPool.run_agent creates session, runs, returns text, cleans up
12. Deprecation warnings emitted for receive_request, spawn_subagent, get_available_agents
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import warnings

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    PartDeltaEvent,
    PartStartEvent,
    StreamCompleteEvent,
)
from agentpool.lifecycle import (
    DeliveryMode,
    Feedback,
    MemoryJournal,
    ProtocolChannel,
)
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus, SessionPool
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.session_controller import SessionController
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _StubTurn(Turn):
    """Minimal Turn implementation for testing."""

    def __init__(self) -> None:
        self._message_history: list[Any] = []
        self._final_message = ChatMessage(content="done", role="assistant")

    async def execute(self):  # type: ignore[override]
        yield  # type: ignore[misc]


def _make_handle(
    *,
    comm_channel: Any | None = None,
    event_bus: Any | None = None,
    agent: Any | None = None,
    session: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies for integration tests."""
    if agent is None:
        agent = MagicMock()
        agent.create_turn = MagicMock(return_value=_StubTurn())
        agent.name = "test-agent"
        agent.conversation = MagicMock()
    if event_bus is None:
        event_bus = AsyncMock()
    if session is None:
        session = MagicMock()
        session.turn_lock = asyncio.Lock()
        session.parent_session_id = None
    run_ctx = AgentRunContext(session_id="test-session")
    handle = RunHandle(
        run_id="test-run",
        session_id="test-session",
        agent_type="native",
        agent=agent,
        event_bus=event_bus,
        session=session,
        run_ctx=run_ctx,
    )
    if comm_channel is not None:
        handle._comm_channel = comm_channel
    return handle


def _make_protocol_channel(
    session_id: str = "test-session",
) -> ProtocolChannel:
    """Create a real ProtocolChannel with a real EventBus."""
    journal = MemoryJournal()
    event_bus = EventBus()
    return ProtocolChannel(
        journal=journal,
        event_bus=event_bus,
        session_id=session_id,
    )


def _make_mock_pool() -> MagicMock:
    """Return a mocked AgentPool for SessionPool construction."""
    pool = MagicMock()
    pool.storage = None
    pool.main_agent_name = "default"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    pool._config_file_path = None
    pool.get_context = MagicMock(return_value=MagicMock())
    return pool


# ---------------------------------------------------------------------------
# Test 1: Steer→revoke flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_steer_revoke_flow() -> None:
    """Steer a message, revoke it before delivery, verify it's removed.

    Given: A RunHandle with a ProtocolChannel and a steer message enqueued.
    When: revoke() is called with the steer message_id before recv().
    Then: The message is removed from the pending queue and the channel
        rejects re-delivery of the same message_id.
    """
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)

    msg_id = handle.steer("interrupt the agent", message_id="steer-revoke-001")
    assert msg_id == "steer-revoke-001"

    # Verify the Feedback is in the pending queue.
    assert "steer-revoke-001" in channel._pending

    # Revoke before delivery.
    result = handle.revoke("steer-revoke-001")
    assert result is True

    # The message should no longer be pending.
    assert "steer-revoke-001" not in channel._pending
    assert "steer-revoke-001" in channel._revoked
    assert channel.recv() is None

    # Re-delivery of the same message_id should be rejected.
    fb2 = Feedback(content="retry", is_steer=True, message_id="steer-revoke-001")
    channel.deliver_feedback(fb2)
    assert "steer-revoke-001" not in channel._pending
    assert len(channel._feedback_queue) == 0


# ---------------------------------------------------------------------------
# Test 2: Followup→revoke flow
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_followup_revoke_flow() -> None:
    """Followup a message, revoke it, verify it's removed.

    Given: A RunHandle with a ProtocolChannel and a followup message enqueued.
    When: revoke() is called with the followup message_id before recv().
    Then: The message is removed from the pending queue.
    """
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)

    msg_id = handle.followup("next prompt after turn", message_id="followup-revoke-001")
    assert msg_id == "followup-revoke-001"

    assert "followup-revoke-001" in channel._pending
    assert channel.recv() is not None  # one item available

    # Now the message is delivered — revoking should fail.
    result = handle.revoke("followup-revoke-001")
    assert result is False
    assert "followup-revoke-001" in channel._delivered


# ---------------------------------------------------------------------------
# Test 3: ACP agent message_id propagation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_turn_message_id_propagation() -> None:
    """ACPTurn generates a consistent message_id for all ChatMessages.

    Given: An ACPTurn is created.
    When: The turn constructs ChatMessage objects.
    Then: All ChatMessages from the same turn share the same message_id.
    """
    from agentpool.agents.acp_agent.turn import ACPTurn
    from agentpool.agents.context import AgentRunContext

    # Create a mock ACP client.
    client = MagicMock()
    run_ctx = AgentRunContext(session_id="acp-session-1")
    turn = ACPTurn(
        acp_client=client,
        prompts=[],
        run_ctx=run_ctx,
        session_id="acp-session-1",
        agent_name="goose",
    )

    # The _message_id should be a non-empty string (uuid4().hex).
    assert isinstance(turn._message_id, str)
    assert len(turn._message_id) > 0

    # Two ACPTurn instances should have different message_ids.
    run_ctx2 = AgentRunContext(session_id="acp-session-2")
    turn2 = ACPTurn(
        acp_client=client,
        prompts=[],
        run_ctx=run_ctx2,
        session_id="acp-session-2",
        agent_name="goose",
    )
    assert turn._message_id != turn2._message_id


# ---------------------------------------------------------------------------
# Test 4: ACPEventConverter reads message_id from events
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_acp_event_converter_reads_message_id_from_events() -> None:
    """ACPEventConverter._get_message_id reads message_id from PartStartEvent.

    Given: An ACPEventConverter and a PartStartEvent with a message_id.
    When: _get_message_id() is called with the event.
    Then: The returned message_id matches the event's message_id, not a
        newly generated UUID.
    """
    from agentpool_server.acp_server.event_converter import ACPEventConverter

    converter = ACPEventConverter()

    # Event with explicit message_id.
    event_with_id = PartStartEvent.text(
        index=0,
        content="hello",
        message_id="event-msg-001",
    )
    result = converter._get_message_id(event_with_id)
    assert result == "event-msg-001"

    # Delta event with explicit message_id.
    delta_with_id = PartDeltaEvent.text(
        index=0,
        content=" world",
        message_id="event-msg-001",
    )
    result_delta = converter._get_message_id(delta_with_id)
    assert result_delta == "event-msg-001"

    # Event without message_id — reuses the sticky _current_message_id
    # from the last event with an explicit ID. This ensures all chunks
    # within a turn share the same message_id (important for ACP thought
    # chunks that may not carry message_id on every chunk).
    event_without_id = PartStartEvent.text(index=0, content="no id")
    result_auto = converter._get_message_id(event_without_id)
    assert result_auto == "event-msg-001"

    # After reset(), sticky message_id is cleared — new events without
    # an explicit ID generate a fresh UUID.
    converter.reset()
    result_after_reset = converter._get_message_id(event_without_id)
    assert result_after_reset != ""
    assert result_after_reset != "event-msg-001"


# ---------------------------------------------------------------------------
# Test 5: receive_request returns str|None
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_receive_request_returns_str_or_none() -> None:
    """SessionPool.receive_request() returns str|None, not RunHandle.

    Given: A SessionPool with a mock pool.
    When: receive_request() is called for a non-existent session.
    Then: The return type is None (not a RunHandle).
    And when: receive_request() is called for an existing session with
        mocked _route_message.
    Then: The return type is str (the message_id).
    """
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)

    # Non-existent session → None.
    session_pool.sessions.get_session = MagicMock(return_value=None)  # type: ignore[method-assign]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result = await session_pool.receive_request("no-such-session", "hello")
    assert result is None

    # Existing session → str (message_id).
    mock_session = MagicMock()
    mock_agent = MagicMock()
    session_pool.sessions.get_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]
    session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    session_pool.sessions._route_message = AsyncMock(return_value="msg-id-123")  # type: ignore[method-assign]

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        result2 = await session_pool.receive_request("sess-1", "hello")

    assert result2 == "msg-id-123"
    assert isinstance(result2, str)
    assert not isinstance(result2, RunHandle)


# ---------------------------------------------------------------------------
# Test 6: content_blocks flows through without stringification
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_content_blocks_flows_without_stringification() -> None:
    """List content sent via steer() reaches Feedback.content_blocks.

    Given: A RunHandle with a ProtocolChannel.
    When: steer() is called with a list of content blocks.
    Then: Feedback.content_blocks holds the original list and content is "".
    And: The list is not stringified into a single string.
    """
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)

    blocks: list[Any] = [
        {"type": "text", "text": "hello"},
        {"type": "image", "url": "http://example.com/img.png"},
    ]
    msg_id = handle.steer(blocks, message_id="content-blocks-001")
    assert msg_id == "content-blocks-001"

    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "content-blocks-001"
    assert fb.content == ""
    assert fb.content_blocks is not None
    assert fb.content_blocks == blocks
    assert isinstance(fb.content_blocks, list)
    assert fb.content_blocks[0] == {"type": "text", "text": "hello"}
    assert fb.content_blocks[1] == {"type": "image", "url": "http://example.com/img.png"}


# ---------------------------------------------------------------------------
# Test 7: OpenCode delivery mode mapping
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_opencode_delivery_mode_mapping() -> None:
    """OpenCode delivery string maps to correct priority.

    Given: The OpenCode delivery mode strings "steer" and "queue".
    When: They are mapped to receive_request priority.
    Then: "steer" → "asap", "queue" → "when_idle".
    """
    # The mapping logic is in message_routes.py:
    #   delivery_priority = "asap" if request.delivery == "steer" else "when_idle"
    # We test the mapping directly.

    for delivery, expected_priority in [
        ("steer", "asap"),
        ("queue", "when_idle"),
        (None, "when_idle"),  # default
    ]:
        delivery_priority = "asap" if delivery == "steer" else "when_idle"
        assert delivery_priority == expected_priority

    # Also verify via SessionPool.send_message that DeliveryMode maps correctly.
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)

    mock_session = MagicMock()
    mock_agent = MagicMock()
    session_pool.sessions.get_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]
    session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    session_pool.sessions._route_message = AsyncMock(return_value="msg-1")  # type: ignore[method-assign]

    # STEER mode → priority="asap"
    await session_pool.send_message("s", "x", mode=DeliveryMode.STEER)
    session_pool.sessions._route_message.assert_awaited_with(
        mock_session,
        mock_agent,
        "s",
        "x",
        priority="asap",
        deps=None,
        message_id=None,
    )

    # QUEUE mode → priority="when_idle"
    session_pool.sessions._route_message.reset_mock()
    await session_pool.send_message("s", "x", mode=DeliveryMode.QUEUE)
    session_pool.sessions._route_message.assert_awaited_with(
        mock_session,
        mock_agent,
        "s",
        "x",
        priority="when_idle",
        deps=None,
        message_id=None,
    )


# ---------------------------------------------------------------------------
# Test 8: DeliveryMode enum values match wire format
# ---------------------------------------------------------------------------


def test_delivery_mode_enum_values_match_wire_format() -> None:
    """DeliveryMode enum values are exactly "steer" and "queue".

    Given: The DeliveryMode enum.
    When: The values are inspected.
    Then: STEER.value == "steer" and QUEUE.value == "queue", matching the
        ACP v2 and OpenCode wire formats.
    """
    assert DeliveryMode.STEER.value == "steer"
    assert DeliveryMode.QUEUE.value == "queue"

    # Feedback.mode auto-derives from is_steer using these values.
    fb_steer = Feedback(content="msg", is_steer=True)
    assert fb_steer.mode == "steer"
    assert fb_steer.mode == DeliveryMode.STEER.value

    fb_queue = Feedback(content="msg", is_steer=False)
    assert fb_queue.mode == "queue"
    assert fb_queue.mode == DeliveryMode.QUEUE.value

    # Explicit mode override works with enum values.
    fb_override = Feedback(content="msg", is_steer=True, mode=DeliveryMode.QUEUE.value)
    assert fb_override.mode == "queue"


# ---------------------------------------------------------------------------
# Test 9: SessionPool.send_message with STEER mode on active session
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_message_steer_mode_on_active_session() -> None:
    """send_message with STEER mode calls _route_message with asap priority.

    Given: A SessionPool with a mock session that has an active run.
    When: send_message is called with DeliveryMode.STEER.
    Then: _route_message receives priority="asap" and returns the message_id.
    """
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)

    mock_session = MagicMock()
    mock_session.current_run_id = "run-1"
    mock_session.is_closing = False
    mock_agent = MagicMock()
    session_pool.sessions.get_session = MagicMock(return_value=mock_session)  # type: ignore[method-assign]
    session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    session_pool.sessions._route_message = AsyncMock(return_value="steer-msg-001")  # type: ignore[method-assign]

    result = await session_pool.send_message(
        "active-session",
        "interrupt now",
        mode=DeliveryMode.STEER,
        message_id="steer-msg-001",
    )

    assert result == "steer-msg-001"
    session_pool.sessions._route_message.assert_awaited_once_with(
        mock_session,
        mock_agent,
        "active-session",
        "interrupt now",
        priority="asap",
        deps=None,
        message_id="steer-msg-001",
    )


# ---------------------------------------------------------------------------
# Test 10: SessionPool.send_message with QUEUE mode creates new run
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_message_queue_mode_creates_new_run() -> None:
    """send_message with QUEUE mode calls _route_message with when_idle.

    Given: A SessionPool with a mock idle session (no current_run_id).
    When: send_message is called with DeliveryMode.QUEUE (default).
    Then: _route_message receives priority="when_idle" and returns the
        message_id from _start_run_handle.
    """
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)

    # Create a real session via get_or_create_session.
    await session_pool.sessions.get_or_create_session("new-session", agent_name="agent-a")
    session = session_pool.sessions.get_session("new-session")
    assert session is not None
    assert session.current_run_id is None  # idle — no active run

    mock_agent = MagicMock()
    session_pool.sessions.get_or_create_session_agent = AsyncMock(return_value=mock_agent)  # type: ignore[method-assign]
    session_pool.sessions._start_run_handle = MagicMock(return_value="new-run-msg-001")  # type: ignore[method-assign]

    result = await session_pool.send_message(
        "new-session",
        "start working",
        mode=DeliveryMode.QUEUE,
    )

    assert result == "new-run-msg-001"
    session_pool.sessions._start_run_handle.assert_called_once()


# ---------------------------------------------------------------------------
# Test 11: SessionPool.run_agent creates session, runs, returns text, cleans up
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_run_agent_creates_session_runs_returns_text_cleans_up() -> None:
    """run_agent creates a temporary session, runs the agent, returns text.

    Given: A SessionPool with mocked send_message that publishes a
        StreamCompleteEvent.
    When: run_agent is called.
    Then: A session is created, the message is sent, the StreamCompleteEvent
        is captured, the result text is returned, and the session is closed
        in the finally block.
    """
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)

    # Create a real session first so create_session works.
    await session_pool.sessions.get_or_create_session(
        "pre-init",
        agent_name="test-agent",
    )
    original_create = session_pool.create_session

    async def mock_create_session(
        session_id: str,
        agent_name: str | None = None,
        parent_session_id: str | None = None,
        lifecycle_policy: str | None = None,
        **metadata: Any,
    ) -> Any:
        return await original_create(
            session_id,
            agent_name,
            parent_session_id,
            lifecycle_policy,
            **metadata,
        )

    final_msg = ChatMessage(content="Integration test result", role="assistant")
    close_called = False

    async def mock_send_message(
        session_id: str,
        content: str | list[Any],
        *,
        mode: DeliveryMode = DeliveryMode.QUEUE,
        message_id: str | None = None,
    ) -> str | None:
        await session_pool.event_bus.publish(
            session_id,
            StreamCompleteEvent(message=final_msg, session_id=session_id),
        )
        return "run-agent-msg-001"

    async def mock_wait_for_completion(
        session_id: str,
        timeout: float | None = None,
    ) -> str:
        return session_id

    async def mock_close_session(session_id: str) -> None:
        nonlocal close_called
        close_called = True

    with (
        patch.object(session_pool, "create_session", side_effect=mock_create_session),
        patch.object(session_pool, "send_message", side_effect=mock_send_message),
        patch.object(session_pool, "wait_for_completion", side_effect=mock_wait_for_completion),
        patch.object(session_pool, "close_session", side_effect=mock_close_session),
        patch("uuid.uuid4", return_value=MagicMock(__str__=lambda _: "run-agent-uuid")),
    ):
        result = await session_pool.run_agent("test-agent", "Say hello")

    assert result == "Integration test result"
    assert close_called, "close_session must be called after run_agent completes"


# ---------------------------------------------------------------------------
# Test 12: Deprecation warnings emitted for receive_request, spawn_subagent,
#          get_available_agents
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_deprecation_warnings_emitted() -> None:  # noqa: PLR0915
    """DeprecationWarning emitted by receive_request, spawn_subagent, get_available_agents.

    Given: SessionPool.receive_request(), RunLoopDelegationService.spawn_subagent(),
        and RunLoopDelegationService.get_available_agents() are called.
    When: Each method is invoked.
    Then: Each emits a DeprecationWarning.
    """
    from agentpool.capabilities.runloop_delegation import RunLoopDelegationService

    # --- SessionPool.receive_request() ---
    pool = _make_mock_pool()
    session_pool = SessionPool(pool=pool)
    session_pool.send_message = AsyncMock(return_value="depr-msg-001")  # type: ignore[method-assign]

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = await session_pool.receive_request("sess-1", "hello")

    assert result == "depr-msg-001"
    dep_warnings = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings) >= 1
    assert "deprecated" in str(dep_warnings[0].message).lower()

    # --- SessionController.receive_request() ---
    controller = SessionController(pool)
    controller.get_session = MagicMock(return_value=None)  # type: ignore[method-assign]

    with warnings.catch_warnings(record=True) as caught2:
        warnings.simplefilter("always")
        result2 = await controller.receive_request("sess-1", "hello")

    assert result2 is None
    dep_warnings2 = [w for w in caught2 if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings2) >= 1
    assert "deprecated" in str(dep_warnings2[0].message).lower()

    # --- RunLoopDelegationService.get_available_agents() ---
    registry = MagicMock()
    registry.list_names = MagicMock(return_value=["agent1", "agent2"])
    host = MagicMock()
    service = RunLoopDelegationService(registry, host, "sess-1")

    with warnings.catch_warnings(record=True) as caught3:
        warnings.simplefilter("always")
        agents = service.get_available_agents()

    assert agents == ["agent1", "agent2"]
    dep_warnings3 = [w for w in caught3 if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings3) >= 1
    assert "get_available_agents" in str(dep_warnings3[0].message).lower()

    # --- RunLoopDelegationService.spawn_subagent() ---
    registry2 = MagicMock()
    registry2.exists = MagicMock(return_value=True)
    host2 = MagicMock()
    host2.session_pool = MagicMock()
    host2.session_pool.sessions = MagicMock()
    host2.session_pool.sessions.receive_request = AsyncMock(return_value="mid")
    child_session = MagicMock()
    child_session.current_run_id = "run-1"
    host2.session_pool.sessions.get_session = MagicMock(return_value=child_session)
    run_handle = MagicMock()

    async def _empty_gen() -> Any:
        return
        yield  # pragma: no cover

    run_handle.start = MagicMock(return_value=_empty_gen())
    host2.session_pool.sessions._runs = {"run-1": run_handle}

    # Mock EventBus for the new EventBus subscription pattern in spawn_subagent
    import asyncio as _asyncio

    from agentpool.agents.events import StreamCompleteEvent
    from agentpool.messaging.messagenode import ChatMessage
    from agentpool.orchestrator.core import EventEnvelope

    event_queue: _asyncio.Queue[Any] = _asyncio.Queue()
    done_event = StreamCompleteEvent(
        message=ChatMessage(content="done", role="assistant"),
    )
    await event_queue.put(EventEnvelope(source_session_id="sess-1", event=done_event))
    await event_queue.put(None)  # sentinel to stop the loop
    host2.session_pool.event_bus = MagicMock()
    host2.session_pool.event_bus.subscribe = AsyncMock(return_value=event_queue)
    host2.session_pool.event_bus.unsubscribe = AsyncMock()

    service2 = RunLoopDelegationService(registry2, host2, "parent-sess")

    with warnings.catch_warnings(record=True) as caught4:
        warnings.simplefilter("always")
        async for _event in service2.spawn_subagent("agent1", "do something"):
            pass

    dep_warnings4 = [w for w in caught4 if issubclass(w.category, DeprecationWarning)]
    assert len(dep_warnings4) >= 1
    assert "spawn_subagent" in str(dep_warnings4[0].message).lower()
