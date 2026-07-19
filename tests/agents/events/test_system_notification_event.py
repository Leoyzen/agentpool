"""Unit tests for the ``SystemNotificationEvent`` dataclass.

Verifies construction (defaults and explicit fields), membership in the
``RichAgentStreamEvent`` union, and that ``text`` is a required field.
"""

from __future__ import annotations

import time
import typing

import pytest

from agentpool.agents.events.events import (
    RichAgentStreamEvent,
    SystemNotificationEvent,
)


pytestmark = pytest.mark.unit


def test_system_notification_event_defaults() -> None:
    """Construction with only the required ``text`` field populates defaults.

    Verifies:
    - ``level`` defaults to ``"info"``
    - ``source`` defaults to ``"system"``
    - ``title`` defaults to ``""``
    - ``session_id`` defaults to ``""``
    - ``ref_session_id`` defaults to ``None``
    - ``ref_label`` defaults to ``None``
    - ``timestamp`` is set to the current epoch (within a small tolerance)
    """
    before = time.time()
    event = SystemNotificationEvent(text="hello world")
    after = time.time()

    assert event.text == "hello world"
    assert event.level == "info"
    assert event.source == "system"
    assert event.title == ""
    assert event.session_id == ""
    assert event.ref_session_id is None
    assert event.ref_label is None
    assert before <= event.timestamp <= after


def test_system_notification_event_explicit_fields() -> None:
    """Construction with all explicit fields preserves the values, including ``ref_label``."""
    event = SystemNotificationEvent(
        session_id="sess-123",
        level="warning",
        source="background_task",
        title="Task Done",
        text="background task completed",
        ref_session_id="child-sess-456",
        ref_label="member: researcher",
        timestamp=1_700_000_000.0,
    )

    assert event.session_id == "sess-123"
    assert event.level == "warning"
    assert event.source == "background_task"
    assert event.title == "Task Done"
    assert event.text == "background task completed"
    assert event.ref_session_id == "child-sess-456"
    assert event.ref_label == "member: researcher"
    assert event.timestamp == 1_700_000_000.0


def test_system_notification_event_is_rich_agent_stream_event() -> None:
    """``SystemNotificationEvent`` is a member of the ``RichAgentStreamEvent`` union.

    ``RichAgentStreamEvent`` is a PEP 695 ``type`` alias (``TypeAliasType``),
    so ``isinstance()`` cannot be used directly. Instead we verify the class
    is one of the union's args.
    """
    union_args = typing.get_args(RichAgentStreamEvent.__value__)
    assert SystemNotificationEvent in union_args


def test_system_notification_event_text_is_required() -> None:
    """Omitting ``text`` raises ``TypeError`` (no default)."""
    with pytest.raises(TypeError):
        SystemNotificationEvent()  # type: ignore[call-arg]


def test_system_notification_event_all_levels() -> None:
    """All documented severity levels are accepted."""
    for level in ("info", "warning", "error", "success"):
        event = SystemNotificationEvent(text="msg", level=level)  # type: ignore[arg-type]
        assert event.level == level


def test_system_notification_event_all_sources() -> None:
    """All documented source values are accepted."""
    for source in (
        "background_task",
        "system",
        "lifecycle",
        "steer",
        "followup",
        "team",
        "custom",
    ):
        event = SystemNotificationEvent(text="msg", source=source)  # type: ignore[arg-type]
        assert event.source == source
