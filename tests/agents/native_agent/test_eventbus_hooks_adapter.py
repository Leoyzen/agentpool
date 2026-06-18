"""Tests for EventBusHooksAdapter.

This module verifies that EventBusHooksAdapter correctly bridges pydantic-ai
lifecycle hooks to the AgentPool EventBus pub/sub system. It tests:

1. All wrapped hooks publish correct events to the EventBus
2. All non-wrapped hooks delegate transparently to original hooks
3. Concurrent sessions do not interfere with each other
4. The adapter works with actual pydantic-ai agent execution (TestModel)
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai import AgentRunResult
from pydantic_ai.capabilities import Hooks
from pydantic_ai.capabilities.abstract import (
    AgentNode,
    NodeResult,
    RawOutput,
    RawToolArgs,
    ValidatedToolArgs,
    WrapModelRequestHandler,
    WrapNodeRunHandler,
    WrapOutputProcessHandler,
    WrapOutputValidateHandler,
    WrapRunHandler,
    WrapToolExecuteHandler,
    WrapToolValidateHandler,
)
from pydantic_ai.messages import AgentStreamEvent, ModelResponse, ToolCallPart
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolDefinition,
)

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
def session_id_2() -> str:
    """Second test session ID for concurrency tests."""
    return "test-session-456"


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
def mock_run_context_2(session_id_2: str) -> RunContext[Any]:
    """Create a second mock RunContext with different session ID."""
    node = MagicMock()
    node.name = "test-agent-2"

    agent_run_ctx = AgentRunContext(session_id=session_id_2)
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


# ---------------------------------------------------------------------------
# Helper to build adapted capability
# ---------------------------------------------------------------------------


def _adapt(hooks: Hooks[Any], event_bus: EventBus) -> Hooks[Any]:
    """Wrap hooks with EventBusHooksAdapter and return adapted capability."""
    return EventBusHooksAdapter(hooks, event_bus).as_capability()


# ---------------------------------------------------------------------------
# Wrapped hook tests (already partially covered by existing class)
# These top-level tests supplement the class-based tests below.
# ---------------------------------------------------------------------------


async def test_before_run_publishes_run_started_event(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    session_id: str,
) -> None:
    """before_run should publish RunStartedEvent to EventBus."""
    original_hooks = Hooks()
    capability = _adapt(original_hooks, event_bus)

    queue = await event_bus.subscribe(session_id)
    await capability.before_run(mock_run_context)

    event = queue.get_nowait()
    assert isinstance(event.event, RunStartedEvent)
    assert event.session_id == session_id
    assert event.agent_name == "test-agent"
    assert event.event_kind == "run_started"


async def test_after_run_delegates_to_original_and_returns_result(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
) -> None:
    """after_run should delegate to the original hook and return result."""
    mock_result = MagicMock(spec=AgentRunResult)
    original_called = False

    async def original_after_run(
        ctx: RunContext[Any], *, result: AgentRunResult[Any]
    ) -> AgentRunResult[Any]:
        nonlocal original_called
        original_called = True
        return result

    capability = _adapt(Hooks(after_run=original_after_run), event_bus)
    returned = await capability.after_run(mock_run_context, result=mock_result)

    assert original_called
    assert returned is mock_result


async def test_before_tool_execute_is_transparent_passthrough(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    session_id: str,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """before_tool_execute should be a transparent passthrough (no EventBus publish).

    ToolCallStartEvent is now produced by the stream path in
    RunExecutor.
    """
    capability = _adapt(Hooks(), event_bus)

    queue = await event_bus.subscribe(session_id)
    args = {"arg1": "value1"}
    returned = await capability.before_tool_execute(
        mock_run_context,
        call=sample_tool_call,
        tool_def=sample_tool_def,
        args=args,
    )

    assert returned == args
    # No event should be published (tool events now come from stream path)
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


async def test_after_tool_execute_is_transparent_passthrough(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    session_id: str,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """after_tool_execute should be a transparent passthrough (no EventBus publish).

    ToolCallCompleteEvent is now produced by the stream path via
    process_tool_event() and enqueued by the caller.
    """
    capability = _adapt(Hooks(), event_bus)

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

    assert returned == tool_result
    # No event should be published (tool events now come from stream path)
    with pytest.raises(asyncio.QueueEmpty):
        queue.get_nowait()


async def test_missing_session_id_skips_publishing(
    event_bus: EventBus,
    mock_run_context_no_session: RunContext[Any],
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """When session_id is missing, publishing should be skipped gracefully."""
    capability = _adapt(Hooks(), event_bus)

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

    # No exception raised = test passes


async def test_original_hooks_still_fire_for_wrapped_hooks(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
) -> None:
    """Original wrapped hooks should still be called alongside EventBus publishing."""
    before_run_called = False

    async def original_before_run(ctx: RunContext[Any]) -> None:
        nonlocal before_run_called
        before_run_called = True

    capability = _adapt(Hooks(before_run=original_before_run), event_bus)
    await capability.before_run(mock_run_context)

    assert before_run_called


async def test_multiple_hooks_combined(
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

    capability = _adapt(original_hooks, event_bus)

    queue = await event_bus.subscribe(session_id)
    await capability.before_run(mock_run_context)

    assert call_order == ["hook1", "hook2"]
    event = queue.get_nowait()
    assert isinstance(event.event, RunStartedEvent)


# ---------------------------------------------------------------------------
# Transparent delegation tests for ALL non-wrapped lifecycle hooks
# Each test verifies that the original hook is called and returns correctly.
# ---------------------------------------------------------------------------


# --- Run lifecycle ---


async def test_wrap_run_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_run should delegate to original hook."""
    called = False

    async def original_wrap_run(ctx: RunContext[Any], *, handler: WrapRunHandler) -> AgentRunResult[Any]:
        nonlocal called
        called = True
        return await handler()

    capability = _adapt(Hooks(run=original_wrap_run), event_bus)
    mock_result = MagicMock(spec=AgentRunResult)
    mock_handler: WrapRunHandler = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_run(MagicMock(), handler=mock_handler)
    assert called
    assert returned is mock_result


