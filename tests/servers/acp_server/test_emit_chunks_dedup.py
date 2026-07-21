"""Unit test for no double display when both paths fire.

Verifies the dedup wiring: ``handle_prompt()`` generates a ``message_id``,
registers it in the per-session dedup set, and emits ``UserMessageChunk``
directly. When the EventBus later delivers a ``UserMessageInsertedEvent``
with the same ``message_id``, the ``ACPEventConverter`` skips it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from acp.schema import UserMessageChunk
from agentpool.agents.events.events import UserMessageInsertedEvent
from agentpool_server.acp_server.event_converter import ACPEventConverter


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def test_dedup_prevents_double_display() -> None:
    """``ACPEventConverter`` skips ``UserMessageInsertedEvent`` already displayed.

    Given: A dedup set containing ``"msg_123"`` (simulating that
        ``handle_prompt`` already emitted the ``UserMessageChunk`` directly).
    When: A ``UserMessageInsertedEvent(message_id="msg_123")`` arrives via
        the EventBus and is passed to the converter.
    Then: The converter yields no notifications (dedup skip).
    """
    dedup_set: set[str] = {"msg_123"}
    converter = ACPEventConverter(displayed_message_ids=dedup_set)
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_123",
        content="Steer message",
        delivery="steer",
        source="protocol",
    )

    results = [update async for update in converter.convert(event)]

    assert results == [], "Converter should skip already-displayed message_id"


async def test_dedup_allows_new_message_id() -> None:
    """A new ``message_id`` not in the dedup set is emitted normally.

    Given: A dedup set containing ``"msg_old"``.
    When: A ``UserMessageInsertedEvent(message_id="msg_new")`` arrives.
    Then: The converter yields a ``UserMessageChunk`` and adds ``"msg_new"``
        to the dedup set.
    """
    dedup_set: set[str] = {"msg_old"}
    converter = ACPEventConverter(displayed_message_ids=dedup_set)
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_new",
        content="New steer message",
        delivery="steer",
        source="protocol",
    )

    results = [update async for update in converter.convert(event)]

    assert len(results) == 1
    assert isinstance(results[0], UserMessageChunk)
    assert results[0].message_id == "msg_new"
    assert results[0].content.text == "New steer message"
    assert "msg_new" in dedup_set


async def test_handle_prompt_registers_message_id_in_dedup_set() -> None:
    """``handle_prompt()`` registers the generated ``message_id`` in the dedup set.

    Given: A ``ACPProtocolHandler`` with a mocked session pool.
    When: ``handle_prompt`` is called.
    Then: The dedup set contains the ``message_id`` passed to ``send_message``,
        preventing double display when the EventBus event fires.
    """
    from agentpool_server.acp_server.handler import ACPProtocolHandler

    dedup_set: set[str] = set()
    host_context = MagicMock()
    session_pool = MagicMock()
    session_pool.sessions._get_dedup_set.return_value = dedup_set
    session_pool.create_session = AsyncMock()
    session_pool.send_message = AsyncMock(return_value="msg_dedup_test")
    session_pool.wait_for_completion = AsyncMock()
    session_pool._get_active_run_handle = MagicMock(return_value=None)
    host_context.session_pool = session_pool

    session_manager = MagicMock()
    session_manager.get_session.return_value = None
    session_manager.session_store = None

    event_converter = MagicMock()
    event_converter.subagent_display_mode = "legacy"
    event_converter.raw_input_mode = "dict"

    client = MagicMock()
    client.session_update = AsyncMock()

    handler = ACPProtocolHandler(
        host_context=host_context,
        session_manager=session_manager,
        event_converter=event_converter,
        client=client,
    )
    handler._ensure_event_consumer = AsyncMock()

    with patch(
        "agentpool_server.acp_server.handler.ACPEventConverter.build_user_message_chunks",
        return_value=[],
    ):
        await handler.handle_prompt("test-session", [], delivery=None)

    # The message_id passed to send_message should be in the dedup set.
    send_call = session_pool.send_message.call_args
    assert send_call is not None
    passed_mid = send_call.kwargs["message_id"]
    assert passed_mid in dedup_set

    # Simulate the EventBus event arriving with the same message_id —
    # the converter should skip it.
    converter = ACPEventConverter(displayed_message_ids=dedup_set)
    event = UserMessageInsertedEvent(
        session_id="test-session",
        message_id=passed_mid,
        content="Steer text",
        delivery="initial",
        source="protocol",
    )

    results = [update async for update in converter.convert(event)]

    assert results == [], "EventBus event with already-displayed message_id should be skipped"
