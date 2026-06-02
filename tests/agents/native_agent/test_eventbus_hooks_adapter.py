"""Tests for EventBusHooksAdapter."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest
from pydantic_ai import AgentRunResult
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition

from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.agents.events import RunStartedEvent, ToolCallCompleteEvent, ToolCallStartEvent
from agentpool.agents.native_agent.eventbus_hooks_adapter import EventBusHooksAdapter
from agentpool.orchestrator.core import EventBus


@pytest.fixture
def event_bus() -> EventBus:
    """Fresh EventBus instance."""
    return EventBus()


@pytest.fixture
def session_id() -> str:
    """Test session ID."""
    return "test-session-123"


@pytest.fixture
def mock_run_context(session_id: str) -> RunContext[Any]:
    """Create a mock RunContext with AgentContext deps."""
    node = MagicMock()
    node.name = "test-agent"

    agent_run_ctx = AgentRunContext(session_id=session_id)
    agent_ctx = AgentContext(node=node, run_ctx=agent_run_ctx)

    model = MagicMock()
    model.system = "test"
    model.model_name = "test-model"

    return RunContext(
        deps=agent_ctx,
        model=model,
        usage=MagicMock(),
    )


@pytest.fixture
def mock_run_context_no_session() -> RunContext[Any]:
    """Create a mock RunContext without a session ID."""
    node = MagicMock()
    node.name = "test-agent"

    agent_ctx = AgentContext(node=node, run_ctx=None)

    model = MagicMock()
    return RunContext(
        deps=agent_ctx,
        model=model,
        usage=MagicMock(),
    )


@pytest.fixture
def sample_tool_call() -> ToolCallPart:
    """Sample ToolCallPart for testing."""
    return ToolCallPart(
        tool_name="test_tool",
        args={"arg1": "value1"},
        tool_call_id="tc-123",
    )


@pytest.fixture
def sample_tool_def() -> ToolDefinition:
    """Sample ToolDefinition for testing."""
    return ToolDefinition(name="test_tool")


class TestEventBusHooksAdapter:
    """Test suite for EventBusHooksAdapter."""

    async def test_as_capability_returns_hooks(self, event_bus: EventBus) -> None:
        """as_capability() should return a Hooks instance."""
        original_hooks = Hooks()
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        assert isinstance(capability, Hooks)

    async def test_before_run_publishes_run_started_event(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
        session_id: str,
    ) -> None:
        """before_run should publish RunStartedEvent to EventBus."""
        original_hooks = Hooks()
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        queue = await event_bus.subscribe(session_id)
        await capability.before_run(mock_run_context)

        # Check that an event was published
        event = queue.get_nowait()
        assert isinstance(event, RunStartedEvent)
        assert event.session_id == session_id
        assert event.agent_name == "test-agent"
        assert event.event_kind == "run_started"

    async def test_after_run_delegates_to_original(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
    ) -> None:
        """after_run should delegate to the original hook and return result."""
        mock_result = MagicMock(spec=AgentRunResult)

        # Create original hooks with an after_run callback
        original_called = False

        async def original_after_run(ctx: RunContext[Any], *, result: AgentRunResult[Any]) -> AgentRunResult[Any]:
            nonlocal original_called
            original_called = True
            return result

        original_hooks = Hooks(after_run=original_after_run)
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        returned = await capability.after_run(mock_run_context, result=mock_result)

        assert original_called
        assert returned is mock_result

    async def test_before_tool_execute_publishes_tool_call_start_event(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
        session_id: str,
        sample_tool_call: ToolCallPart,
        sample_tool_def: ToolDefinition,
    ) -> None:
        """before_tool_execute should publish ToolCallStartEvent to EventBus."""
        original_hooks = Hooks()
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        queue = await event_bus.subscribe(session_id)
        args = {"arg1": "value1"}
        returned = await capability.before_tool_execute(
            mock_run_context,
            call=sample_tool_call,
            tool_def=sample_tool_def,
            args=args,
        )

        # Original hook should return args unchanged
        assert returned == args

        # Check that an event was published
        event = queue.get_nowait()
        assert isinstance(event, ToolCallStartEvent)
        assert event.tool_call_id == "tc-123"
        assert event.tool_name == "test_tool"
        assert event.title == "Executing: test_tool"
        assert event.raw_input == args
        assert event.event_kind == "tool_call_start"

    async def test_after_tool_execute_publishes_tool_call_complete_event(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
        session_id: str,
        sample_tool_call: ToolCallPart,
        sample_tool_def: ToolDefinition,
    ) -> None:
        """after_tool_execute should publish ToolCallCompleteEvent to EventBus."""
        original_hooks = Hooks()
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        queue = await event_bus.subscribe(session_id)
        args = {"arg1": "value1"}
        tool_result = {"status": "ok"}
        returned = await capability.after_tool_execute(
            mock_run_context,
            call=sample_tool_call,
            tool_def=sample_tool_def,
            args=args,
            result=tool_result,
        )

        # Original hook should return result unchanged
        assert returned == tool_result

        # Check that an event was published
        event = queue.get_nowait()
        assert isinstance(event, ToolCallCompleteEvent)
        assert event.tool_call_id == "tc-123"
        assert event.tool_name == "test_tool"
        assert event.tool_input == args
        assert event.tool_result == tool_result
        assert event.agent_name == "test-agent"
        assert event.event_kind == "tool_call_complete"

    async def test_missing_session_id_skips_publishing(
        self,
        event_bus: EventBus,
        mock_run_context_no_session: RunContext[Any],
        sample_tool_call: ToolCallPart,
        sample_tool_def: ToolDefinition,
    ) -> None:
        """When session_id is missing, publishing should be skipped gracefully."""
        original_hooks = Hooks()
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        # These should not raise even though there's no session_id
        await capability.before_run(mock_run_context_no_session)
        await capability.before_tool_execute(
            mock_run_context_no_session,
            call=sample_tool_call,
            tool_def=sample_tool_def,
            args={},
        )
        await capability.after_tool_execute(
            mock_run_context_no_session,
            call=sample_tool_call,
            tool_def=sample_tool_def,
            args={},
            result="result",
        )

        # No subscribers means no queues to check, but the fact that
        # we got here without exception is the test.

    async def test_other_hooks_delegate_transparently(
        self,
        event_bus: EventBus,
    ) -> None:
        """Hooks that are not explicitly wrapped should still delegate."""
        before_model_called = False

        async def original_before_model(ctx: RunContext[Any], request_context: Any) -> Any:
            nonlocal before_model_called
            before_model_called = True
            return request_context

        original_hooks = Hooks(before_model_request=original_before_model)
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        mock_ctx = MagicMock(spec=RunContext)
        mock_request = MagicMock()
        returned = await capability.before_model_request(mock_ctx, mock_request)

        assert before_model_called
        assert returned is mock_request

    async def test_original_hooks_still_fire_for_wrapped_hooks(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
    ) -> None:
        """Original wrapped hooks should still be called alongside EventBus publishing."""
        before_run_called = False

        async def original_before_run(ctx: RunContext[Any]) -> None:
            nonlocal before_run_called
            before_run_called = True

        original_hooks = Hooks(before_run=original_before_run)
        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        await capability.before_run(mock_run_context)

        assert before_run_called

    async def test_multiple_hooks_combined(
        self,
        event_bus: EventBus,
        mock_run_context: RunContext[Any],
        session_id: str,
    ) -> None:
        """Multiple original hooks should all fire through the adapter."""
        call_order: list[str] = []

        async def hook1(ctx: RunContext[Any]) -> None:
            call_order.append("hook1")

        async def hook2(ctx: RunContext[Any]) -> None:
            call_order.append("hook2")

        original_hooks = Hooks()
        original_hooks.on.before_run(hook1)
        original_hooks.on.before_run(hook2)

        adapter = EventBusHooksAdapter(original_hooks, event_bus)
        capability = adapter.as_capability()

        queue = await event_bus.subscribe(session_id)
        await capability.before_run(mock_run_context)

        assert call_order == ["hook1", "hook2"]
        # Event should also be published
        event = queue.get_nowait()
        assert isinstance(event, RunStartedEvent)
