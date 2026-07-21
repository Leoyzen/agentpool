"""L2 test: verify part ID mismatch between DB and SSE doesn't cause duplication.

Root cause: ``chat_message_to_opencode`` generates NEW part IDs when
reconstructing messages from DB. The EventProcessor's ``_deserialize_part``
preserves the ORIGINAL part IDs from ``meta.parts``. When the TUI receives
both (from ``sync.session.sync()`` and SSE ``message.part.updated``),
the binary search by part ID fails to deduplicate, causing the text to
appear twice.

Fix: for ``source="protocol"`` messages, EventProcessor only yields
``MessageUpdatedEvent`` (not ``PartUpdatedEvent``). Parts come from
``sync.session.sync()`` which loads from DB.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool_server.opencode_server.event_processor import (
    EventProcessor,
    OpenCodeUserMessageMeta,
)
from agentpool_server.opencode_server.models.message import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)


def _make_ctx(session_id: str = "test-session") -> Any:
    """Create a minimal EventProcessorContext for testing."""
    from agentpool_server.opencode_server.event_processor_context import (
        EventProcessorContext,
    )

    assistant_msg = MessageWithParts.assistant(
        message_id="msg_assistant_001",
        session_id=session_id,
        time=MessageTime(created=0),
        agent_name="test-agent",
        model_id="test-model",
        parent_id="parent-001",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    ctx = EventProcessorContext(
        session_id=session_id,
        assistant_msg_id="msg_assistant_001",
        assistant_msg=assistant_msg,
        state=MagicMock(),
        working_dir="/tmp",
    )
    ctx.state.messages = {}
    return ctx


@pytest.mark.unit
async def test_protocol_source_does_not_yield_part_updated_events() -> None:
    """source=protocol should NOT yield PartUpdatedEvent.

    Parts come from sync.session.sync() (DB). Sending PartUpdatedEvent
    with original part IDs would conflict with DB-reconstructed parts
    (which have different IDs), causing duplicate text in TUI.
    """
    processor = EventProcessor()
    ctx = _make_ctx("test-session")
    ctx.state = MagicMock()
    ctx.state.messages = {}

    # Mock append_message_to_session to avoid actual state mutation
    with patch(
        "agentpool_server.opencode_server.opencode_message_bridge.append_message_to_session",
        new_callable=AsyncMock,
    ):
        meta = OpenCodeUserMessageMeta(
            parts=[
                {
                    "type": "text",
                    "id": "part_original_001",
                    "text": "hello world",
                    "message_id": "",
                    "session_id": "",
                }
            ],
        )
        events = []
        async for e in processor._process_user_message_inserted(
            ctx,
            message_id="msg_test_001",
            content="hello world",
            timestamp=1000.0,
            meta=meta,
            source="protocol",
        ):
            events.append(e)  # noqa: PERF401

    # Should yield exactly 1 event: MessageUpdatedEvent (no PartUpdatedEvent)
    assert len(events) == 1, f"Expected 1 event for protocol source, got {len(events)}: {events}"
    assert events[0].type == "message.updated", f"Expected message.updated, got {events[0].type}"


@pytest.mark.unit
async def test_internal_source_yields_part_updated_events() -> None:
    """source="background_task" SHOULD yield PartUpdatedEvent.

    Internal messages have no sync() to load parts from DB, so parts
    must come via SSE.
    """
    processor = EventProcessor()
    ctx = _make_ctx("test-session")
    ctx.state = MagicMock()
    ctx.state.messages = {}

    with patch(
        "agentpool_server.opencode_server.opencode_message_bridge.append_message_to_session",
        new_callable=AsyncMock,
    ):
        meta = OpenCodeUserMessageMeta(
            parts=[
                {
                    "type": "text",
                    "id": "part_internal_001",
                    "text": "background task result",
                    "message_id": "",
                    "session_id": "",
                }
            ],
        )
        events = []
        async for e in processor._process_user_message_inserted(
            ctx,
            message_id="msg_test_002",
            content="background task result",
            timestamp=1000.0,
            meta=meta,
            source="background_task",
        ):
            events.append(e)  # noqa: PERF401

    # Should yield 2 events: MessageUpdatedEvent + PartUpdatedEvent
    assert len(events) == 2, f"Expected 2 events for internal source, got {len(events)}"
    assert events[0].type == "message.updated"
    assert events[1].type == "message.part.updated"


@pytest.mark.unit
async def test_protocol_source_part_ids_differ_from_db_reconstruction() -> None:
    """Verify the root cause: DB reconstruction creates different part IDs.

    This test demonstrates WHY sending PartUpdatedEvent for protocol
    sources causes duplication: ``chat_message_to_opencode`` generates
    new part IDs, different from the original parts in meta.
    """
    from agentpool.messaging.messages import ChatMessage
    from agentpool_server.opencode_server.converters import chat_message_to_opencode

    # Create a ChatMessage as stored in DB
    chat_msg = ChatMessage[str](
        message_id="msg_test_003",
        session_id="test-session",
        content="hello world",
        role="user",
        timestamp=MagicMock(),
    )

    # Convert back to OpenCode format (as GET /message would)
    db_msg = chat_message_to_opencode(
        chat_msg,
        session_id="test-session",
        agent_name="test-agent",
    )

    # DB-reconstructed part has a NEW ID (not the original)
    db_part_id = db_msg.parts[0].id
    original_part_id = "part_original_001"

    # The IDs are DIFFERENT — this is the root cause of duplication
    assert db_part_id != original_part_id, (
        "DB reconstruction should generate a new part ID, "
        "different from the original. If they match, the duplication "
        "bug would not occur."
    )


@pytest.mark.unit
async def test_protocol_source_no_part_updated_with_text_content() -> None:
    """source=protocol with text-only content (no meta) should not yield PartUpdatedEvent."""
    processor = EventProcessor()
    ctx = _make_ctx("test-session")
    ctx.state = MagicMock()
    ctx.state.messages = {}

    with patch(
        "agentpool_server.opencode_server.opencode_message_bridge.append_message_to_session",
        new_callable=AsyncMock,
    ):
        events = []
        async for e in processor._process_user_message_inserted(
            ctx,
            message_id="msg_test_004",
            content="plain text message",
            timestamp=1000.0,
            meta=None,
            source="protocol",
        ):
            events.append(e)  # noqa: PERF401

    # Should yield exactly 1 event: MessageUpdatedEvent only
    assert len(events) == 1
    assert events[0].type == "message.updated"


@pytest.mark.unit
async def test_internal_source_text_content_yields_part_updated() -> None:
    """source="internal" with text-only content SHOULD yield PartUpdatedEvent."""
    processor = EventProcessor()
    ctx = _make_ctx("test-session")
    ctx.state = MagicMock()
    ctx.state.messages = {}

    with patch(
        "agentpool_server.opencode_server.opencode_message_bridge.append_message_to_session",
        new_callable=AsyncMock,
    ):
        events = []
        async for e in processor._process_user_message_inserted(
            ctx,
            message_id="msg_test_005",
            content="internal steer message",
            timestamp=1000.0,
            meta=None,
            source="internal",
        ):
            events.append(e)  # noqa: PERF401

    # Should yield 2 events: MessageUpdatedEvent + PartUpdatedEvent
    assert len(events) == 2
    assert events[0].type == "message.updated"
    assert events[1].type == "message.part.updated"
