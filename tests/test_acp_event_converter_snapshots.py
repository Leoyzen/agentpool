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
