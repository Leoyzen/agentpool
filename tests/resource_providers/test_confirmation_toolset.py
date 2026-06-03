"""Tests for ResourceProvider.as_capability() confirmation toolset mapping."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Toolset
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    CombinedToolset,
    FunctionToolset,
)

from agentpool.resource_providers import StaticResourceProvider


def _make_run_context() -> RunContext[Any]:
    """Create a minimal RunContext for testing toolset resolution."""
    return RunContext(
        deps=MagicMock(),
        model=MagicMock(),
        usage=MagicMock(),
        messages=[],
        tracer=MagicMock(),
        retries={},
    )


async def _resolve_toolset(cap: Toolset[Any]) -> AbstractToolset[Any] | None:
    """Resolve a Toolset capability to its underlying AbstractToolset."""
    from collections.abc import Callable

    toolset_or_callable = cap.get_toolset()
    if toolset_or_callable is None:
        return None
    if isinstance(toolset_or_callable, AbstractToolset):
        return toolset_or_callable
    mock_ctx = _make_run_context()
    callable_ts = cast(Callable[[Any], Any], toolset_or_callable)
    result = callable_ts(mock_ctx)
    if isinstance(result, Awaitable):
        return await result
    return result


@pytest.mark.unit
class TestConfirmationToolsetMapping:
    """Tests mapping Tool.requires_confirmation to ApprovalRequiredToolset."""

    async def test_normal_tools_no_confirmation(self) -> None:
        """Tools with requires_confirmation=False use plain FunctionToolset."""

        class NormalProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="normal")
                self._tools = [
                    self.create_tool(lambda x: x, name_override="identity")
                ]

        provider = NormalProvider()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert isinstance(toolset, FunctionToolset)
        assert not isinstance(toolset, ApprovalRequiredToolset)

    async def test_confirmation_tools_wrapped(self) -> None:
        """Tools with requires_confirmation=True are wrapped in ApprovalRequiredToolset."""

        class ConfirmProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="confirm")
                self._tools = [
                    self.create_tool(
                        lambda x: x,
                        name_override="dangerous",
                        requires_confirmation=True,
                    )
                ]

        provider = ConfirmProvider()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert isinstance(toolset, ApprovalRequiredToolset)
        assert isinstance(toolset.wrapped, FunctionToolset)

    async def test_mixed_tools_combined(self) -> None:
        """Mixed tools produce CombinedToolset with both normal and approval-required."""

        class MixedProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="mixed")
                self._tools = [
                    self.create_tool(lambda x: x, name_override="safe"),
                    self.create_tool(
                        lambda x: x,
                        name_override="dangerous",
                        requires_confirmation=True,
                    ),
                ]

        provider = MixedProvider()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert isinstance(toolset, CombinedToolset)
        assert len(toolset.toolsets) == 2

        # One should be plain FunctionToolset, the other ApprovalRequiredToolset
        types_found = {type(ts) for ts in toolset.toolsets}
        assert FunctionToolset in types_found
        assert ApprovalRequiredToolset in types_found

    async def test_empty_tools_returns_none(self) -> None:
        """Provider with no tools returns None from resolved toolset."""

        class EmptyProvider(StaticResourceProvider):
            pass

        provider = EmptyProvider(name="empty")
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is None

    async def test_tool_metadata_preserved(self) -> None:
        """Tool name, description and schema are preserved through wrapping."""

        def dangerous_action(path: str) -> str:
            """Delete a file at the given path."""
            return f"deleted {path}"

        class MetaProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="meta")
                self._tools = [
                    self.create_tool(
                        dangerous_action,
                        name_override="delete_file",
                        requires_confirmation=True,
                    )
                ]

        provider = MetaProvider()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert isinstance(toolset, ApprovalRequiredToolset)
        wrapped = cast(FunctionToolset[Any], toolset.wrapped)
        tools = await wrapped.get_tools(_make_run_context())

        assert "delete_file" in tools
        tool = tools["delete_file"]
        assert tool.tool_def.name == "delete_file"
        desc = tool.tool_def.description
        assert desc is not None and "Delete a file" in desc

    async def test_requires_confirmation_attribute_unchanged(self) -> None:
        """Tool.requires_confirmation is not mutated during capability conversion."""

        class PreserveProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="preserve")
                self._tools = [
                    self.create_tool(
                        lambda x: x,
                        name_override="mut_test",
                        requires_confirmation=True,
                    )
                ]

        provider = PreserveProvider()
        tools = await provider.get_tools()

        assert len(tools) == 1
        assert tools[0].requires_confirmation is True

        # Convert to capability and verify attribute is still True
        cap = cast(Toolset[Any], provider.as_capability())
        await _resolve_toolset(cap)

        tools_after = await provider.get_tools()
        assert tools_after[0].requires_confirmation is True