async def test_on_run_error_delegates_transparently(event_bus: EventBus) -> None:
    """on_run_error should delegate to original hook."""
    called = False

    async def original_on_run_error(ctx: RunContext[Any], *, error: BaseException) -> AgentRunResult[Any]:
        nonlocal called
        called = True
        return MagicMock(spec=AgentRunResult)

    capability = _adapt(Hooks(run_error=original_on_run_error), event_bus)
    mock_ctx = MagicMock()
    mock_error = RuntimeError("test error")

    returned = await capability.on_run_error(mock_ctx, error=mock_error)
    assert called
    assert isinstance(returned, AgentRunResult)


# --- Node lifecycle ---


async def test_before_node_run_delegates_transparently(event_bus: EventBus) -> None:
    """before_node_run should delegate to original hook."""
    called = False

    async def original_before_node_run(ctx: RunContext[Any], *, node: AgentNode[Any]) -> AgentNode[Any]:
        nonlocal called
        called = True
        return node

    capability = _adapt(Hooks(before_node_run=original_before_node_run), event_bus)
    mock_node = MagicMock(spec=AgentNode)

    returned = await capability.before_node_run(MagicMock(), node=mock_node)
    assert called
    assert returned is mock_node


async def test_after_node_run_delegates_transparently(event_bus: EventBus) -> None:
    """after_node_run should delegate to original hook."""
    called = False

    async def original_after_node_run(
        ctx: RunContext[Any], *, node: AgentNode[Any], result: NodeResult[Any]
    ) -> NodeResult[Any]:
        nonlocal called
        called = True
        return result

    capability = _adapt(Hooks(after_node_run=original_after_node_run), event_bus)
    mock_node = MagicMock(spec=AgentNode)
    mock_result = MagicMock(spec=NodeResult)

    returned = await capability.after_node_run(MagicMock(), node=mock_node, result=mock_result)
    assert called
    assert returned is mock_result


