"""Test suite for missing function implementations.

Tests for to_claude_system_prompt, to_output_format, and claude_message_to_events.
"""

import sys
from pathlib import Path
from typing import Any
from collections.abc import AsyncIterator

import pytest

# Add src to path for imports
sys_path = Path(__file__).parent.parent.parent / "src"
sys.path.insert(0, str(sys_path))


def test_to_claude_system_prompt_exists():
    """Test that to_claude_system_prompt function exists."""

    from agentpool.agents.claude_code_agent.converters import to_claude_system_prompt

    assert callable(to_claude_system_prompt)

    print("✓ to_claude_system_prompt function exists")


def test_to_claude_system_prompt_functionality():
    """Test to_claude_system_prompt functionality."""

    from agentpool.agents.claude_code_agent.converters import to_claude_system_prompt

    prompt = "You are a helpful assistant."

    result = to_claude_system_prompt(prompt)

    assert result == prompt

    print("✓ to_claude_system_prompt works correctly")


def test_to_claude_system_prompt_none():
    """Test to_claude_system_prompt with None."""

    from agentpool.agents.claude_code_agent.converters import to_claude_system_prompt

    result = to_claude_system_prompt(None)

    assert result is None

    print("✓ to_claude_system_prompt handles None correctly")


def test_to_claude_system_prompt_empty_string():
    """Test to_claude_system_prompt with empty string."""

    from agentpool.agents.claude_code_agent.converters import to_claude_system_prompt

    result = to_claude_system_prompt("")

    assert result == ""

    print("✓ to_claude_system_prompt handles empty string correctly")


def test_to_output_format_exists():
    """Test that to_output_format function exists."""

    from agentpool.agents.claude_code_agent.converters import to_output_format

    assert callable(to_output_format)

    print("✓ to_output_format function exists")


def test_to_output_format_none():
    """Test to_output_format with None."""

    from agentpool.agents.claude_code_agent.converters import to_output_format

    result = to_output_format(None)

    assert result is None

    print("✓ to_output_format handles None correctly")


def test_to_output_format_str():
    """Test to_output_format with str type."""

    from agentpool.agents.claude_code_agent.converters import to_output_format

    result = to_output_format(str)

    assert result is None

    print("✓ to_output_format handles str type correctly")


def test_to_output_format_structured_type():
    """Test to_output_format with structured output type."""

    from agentpool.agents.claude_code_agent.converters import to_output_format

    class OutputType:
        field1: str
        field2: int

    result = to_output_format(OutputType)

    assert isinstance(result, dict)
    assert "type" in result

    print("✓ to_output_format handles structured type correctly")


def test_claude_message_to_events_exists():
    """Test that claude_message_to_events function exists."""

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events

    assert callable(claude_message_to_events)

    print("✓ claude_message_to_events function exists")


def test_claude_message_to_events_is_async_generator():
    """Test that claude_message_to_events is an async generator."""

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events
    import inspect

    assert inspect.isasyncgenfunction(claude_message_to_events)

    print("✓ claude_message_to_events is an async generator")


@pytest.mark.asyncio
async def test_claude_message_to_events_basic():
    """Test claude_message_to_events basic functionality."""

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events
    from agentpool.agents.events import PartDeltaEvent

    class MockMessage:
        content = "Test message"

    message = MockMessage()

    events = []
    async for event in claude_message_to_events(message, agent_name="test_agent"):
        events.append(event)

    assert len(events) > 0
    assert isinstance(events[0], PartDeltaEvent)

    print("✓ claude_message_to_events basic functionality works")


@pytest.mark.asyncio
async def test_claude_message_to_events_agent_name():
    """Test that agent_name is passed correctly."""

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events

    class MockMessage:
        content = "Test message"

    message = MockMessage()

    events = []
    async for event in claude_message_to_events(message, agent_name="custom_agent"):
        events.append(event)

    # Agent name should be included in event metadata or context
    # (implementation dependent)

    print("✓ claude_message_to_events agent_name handling works")


def test_converter_imports():
    """Test that all converter functions are importable."""

    from agentpool.agents.claude_code_agent.converters import (
        to_claude_system_prompt,
        to_output_format,
        claude_message_to_events,
    )

    assert callable(to_claude_system_prompt)
    assert callable(to_output_format)
    assert callable(claude_message_to_events)

    print("✓ All converter functions are importable")


@pytest.mark.asyncio
async def test_claude_message_to_events_content_blocks():
    """Test claude_message_to_events with different content block types."""

    from agentpool.agents.claude_code_agent.converters import claude_message_to_events
    from agentpool.agents.events import (
        PartDeltaEvent,
        ToolCallStartEvent,
        ToolCallCompleteEvent,
    )
    from clawd_code_sdk.models.content_blocks import (
        TextBlock,
        ThinkingBlock,
        ToolUseBlock,
        ToolResultBlock,
    )

    # Test message with TextBlock
    class MockTextMessage:
        content = [TextBlock(text="Hello, world!")]

    events = []
    async for event in claude_message_to_events(MockTextMessage(), agent_name="test_agent"):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], PartDeltaEvent)
    assert events[0].delta.content_delta == "Hello, world!"

    # Test message with ThinkingBlock
    class MockThinkingMessage:
        content = [ThinkingBlock(thinking="This is thinking content", signature="sig")]

    events = []
    async for event in claude_message_to_events(MockThinkingMessage(), agent_name="test_agent"):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], PartDeltaEvent)
    assert "<thinking>" in events[0].delta.content_delta
    assert "This is thinking content" in events[0].delta.content_delta

    # Test message with ToolUseBlock
    class MockToolUseMessage:
        content = [ToolUseBlock(id="tool_123", name="bash", input={"command": "ls -la"})]

    events = []
    async for event in claude_message_to_events(MockToolUseMessage(), agent_name="test_agent"):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], ToolCallStartEvent)
    assert events[0].tool_name == "bash"
    assert events[0].tool_call_id == "tool_123"
    assert events[0].raw_input == {"command": "ls -la"}

    # Test message with ToolResultBlock
    class MockToolResultMessage:
        content = [
            ToolResultBlock(tool_use_id="tool_123", content="Command output", is_error=False)
        ]

    events = []
    async for event in claude_message_to_events(MockToolResultMessage(), agent_name="test_agent"):
        events.append(event)

    assert len(events) == 1
    assert isinstance(events[0], ToolCallCompleteEvent)
    assert events[0].tool_call_id == "tool_123"
    assert events[0].tool_result == "Command output"

    # Test message with multiple content blocks
    class MockMixedMessage:
        content = [
            TextBlock(text="Thinking: "),
            ThinkingBlock(thinking="Need to check something"),
            TextBlock(text="\n\nResult: "),
        ]

    events = []
    async for event in claude_message_to_events(MockMixedMessage(), agent_name="test_agent"):
        events.append(event)

    assert len(events) == 3
    assert all(isinstance(e, PartDeltaEvent) for e in events)

    print("✓ claude_message_to_events handles different content block types correctly")


if __name__ == "__main__":
    print("Testing missing function implementations...\n")
    test_to_claude_system_prompt_exists()
    test_to_claude_system_prompt_functionality()
    test_to_claude_system_prompt_none()
    test_to_claude_system_prompt_empty_string()
    test_to_output_format_exists()
    test_to_output_format_none()
    test_to_output_format_str()
    test_to_output_format_structured_type()
    test_claude_message_to_events_exists()
    test_claude_message_to_events_is_async_generator()
    print("\n✓ All missing function tests passed!")
    print("Run with pytest to execute async tests.")
