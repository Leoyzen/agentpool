"""Tests for stop_event_consumer exception handling (2nd round review).

Verifies that when one child's stop_event_consumer raises an exception,
the remaining children are still stopped (the loop doesn't break).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from agentpool_server.opencode_server.opencode_event_bridge import (
    OpenCodeEventBridgeMixin,
)


pytestmark = pytest.mark.unit


class _FakeBridge(OpenCodeEventBridgeMixin):
    """Minimal concrete subclass for testing the mixin."""

    def __init__(self) -> None:
        self.session_pool = MagicMock()
        self.server_state = MagicMock()
        self._contexts: dict[str, Any] = {}
        self._adapters: dict[str, Any] = {}
        self._message_registered: dict[str, bool] = {}
        self._child_to_parent: dict[str, str] = {}
        self._child_spawns: dict[str, Any] = {}
        self._children_of: dict[str, set[str]] = {}
        self._resume_contexts: dict[str, dict[str, Any]] = {}
        self._pending_message_ids: dict[str, str] = {}


@pytest.mark.anyio
async def test_stop_event_consumer_exception_does_not_break_loop() -> None:
    """Stop one child's consumer must not break the loop.

    When stop_event_consumer raises for one child, remaining children
    must still be stopped.
    """
    bridge = _FakeBridge()

    child1 = "child-1"
    child2 = "child-2"
    parent = "parent-session"
    bridge._children_of[parent] = {child1, child2}

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)
        if child_id == child1:
            raise RuntimeError("simulated failure for child-1")

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert len(attempted) == 2, f"Expected both children to be attempted, but only got {attempted}"
    assert child1 in attempted, "child-1 was not attempted"
    assert child2 in attempted, "child-2 was not attempted"
    assert parent not in bridge._children_of


@pytest.mark.anyio
async def test_stop_event_consumer_all_succeed() -> None:
    """Normal case: all children stopped successfully."""
    bridge = _FakeBridge()

    child1 = "child-1"
    child2 = "child-2"
    parent = "parent-session"
    bridge._children_of[parent] = {child1, child2}

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert len(attempted) == 2
    assert parent not in bridge._children_of


@pytest.mark.anyio
async def test_stop_event_consumer_no_children() -> None:
    """When there are no children, _after_consumer_loop runs cleanly."""
    bridge = _FakeBridge()
    parent = "parent-session"

    attempted: list[str] = []

    async def fake_stop(child_id: str) -> None:
        attempted.append(child_id)

    bridge.stop_event_consumer = fake_stop  # type: ignore[method-assign]

    await bridge._after_consumer_loop(parent)

    assert attempted == []