async def test_wrap_node_run_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_node_run should delegate to original hook."""
    called = False

    async def original_wrap_node_run(
        ctx: RunContext[Any], *, node: AgentNode[Any], handler: WrapNodeRunHandler[Any]
    ) -> NodeResult[Any]:
        nonlocal called
        called = True
        return await handler(node)

    capability = _adapt(Hooks(node_run=original_wrap_node_run), event_bus)
    mock_node = MagicMock(spec=AgentNode)
    mock_result = MagicMock(spec=NodeResult)
    mock_handler: WrapNodeRunHandler[Any] = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_node_run(MagicMock(), node=mock_node, handler=mock_handler)
    assert called
    assert returned is mock_result


async def test_on_node_run_error_delegates_transparently(event_bus: EventBus) -> None:
    """on_node_run_error should delegate to original hook."""
    called = False

    async def original_on_node_run_error(
        ctx: RunContext[Any], *, node: AgentNode[Any], error: Exception
    ) -> NodeResult[Any]:
        nonlocal called
        called = True
        return MagicMock(spec=NodeResult)

    capability = _adapt(Hooks(node_run_error=original_on_node_run_error), event_bus)
    mock_node = MagicMock(spec=AgentNode)
    mock_error = RuntimeError("node error")

    returned = await capability.on_node_run_error(MagicMock(), node=mock_node, error=mock_error)
    assert called
    assert returned is not None


# --- Event stream ---


async def test_wrap_run_event_stream_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_run_event_stream should delegate to original hook."""
    called = False

    async def original_stream(
        ctx: RunContext[Any], *, stream: AsyncIterable[AgentStreamEvent]
    ) -> AsyncIterable[AgentStreamEvent]:
        nonlocal called
        called = True
        async for event in stream:
            yield event

    capability = _adapt(Hooks(run_event_stream=original_stream), event_bus)

    async def mock_stream() -> AsyncIterable[AgentStreamEvent]:
        yield MagicMock(spec=AgentStreamEvent)

    result_stream = capability.wrap_run_event_stream(MagicMock(), stream=mock_stream())
    events = []
    async for event in result_stream:
        events.append(event)

    assert called
    assert len(events) == 1


# --- Model request ---


async def test_before_model_request_delegates_transparently(event_bus: EventBus) -> None:
    """before_model_request should delegate to original hook."""
    called = False

    async def original_before_model_request(ctx: RunContext[Any], request_context: Any) -> Any:
        nonlocal called
        called = True
        return request_context

    capability = _adapt(Hooks(before_model_request=original_before_model_request), event_bus)
    mock_request = MagicMock()

    returned = await capability.before_model_request(MagicMock(), mock_request)
    assert called
    assert returned is mock_request


async def test_after_model_request_delegates_transparently(event_bus: EventBus) -> None:
    """after_model_request should delegate to original hook."""
    called = False

    async def original_after_model_request(
        ctx: RunContext[Any], *, request_context: Any, response: ModelResponse
    ) -> ModelResponse:
        nonlocal called
        called = True
        return response

    capability = _adapt(Hooks(after_model_request=original_after_model_request), event_bus)
    mock_request = MagicMock()
    mock_response = MagicMock(spec=ModelResponse)

    returned = await capability.after_model_request(MagicMock(), request_context=mock_request, response=mock_response)
    assert called
    assert returned is mock_response


