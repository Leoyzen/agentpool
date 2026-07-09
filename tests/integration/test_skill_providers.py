"""Integration tests for CombinedToolsetCapability.

Tests cover toolset aggregation, instructions concatenation, change event
propagation via ``on_change()``, lifecycle delegation via
``__aenter__``/``__aexit__``, and the ``capabilities`` property.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Self
from unittest.mock import MagicMock

import pytest
from upathtools import UPath

from agentpool.capabilities.change_event import ChangeEvent
from agentpool.capabilities.combined_toolset import CombinedToolsetCapability
from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.skills.skill import Skill


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from types import TracebackType

    from agentpool.capabilities.change_event import ChangeKind
    from agentpool.prompts.prompts import BasePrompt
    from agentpool.tools.base import Tool


# =============================================================================
# Mock Capabilities for Testing
# =============================================================================


class MockCapability(FunctionToolsetCapability):
    """Mock capability simulating SkillCapability or MCPCapability behavior.

    Extends :class:`FunctionToolsetCapability` with:
    - Lifecycle tracking (``entered``/``exited`` flags)
    - Change event emission via ``on_change()`` (internal asyncio.Queue)
    """

    def __init__(
        self,
        name: str = "mock",
        tools: list[Tool] | None = None,
        instructions: str | None = None,
        prompts: list[BasePrompt] | None = None,
        resources: list[Any] | None = None,
    ) -> None:
        super().__init__(
            tools=tools,
            name=name,
            instructions=instructions,
            prompts=prompts,
            resources=resources,
        )
        self.entered = False
        self.exited = False
        self._change_queue: asyncio.Queue[ChangeEvent | None] = asyncio.Queue()

    async def __aenter__(self) -> Self:
        """Async context entry."""
        self.entered = True
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Async context cleanup."""
        self.exited = True

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Return a merged change event stream from an internal queue."""
        return self._change_generator()

    async def _change_generator(self) -> AsyncIterator[ChangeEvent]:
        """Yield change events from the internal queue until ``None``."""
        while True:
            event = await self._change_queue.get()
            if event is None:
                break
            yield event

    async def emit_change(
        self,
        kind: ChangeKind = "tools_changed",
    ) -> None:
        """Emit a change event for testing."""
        await self._change_queue.put(ChangeEvent(capability_name=self.name, kind=kind))

    async def stop_changes(self) -> None:
        """Signal the change generator to stop."""
        await self._change_queue.put(None)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_skill_local() -> Skill:
    """Create a mock skill from local provider."""
    return Skill(
        name="local-skill",
        description="A skill from local provider",
        skill_path=UPath("/tmp/local-skill"),
        metadata={"source": "local"},
    )


@pytest.fixture
def mock_skill_mcp() -> Skill:
    """Create a mock skill from MCP provider."""
    return Skill(
        name="mcp-skill",
        description="A skill from MCP provider",
        skill_path=UPath("mcp://test/mcp-skill"),
        metadata={"source": "mcp"},
    )


@pytest.fixture
def mock_tool_local() -> MagicMock:
    """Create a mock tool from local provider."""
    return MagicMock(name="local_tool")


@pytest.fixture
def mock_tool_mcp() -> MagicMock:
    """Create a mock tool from MCP provider."""
    return MagicMock(name="mcp_tool")


# =============================================================================
# Test Class: CombinedToolsetBasics
# =============================================================================


@pytest.mark.integration
class TestCombinedToolsetBasics:
    """Test basic CombinedToolsetCapability functionality."""

    async def test_empty_capability_list(self) -> None:
        """Test combined capability with no children returns None for toolset and instructions."""
        combined = CombinedToolsetCapability(capabilities=[], name="empty")

        assert combined.get_toolset() is None
        assert combined.get_instructions() is None
        assert combined.on_change() is None

    async def test_single_capability_toolset(self, mock_tool_local: MagicMock) -> None:
        """Test combined capability aggregates tools from single child via get_tools()."""
        child = MockCapability(name="local", tools=[mock_tool_local])
        combined = CombinedToolsetCapability(capabilities=[child])

        # get_tools() is the backward-compat method that collects from children
        tools = await combined.get_tools()
        assert len(tools) == 1

    async def test_single_capability_instructions(self) -> None:
        """Test combined capability aggregates instructions from single child."""
        child = MockCapability(name="local", instructions="Hello world")
        combined = CombinedToolsetCapability(capabilities=[child])

        instructions = combined.get_instructions()
        assert instructions == "Hello world"

    async def test_multiple_capability_instructions(self) -> None:
        """Test combined capability concatenates instructions from multiple children."""
        child1 = MockCapability(name="local", instructions="Local instructions")
        child2 = MockCapability(name="mcp", instructions="MCP instructions")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        instructions = combined.get_instructions()
        assert instructions == "Local instructions\n\nMCP instructions"

    async def test_name_derivation(self) -> None:
        """Test that name is derived from child capability names."""
        child1 = MockCapability(name="alpha")
        child2 = MockCapability(name="beta")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        assert combined.name == "combined:alpha,beta"

    async def test_name_override(self) -> None:
        """Test that explicit name takes precedence over derivation."""
        child = MockCapability(name="alpha")
        combined = CombinedToolsetCapability(capabilities=[child], name="custom")

        assert combined.name == "custom"

    async def test_empty_name_derivation(self) -> None:
        """Test name derivation with no children."""
        combined = CombinedToolsetCapability(capabilities=[])

        assert combined.name == "combined:empty"


# =============================================================================
# Test Class: CapabilitiesProperty
# =============================================================================


@pytest.mark.integration
class TestCapabilitiesProperty:
    """Test the capabilities property."""

    async def test_capabilities_returns_list(self) -> None:
        """Test that capabilities property returns the list of children."""
        child1 = MockCapability(name="local")
        child2 = MockCapability(name="mcp")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        caps = combined.capabilities

        assert len(caps) == 2
        assert caps[0] is child1
        assert caps[1] is child2

    async def test_capabilities_is_copy(self) -> None:
        """Test that capabilities property returns a copy, not the internal list."""
        child = MockCapability(name="local")
        combined = CombinedToolsetCapability(capabilities=[child])

        caps = combined.capabilities
        caps.clear()

        # Internal list should not be affected
        assert len(combined.capabilities) == 1


# =============================================================================
# Test Class: ChangeEventPropagation
# =============================================================================


@pytest.mark.integration
class TestChangeEventPropagation:
    """Test change event propagation via on_change()."""

    async def test_change_event_from_single_child(self) -> None:
        """Test that change events from a single child propagate through on_change()."""
        child = MockCapability(name="local")
        combined = CombinedToolsetCapability(capabilities=[child])

        change_stream = combined.on_change()
        assert change_stream is not None

        results: list[ChangeEvent] = []
        consumer_task = asyncio.create_task(self._consume(change_stream, results))

        # Give consumer time to start
        await asyncio.sleep(0.01)

        await child.emit_change("tools_changed")
        await asyncio.sleep(0.01)

        await child.stop_changes()
        await consumer_task

        assert len(results) == 1
        assert results[0].capability_name == "local"
        assert results[0].kind == "tools_changed"

    async def test_change_events_from_multiple_children(self) -> None:
        """Test that change events from multiple children merge into one stream."""
        child1 = MockCapability(name="alpha")
        child2 = MockCapability(name="beta")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        change_stream = combined.on_change()
        assert change_stream is not None

        results: list[ChangeEvent] = []
        consumer_task = asyncio.create_task(self._consume(change_stream, results))

        await asyncio.sleep(0.01)

        await child1.emit_change("tools_changed")
        await child2.emit_change("skills_changed")
        await asyncio.sleep(0.01)

        await child1.stop_changes()
        await child2.stop_changes()
        await consumer_task

        assert len(results) == 2
        names = {e.capability_name for e in results}
        assert names == {"alpha", "beta"}

    async def test_no_change_capable_children_returns_none(self) -> None:
        """Test that on_change() returns None when no child supports it."""
        # FunctionToolsetCapability.on_change() returns None
        child = FunctionToolsetCapability(name="static")
        combined = CombinedToolsetCapability(capabilities=[child])

        assert combined.on_change() is None

    async def test_partial_change_capable_children(self) -> None:
        """Test that on_change() works when only some children support it."""
        static_child = FunctionToolsetCapability(name="static")
        dynamic_child = MockCapability(name="dynamic")
        combined = CombinedToolsetCapability(capabilities=[static_child, dynamic_child])

        change_stream = combined.on_change()
        assert change_stream is not None

        results: list[ChangeEvent] = []
        consumer_task = asyncio.create_task(self._consume(change_stream, results))

        await asyncio.sleep(0.01)

        await dynamic_child.emit_change("tools_changed")
        await asyncio.sleep(0.01)

        await dynamic_child.stop_changes()
        await consumer_task

        assert len(results) == 1
        assert results[0].capability_name == "dynamic"

    @staticmethod
    async def _consume(
        stream: AsyncIterator[ChangeEvent],
        results: list[ChangeEvent],
    ) -> None:
        """Consume a change event stream into a results list."""
        results.extend([event async for event in stream])


# =============================================================================
# Test Class: LifecycleDelegation
# =============================================================================


@pytest.mark.integration
class TestLifecycleDelegation:
    """Test __aenter__/__aexit__ delegation to children."""

    async def test_enters_all_children(self) -> None:
        """Test that __aenter__ enters all children."""
        child1 = MockCapability(name="alpha")
        child2 = MockCapability(name="beta")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        assert not child1.entered
        assert not child2.entered

        async with combined:
            assert child1.entered
            assert child2.entered

    async def test_exits_all_children(self) -> None:
        """Test that __aexit__ exits all children."""
        child1 = MockCapability(name="alpha")
        child2 = MockCapability(name="beta")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        async with combined:
            pass

        assert child1.exited
        assert child2.exited

    async def test_exit_order_is_lifo(self) -> None:
        """Test that children are exited in reverse order (LIFO)."""
        exit_order: list[str] = []

        class TrackingCapability(MockCapability):
            async def __aexit__(
                self,
                exc_type: type[BaseException] | None,
                exc_val: BaseException | None,
                exc_tb: TracebackType | None,
            ) -> None:
                exit_order.append(self.name)
                self.exited = True

        child1 = TrackingCapability(name="first")
        child2 = TrackingCapability(name="second")
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        async with combined:
            pass

        assert exit_order == ["second", "first"]

    async def test_empty_capability_lifecycle(self) -> None:
        """Test that empty combined capability enters and exits without error."""
        combined = CombinedToolsetCapability(capabilities=[], name="empty")

        async with combined:
            pass

    async def test_static_children_lifecycle(self) -> None:
        """Test lifecycle with children that have no-op __aenter__/__aexit__."""
        child = FunctionToolsetCapability(name="static")
        combined = CombinedToolsetCapability(capabilities=[child])

        # Should not raise even though FunctionToolsetCapability has no-ops
        async with combined:
            pass


# =============================================================================
# Test Class: ToolsetAggregation
# =============================================================================


@pytest.mark.integration
class TestToolsetAggregation:
    """Test get_toolset() aggregation behavior."""

    async def test_empty_toolset_returns_none(self) -> None:
        """Test that combined capability with no tools returns None for toolset."""
        child = MockCapability(name="empty", tools=[])
        combined = CombinedToolsetCapability(capabilities=[child])

        assert combined.get_toolset() is None

    async def test_single_capability_toolset(self, mock_tool_local: MagicMock) -> None:
        """Test that tools from single child are collected via get_tools()."""
        child = MockCapability(name="local", tools=[mock_tool_local])
        combined = CombinedToolsetCapability(capabilities=[child])

        tools = await combined.get_tools()
        assert len(tools) == 1

    async def test_multiple_capability_toolset(
        self,
        mock_tool_local: MagicMock,
        mock_tool_mcp: MagicMock,
    ) -> None:
        """Test that tools from multiple children are collected via get_tools()."""
        child1 = MockCapability(name="local", tools=[mock_tool_local])
        child2 = MockCapability(name="mcp", tools=[mock_tool_mcp])
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        tools = await combined.get_tools()
        assert len(tools) == 2

    async def test_get_tools_backward_compat(
        self,
        mock_tool_local: MagicMock,
        mock_tool_mcp: MagicMock,
    ) -> None:
        """Test that get_tools() backward compat collects from all children."""
        child1 = MockCapability(name="local", tools=[mock_tool_local])
        child2 = MockCapability(name="mcp", tools=[mock_tool_mcp])
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        tools = await combined.get_tools()

        assert len(tools) == 2

    async def test_get_tools_no_tool_collecting_children(self) -> None:
        """Test that get_tools() returns empty list when no child supports it."""
        child = FunctionToolsetCapability(name="static", tools=[])
        combined = CombinedToolsetCapability(capabilities=[child])

        tools = await combined.get_tools()
        assert tools == []


# =============================================================================
# Test Class: InstructionsAggregation
# =============================================================================


@pytest.mark.integration
class TestInstructionsAggregation:
    """Test get_instructions() aggregation behavior."""

    async def test_no_instructions_returns_none(self) -> None:
        """Test that None instructions from all children returns None."""
        child1 = MockCapability(name="alpha", instructions=None)
        child2 = MockCapability(name="beta", instructions=None)
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        assert combined.get_instructions() is None

    async def test_partial_instructions_concatenated(self) -> None:
        """Test that only non-None instructions are concatenated."""
        child1 = MockCapability(name="alpha", instructions="First")
        child2 = MockCapability(name="beta", instructions=None)
        child3 = MockCapability(name="gamma", instructions="Third")
        combined = CombinedToolsetCapability(capabilities=[child1, child2, child3])

        assert combined.get_instructions() == "First\n\nThird"

    async def test_single_instruction(self) -> None:
        """Test that a single non-None instruction is returned as-is."""
        child = MockCapability(name="alpha", instructions="Only one")
        combined = CombinedToolsetCapability(capabilities=[child])

        assert combined.get_instructions() == "Only one"


# =============================================================================
# Test Class: EndToEndIntegration
# =============================================================================


@pytest.mark.integration
class TestEndToEndIntegration:
    """Test end-to-end integration of CombinedToolsetCapability."""

    async def test_complete_integration(
        self,
        mock_tool_local: MagicMock,
        mock_tool_mcp: MagicMock,
    ) -> None:
        """Test complete integration: toolset + instructions + lifecycle + change events."""
        child1 = MockCapability(
            name="alpha",
            tools=[mock_tool_local],
            instructions="Alpha instructions",
        )
        child2 = MockCapability(
            name="beta",
            tools=[mock_tool_mcp],
            instructions="Beta instructions",
        )
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        # Verify capabilities stored
        assert len(combined.capabilities) == 2

        # Verify tool aggregation via get_tools()
        tools = await combined.get_tools()
        assert len(tools) == 2

        # Verify instructions aggregation
        instructions = combined.get_instructions()
        assert instructions == "Alpha instructions\n\nBeta instructions"

        # Verify lifecycle
        async with combined:
            assert child1.entered
            assert child2.entered
        assert child1.exited
        assert child2.exited

        # Verify change events
        change_stream = combined.on_change()
        assert change_stream is not None

        results: list[ChangeEvent] = []
        consumer_task = asyncio.create_task(_consume_stream(change_stream, results))

        await asyncio.sleep(0.01)
        await child1.emit_change("tools_changed")
        await asyncio.sleep(0.01)
        await child1.stop_changes()
        await child2.stop_changes()
        await consumer_task

        assert len(results) == 1
        assert results[0].capability_name == "alpha"

    async def test_skills_used_as_instructions(
        self,
        mock_skill_local: Skill,
        mock_skill_mcp: Skill,
    ) -> None:
        """Test that skill descriptions can be used as instructions."""
        child1 = MockCapability(
            name="local",
            instructions=f"Skill: {mock_skill_local.name}",
        )
        child2 = MockCapability(
            name="mcp",
            instructions=f"Skill: {mock_skill_mcp.name}",
        )
        combined = CombinedToolsetCapability(capabilities=[child1, child2])

        instructions = combined.get_instructions()
        assert "local-skill" in instructions
        assert "mcp-skill" in instructions


async def _consume_stream(
    stream: AsyncIterator[ChangeEvent],
    results: list[ChangeEvent],
) -> None:
    """Consume a change event stream into a results list."""
    results.extend([event async for event in stream])
