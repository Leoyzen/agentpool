"""Tests for ResourceProvider capability mapping.

Consolidated from:
- test_confirmation_toolset.py (Tool.requires_confirmation → ApprovalRequiredToolset mapping)
- test_custom_capability.py (custom ResourceProvider subclasses overriding as_capability())
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock

from pydantic_ai import RunContext
from pydantic_ai.capabilities import Hooks, Toolset
from pydantic_ai.toolsets import (
    AbstractToolset,
    ApprovalRequiredToolset,
    CombinedToolset,
    FunctionToolset,
)
import pytest

from agentpool.resource_providers import StaticResourceProvider
from agentpool.resource_providers.base import ResourceProvider


# ============================================================================
# Helpers
# ============================================================================


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


# ============================================================================
# Confirmation toolset mapping tests
# ============================================================================


@pytest.mark.unit
class TestConfirmationToolsetMapping:
    """Tests mapping Tool.requires_confirmation to ApprovalRequiredToolset."""

    async def test_normal_tools_no_confirmation(self) -> None:
        """Tools with requires_confirmation=False use plain FunctionToolset."""

        class NormalProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="normal")
                self._tools = [self.create_tool(lambda x: x, name_override="identity")]

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
        assert desc is not None
        assert "Delete a file" in desc

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

        cap = cast(Toolset[Any], provider.as_capability())
        await _resolve_toolset(cap)

        tools_after = await provider.get_tools()
        assert tools_after[0].requires_confirmation is True


# ============================================================================
# Custom capability tests
# ============================================================================


class CustomHooksProvider(ResourceProvider):
    """Custom provider that returns a Hooks capability with a before_run callback."""

    def __init__(self, name: str = "custom_hooks") -> None:
        super().__init__(name=name)
        self.before_run_called = False

    async def _before_run(self, ctx: RunContext[Any]) -> None:
        """Hook that records it was called."""
        self.before_run_called = True

    def as_capability(self) -> Hooks[Any]:
        return Hooks(before_run=self._before_run)


class CustomToolsetProvider(ResourceProvider):
    """Custom provider that returns a Toolset capability with a simple tool."""

    def __init__(self, name: str = "custom_toolset") -> None:
        super().__init__(name=name)

    def as_capability(self) -> Toolset[Any]:
        async def _build_toolset(ctx: Any) -> FunctionToolset[Any] | None:
            def greet(name: str) -> str:
                """Greet someone by name."""
                return f"Hello, {name}!"

            from pydantic_ai.tools import Tool

            tool = Tool(greet)
            return FunctionToolset([tool], id=self.name)

        return Toolset(_build_toolset)


@pytest.mark.unit
class TestCustomHooksCapability:
    """Tests for custom ResourceProvider returning Hooks capability."""

    async def test_returns_hooks_instance(self) -> None:
        provider = CustomHooksProvider()
        cap = provider.as_capability()
        assert isinstance(cap, Hooks)

    async def test_hooks_capability_is_callable(self) -> None:
        provider = CustomHooksProvider()
        cap = provider.as_capability()
        import inspect

        assert inspect.iscoroutinefunction(cap.before_run)

    async def test_before_run_hook_fires(self) -> None:
        provider = CustomHooksProvider()
        cap = provider.as_capability()
        ctx = _make_run_context()
        await cap.before_run(ctx)
        assert provider.before_run_called is True

    async def test_hooks_capability_has_empty_toolset(self) -> None:
        provider = CustomHooksProvider()
        cap = provider.as_capability()
        toolset = cap.get_toolset()
        assert toolset is None


@pytest.mark.unit
class TestCustomToolsetCapability:
    """Tests for custom ResourceProvider returning Toolset capability."""

    async def test_returns_toolset_instance(self) -> None:
        provider = CustomToolsetProvider()
        cap = provider.as_capability()
        assert isinstance(cap, Toolset)

    async def test_toolset_resolves_to_function_toolset(self) -> None:
        provider = CustomToolsetProvider()
        cap = provider.as_capability()
        ctx = _make_run_context()
        toolset_or_callable = cap.get_toolset()

        assert toolset_or_callable is not None
        assert callable(toolset_or_callable)

        callable_ts = cast(Callable[[Any], Any], toolset_or_callable)
        result = callable_ts(ctx)
        if isinstance(result, Awaitable):
            result = await result

        assert isinstance(result, FunctionToolset)

    async def test_toolset_contains_expected_tool(self) -> None:
        provider = CustomToolsetProvider()
        cap = provider.as_capability()
        ctx = _make_run_context()
        toolset_or_callable = cap.get_toolset()
        assert toolset_or_callable is not None
        assert callable(toolset_or_callable)

        callable_ts = cast(Callable[[Any], Any], toolset_or_callable)
        result = callable_ts(ctx)
        if isinstance(result, Awaitable):
            result = await result

        assert isinstance(result, FunctionToolset)
        tools = await result.get_tools(ctx)
        assert "greet" in tools


@pytest.mark.unit
class TestCustomProviderArbitraryCapability:
    """Tests that custom providers can return any AbstractCapability subtype."""

    async def test_hooks_is_abstract_capability(self) -> None:
        from pydantic_ai.capabilities import AbstractCapability

        provider = CustomHooksProvider()
        cap = provider.as_capability()
        assert isinstance(cap, AbstractCapability)

    async def test_toolset_is_abstract_capability(self) -> None:
        from pydantic_ai.capabilities import AbstractCapability

        provider = CustomToolsetProvider()
        cap = provider.as_capability()
        assert isinstance(cap, AbstractCapability)