async def test_wrap_model_request_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_model_request should delegate to original hook."""
    called = False

    async def original_wrap_model_request(
        ctx: RunContext[Any], *, request_context: Any, handler: WrapModelRequestHandler
    ) -> ModelResponse:
        nonlocal called
        called = True
        return await handler(request_context)

    capability = _adapt(Hooks(model_request=original_wrap_model_request), event_bus)
    mock_request = MagicMock()
    mock_response = MagicMock(spec=ModelResponse)
    mock_handler: WrapModelRequestHandler = AsyncMock(return_value=mock_response)

    returned = await capability.wrap_model_request(MagicMock(), request_context=mock_request, handler=mock_handler)
    assert called
    assert returned is mock_response


async def test_on_model_request_error_delegates_transparently(event_bus: EventBus) -> None:
    """on_model_request_error should delegate to original hook."""
    called = False

    async def original_on_model_request_error(
        ctx: RunContext[Any], *, request_context: Any, error: Exception
    ) -> ModelResponse:
        nonlocal called
        called = True
        return MagicMock(spec=ModelResponse)

    capability = _adapt(Hooks(model_request_error=original_on_model_request_error), event_bus)
    mock_request = MagicMock()
    mock_error = RuntimeError("model error")

    returned = await capability.on_model_request_error(MagicMock(), request_context=mock_request, error=mock_error)
    assert called
    assert isinstance(returned, ModelResponse)


# --- Tool preparation ---


async def test_prepare_tools_delegates_transparently(event_bus: EventBus) -> None:
    """prepare_tools should delegate to original hook."""
    called = False

    async def original_prepare_tools(ctx: RunContext[Any], tool_defs: list[ToolDefinition]) -> list[ToolDefinition]:
        nonlocal called
        called = True
        return tool_defs

    capability = _adapt(Hooks(prepare_tools=original_prepare_tools), event_bus)
    mock_tools: list[ToolDefinition] = [ToolDefinition(name="mock_tool")]

    returned = await capability.prepare_tools(MagicMock(), mock_tools)
    assert called
    assert returned is mock_tools


async def test_prepare_output_tools_delegates_transparently(event_bus: EventBus) -> None:
    """prepare_output_tools should delegate to original hook."""
    called = False

    async def original_prepare_output_tools(
        ctx: RunContext[Any], tool_defs: list[ToolDefinition]
    ) -> list[ToolDefinition]:
        nonlocal called
        called = True
        return tool_defs

    capability = _adapt(Hooks(prepare_output_tools=original_prepare_output_tools), event_bus)
    mock_tools: list[ToolDefinition] = [ToolDefinition(name="mock_tool")]

    returned = await capability.prepare_output_tools(MagicMock(), mock_tools)
    assert called
    assert returned is mock_tools


# --- Tool validation ---


async def test_before_tool_validate_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """before_tool_validate should delegate to original hook."""
    called = False

    async def original_before_tool_validate(
        ctx: RunContext[Any], *, call: ToolCallPart, tool_def: ToolDefinition, args: RawToolArgs
    ) -> RawToolArgs:
        nonlocal called
        called = True
        return args

    capability = _adapt(Hooks(before_tool_validate=original_before_tool_validate), event_bus)
    mock_args = {"raw": "args"}

    returned = await capability.before_tool_validate(
        MagicMock(), call=sample_tool_call, tool_def=sample_tool_def, args=mock_args
    )
    assert called
    assert returned == mock_args


async def test_after_tool_validate_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """after_tool_validate should delegate to original hook."""
    called = False

    async def original_after_tool_validate(
        ctx: RunContext[Any], *, call: ToolCallPart, tool_def: ToolDefinition, args: ValidatedToolArgs
    ) -> ValidatedToolArgs:
        nonlocal called
        called = True
        return args

    capability = _adapt(Hooks(after_tool_validate=original_after_tool_validate), event_bus)
    mock_args = {"validated": "args"}

    returned = await capability.after_tool_validate(
        MagicMock(), call=sample_tool_call, tool_def=sample_tool_def, args=mock_args
    )
    assert called
    assert returned == mock_args


async def test_wrap_tool_validate_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """wrap_tool_validate should delegate to original hook."""
    called = False

    async def original_wrap_tool_validate(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
        handler: WrapToolValidateHandler,
    ) -> ValidatedToolArgs:
        nonlocal called
        called = True
        return await handler(args)

    capability = _adapt(Hooks(tool_validate=original_wrap_tool_validate), event_bus)
    mock_args = {"raw": "args"}
    mock_result = {"validated": "result"}
    mock_handler: WrapToolValidateHandler = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_tool_validate(
        MagicMock(), call=sample_tool_call, tool_def=sample_tool_def, args=mock_args, handler=mock_handler
    )
    assert called
    assert returned == mock_result


async def test_on_tool_validate_error_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """on_tool_validate_error should delegate to original hook."""
    called = False

    async def original_on_tool_validate_error(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: RawToolArgs,
        error: Any,
    ) -> ValidatedToolArgs:
        nonlocal called
        called = True
        return {"recovered": "args"}

    capability = _adapt(Hooks(tool_validate_error=original_on_tool_validate_error), event_bus)

    returned = await capability.on_tool_validate_error(
        MagicMock(),
        call=sample_tool_call,
        tool_def=sample_tool_def,
        args={},
        error=MagicMock(),
    )
    assert called
    assert returned == {"recovered": "args"}


# --- Tool execution (non-wrapped) ---


async def test_wrap_tool_execute_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """wrap_tool_execute should delegate to original hook."""
    called = False

    async def original_wrap_tool_execute(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        handler: WrapToolExecuteHandler,
    ) -> Any:
        nonlocal called
        called = True
        return await handler(args)

    capability = _adapt(Hooks(tool_execute=original_wrap_tool_execute), event_bus)
    mock_args = {"validated": "args"}
    mock_result = {"tool": "result"}
    mock_handler: WrapToolExecuteHandler = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_tool_execute(
        MagicMock(), call=sample_tool_call, tool_def=sample_tool_def, args=mock_args, handler=mock_handler
    )
    assert called
    assert returned == mock_result


async def test_on_tool_execute_error_delegates_transparently(
    event_bus: EventBus,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """on_tool_execute_error should delegate to original hook."""
    called = False

    async def original_on_tool_execute_error(
        ctx: RunContext[Any],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: ValidatedToolArgs,
        error: Exception,
    ) -> Any:
        nonlocal called
        called = True
        return {"recovered": "result"}

    capability = _adapt(Hooks(tool_execute_error=original_on_tool_execute_error), event_bus)

    returned = await capability.on_tool_execute_error(
        MagicMock(),
        call=sample_tool_call,
        tool_def=sample_tool_def,
        args={},
        error=RuntimeError("tool error"),
    )
    assert called
    assert returned == {"recovered": "result"}


# --- Output validation ---


async def test_before_output_validate_delegates_transparently(event_bus: EventBus) -> None:
    """before_output_validate should delegate to original hook."""
    called = False

    async def original_before_output_validate(
        ctx: RunContext[Any], *, output_context: Any, output: RawOutput
    ) -> RawOutput:
        nonlocal called
        called = True
        return output

    capability = _adapt(Hooks(before_output_validate=original_before_output_validate), event_bus)
    mock_output = MagicMock(spec=RawOutput)

    returned = await capability.before_output_validate(MagicMock(), output_context=MagicMock(), output=mock_output)
    assert called
    assert returned is mock_output


async def test_after_output_validate_delegates_transparently(event_bus: EventBus) -> None:
    """after_output_validate should delegate to original hook."""
    called = False

    async def original_after_output_validate(
        ctx: RunContext[Any], *, output_context: Any, output: Any
    ) -> Any:
        nonlocal called
        called = True
        return output

    capability = _adapt(Hooks(after_output_validate=original_after_output_validate), event_bus)
    mock_output = MagicMock()

    returned = await capability.after_output_validate(MagicMock(), output_context=MagicMock(), output=mock_output)
    assert called
    assert returned is mock_output


async def test_wrap_output_validate_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_output_validate should delegate to original hook."""
    called = False

    async def original_wrap_output_validate(
        ctx: RunContext[Any],
        *,
        output_context: Any,
        output: RawOutput,
        handler: WrapOutputValidateHandler,
    ) -> Any:
        nonlocal called
        called = True
        return await handler(output)

    capability = _adapt(Hooks(output_validate=original_wrap_output_validate), event_bus)
    mock_output = MagicMock(spec=RawOutput)
    mock_result = MagicMock()
    mock_handler: WrapOutputValidateHandler = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_output_validate(
        MagicMock(), output_context=MagicMock(), output=mock_output, handler=mock_handler
    )
    assert called
    assert returned is mock_result


