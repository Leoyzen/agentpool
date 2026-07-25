"""Unit tests for OpenCode EventProcessor UserMessageInsertedEvent dedup.

Verifies that:
- Two UserMessageInsertedEvent with the same message_id → only the first
  produces events.
- Two UserMessageInsertedEvent with different message_ids → both produce
  events.
- The dedup set (displayed_message_ids) persists across turns.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool_server.opencode_server.event_processor import EventProcessor
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models.message import (
    MessagePath,
    MessageTime,
    MessageWithParts,
)


pytestmark = pytest.mark.unit


def _make_ctx() -> EventProcessorContext:
    """Create a minimal EventProcessorContext for dedup testing."""
    assistant_msg = MessageWithParts.assistant(
        message_id="msg_assistant",
        session_id="test-session",
        time=MessageTime(created=0),
        agent_name="agent",
        model_id="default",
        parent_id="",
        provider_id="agentpool",
        path=MessagePath(cwd="/tmp", root="/tmp"),
    )
    state = MagicMock()
    return EventProcessorContext(
        session_id="test-session",
        assistant_msg_id="msg_assistant",
        assistant_msg=assistant_msg,
        state=state,
        working_dir="/tmp",
    )


_BRIDGE_PATH = "agentpool_server.opencode_server.opencode_message_bridge.append_message_to_session"


async def test_dedup_same_message_id_skips_second() -> None:
    """Two events with the same message_id → only first produces events."""
    processor = EventProcessor()
    ctx = _make_ctx()

    with patch(_BRIDGE_PATH, new_callable=AsyncMock):
        events1 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-1", "first message", time.time()
            )
        ]
        events2 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-1", "first message", time.time()
            )
        ]

    assert len(events1) > 0, "First event should produce output"
    assert len(events2) == 0, "Second event with same message_id should be skipped"


async def test_dedup_different_message_ids_both_emitted() -> None:
    """Two events with different message_ids → both produce events."""
    processor = EventProcessor()
    ctx = _make_ctx()

    with patch(_BRIDGE_PATH, new_callable=AsyncMock):
        events1 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-1", "first", time.time()
            )
        ]
        events2 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-2", "second", time.time()
            )
        ]

    assert len(events1) > 0, "First event should produce output"
    assert len(events2) > 0, "Second event with different ID should produce output"


async def test_dedup_persists_across_calls() -> None:
    """displayed_message_ids persists — dedup works across multiple calls."""
    processor = EventProcessor()
    ctx = _make_ctx()

    with patch(_BRIDGE_PATH, new_callable=AsyncMock):
        events1 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-persistent", "first", time.time()
            )
        ]
        events2 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "msg-persistent", "first", time.time()
            )
        ]

    assert len(events1) > 0
    assert len(events2) == 0, "Dedup should persist across calls"
    assert "msg-persistent" in ctx.displayed_message_ids


async def test_dedup_empty_message_id_not_tracked() -> None:
    """Events with empty message_id are NOT deduped (always emitted)."""
    processor = EventProcessor()
    ctx = _make_ctx()

    with patch(_BRIDGE_PATH, new_callable=AsyncMock):
        events1 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "", "no-id-first", time.time()
            )
        ]
        events2 = [
            e
            async for e in processor._process_user_message_inserted(
                ctx, "", "no-id-second", time.time()
            )
        ]

    assert len(events1) > 0
    assert len(events2) > 0, "Empty message_id should not be deduped"
