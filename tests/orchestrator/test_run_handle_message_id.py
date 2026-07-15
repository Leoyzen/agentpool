"""Unit tests for v2 message_id infrastructure in RunHandle.

Tests the steer/followup/revoke signature changes, content_blocks
support, message_id propagation, and D17 initial-prompt-via-followup
behavior.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.lifecycle import ProtocolChannel
from agentpool.lifecycle.comm_channel import DirectChannel
from agentpool.lifecycle.journal import MemoryJournal
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import EventBus
from agentpool.orchestrator.run import RunHandle
from agentpool.orchestrator.turn import Turn


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
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
    run_state: Any = None,
    agent: Any | None = None,
    session: Any | None = None,
    event_bus: Any | None = None,
) -> RunHandle:
    """Create a RunHandle with mocked dependencies."""
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
    if run_state is not None:
        handle._run_state = run_state
    return handle


def _make_protocol_channel() -> ProtocolChannel:
    """Create a ProtocolChannel with real EventBus."""
    journal = MemoryJournal()
    event_bus = EventBus()
    return ProtocolChannel(journal=journal, event_bus=event_bus, session_id="test-session")


# ---------------------------------------------------------------------------
# steer() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_with_explicit_message_id() -> None:
    """steer() with explicit message_id returns that ID."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.steer("hello", message_id="custom-msg-001")
    assert result == "custom-msg-001"
    # Verify Feedback in channel has the right message_id.
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "custom-msg-001"
    assert fb.content == "hello"
    assert fb.content_blocks is None
    assert fb.is_steer is True


@pytest.mark.unit
async def test_steer_auto_generates_message_id() -> None:
    """steer() without message_id auto-generates a UUID string."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.steer("hello")
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0
    # Should be a UUID format (36 chars with dashes).
    assert len(result) == 36


@pytest.mark.unit
async def test_steer_with_list_content_blocks() -> None:
    """steer() with list message stores in content_blocks, content=''."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    blocks: list[Any] = ["text part", {"type": "image", "url": "http://example.com/img.png"}]
    result = handle.steer(blocks, message_id="list-msg-001")
    assert result == "list-msg-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "list-msg-001"
    assert fb.content == ""
    assert fb.content_blocks == blocks
    assert fb.is_steer is True


@pytest.mark.unit
async def test_steer_returns_none_when_closing() -> None:
    """steer() returns None when handle is closing."""
    handle = _make_handle()
    handle._closing = True
    result = handle.steer("message")
    assert result is None


@pytest.mark.unit
async def test_steer_raises_after_close() -> None:
    """steer() raises RuntimeError after close()."""
    handle = _make_handle()
    handle._closed = True
    with pytest.raises(RuntimeError, match="Cannot steer after close"):
        handle.steer("message")


# ---------------------------------------------------------------------------
# followup() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_followup_with_explicit_message_id() -> None:
    """followup() with explicit message_id returns that ID."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.followup("next prompt", message_id="followup-001")
    assert result == "followup-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.message_id == "followup-001"
    assert fb.content == "next prompt"
    assert fb.is_steer is False


@pytest.mark.unit
async def test_followup_with_list_content_blocks() -> None:
    """followup() with list message stores in content_blocks."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    blocks: list[Any] = ["text", {"type": "image"}]
    result = handle.followup(blocks, message_id="followup-list-001")
    assert result == "followup-list-001"
    fb = channel.recv()
    assert fb is not None
    assert fb.content == ""
    assert fb.content_blocks == blocks
    assert fb.is_steer is False


@pytest.mark.unit
async def test_followup_directchannel_preserves_message_id() -> None:
    """followup() with DirectChannel constructs Feedback and returns message_id.

    D17 BLOCKER 2 fix: Feedback is constructed BEFORE deliver_feedback(),
    so message_id is preserved even when DirectChannel returns False.
    """
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    result = handle.followup("standalone prompt", message_id="direct-001")
    assert result == "direct-001"
    # DirectChannel doesn't store feedback — content goes to _message_queue.
    assert handle._message_queue == ["standalone prompt"]


@pytest.mark.unit
async def test_followup_directchannel_content_blocks_in_queue() -> None:
    """followup() with DirectChannel and list content appends content_blocks to queue."""
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    blocks: list[Any] = ["text", {"type": "image"}]
    result = handle.followup(blocks, message_id="direct-list-001")
    assert result == "direct-list-001"
    assert handle._message_queue == [blocks]