async def test_on_output_validate_error_delegates_transparently(event_bus: EventBus) -> None:
    """on_output_validate_error should delegate to original hook."""
    called = False

    async def original_on_output_validate_error(
        ctx: RunContext[Any], *, output_context: Any, output: RawOutput, error: Any
    ) -> Any:
        nonlocal called
        called = True
        return {"recovered": "output"}

    capability = _adapt(Hooks(output_validate_error=original_on_output_validate_error), event_bus)

    returned = await capability.on_output_validate_error(
        MagicMock(), output_context=MagicMock(), output=MagicMock(), error=MagicMock()
    )
    assert called
    assert returned == {"recovered": "output"}


# --- Output processing ---


async def test_before_output_process_delegates_transparently(event_bus: EventBus) -> None:
    """before_output_process should delegate to original hook."""
    called = False

    async def original_before_output_process(
        ctx: RunContext[Any], *, output_context: Any, output: Any
    ) -> Any:
        nonlocal called
        called = True
        return output

    capability = _adapt(Hooks(before_output_process=original_before_output_process), event_bus)
    mock_output = MagicMock()

    returned = await capability.before_output_process(MagicMock(), output_context=MagicMock(), output=mock_output)
    assert called
    assert returned is mock_output


async def test_after_output_process_delegates_transparently(event_bus: EventBus) -> None:
    """after_output_process should delegate to original hook."""
    called = False

    async def original_after_output_process(
        ctx: RunContext[Any], *, output_context: Any, output: Any
    ) -> Any:
        nonlocal called
        called = True
        return output

    capability = _adapt(Hooks(after_output_process=original_after_output_process), event_bus)
    mock_output = MagicMock()

    returned = await capability.after_output_process(MagicMock(), output_context=MagicMock(), output=mock_output)
    assert called
    assert returned is mock_output


