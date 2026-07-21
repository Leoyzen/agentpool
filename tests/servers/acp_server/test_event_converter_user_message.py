"""Unit tests for ``ACPEventConverter`` handling ``UserMessageInsertedEvent``.

Tests v1-only path: ``UserMessageInsertedEvent`` → ``UserMessageChunk``
emission, dedup skip, and multi-modal content conversion.
"""

from __future__ import annotations

from typing import Any

import pytest

from acp.schema import UserMessageChunk
from agentpool.agents.events.events import UserMessageInsertedEvent
from agentpool_server.acp_server.event_converter import ACPEventConverter


pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def _collect(converter: ACPEventConverter, event: Any) -> list[Any]:
    """Collect all notifications yielded by ``converter.convert(event)``."""
    results: list[Any] = []
    results.extend([update async for update in converter.convert(event)])
    return results


async def test_user_message_inserted_emits_user_message_chunk() -> None:
    """``UserMessageInsertedEvent`` with text content emits ``UserMessageChunk``.

    Given: A converter with no dedup set and a ``UserMessageInsertedEvent``
        with string content.
    When: The event is converted.
    Then: Exactly one ``UserMessageChunk`` is yielded with the event's text.
    """
    converter = ACPEventConverter()
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_001",
        content="Hello from steer!",
        delivery="steer",
        source="protocol",
    )

    results = await _collect(converter, event)

    assert len(results) == 1
    chunk = results[0]
    assert isinstance(chunk, UserMessageChunk)
    assert chunk.message_id == "msg_001"
    assert chunk.content.text == "Hello from steer!"


async def test_user_message_inserted_dedup_skip() -> None:
    """Converter skips emission when ``message_id`` is already in dedup set.

    Given: A converter with a dedup set containing ``"msg_dup"``.
    When: A ``UserMessageInsertedEvent`` with ``message_id="msg_dup"`` arrives.
    Then: No notifications are yielded.
    """
    dedup_set: set[str] = {"msg_dup"}
    converter = ACPEventConverter(displayed_message_ids=dedup_set)
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_dup",
        content="This should be skipped",
        delivery="steer",
        source="protocol",
    )

    results = await _collect(converter, event)

    assert results == []


async def test_user_message_inserted_adds_to_dedup_set_after_emission() -> None:
    """After emitting, the ``message_id`` is added to the dedup set.

    Given: A converter with an empty dedup set.
    When: A ``UserMessageInsertedEvent`` with ``message_id="msg_new"`` is converted.
    Then: The dedup set now contains ``"msg_new"``.
    """
    dedup_set: set[str] = set()
    converter = ACPEventConverter(displayed_message_ids=dedup_set)
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_new",
        content="Content here",
        delivery="followup",
        source="internal",
    )

    await _collect(converter, event)

    assert "msg_new" in dedup_set


async def test_user_message_inserted_multimodal_content() -> None:
    """Multi-modal content (list with text dicts) converts to multiple chunks.

    Given: A ``UserMessageInsertedEvent`` with ``content`` as a list of
        dicts with ``"type": "text"``.
    When: The event is converted.
    Then: One ``UserMessageChunk`` per text block is yielded.
    """
    converter = ACPEventConverter()
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_multi",
        content=[
            {"type": "text", "text": "First part"},
            {"type": "text", "text": "Second part"},
        ],
        delivery="steer",
        source="protocol",
    )

    results = await _collect(converter, event)

    assert len(results) == 2
    assert all(isinstance(r, UserMessageChunk) for r in results)
    assert results[0].content.text == "First part"
    assert results[1].content.text == "Second part"
    assert all(r.message_id == "msg_multi" for r in results)


async def test_user_message_inserted_skips_non_text_items_in_list() -> None:
    """Non-text items in a multi-modal list are silently skipped (v1).

    Given: A ``UserMessageInsertedEvent`` with content containing an image
        dict and a text dict.
    When: The event is converted.
    Then: Only the text block yields a ``UserMessageChunk``.
    """
    converter = ACPEventConverter()
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_mixed",
        content=[
            {"type": "image", "data": "base64..."},
            {"type": "text", "text": "Only text matters"},
        ],
        delivery="steer",
        source="protocol",
    )

    results = await _collect(converter, event)

    assert len(results) == 1
    assert isinstance(results[0], UserMessageChunk)
    assert results[0].content.text == "Only text matters"


async def test_user_message_inserted_empty_string_content_yields_nothing() -> None:
    """Empty string content yields no chunks.

    Given: A ``UserMessageInsertedEvent`` with ``content=""``.
    When: The event is converted.
    Then: No notifications are yielded.
    """
    converter = ACPEventConverter()
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_empty",
        content="",
        delivery="steer",
        source="protocol",
    )

    results = await _collect(converter, event)

    assert results == []


async def test_user_message_inserted_no_dedup_set_always_emits() -> None:
    """Without a dedup set, the converter always emits (no dedup).

    Given: A converter with ``displayed_message_ids=None`` (default).
    When: The same ``message_id`` arrives twice.
    Then: Both events emit ``UserMessageChunk`` notifications.
    """
    converter = ACPEventConverter()
    event = UserMessageInsertedEvent(
        session_id="s1",
        message_id="msg_repeat",
        content="Repeat",
        delivery="steer",
        source="protocol",
    )

    results1 = await _collect(converter, event)
    results2 = await _collect(converter, event)

    assert len(results1) == 1
    assert len(results2) == 1
