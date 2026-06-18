"""Snapshot tests for ACP event converter subagent modes.

These tests use pytest-snapshot to verify the exact JSON output format for
subagent events in both tool_box and inline modes as specified in RFC-0001.

Per RFC-0001:

**Tool Box Mode (Summary Updates Only)**:
- Title format: [{icon} {role}]: {update}
- Content field is NEVER sent in tool_box mode
- Summary updates only via title field
- Title updates track: Thinking, Call params, Tool completed

**Inline Mode (All Events as Tool Outputs)**:
- All events treated as tool outputs (ToolCallProgress/ToolCallStart)
- Subagents distinguished via title [{source_name}]
- No AgentMessageChunk.text events after header
- Avoids concurrency issues: Multiple agents thinking don't conflict (same
  event types, different titles)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Generator

    from syrupy import SnapshotAssertion  # type: ignore[attr-defined]

from agentpool_server.acp_server.event_converter import ACPEventConverter
from tests.fixtures.subagent_events import TEST_EVENT_SEQUENCES

from agentpool.agents.events import StreamCompleteEvent
from agentpool.messaging.messages import ChatMessage
from pydantic_ai.usage import RequestUsage


async def collect_updates(converter: ACPEventConverter, event) -> list[dict[str, object]]:
    """Helper to collect all updates from an event and convert to dict for snapshots.

    Snapshot tests need serializable objects, so we convert to dict.
    """
    updates: list[dict[str, object]] = []
    async for update in converter.convert(event):
        # Convert Pydantic models to dict for snapshot comparison
        if hasattr(update, "model_dump"):
            updates.append(update.model_dump(exclude_none=True))
        else:
            # Fallback for non-Pydantic objects
            updates.append({"_str": str(update)})
    return updates


@pytest.fixture
def tool_box_converter() -> Generator[ACPEventConverter]:
    """Converter configured for tool_box mode."""
    import os

    # Set feature flag to tool_box mode
    original = os.environ.get("ACP_SUBAGENT_DISPLAY_MODE")
    os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = "tool_box"
    try:
        converter = ACPEventConverter()
        converter._current_message_id = "test-message-id"
        yield converter
    finally:
        # Restore original value
        if original is None:
            os.environ.pop("ACP_SUBAGENT_DISPLAY_MODE", None)
        else:
            os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = original


@pytest.fixture
def inline_converter() -> Generator[ACPEventConverter]:
    """Converter configured for inline mode."""
    import os

    # Set feature flag to inline mode
    original = os.environ.get("ACP_SUBAGENT_DISPLAY_MODE")
    os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = "inline"
    try:
        converter = ACPEventConverter()
        converter._current_message_id = "test-message-id"
        yield converter
    finally:
        # Restore original value
        if original is None:
            os.environ.pop("ACP_SUBAGENT_DISPLAY_MODE", None)
        else:
            os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = original


@pytest.fixture
def legacy_converter() -> Generator[ACPEventConverter]:
    """Converter configured for legacy mode (default behavior)."""
    import os

    # Set feature flag to legacy mode
    original = os.environ.get("ACP_SUBAGENT_DISPLAY_MODE")
    os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = "legacy"
    try:
        converter = ACPEventConverter()
        converter._current_message_id = "test-message-id"
        yield converter
    finally:
        # Restore original value
        if original is None:
            os.environ.pop("ACP_SUBAGENT_DISPLAY_MODE", None)
        else:
            os.environ["ACP_SUBAGENT_DISPLAY_MODE"] = original


class TestToolBoxModeSnapshots:
    """Snapshot tests for tool_box subagent mode."""

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_text_stream(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Text streaming emits title updates only, no content."""
        events = TEST_EVENT_SEQUENCES["text_stream"]("assistant")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_thinking_stream(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Thinking stream emits title updates, no content."""
        events = TEST_EVENT_SEQUENCES["thinking_stream"]("researcher")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_tool_call(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Tool calls emit title updates through lifecycle, no content."""
        events = TEST_EVENT_SEQUENCES["tool_call"]("coder")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_mixed_events(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Mixed events track each event type via title, no content."""
        events = TEST_EVENT_SEQUENCES["mixed_events"]("analyzer")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_tool_call_error(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Tool errors shown in title, no content."""
        events = TEST_EVENT_SEQUENCES["tool_call_error"]("executor")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_long_text(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Long text stream - header on first update only."""
        events = TEST_EVENT_SEQUENCES["long_text"]("writer")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_nested_subagents(
        self, tool_box_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Tool_box mode: Nested subagents with depth-based title formatting."""
        events = TEST_EVENT_SEQUENCES["nested_subagents"]()

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(tool_box_converter, event))

        assert all_updates == snapshot


class TestInlineModeSnapshots:
    """Snapshot tests for inline subagent mode."""

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_text_stream(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Text stream yields ToolCallProgress with raw_output."""
        events = TEST_EVENT_SEQUENCES["text_stream"]("assistant")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_thinking_stream(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Thinking stream yields ToolCallProgress with Thinking prefix."""
        events = TEST_EVENT_SEQUENCES["thinking_stream"]("researcher")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_tool_call(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Tool calls yield ToolCallStart, ToolCallProgress for results."""
        events = TEST_EVENT_SEQUENCES["tool_call"]("coder")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_mixed_events(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Mixed events each yield appropriate tool output types."""
        events = TEST_EVENT_SEQUENCES["mixed_events"]("analyzer")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_tool_call_error(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Tool errors yield ToolCallProgress with error status."""
        events = TEST_EVENT_SEQUENCES["tool_call_error"]("executor")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_long_text(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Long text stream - header on first event only via title."""
        events = TEST_EVENT_SEQUENCES["long_text"]("writer")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_nested_subagents(
        self, inline_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Inline mode: Nested subagents distinguished by title, not event types."""
        events = TEST_EVENT_SEQUENCES["nested_subagents"]()

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(inline_converter, event))

        assert all_updates == snapshot


class TestLegacyModeSnapshots:
    """Snapshot tests for legacy mode (current behavior).

    These tests verify the current legacy behavior before changes,
    providing a baseline for comparison.
    """

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_text_stream(
        self, legacy_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Legacy mode: Shows current text streaming behavior (prefix repetition)."""
        events = TEST_EVENT_SEQUENCES["text_stream"]("assistant")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(legacy_converter, event))

        assert all_updates == snapshot

    @pytest.mark.anyio
    @pytest.mark.acp_snapshot
    async def test_tool_call(
        self, legacy_converter: ACPEventConverter, snapshot: SnapshotAssertion
    ):
        """Legacy mode: Shows current tool call behavior (emoji accumulation)."""
        events = TEST_EVENT_SEQUENCES["tool_call"]("coder")

        all_updates = []
        for event in events:
            all_updates.extend(await collect_updates(legacy_converter, event))

        assert all_updates == snapshot


class TestTurnCompleteConditional:
    """Tests for conditional TurnCompleteUpdate emission."""

    @staticmethod
    def _make_stream_complete_event() -> StreamCompleteEvent[str]:
        """Create a minimal StreamCompleteEvent for testing."""
        message = ChatMessage(
            content="Hello",
            role="assistant",  # type: ignore[arg-type]
            usage=RequestUsage(),
        )
        return StreamCompleteEvent(message=message)

    @staticmethod
    async def _collect_updates_raw(converter, event) -> list[object]:
        """Helper to collect all update objects without dict conversion."""
        return [u async for u in converter.convert(event)]

    @pytest.mark.anyio
    async def test_turn_complete_yielded_when_flag_true(self):
        """When client_supports_turn_complete=True, TurnCompleteUpdate is yielded."""
        converter = ACPEventConverter(client_supports_turn_complete=True)
        event = self._make_stream_complete_event()

        updates = await self._collect_updates_raw(converter, event)
        types = [type(u).__name__ for u in updates]

        assert "TurnCompleteUpdate" in types

    @pytest.mark.anyio
    async def test_turn_complete_not_yielded_when_flag_false(self):
        """When client_supports_turn_complete=False, TurnCompleteUpdate is NOT yielded."""
        converter = ACPEventConverter(client_supports_turn_complete=False)
        event = self._make_stream_complete_event()

        updates = await self._collect_updates_raw(converter, event)
        types = [type(u).__name__ for u in updates]

        assert "TurnCompleteUpdate" not in types

    @pytest.mark.anyio
    async def test_turn_complete_not_yielded_by_default(self):
        """By default (no flag), TurnCompleteUpdate is NOT yielded."""
        converter = ACPEventConverter()
        event = self._make_stream_complete_event()

        updates = await self._collect_updates_raw(converter, event)
        types = [type(u).__name__ for u in updates]

        assert "TurnCompleteUpdate" not in types

    def test_reset_preserves_client_supports_turn_complete(self):
        """reset() must NOT clear the client_supports_turn_complete flag."""
        converter = ACPEventConverter(client_supports_turn_complete=True)
        assert converter.client_supports_turn_complete is True

        converter.reset()

        assert converter.client_supports_turn_complete is True