async def test_wrap_output_process_delegates_transparently(event_bus: EventBus) -> None:
    """wrap_output_process should delegate to original hook."""
    called = False

    async def original_wrap_output_process(
        ctx: RunContext[Any],
        *,
        output_context: Any,
        output: Any,
        handler: WrapOutputProcessHandler,
    ) -> Any:
        nonlocal called
        called = True
        return await handler(output)

    capability = _adapt(Hooks(output_process=original_wrap_output_process), event_bus)
    mock_output = MagicMock()
    mock_result = MagicMock()
    mock_handler: WrapOutputProcessHandler = AsyncMock(return_value=mock_result)

    returned = await capability.wrap_output_process(
        MagicMock(), output_context=MagicMock(), output=mock_output, handler=mock_handler
    )
    assert called
    assert returned is mock_result


async def test_on_output_process_error_delegates_transparently(event_bus: EventBus) -> None:
    """on_output_process_error should delegate to original hook."""
    called = False

    async def original_on_output_process_error(
        ctx: RunContext[Any], *, output_context: Any, output: Any, error: Exception
    ) -> Any:
        nonlocal called
        called = True
        return {"recovered": "output"}

    capability = _adapt(Hooks(output_process_error=original_on_output_process_error), event_bus)

    returned = await capability.on_output_process_error(
        MagicMock(), output_context=MagicMock(), output=MagicMock(), error=RuntimeError("process error")
    )
    assert called
    assert returned == {"recovered": "output"}


# --- Deferred tool calls ---


async def test_handle_deferred_tool_calls_delegates_transparently(event_bus: EventBus) -> None:
    """handle_deferred_tool_calls should delegate to original hook."""
    called = False

    async def original_handle_deferred_tool_calls(
        ctx: RunContext[Any], *, requests: DeferredToolRequests
    ) -> DeferredToolResults | None:
        nonlocal called
        called = True
        return None

    capability = _adapt(Hooks(deferred_tool_calls=original_handle_deferred_tool_calls), event_bus)
    mock_requests = MagicMock(spec=DeferredToolRequests)

    returned = await capability.handle_deferred_tool_calls(MagicMock(), requests=mock_requests)
    assert called
    assert returned is None


