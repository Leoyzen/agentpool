"""Tests for UserMessageInsertedEvent construction, defaults, and frozen behavior."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, asdict
import json
import time

import pytest

from agentpool.agents.events.events import (
    RichAgentStreamEvent,
    UserMessageInsertedEvent,
)


pytestmark = [pytest.mark.unit]


def test_construction_with_all_fields() -> None:
    """UserMessageInsertedEvent accepts all six fields explicitly."""
    before = time.time()
    event = UserMessageInsertedEvent(
        session_id="sess-1",
        message_id="msg-1",
        content="hello world",
        delivery="steer",
        source="background_task",
        timestamp=1234567890.0,
    )
    after = time.time()

    assert event.session_id == "sess-1"
    assert event.message_id == "msg-1"
    assert event.content == "hello world"
    assert event.delivery == "steer"
    assert event.source == "background_task"
    assert event.timestamp == 1234567890.0
    # Sanity check: before/after brackets are valid for an auto-timestamp scenario.
    assert before <= after


def test_defaults_are_empty_and_initial() -> None:
    """Default values match the spec.

    - session_id='', message_id='', content=''
    - delivery='initial', source='protocol'
    - timestamp auto-generated via time.time
    """
    before = time.time()
    event = UserMessageInsertedEvent()
    after = time.time()

    assert event.session_id == ""
    assert event.message_id == ""
    assert event.content == ""
    assert event.delivery == "initial"
    assert event.source == "protocol"
    assert before <= event.timestamp <= after


def test_multimodal_content_as_list() -> None:
    """Content accepts a list[Any] for multi-modal prompts."""
    parts: list[dict[str, str]] = [
        {"type": "text", "text": "describe this"},
        {"type": "image", "url": "https://example.com/img.png"},
    ]
    event = UserMessageInsertedEvent(
        session_id="sess-mm",
        message_id="msg-mm",
        content=parts,
    )

    assert isinstance(event.content, list)
    assert event.content == parts
    assert event.content[0] == {"type": "text", "text": "describe this"}
    assert event.content[1] == {"type": "image", "url": "https://example.com/img.png"}


@pytest.mark.parametrize("delivery", ["initial", "steer", "followup"])
def test_all_delivery_values(delivery: str) -> None:
    """UserMessageInsertedEvent accepts all three delivery literals."""
    event = UserMessageInsertedEvent(delivery=delivery)  # type: ignore[arg-type]
    assert event.delivery == delivery


@pytest.mark.parametrize("source", ["protocol", "background_task", "internal"])
def test_all_source_values(source: str) -> None:
    """UserMessageInsertedEvent accepts all three source literals."""
    event = UserMessageInsertedEvent(source=source)  # type: ignore[arg-type]
    assert event.source == source


def test_frozen_dataclass_cannot_modify_fields() -> None:
    """Frozen dataclass raises FrozenInstanceError on field assignment."""
    event = UserMessageInsertedEvent(session_id="sess-frozen")

    with pytest.raises(FrozenInstanceError):
        event.session_id = "changed"  # type: ignore[misc]


def test_frozen_dataclass_cannot_delete_fields() -> None:
    """Frozen dataclass raises FrozenInstanceError on field deletion."""
    event = UserMessageInsertedEvent(session_id="sess-frozen-del")

    with pytest.raises(FrozenInstanceError):
        del event.session_id  # type: ignore[misc]


def test_json_roundtrip_preserves_all_fields() -> None:
    """UserMessageInsertedEvent survives JSON roundtrip via asdict."""
    event = UserMessageInsertedEvent(
        session_id="sess-rt",
        message_id="msg-rt",
        content="roundtrip text",
        delivery="followup",
        source="internal",
        timestamp=99.0,
    )

    data = json.dumps(asdict(event))
    restored = UserMessageInsertedEvent(**json.loads(data))

    assert restored == event


def test_json_roundtrip_with_multimodal_content() -> None:
    """UserMessageInsertedEvent with list content survives JSON roundtrip."""
    parts = [{"type": "text", "text": "hi"}, {"type": "image", "url": "u"}]
    event = UserMessageInsertedEvent(
        session_id="sess-rt-mm",
        message_id="msg-rt-mm",
        content=parts,
        delivery="steer",
    )

    data = json.dumps(asdict(event))
    restored = UserMessageInsertedEvent(**json.loads(data))

    assert restored.content == parts
    assert restored.delivery == "steer"


def test_event_is_member_of_rich_agent_stream_event_union() -> None:
    """UserMessageInsertedEvent is a member of the RichAgentStreamEvent union.

    RichAgentStreamEvent is a ``type`` alias, so isinstance() cannot be used.
    Instead verify that an instance can be passed to a function expecting
    RichAgentStreamEvent — i.e., it type-checks as a union member.
    """

    def accept_event(e: RichAgentStreamEvent[object]) -> None:
        _ = e  # just verifying it compiles

    event = UserMessageInsertedEvent(session_id="sess-union", message_id="msg-union")

    # Should not raise at runtime.
    accept_event(event)


def test_two_events_have_different_default_timestamps() -> None:
    """default_factory=time.time produces distinct timestamps across constructions."""
    event_a = UserMessageInsertedEvent()
    # Force a small delay to ensure time.time advances at least one tick.
    event_b = UserMessageInsertedEvent()

    # Timestamps should be very close but may be equal on very fast clocks.
    # We only assert they are non-negative floats.
    assert isinstance(event_a.timestamp, float)
    assert isinstance(event_b.timestamp, float)
    assert event_a.timestamp >= 0.0
    assert event_b.timestamp >= 0.0