@pytest.mark.unit
async def test_followup_returns_none_when_closing() -> None:
    """followup() returns None when handle is closing."""
    handle = _make_handle()
    handle._closing = True
    result = handle.followup("message")
    assert result is None


# ---------------------------------------------------------------------------
# revoke() tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_revoke_delegates_to_protocol_channel() -> None:
    """revoke() delegates to ProtocolChannel.revoke()."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    # First, steer to put a message in _pending.
    msg_id = handle.steer("hello", message_id="revoke-test-001")
    assert msg_id == "revoke-test-001"
    # Revoke before delivery.
    result = handle.revoke("revoke-test-001")
    assert result is True
    # Verify the feedback was removed from _pending.
    assert "revoke-test-001" in channel._revoked


@pytest.mark.unit
async def test_revoke_returns_false_for_direct_channel() -> None:
    """revoke() returns False when CommChannel is DirectChannel."""
    journal = MemoryJournal()
    direct_channel = DirectChannel(journal)
    handle = _make_handle(comm_channel=direct_channel)
    result = handle.revoke("some-id")
    assert result is False


@pytest.mark.unit
async def test_revoke_returns_false_when_no_comm_channel() -> None:
    """revoke() returns False when comm_channel is None."""
    handle = _make_handle()
    handle._comm_channel = None
    result = handle.revoke("some-id")
    assert result is False


@pytest.mark.unit
async def test_revoke_after_delivery_returns_false() -> None:
    """revoke() returns False for already-delivered messages."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    msg_id = handle.steer("hello", message_id="delivered-001")
    assert msg_id == "delivered-001"
    # Recv delivers the feedback — transitions to _delivered.
    fb = channel.recv()
    assert fb is not None
    # Now revoke should return False.
    result = handle.revoke("delivered-001")
    assert result is False


@pytest.mark.unit
async def test_revoke_unknown_returns_true() -> None:
    """revoke() returns True for unknown message_id (idempotent)."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = handle.revoke("unknown-id")
    assert result is True


# ---------------------------------------------------------------------------
# start() D17 tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_start_empty_prompt_produces_empty_list() -> None:
    """start('') produces current_prompts=[] not [''], triggering _idle_loop().

    This is the CRITICAL D17 fix: without this, [""] is a non-empty
    list that bypasses _idle_loop() and executes a spurious empty-prompt turn.
    """
    handle = _make_handle()
    # Followup before start to avoid hanging in _idle_loop().
    handle.followup("queued prompt", message_id="d17-001")
    # Start with empty initial_prompt.
    gen = handle.start("")

    events: list[Any] = []
    try:
        async with asyncio.timeout(5):
            async for event in gen:
                events.append(event)
                if len(events) > 10:
                    break
    except (TimeoutError, asyncio.CancelledError):
        pass

    handle.close()
    # The turn should have been executed with the followup prompt,
    # not with an empty string.
    agent = handle.agent
    assert agent is not None
    agent.create_turn.assert_called_once()
    call_kwargs = agent.create_turn.call_args
    prompts = call_kwargs.kwargs.get("prompts", call_kwargs.args[0] if call_kwargs.args else [])
    # Should NOT be [""] — should be ["queued prompt"] from the followup.
    assert prompts != [""]
    assert "queued prompt" in prompts


# ---------------------------------------------------------------------------
# _steer_callback_wrapper tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_steer_callback_wrapper_returns_message_id() -> None:
    """_steer_callback_wrapper returns str|None (message_id), not bool."""
    channel = _make_protocol_channel()
    handle = _make_handle(comm_channel=channel)
    result = await handle._steer_callback_wrapper("session-id", "steer me")
    assert result is not None
    assert isinstance(result, str)
    assert len(result) > 0


# ---------------------------------------------------------------------------
# _active_agent_run property test
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_active_agent_run_property_matches_field() -> None:
    """_active_agent_run property returns same value as active_agent_run field."""
    handle = _make_handle()
    assert handle._active_agent_run is None
    assert handle._active_agent_run is handle.active_agent_run
    # Set a mock value.
    mock_run = MagicMock()
    handle.active_agent_run = mock_run
    assert handle._active_agent_run is mock_run
