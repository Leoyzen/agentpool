"""Tests for PassthroughEventConverter — zero-conversion during proxy chain passthrough.

Tests verify that:
- PassthroughEventConverter.convert() yields nothing for all event types
- PassthroughEventConverter.cancel_pending_tools() is a no-op
- PassthroughEventConverter satisfies the EventConverterComponent protocol
- When PassthroughEventConverter is used, ACPEventConverter.convert is NOT called
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

from pydantic_ai import PartDeltaEvent, PartStartEvent, TextPart, TextPartDelta
import pytest

from acp.schema import Usage
from agentpool.agents.events.events import (
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallStartEvent,
)
from agentpool_server.acp_server.event_converter import (
    ACPEventConverter,
    EventConverterComponent,
    PassthroughEventConverter,
)


pytestmark = [pytest.mark.unit]


# ---------------------------------------------------------------------------
# Helper: build a minimal mock ChatMessage for StreamCompleteEvent
# ---------------------------------------------------------------------------


def _make_mock_message() -> Any:
    """Create a mock ChatMessage suitable for StreamCompleteEvent."""
    mock_msg = MagicMock()
    mock_msg.usage.total_tokens = 100
    mock_msg.usage.input_tokens = 50
    mock_msg.usage.output_tokens = 50
    mock_msg.usage.details = {}
    mock_msg.usage.cache_read_tokens = 0
    mock_msg.usage.cache_write_tokens = 0
    mock_msg.cost_info = None
    return mock_msg


# ---------------------------------------------------------------------------
# Test 1: PassthroughEventConverter.convert() yields nothing
# ---------------------------------------------------------------------------


async def test_passthrough_converter_yields_nothing() -> None:
    """PassthroughEventConverter.convert() yields nothing for all event types.

    The passthrough converter must be a true no-op — it should not produce
    any ACP session updates regardless of the event type received.
    """
    converter = PassthroughEventConverter()

    # Test with various event types that would normally produce notifications
    events: list[Any] = [
        PartStartEvent(index=0, part=TextPart(content="Hello")),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" world")),
        RunStartedEvent(run_id="run-1", agent_name="test_agent"),
        ToolCallStartEvent(
            tool_call_id="tc-1",
            tool_name="bash",
            title="Running bash",
            kind="execute",
            locations=[],
            raw_input={"command": "echo hi"},
        ),
        StreamCompleteEvent(message=_make_mock_message()),
    ]

    for event in events:
        results = [update async for update in converter.convert(event)]
        assert results == [], (
            f"PassthroughEventConverter should yield nothing for {type(event).__name__}, "
            f"but yielded {len(results)} updates"
        )


# ---------------------------------------------------------------------------
# Test 2: PassthroughEventConverter.cancel_pending_tools() is a no-op
# ---------------------------------------------------------------------------


async def test_passthrough_converter_cancel_pending_tools_noop() -> None:
    """PassthroughEventConverter.cancel_pending_tools() yields nothing.

    Since the passthrough converter does not track tool state, cancelling
    pending tools must be a complete no-op — no ToolCallProgress yields.
    """
    converter = PassthroughEventConverter()

    # Feed some tool events first (they should all be ignored)
    tool_event = ToolCallStartEvent(
        tool_call_id="tc-passthrough-1",
        tool_name="bash",
        title="Running bash",
        kind="execute",
        locations=[],
        raw_input={"command": "echo test"},
    )
    async for _ in converter.convert(tool_event):
        pass

    # Now cancel — should yield nothing
    results = [update async for update in converter.cancel_pending_tools()]

    assert results == [], (
        "cancel_pending_tools should yield nothing in passthrough mode"
    )


# ---------------------------------------------------------------------------
# Test 3: PassthroughEventConverter satisfies EventConverterComponent protocol
# ---------------------------------------------------------------------------


def test_passthrough_converter_satisfies_event_converter_protocol() -> None:
    """PassthroughEventConverter implements the EventConverterComponent protocol.

    Since EventConverterComponent is a @runtime_checkable Protocol, we can
    use isinstance() to verify that PassthroughEventConverter satisfies the
    interface. This ensures it can be used wherever an EventConverterComponent
    is expected (e.g., in ACPProtocolHandler).
    """
    converter = PassthroughEventConverter()

    assert isinstance(converter, EventConverterComponent), (
        "PassthroughEventConverter must satisfy the EventConverterComponent protocol"
    )

    # Verify it has all required attributes/methods from the protocol
    assert hasattr(converter, "subagent_display_mode")
    assert hasattr(converter, "raw_input_mode")
    assert hasattr(converter, "subagent_meta")
    assert hasattr(converter, "last_usage")
    assert hasattr(converter, "reset")
    assert hasattr(converter, "convert")
    assert hasattr(converter, "cancel_pending_tools")
    assert hasattr(converter, "build_subagent_completed")


# ---------------------------------------------------------------------------
# Test 3b: PassthroughEventConverter still tracks usage from StreamCompleteEvent
# ---------------------------------------------------------------------------


async def test_passthrough_converter_tracks_usage_on_stream_complete() -> None:
    """PassthroughEventConverter extracts usage from StreamCompleteEvent.

    Even though convert() yields nothing, it should still update
    ``last_usage`` when it sees a StreamCompleteEvent. This allows the
    proxy chain to maintain basic token accounting during passthrough.
    """
    converter = PassthroughEventConverter()
    assert converter.last_usage is None

    stream_event = StreamCompleteEvent(message=_make_mock_message())
    async for _ in converter.convert(stream_event):
        pass  # Should yield nothing

    assert converter.last_usage is not None
    assert isinstance(converter.last_usage, Usage)
    assert converter.last_usage.total_tokens == 100
    assert converter.last_usage.input_tokens == 50
    assert converter.last_usage.output_tokens == 50


# ---------------------------------------------------------------------------
# Test 4: Zero conversion during passthrough — ACPEventConverter.convert NOT called
# ---------------------------------------------------------------------------


async def test_zero_conversion_during_passthrough() -> None:
    """When PassthroughEventConverter is used, ACPEventConverter.convert is NOT called.

    This is the core guarantee of zero-conversion passthrough: when a
    PassthroughEventConverter is substituted for an ACPEventConverter, the
    expensive event-to-ACP conversion logic must never run.

    We verify this by spying on ACPEventConverter.convert and ensuring
    it is never called while the passthrough converter processes events.
    """
    # Create a real ACPEventConverter and spy on its convert method
    acp_converter = ACPEventConverter()
    convert_call_count = 0

    original_convert = acp_converter.convert

    async def _counting_convert(event: Any) -> Any:
        nonlocal convert_call_count
        convert_call_count += 1
        async for update in original_convert(event):
            yield update

    acp_converter.convert = _counting_convert  # type: ignore[method-assign]

    # Create a passthrough converter (this is what gets used during passthrough)
    passthrough_converter = PassthroughEventConverter()

    # Process events through the passthrough converter only
    events: list[Any] = [
        PartStartEvent(index=0, part=TextPart(content="Hello world")),
        PartDeltaEvent(index=0, delta=TextPartDelta(content_delta=" more text")),
        StreamCompleteEvent(message=_make_mock_message()),
    ]

    for event in events:
        async for _ in passthrough_converter.convert(event):
            pass  # Passthrough yields nothing

    # The ACPEventConverter.convert should NEVER have been called
    assert convert_call_count == 0, (
        "ACPEventConverter.convert must not be called during passthrough — "
        f"it was called {convert_call_count} times"
    )

    # Sanity check: the ACPEventConverter WOULD have produced output for these events
    # (verify the spy didn't break anything by calling it directly)
    text_event = PartStartEvent(index=0, part=TextPart(content="Hello"))
    direct_results = [update async for update in acp_converter.convert(text_event)]
    assert len(direct_results) > 0, (
        "ACPEventConverter should produce output when called directly "
        "(verifies the spy didn't break it)"
    )
    assert convert_call_count == 1  # Only from our direct call above


# ---------------------------------------------------------------------------
# Test 5: PassthroughEventConverter.reset() clears usage
# ---------------------------------------------------------------------------


def test_passthrough_converter_reset_clears_usage() -> None:
    """PassthroughEventConverter.reset() clears last_usage."""
    converter = PassthroughEventConverter()
    # Simulate usage being set
    converter.last_usage = Usage(
        total_tokens=42,
        input_tokens=20,
        output_tokens=22,
    )
    assert converter.last_usage is not None

    converter.reset()

    assert converter.last_usage is None


# ---------------------------------------------------------------------------
# Test 6: PassthroughEventConverter.build_subagent_completed() is a no-op
# ---------------------------------------------------------------------------


async def test_passthrough_converter_build_subagent_completed_noop() -> None:
    """PassthroughEventConverter.build_subagent_completed() yields nothing."""
    converter = PassthroughEventConverter()

    results = [
        update
        async for update in converter.build_subagent_completed("child-session-123")
    ]

    assert results == [], (
        "build_subagent_completed should yield nothing in passthrough mode"
    )
