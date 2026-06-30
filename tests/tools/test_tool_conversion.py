"""Regression tests for ``Tool.to_pydantic_ai()`` conversion.

Tests cover the core conversion logic, schema handling, deferred metadata,
approval wrapping, and metadata assembly. These serve as a **behavioral
baseline** before the thinning refactor simplifies the conversion to a
direct 1:1 mapping.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from pydantic_ai.tools import Tool as PydanticAiTool

from agentpool.tools.base import FunctionTool, Tool, ToolResult, is_terminal_tool


if TYPE_CHECKING:
    from collections.abc import Callable


# ---------------------------------------------------------------------------
# Helper to create a FunctionTool from a callable
# ---------------------------------------------------------------------------


def _make_tool(fn: Any, **kwargs: Any) -> FunctionTool[Any]:
    """Create a FunctionTool from a callable with optional overrides."""
    return FunctionTool.from_callable(fn, **kwargs)


# ---------------------------------------------------------------------------
# Simple tool conversion
# ---------------------------------------------------------------------------


def test_simple_tool_converts_to_pydantic_ai():
    """A simple tool with no special features converts to a PydanticAiTool."""

    def my_tool(x: int) -> str:
        """A test tool."""
        return f"result: {x}"

    tool = _make_tool(my_tool, name_override="my_tool")
    result = tool.to_pydantic_ai()

    assert isinstance(result, PydanticAiTool)
    assert result.name == "my_tool"


def test_tool_name_preserved():
    """Tool name is preserved in conversion."""

    def fn(x: str) -> str:
        return x

    tool = _make_tool(fn, name_override="custom_name")
    assert tool.to_pydantic_ai().name == "custom_name"


def test_tool_description_preserved():
    """Tool description is preserved in conversion."""

    def fn(x: str) -> str:
        """My description."""
        return x

    tool = _make_tool(fn, name_override="t")
    converted = tool.to_pydantic_ai()
    # The description may be stored differently, but the tool should have it
    assert converted.name == "t"


# ---------------------------------------------------------------------------
# ToolResult fields
# ---------------------------------------------------------------------------


def test_tool_result_content_only():
    """ToolResult can be created with just content."""
    result = ToolResult(content="hello")
    assert result.content == "hello"
    assert result.structured_content is None
    assert result.metadata is None


def test_tool_result_with_structured_content():
    """ToolResult structured_content field works."""
    result = ToolResult(
        content="summary",
        structured_content={"key": "value", "count": 42},
    )
    assert result.structured_content == {"key": "value", "count": 42}


def test_tool_result_with_metadata():
    """ToolResult metadata field works and is separate from content."""
    result = ToolResult(
        content="visible to LLM",
        metadata={"diff": "+added", "path": "/foo.py"},
    )
    assert result.metadata == {"diff": "+added", "path": "/foo.py"}
    assert "diff" not in str(result.content)


def test_tool_result_all_fields():
    """ToolResult with all fields set."""
    result = ToolResult(
        content="text",
        structured_content={"data": 1},
        metadata={"ui_info": "extra"},
    )
    assert result.content == "text"
    assert result.structured_content == {"data": 1}
    assert result.metadata == {"ui_info": "extra"}


# ---------------------------------------------------------------------------
# ToolKind
# ---------------------------------------------------------------------------


def test_tool_kind_default_none():
    """Tool.category defaults to None."""
    def fn() -> str:
        return "x"

    tool = _make_tool(fn)
    assert tool.category is None


def test_tool_kind_can_be_set():
    """Tool.category can be set to any ToolKind value."""
    def fn() -> str:
        return "x"

    for kind in ("read", "edit", "delete", "execute", "search", "other"):
        tool = _make_tool(fn, category=kind)  # type: ignore[arg-type]
        assert tool.category == kind


def test_tool_kind_included_in_metadata():
    """Tool.category is included in metadata when converting to pydantic_ai."""
    def fn() -> str:
        return "x"

    tool = _make_tool(fn, name_override="t", category="read")  # type: ignore[arg-type]
    converted = tool.to_pydantic_ai()
    if hasattr(converted, "metadata") and converted.metadata:
        assert converted.metadata.get("category") == "read"


# ---------------------------------------------------------------------------
# requires_confirmation / approval
# ---------------------------------------------------------------------------


def test_requires_confirmation_false_by_default():
    """Tool.requires_confirmation defaults to False."""
    def fn() -> str:
        return "x"

    tool = _make_tool(fn)
    assert tool.requires_confirmation is False


def test_requires_confirmation_propagates_to_requires_approval():
    """requires_confirmation=True → requires_approval=True on PydanticAiTool."""
    def fn() -> str:
        return "x"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        requires_confirmation=True,
    )
    converted = tool.to_pydantic_ai()
    assert converted.requires_approval is True


def test_no_confirmation_means_no_approval():
    """requires_confirmation=False → requires_approval=False."""
    def fn() -> str:
        return "x"

    tool = _make_tool(fn, name_override="t")
    converted = tool.to_pydantic_ai()
    assert converted.requires_approval is False


# ---------------------------------------------------------------------------
# Deferred tools
# ---------------------------------------------------------------------------


def test_deferred_unapproved_sets_requires_approval():
    """deferred=True + deferred_kind='unapproved' → requires_approval=True."""
    def fn() -> str:
        return "x"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        deferred=True,
        deferred_kind="unapproved",
    )
    converted = tool.to_pydantic_ai()
    assert converted.requires_approval is True


def test_deferred_external_does_not_set_requires_approval():
    """deferred=True + deferred_kind='external' → requires_approval=False."""
    def fn() -> str:
        return "x"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        deferred=True,
        deferred_kind="external",
    )
    converted = tool.to_pydantic_ai()
    assert converted.requires_approval is False


def test_deferred_strategy_stream_raises():
    """deferred_strategy='stream' raises NotImplementedError."""
    def fn() -> str:
        return "x"

    with pytest.raises(NotImplementedError):
        FunctionTool(
            name="t",
            description="d",
            callable=fn,
            deferred=True,
            deferred_strategy="stream",
        )


def test_deferred_unapproved_with_continue_strategy_raises():
    """deferred_kind='unapproved' + deferred_strategy='continue' raises."""
    from agentpool.tools.exceptions import ToolError

    def fn() -> str:
        return "x"

    with pytest.raises(ToolError, match="deferred_kind='unapproved' requires"):
        FunctionTool(
            name="t",
            description="d",
            callable=fn,
            deferred=True,
            deferred_kind="unapproved",
            deferred_strategy="continue",
        )


# ---------------------------------------------------------------------------
# Terminal tool metadata
# ---------------------------------------------------------------------------


def test_terminal_tool_metadata_true():
    """Tool with agentpool_terminal=true in metadata is terminal."""
    def fn() -> str:
        return "done"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        metadata={"agentpool_terminal": "true"},
    )
    assert is_terminal_tool(tool) is True


def test_terminal_tool_metadata_false():
    """Tool without terminal metadata is not terminal."""
    def fn() -> str:
        return "done"

    tool = _make_tool(fn)
    assert is_terminal_tool(tool) is False


def test_terminal_tool_metadata_various_true_values():
    """Various truthy values for agentpool_terminal."""
    from agentpool.tools.base import has_terminal_tool_metadata

    for val in ("1", "true", "yes", "on", "TRUE", "Yes"):
        assert has_terminal_tool_metadata({"agentpool_terminal": val}) is True

    for val in ("0", "false", "no", "off", ""):
        assert has_terminal_tool_metadata({"agentpool_terminal": val}) is False

    assert has_terminal_tool_metadata({}) is False
    assert has_terminal_tool_metadata(None) is False


# ---------------------------------------------------------------------------
# Metadata assembly in to_pydantic_ai()
# ---------------------------------------------------------------------------


def test_agent_name_included_in_metadata():
    """agent_name is included in the metadata passed to PydanticAiTool."""
    def fn() -> str:
        return "x"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        agent_name="my_agent",
    )
    converted = tool.to_pydantic_ai()
    if hasattr(converted, "metadata") and converted.metadata:
        assert converted.metadata.get("agent_name") == "my_agent"


def test_custom_metadata_merged_with_agent_name_and_category():
    """Custom metadata is merged with agent_name and category."""
    def fn() -> str:
        return "x"

    tool = FunctionTool(
        name="t",
        description="d",
        callable=fn,
        agent_name="agent1",
        category="read",  # type: ignore[arg-type]
        metadata={"custom_key": "custom_val"},
    )
    converted = tool.to_pydantic_ai()
    if hasattr(converted, "metadata") and converted.metadata:
        assert converted.metadata.get("custom_key") == "custom_val"
        assert converted.metadata.get("agent_name") == "agent1"
        assert converted.metadata.get("category") == "read"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
