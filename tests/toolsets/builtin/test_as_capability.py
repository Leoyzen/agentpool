"""Tests for builtin tool provider as_capability() methods."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from pydantic_ai import RunContext
from pydantic_ai.capabilities import Toolset
from pydantic_ai.toolsets import AbstractToolset

from agentpool.resource_providers import ResourceProvider, StaticResourceProvider
from agentpool_toolsets.builtin import (
    CodeTools,
    DebugTools,
    ProcessManagementTools,
    SkillsTools,
    SubagentTools,
    WorkersTools,
)


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
    """Resolve a Toolset capability to its underlying AbstractToolset.

    Since as_capability() returns Toolset(callable) for lazy evaluation,
    we need to invoke the callable with a mock RunContext to get the
    actual toolset.
    """
    from collections.abc import Callable

    toolset_or_callable = cap.get_toolset()
    if toolset_or_callable is None:
        return None
    if isinstance(toolset_or_callable, AbstractToolset):
        return toolset_or_callable
    # It's a callable - invoke with mock context
    mock_ctx = _make_run_context()
    callable_ts = cast(Callable[[Any], Any], toolset_or_callable)
    result = callable_ts(mock_ctx)
    if isinstance(result, Awaitable):
        return await result
    return result


@pytest.mark.unit
class TestResourceProviderAsCapability:
    """Tests for ResourceProvider.as_capability() base implementation."""

    async def test_returns_toolset_capability(self) -> None:
        """Default implementation returns a Toolset capability."""

        class SimpleProvider(StaticResourceProvider):
            def __init__(self) -> None:
                super().__init__(name="simple")
                self._tools = [self.create_tool(lambda x: x, name_override="identity")]

        provider = SimpleProvider()
        cap = provider.as_capability()

        assert isinstance(cap, Toolset)

    async def test_empty_tools_returns_toolset(self) -> None:
        """Provider with no tools still returns a Toolset capability."""

        class EmptyProvider(ResourceProvider):
            pass

        provider = EmptyProvider(name="empty")
        cap = provider.as_capability()

        assert isinstance(cap, Toolset)


@pytest.mark.unit
class TestDebugToolsAsCapability:
    """Tests for DebugTools.as_capability()."""

    async def test_returns_toolset_capability(self) -> None:
        """DebugTools returns a Toolset capability."""
        provider = DebugTools()
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_toolset_contains_expected_tools(self) -> None:
        """Capability toolset includes introspection and platform_paths tools."""
        provider = DebugTools()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is not None
        tools = await toolset.get_tools(_make_run_context())

        assert "execute_introspection" in tools
        assert "get_platform_paths" in tools


@pytest.mark.unit
class TestSubagentToolsAsCapability:
    """Tests for SubagentTools.as_capability()."""

    async def test_returns_toolset_capability(self) -> None:
        """SubagentTools returns a Toolset capability."""
        provider = SubagentTools()
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_toolset_contains_expected_tools(self) -> None:
        """Capability toolset includes list_available_nodes and task tools."""
        provider = SubagentTools()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is not None
        tools = await toolset.get_tools(_make_run_context())

        assert "list_available_nodes" in tools
        assert "task" in tools


@pytest.mark.unit
class TestSkillsToolsAsCapability:
    """Tests for SkillsTools.as_capability()."""

    async def test_returns_toolset_capability(self) -> None:
        """SkillsTools returns a Toolset capability."""
        provider = SkillsTools()
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_toolset_contains_expected_tools(self) -> None:
        """Capability toolset includes load_skill and list_skills tools."""
        provider = SkillsTools()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is not None
        tools = await toolset.get_tools(_make_run_context())

        assert "load_skill" in tools
        assert "list_skills" in tools


@pytest.mark.unit
class TestCodeToolsAsCapability:
    """Tests for CodeTools.as_capability()."""

    async def test_returns_toolset_capability(self) -> None:
        """CodeTools returns a Toolset capability."""
        provider = CodeTools()
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_toolset_contains_format_code_tool(self) -> None:
        """Capability toolset always includes format_code tool."""
        provider = CodeTools()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is not None
        tools = await toolset.get_tools(_make_run_context())

        assert "format_code" in tools


@pytest.mark.unit
class TestProcessManagementToolsAsCapability:
    """Tests for ProcessManagementTools.as_capability()."""

    async def test_returns_toolset_capability(self) -> None:
        """ProcessManagementTools returns a Toolset capability."""
        provider = ProcessManagementTools()
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_toolset_contains_expected_tools(self) -> None:
        """Capability toolset includes process management tools."""
        provider = ProcessManagementTools()
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is not None
        tools = await toolset.get_tools(_make_run_context())

        assert "start_process" in tools
        assert "get_process_output" in tools
        assert "wait_for_process" in tools
        assert "kill_process" in tools
        assert "release_process" in tools
        assert "list_processes" in tools


@pytest.mark.unit
class TestWorkersToolsAsCapability:
    """Tests for WorkersTools.as_capability()."""

    async def test_returns_toolset_capability_with_no_workers(self) -> None:
        """WorkersTools with no workers returns a Toolset capability."""
        provider = WorkersTools(workers=[])
        cap = cast(Toolset[Any], provider.as_capability())

        assert isinstance(cap, Toolset)

    async def test_empty_workers_yields_no_tools(self) -> None:
        """WorkersTools with empty workers list produces empty toolset."""
        provider = WorkersTools(workers=[])
        cap = cast(Toolset[Any], provider.as_capability())
        toolset = await _resolve_toolset(cap)

        assert toolset is None
