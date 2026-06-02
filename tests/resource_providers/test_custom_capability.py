"""Tests for custom ResourceProvider subclasses overriding as_capability().

Validates that custom providers can return arbitrary AbstractCapability
instances (Hooks, Toolset, etc.) through as_capability().
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Hooks, Toolset
from pydantic_ai.toolsets import FunctionToolset

from agentpool.resource_providers.base import ResourceProvider


def _make_run_context() -> RunContext[Any]:
    """Create a minimal RunContext for testing capabilities."""
    return RunContext(
        deps=MagicMock(),
        model=MagicMock(),
        usage=MagicMock(),
        messages=[],
        tracer=MagicMock(),
        retries={},
    )


class CustomHooksProvider(ResourceProvider):
    """Custom provider that returns a Hooks capability with a before_run callback."""

    def __init__(self, name: str = "custom_hooks") -> None:
        """Initialize with a tracked hook flag."""
        super().__init__(name=name)
        self.before_run_called = False

    async def _before_run(self, ctx: RunContext[Any]) -> None:
        """Hook that records it was called."""
        self.before_run_called = True

    def as_capability(self) -> Hooks[Any]:
        """Return a Hooks capability with a custom before_run callback.

        Returns:
            A pydantic-ai Hooks instance.
        """
        return Hooks(before_run=self._before_run)


class CustomToolsetProvider(ResourceProvider):
    """Custom provider that returns a Toolset capability with a simple tool."""

    def __init__(self, name: str = "custom_toolset") -> None:
        """Initialize the provider."""
        super().__init__(name=name)

    def as_capability(self) -> Toolset[Any]:
        """Return a Toolset capability with a custom tool.

        Returns:
            A pydantic-ai Toolset wrapping a FunctionToolset.
        """

        async def _build_toolset(ctx: Any) -> FunctionToolset[Any] | None:
            def greet(name: str) -> str:
                """Greet someone by name."""
                return f"Hello, {name}!"

            from pydantic_ai.tools import Tool

            tool = Tool(greet)
            return FunctionToolset([tool], id=self.name)

        return Toolset(_build_toolset)


class TestCustomHooksCapability:
    """Tests for custom ResourceProvider returning Hooks capability."""

    async def test_returns_hooks_instance(self) -> None:
        """Custom provider returns a Hooks AbstractCapability."""
        provider = CustomHooksProvider()
        cap = provider.as_capability()

        assert isinstance(cap, Hooks)

    async def test_hooks_capability_is_callable(self) -> None:
        """The returned Hooks has async lifecycle methods."""
        provider = CustomHooksProvider()
        cap = provider.as_capability()

        import inspect

        assert inspect.iscoroutinefunction(cap.before_run)

    async def test_before_run_hook_fires(self) -> None:
        """The custom before_run hook is invoked and tracks state."""
        provider = CustomHooksProvider()
        cap = provider.as_capability()

        ctx = _make_run_context()
        await cap.before_run(ctx)

        assert provider.before_run_called is True

    async def test_hooks_capability_has_empty_toolset(self) -> None:
        """Hooks capability does not expose tools by default."""
        provider = CustomHooksProvider()
        cap = provider.as_capability()

        toolset = cap.get_toolset()
        assert toolset is None


class TestCustomToolsetCapability:
    """Tests for custom ResourceProvider returning Toolset capability."""

    async def test_returns_toolset_instance(self) -> None:
        """Custom provider returns a Toolset AbstractCapability."""
        provider = CustomToolsetProvider()
        cap = provider.as_capability()

        assert isinstance(cap, Toolset)

    async def test_toolset_resolves_to_function_toolset(self) -> None:
        """The Toolset resolves to a FunctionToolset when invoked."""
        provider = CustomToolsetProvider()
        cap = provider.as_capability()

        ctx = _make_run_context()
        toolset_or_callable = cap.get_toolset()

        assert toolset_or_callable is not None
        # Toolset wraps a callable for lazy evaluation
        assert callable(toolset_or_callable)

        callable_ts = cast(Callable[[Any], Any], toolset_or_callable)
        result = callable_ts(ctx)
        if isinstance(result, Awaitable):
            result = await result

        assert isinstance(result, FunctionToolset)

    async def test_toolset_contains_expected_tool(self) -> None:
        """The resolved toolset includes the 'greet' tool."""
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


class TestCustomProviderArbitraryCapability:
    """Tests that custom providers can return any AbstractCapability subtype."""

    async def test_hooks_is_abstract_capability(self) -> None:
        """Hooks is a valid AbstractCapability for custom providers."""
        from pydantic_ai.capabilities import AbstractCapability

        provider = CustomHooksProvider()
        cap = provider.as_capability()

        assert isinstance(cap, AbstractCapability)

    async def test_toolset_is_abstract_capability(self) -> None:
        """Toolset is a valid AbstractCapability for custom providers."""
        from pydantic_ai.capabilities import AbstractCapability

        provider = CustomToolsetProvider()
        cap = provider.as_capability()

        assert isinstance(cap, AbstractCapability)