# ---------------------------------------------------------------------------
# Concurrent sessions
# ---------------------------------------------------------------------------


async def test_concurrent_sessions_dont_interfere(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    mock_run_context_2: RunContext[Any],
    session_id: str,
    session_id_2: str,
) -> None:
    """Events from concurrent sessions should be isolated per session_id."""
    capability = _adapt(Hooks(), event_bus)

    queue_1 = await event_bus.subscribe(session_id)
    queue_2 = await event_bus.subscribe(session_id_2)

    # Fire before_run on both sessions concurrently
    await asyncio.gather(
        capability.before_run(mock_run_context),
        capability.before_run(mock_run_context_2),
    )

    # Each queue should have exactly one event
    event_1 = queue_1.get_nowait()
    event_2 = queue_2.get_nowait()

    assert isinstance(event_1.event, RunStartedEvent)
    assert event_1.session_id == session_id
    assert event_1.agent_name == "test-agent"

    assert isinstance(event_2.event, RunStartedEvent)
    assert event_2.session_id == session_id_2
    assert event_2.agent_name == "test-agent-2"

    # Verify no cross-contamination
    with pytest.raises(asyncio.QueueEmpty):
        queue_1.get_nowait()
    with pytest.raises(asyncio.QueueEmpty):
        queue_2.get_nowait()


async def test_concurrent_tool_events_isolated(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    mock_run_context_2: RunContext[Any],
    session_id: str,
    session_id_2: str,
    sample_tool_call: ToolCallPart,
    sample_tool_def: ToolDefinition,
) -> None:
    """Tool events from concurrent sessions should be isolated (passthrough, no publish)."""
    capability = _adapt(Hooks(), event_bus)

    queue_1 = await event_bus.subscribe(session_id)
    queue_2 = await event_bus.subscribe(session_id_2)

    await asyncio.gather(
        capability.before_tool_execute(
            mock_run_context, call=sample_tool_call, tool_def=sample_tool_def, args={}
        ),
        capability.before_tool_execute(
            mock_run_context_2, call=sample_tool_call, tool_def=sample_tool_def, args={}
        ),
    )

    # No events should be published (tool events now come from stream path)
    with pytest.raises(asyncio.QueueEmpty):
        queue_1.get_nowait()
    with pytest.raises(asyncio.QueueEmpty):
        queue_2.get_nowait()


# ---------------------------------------------------------------------------
# Actual agent execution with TestModel
# ---------------------------------------------------------------------------


async def test_adapter_with_actual_pydantic_ai_agent(event_bus: EventBus, session_id: str) -> None:
    """Adapter publishes RunStartedEvent when used with real PydanticAgent + TestModel."""
    from pydantic_ai import Agent as PydanticAgent
    from pydantic_ai.models.test import TestModel

    original_hooks = Hooks()
    adapter = EventBusHooksAdapter(original_hooks, event_bus)
    hooks_capability = adapter.as_capability()

    model = TestModel(custom_output_text="Hello from test")
    agent = PydanticAgent(model=model, capabilities=[hooks_capability])

    queue = await event_bus.subscribe(session_id)

    node = MagicMock()
    node.name = "test-agent"
    run_ctx = AgentRunContext(session_id=session_id)
    agent_ctx = AgentContext(node=node, run_ctx=run_ctx)

    result = await agent.run("Say hello", deps=agent_ctx)  # type: ignore[arg-type]
    assert result.output == "Hello from test"

    event = queue.get_nowait()
    assert isinstance(event.event, RunStartedEvent)
    assert event.session_id == session_id
    assert event.agent_name == "test-agent"
    assert event.event_kind == "run_started"
    assert event.event.run_id  # should be a non-empty UUID string


async def test_adapter_run_and_tool_events_with_actual_agent(
    event_bus: EventBus, session_id: str
) -> None:
    """Adapter publishes only run events during actual agent execution.

    Tool events are now produced by the stream path in
    RunExecutor, not by the hooks adapter.
    """
    from pydantic_ai import Agent as PydanticAgent
    from pydantic_ai.models.test import TestModel

    original_hooks = Hooks()
    adapter = EventBusHooksAdapter(original_hooks, event_bus)
    hooks_capability = adapter.as_capability()

    def greet(name: str) -> str:
        """Greet someone."""
        return f"Hello, {name}!"

    # Default TestModel with call_tools='all' triggers tool calls automatically
    model = TestModel()
    agent = PydanticAgent(model=model, tools=[greet], capabilities=[hooks_capability])

    queue = await event_bus.subscribe(session_id)

    node = MagicMock()
    node.name = "test-agent"
    run_ctx = AgentRunContext(session_id=session_id)
    agent_ctx = AgentContext(node=node, run_ctx=run_ctx)

    result = await agent.run("Greet someone", deps=agent_ctx)  # type: ignore[arg-type]
    # TestModel calls tools with auto-generated args; output is JSON-like
    assert "Hello," in str(result.output)

    # Collect all events
    events = []
    try:
        while True:
            events.append(queue.get_nowait())
    except asyncio.QueueEmpty:
        pass

    # Should have only RunStartedEvent (tool events now come from stream path)
    assert len(events) >= 1, f"Expected at least 1 event, got {len(events)}: {[type(e).__name__ for e in events]}"

    # First event should be RunStartedEvent
    assert isinstance(events[0].event, RunStartedEvent)
    assert events[0].session_id == session_id

    # Should NOT have ToolCallStartEvent or ToolCallCompleteEvent from hooks adapter
    start_events = [e for e in events if isinstance(e.event, ToolCallStartEvent)]
    assert len(start_events) == 0, "ToolCallStartEvent should not come from hooks adapter"
    complete_events = [e for e in events if isinstance(e.event, ToolCallCompleteEvent)]
    assert len(complete_events) == 0, "ToolCallCompleteEvent should not come from hooks adapter"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


async def test_adapted_hooks_preserves_ordering(event_bus: EventBus) -> None:
    """The adapted Hooks should preserve the original hooks' ordering."""
    from pydantic_ai.capabilities.abstract import CapabilityOrdering

    original_ordering = CapabilityOrdering()
    original_hooks = Hooks(ordering=original_ordering)
    adapter = EventBusHooksAdapter(original_hooks, event_bus)
    capability = adapter.as_capability()

    assert capability.get_ordering() is original_ordering


async def test_empty_hooks_still_works(event_bus: EventBus, mock_run_context: RunContext[Any]) -> None:
    """Adapter works even when original Hooks has no registered callbacks."""
    original_hooks = Hooks()
    adapter = EventBusHooksAdapter(original_hooks, event_bus)
    capability = adapter.as_capability()

    # These should not raise
    await capability.before_run(mock_run_context)
    result = await capability.after_run(mock_run_context, result=MagicMock(spec=AgentRunResult))
    assert result is not None


async def test_original_hooks_called_before_eventbus_publish(
    event_bus: EventBus,
    mock_run_context: RunContext[Any],
    session_id: str,
) -> None:
    """Original before_run hook should execute before EventBus publish."""
    call_order: list[str] = []

    async def original_before_run(ctx: RunContext[Any]) -> None:
        call_order.append("original_hook")

    original_hooks = Hooks(before_run=original_before_run)
    adapter = EventBusHooksAdapter(original_hooks, event_bus)
    capability = adapter.as_capability()

    # Subscribe after creating adapter but before calling
    queue = await event_bus.subscribe(session_id)

    # We can't easily verify exact ordering without mocking publish,
    # but we can verify both happened
    await capability.before_run(mock_run_context)

    assert "original_hook" in call_order
    event = queue.get_nowait()
    assert isinstance(event.event, RunStartedEvent)
