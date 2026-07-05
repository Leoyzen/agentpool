"""Unit tests for the Durable Elicitation Bridge implementation.

Covers Tasks 9.1-9.7, 9.14-9.16, 9.18-9.19, 9.21-9.22 from the
durable-elicitation-bridge plan.

Refs: https://github.com/Leoyzen/agentpool/issues/107
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from mcp.types import ElicitRequestFormParams, ElicitResult
from pydantic import TypeAdapter
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import DeferredToolRequests, RunContext
import pytest

from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.agents.events.events import ElicitationDeferredEvent
from agentpool.agents.native_agent.elicitation_bridge import (
    ElicitationFutureRegistry,
    create_elicitation_bridge_capability,
)
from agentpool.agents.native_agent.elicitation_strategy import (
    CheckpointResolutionStrategy,
    ElicitationResolutionStrategy,
    ProtocolResolutionStrategy,
)
from agentpool.sessions.models import ElicitationResumePayload, PendingDeferredCall
from agentpool.tools import CallDeferred
from agentpool.ui.base import InputProvider


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def agent_ctx() -> AgentContext:
    """Create a minimal AgentContext for testing."""
    node = MagicMock()
    node.name = "test-agent"
    run_ctx = AgentRunContext(session_id="test-session")
    return AgentContext(node=node, run_ctx=run_ctx)


@pytest.fixture
def run_ctx(agent_ctx: AgentContext) -> RunContext[Any]:
    """Create a mock RunContext with AgentContext deps."""
    model = MagicMock()
    model.system = "test"
    model.model_name = "test-model"
    return RunContext(
        deps=agent_ctx,
        model=model,
        usage=MagicMock(),
    )


@pytest.fixture
def form_params() -> ElicitRequestFormParams:
    """Create form elicitation params for testing."""
    return ElicitRequestFormParams(
        message="Please enter your name",
        requestedSchema={"type": "object", "properties": {"name": {"type": "string"}}},
        mode="form",
    )


# ============================================================================
# Task 9.1: Serialization roundtrip
# ============================================================================


@pytest.mark.unit
def test_pending_deferred_call_serialization_roundtrip() -> None:
    """PendingDeferredCall with elicitation fields survives serialization roundtrip."""
    original = PendingDeferredCall(
        tool_call_id="tc-001",
        tool_name="mcp_tool",
        deferred_kind="elicitation",
        deferred_strategy="block",
        elicitation_message="Enter your API key",
        elicitation_schema={"type": "object", "properties": {"key": {"type": "string"}}},
        elicitation_mode="form",
        mcp_server_id="server-1",
    )
    adapter: TypeAdapter[list[PendingDeferredCall]] = TypeAdapter(list[PendingDeferredCall])
    serialized = adapter.dump_python([original])
    deserialized_list = adapter.validate_python(serialized)
    assert len(deserialized_list) == 1
    result = deserialized_list[0]
    assert result.tool_call_id == "tc-001"
    assert result.tool_name == "mcp_tool"
    assert result.deferred_kind == "elicitation"
    assert result.deferred_strategy == "block"
    assert result.elicitation_message == "Enter your API key"
    assert result.elicitation_schema == {
        "type": "object",
        "properties": {"key": {"type": "string"}},
    }
    assert result.elicitation_mode == "form"
    assert result.mcp_server_id == "server-1"


@pytest.mark.unit
def test_pending_deferred_call_serialization_none_fields() -> None:
    """PendingDeferredCall with None elicitation fields serializes correctly."""
    original = PendingDeferredCall(
        tool_call_id="tc-002",
        tool_name="bash",
        deferred_kind="external",
        deferred_strategy="block",
    )
    adapter: TypeAdapter[list[PendingDeferredCall]] = TypeAdapter(list[PendingDeferredCall])
    serialized = adapter.dump_python([original])
    deserialized_list = adapter.validate_python(serialized)
    assert len(deserialized_list) == 1
    result = deserialized_list[0]
    assert result.tool_call_id == "tc-002"
    assert result.elicitation_message is None
    assert result.elicitation_schema is None
    assert result.elicitation_mode is None
    assert result.mcp_server_id is None


# ============================================================================
# Task 9.2: handle_elicitation durable=True
# ============================================================================


@pytest.mark.unit
async def test_handle_elicitation_durable_true(
    agent_ctx: AgentContext, form_params: ElicitRequestFormParams
) -> None:
    """handle_elicitation raises CallDeferred directly when durable."""
    provider = MagicMock(spec=InputProvider)
    provider.supports_durable_elicitation = True
    agent_ctx.input_provider = provider
    with pytest.raises(CallDeferred) as exc_info:
        await agent_ctx.handle_elicitation(form_params)
    assert exc_info.value.metadata is not None
    assert exc_info.value.metadata["deferred_kind"] == "elicitation"
    elicitation = exc_info.value.metadata["elicitation"]
    assert elicitation["message"] == "Please enter your name"
    assert elicitation["requestedSchema"] == {
        "type": "object",
        "properties": {"name": {"type": "string"}},
    }
    assert elicitation["mode"] == "form"
    # Side-channel should NOT be set by handle_elicitation directly.
    # It is only set by the MCP elicitation callback wrapper.
    assert agent_ctx._pending_elicitation_deferral is None


# ============================================================================
# Task 9.3: handle_elicitation durable=False
# ============================================================================


@pytest.mark.unit
async def test_handle_elicitation_durable_false(
    agent_ctx: AgentContext, form_params: ElicitRequestFormParams
) -> None:
    """handle_elicitation calls get_elicitation when not durable."""
    expected_result = ElicitResult(action="accept", content={"name": "Alice"})
    provider = MagicMock(spec=InputProvider)
    provider.supports_durable_elicitation = False
    provider.get_elicitation = AsyncMock(return_value=expected_result)
    agent_ctx.input_provider = provider
    result = await agent_ctx.handle_elicitation(form_params)
    provider.get_elicitation.assert_awaited_once_with(form_params)
    assert result == expected_result
    assert agent_ctx._pending_elicitation_deferral is None


# ============================================================================
# Task 9.4: call_tool raises CallDeferred
# ============================================================================


@pytest.mark.unit
async def test_call_tool_raises_call_deferred(agent_ctx: AgentContext) -> None:
    """MCPClient.call_tool raises CallDeferred when side-channel is set."""
    from agentpool.mcp_server.client import MCPClient
    from agentpool_config.mcp_server import StdioMCPServerConfig

    agent_ctx._pending_elicitation_deferral = {
        "message": "Enter credentials",
        "requestedSchema": {"type": "object"},
        "mode": "form",
    }
    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config=config)
    mock_inner_client = MagicMock()
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.content = []
    mock_call_result.data = "test-data"
    mock_inner_client.call_tool = AsyncMock(return_value=mock_call_result)
    mock_inner_client.is_connected.return_value = True
    mock_inner_client.session = MagicMock()
    mock_inner_client.session.get_server_capabilities.return_value = None
    client._client = mock_inner_client

    with (
        patch(
            "agentpool.mcp_server.conversions.from_mcp_content",
            new=AsyncMock(return_value=[]),
        ),
        pytest.raises(CallDeferred) as exc_info,
    ):
        await client.call_tool("test_tool", MagicMock(), {"arg": "val"}, agent_ctx)
    assert exc_info.value.metadata is not None
    assert exc_info.value.metadata["deferred_kind"] == "elicitation"
    elicitation_params = exc_info.value.metadata["elicitation"]
    assert elicitation_params["message"] == "Enter credentials"
    assert agent_ctx._pending_elicitation_deferral is None


# ============================================================================
# Task 9.5: call_tool normal path (no deferral)
# ============================================================================


@pytest.mark.unit
async def test_call_tool_normal_path(agent_ctx: AgentContext) -> None:
    """MCPClient.call_tool returns normally when no deferral is pending."""
    from agentpool.mcp_server.client import MCPClient
    from agentpool_config.mcp_server import StdioMCPServerConfig

    assert agent_ctx._pending_elicitation_deferral is None
    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config=config)
    mock_inner_client = MagicMock()
    mock_call_result = MagicMock()
    mock_call_result.is_error = False
    mock_call_result.content = []
    mock_call_result.data = "normal-result"
    mock_inner_client.call_tool = AsyncMock(return_value=mock_call_result)
    mock_inner_client.is_connected.return_value = True
    mock_inner_client.session = MagicMock()
    mock_inner_client.session.get_server_capabilities.return_value = None
    client._client = mock_inner_client

    with patch(
        "agentpool.mcp_server.conversions.from_mcp_content",
        new=AsyncMock(return_value=[]),
    ):
        result = await client.call_tool("test_tool", MagicMock(), {"arg": "val"}, agent_ctx)
    assert result == "normal-result"


# ============================================================================
# Task 9.6: Bridge handles elicitation + passthrough
# ============================================================================


@pytest.mark.unit
async def test_bridge_handles_elicitation_and_passthrough(run_ctx: RunContext[Any]) -> None:
    """Elicitation bridge handles elicitation calls and passes through non-elicitation."""
    registry = ElicitationFutureRegistry()
    mock_checkpoint = MagicMock()
    mock_checkpoint.checkpoint = AsyncMock()
    cap = create_elicitation_bridge_capability(
        registry=registry,
        checkpoint_manager=mock_checkpoint,
        agent_config_hash="abc123",
    )
    elicitation_call = ToolCallPart(
        tool_name="mcp_tool",
        args={"query": "data"},
        tool_call_id="tc-elicit-1",
    )
    normal_call = ToolCallPart(
        tool_name="bash",
        args={"cmd": "ls"},
        tool_call_id="tc-normal-1",
    )
    requests = DeferredToolRequests(
        calls=[elicitation_call, normal_call],
        metadata={
            "tc-elicit-1": {
                "deferred_kind": "elicitation",
                "elicitation": {
                    "message": "Enter key",
                    "requestedSchema": {"type": "object"},
                    "mode": "form",
                },
            },
            "tc-normal-1": {
                "deferred_kind": "external",
            },
        },
    )
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    run_ctx.deps.run_ctx.event_bus = mock_bus

    with patch(
        "agentpool.agents.native_agent.elicitation_bridge._emit_elicitation_event",
        new_callable=AsyncMock,
    ) as mock_emit:
        result = await cap.handle_deferred_tool_calls(run_ctx, requests=requests)

    mock_checkpoint.checkpoint.assert_awaited_once()
    checkpoint_kwargs = mock_checkpoint.checkpoint.call_args.kwargs
    assert checkpoint_kwargs["session_id"] == "test-session"
    assert checkpoint_kwargs["agent_config_hash"] == "abc123"
    pending_calls = checkpoint_kwargs["pending_calls"]
    assert len(pending_calls) == 1
    assert pending_calls[0].deferred_kind == "elicitation"
    assert pending_calls[0].tool_call_id == "tc-elicit-1"
    mock_emit.assert_awaited_once()
    emit_event = mock_emit.call_args[0][1]
    assert isinstance(emit_event, ElicitationDeferredEvent)
    assert emit_event.deferred_handle == "tc-elicit-1"
    assert emit_event.message == "Enter key"
    assert "tc-elicit-1" in registry
    assert run_ctx.deps.run_ctx.checkpointed is True
    assert result is None


# ============================================================================
# Task 9.7: FutureRegistry lifecycle
# ============================================================================


@pytest.mark.unit
async def test_future_registry_lifecycle() -> None:
    """ElicitationFutureRegistry register → resolve → reject_all lifecycle."""
    registry = ElicitationFutureRegistry()
    future1 = registry.register("handle1")
    assert not future1.done()
    payload = ElicitationResumePayload(
        deferred_handle="handle1",
        action="accept",
        content={"value": "test"},
    )
    registry.resolve("handle1", payload)
    assert future1.done()
    assert future1.result() == payload
    assert "handle1" not in registry
    future2 = registry.register("handle2")
    assert not future2.done()
    test_exception = Exception("session closed")
    registry.reject_all(test_exception)
    assert future2.done()
    assert future2.exception() is test_exception
    assert "handle2" not in registry


# ============================================================================
# Task 9.14: supports_durable_elicitation dynamic property
# ============================================================================


@pytest.mark.unit
def test_acp_input_provider_supports_durable_elicitation() -> None:
    """ACPInputProvider.supports_durable_elicitation reflects session.checkpoint_enabled."""
    from agentpool_server.acp_server.input_provider import ACPInputProvider

    mock_session = MagicMock()
    mock_session.checkpoint_enabled = True
    provider = ACPInputProvider(session=mock_session)
    assert provider.supports_durable_elicitation is True
    mock_session.checkpoint_enabled = False
    assert provider.supports_durable_elicitation is False


@pytest.mark.unit
def test_opencode_input_provider_supports_durable_elicitation() -> None:
    """OpenCodeInputProvider.supports_durable_elicitation checks session state."""
    from agentpool_server.opencode_server.input_provider import OpenCodeInputProvider

    mock_state = MagicMock()
    mock_session = MagicMock()
    mock_session.checkpoint_enabled = True
    mock_controller = MagicMock()
    mock_controller.get_session.return_value = mock_session
    mock_state.session_controller = mock_controller
    provider = OpenCodeInputProvider(state=mock_state, session_id="sess-1")
    assert provider.supports_durable_elicitation is True
    mock_session.checkpoint_enabled = False
    assert provider.supports_durable_elicitation is False
    mock_controller.get_session.return_value = None
    assert provider.supports_durable_elicitation is False
    mock_state.session_controller = None
    assert provider.supports_durable_elicitation is False


# ============================================================================
# Task 9.15: Side-channel cleanup on error
# ============================================================================


@pytest.mark.unit
async def test_side_channel_cleanup_on_error(agent_ctx: AgentContext) -> None:
    """Per-call elicitation handler is cleaned up when call_tool raises an error.

    The finally block in call_tool() clears _current_elicitation_handler
    regardless of success or failure, ensuring no stale handler leaks
    into the next call.
    """
    from agentpool.mcp_server.client import MCPClient
    from agentpool_config.mcp_server import StdioMCPServerConfig

    agent_ctx._pending_elicitation_deferral = {
        "message": "Enter key",
        "requestedSchema": {"type": "object"},
        "mode": "form",
    }
    config = StdioMCPServerConfig(command="echo", args=["test"])
    client = MCPClient(config=config)
    mock_inner_client = MagicMock()
    mock_inner_client.call_tool = AsyncMock(side_effect=RuntimeError("connection lost"))
    mock_inner_client.is_connected.return_value = True
    mock_inner_client.session = MagicMock()
    mock_inner_client.session.get_server_capabilities.return_value = None
    client._client = mock_inner_client

    with pytest.raises(RuntimeError, match="MCP tool call failed"):
        await client.call_tool("test_tool", MagicMock(), {"arg": "val"}, agent_ctx)
    # The finally block must clear the per-call elicitation handler
    assert client._current_elicitation_handler is None


# ============================================================================
# Task 9.16: Bridge positioning in capability chain
# ============================================================================


@pytest.mark.unit
async def test_bridge_positioning_elicitation_before_approval(run_ctx: RunContext[Any]) -> None:
    """Elicitation bridge handles elicitation calls before approval bridge sees them."""
    registry = ElicitationFutureRegistry()
    cap = create_elicitation_bridge_capability(registry=registry)
    elicitation_call = ToolCallPart(
        tool_name="mcp_elicitation_tool",
        args={"prompt": "Enter credentials"},
        tool_call_id="tc-both-1",
    )
    requests = DeferredToolRequests(
        calls=[elicitation_call],
        metadata={
            "tc-both-1": {
                "deferred_kind": "elicitation",
                "elicitation": {
                    "message": "Enter credentials",
                    "requestedSchema": {"type": "object"},
                    "mode": "form",
                },
            },
        },
    )
    mock_bus = MagicMock()
    mock_bus.publish = AsyncMock()
    run_ctx.deps.run_ctx.event_bus = mock_bus

    with patch(
        "agentpool.agents.native_agent.elicitation_bridge._emit_elicitation_event",
        new_callable=AsyncMock,
    ):
        result = await cap.handle_deferred_tool_calls(run_ctx, requests=requests)

    assert result is None
    assert "tc-both-1" in registry
    assert run_ctx.deps.run_ctx.checkpointed is True


# ============================================================================
# Task 9.18: Backward compatibility serialization
# ============================================================================


@pytest.mark.unit
def test_backward_compatibility_old_format_serialization() -> None:
    """Old-format PendingDeferredCall (without elicitation fields) deserializes correctly."""
    old_format: dict[str, Any] = {
        "tool_call_id": "tc-old-1",
        "tool_name": "external_tool",
        "deferred_kind": "external",
        "deferred_strategy": "block",
    }
    adapter: TypeAdapter[list[PendingDeferredCall]] = TypeAdapter(list[PendingDeferredCall])
    deserialized_list = adapter.validate_python([old_format])
    assert len(deserialized_list) == 1
    result = deserialized_list[0]
    assert result.tool_call_id == "tc-old-1"
    assert result.tool_name == "external_tool"
    assert result.deferred_kind == "external"
    assert result.deferred_strategy == "block"
    assert result.elicitation_message is None
    assert result.elicitation_schema is None
    assert result.elicitation_mode is None
    assert result.mcp_server_id is None


# ============================================================================
# Task 9.19: ElicitationResumePayload decline/cancel actions
# ============================================================================


@pytest.mark.unit
def test_elicitation_resume_payload_decline_and_cancel() -> None:
    """ElicitationResumePayload constructs correctly for decline and cancel actions."""
    decline_payload = ElicitationResumePayload(
        deferred_handle="tc-001",
        action="decline",
    )
    assert decline_payload.deferred_handle == "tc-001"
    assert decline_payload.action == "decline"
    assert decline_payload.content is None

    cancel_payload = ElicitationResumePayload(
        deferred_handle="tc-002",
        action="cancel",
    )
    assert cancel_payload.deferred_handle == "tc-002"
    assert cancel_payload.action == "cancel"
    assert cancel_payload.content is None

    accept_payload = ElicitationResumePayload(
        deferred_handle="tc-003",
        action="accept",
        content={"name": "Alice"},
    )
    assert accept_payload.action == "accept"
    assert accept_payload.content == {"name": "Alice"}


@pytest.mark.unit
async def test_handle_elicitation_cached_response(
    agent_ctx: AgentContext, form_params: ElicitRequestFormParams
) -> None:
    """handle_elicitation returns cached response when available (crash recovery)."""
    cached_result = ElicitResult(action="accept", content={"name": "Bob"})
    agent_ctx.tool_call_id = "tc-cached-1"
    assert agent_ctx.run_ctx is not None
    agent_ctx.run_ctx.cached_elicitation_responses["tc-cached-1"] = cached_result
    provider = MagicMock(spec=InputProvider)
    provider.supports_durable_elicitation = True
    provider.get_elicitation = AsyncMock()
    agent_ctx.input_provider = provider
    result = await agent_ctx.handle_elicitation(form_params)
    assert result == cached_result
    provider.get_elicitation.assert_not_awaited()
    assert agent_ctx._pending_elicitation_deferral is None


# ============================================================================
# Task 9.21: Strategy classes
# ============================================================================


@pytest.mark.unit
async def test_checkpoint_resolution_strategy_delegates() -> None:
    """CheckpointResolutionStrategy.resolve() calls CheckpointManager.checkpoint()."""
    mock_manager = MagicMock()
    mock_manager.checkpoint = AsyncMock()
    strategy = CheckpointResolutionStrategy(
        checkpoint_manager=mock_manager,
        session_id="sess-strategy",
        message_history=[],
        agent_config_hash="hash123",
    )
    pending_call = PendingDeferredCall(
        tool_call_id="tc-strategy-1",
        tool_name="mcp_tool",
        deferred_kind="elicitation",
        deferred_strategy="block",
    )
    response = ElicitationResumePayload(
        deferred_handle="tc-strategy-1",
        action="accept",
        content={"value": "yes"},
    )
    result = await strategy.resolve(pending_call, response)
    mock_manager.checkpoint.assert_awaited_once()
    checkpoint_kwargs = mock_manager.checkpoint.call_args.kwargs
    assert checkpoint_kwargs["session_id"] == "sess-strategy"
    assert checkpoint_kwargs["agent_config_hash"] == "hash123"
    assert checkpoint_kwargs["pending_calls"] == [pending_call]
    assert result == response


@pytest.mark.unit
async def test_protocol_resolution_strategy_raises() -> None:
    """ProtocolResolutionStrategy.resolve() raises NotImplementedError."""
    strategy = ProtocolResolutionStrategy()
    pending_call = PendingDeferredCall(
        tool_call_id="tc-proto-1",
        tool_name="mcp_tool",
        deferred_kind="elicitation",
        deferred_strategy="block",
    )
    response = ElicitationResumePayload(
        deferred_handle="tc-proto-1",
        action="decline",
    )
    with pytest.raises(NotImplementedError, match="MRTR support not yet available"):
        await strategy.resolve(pending_call, response)


@pytest.mark.unit
def test_elicitation_resolution_strategy_runtime_checkable() -> None:
    """ElicitationResolutionStrategy is runtime_checkable and works with isinstance()."""
    checkpoint_strategy = CheckpointResolutionStrategy(
        checkpoint_manager=MagicMock(),
        session_id="sess-1",
        message_history=[],
    )
    assert isinstance(checkpoint_strategy, ElicitationResolutionStrategy)
    protocol_strategy = ProtocolResolutionStrategy()
    assert isinstance(protocol_strategy, ElicitationResolutionStrategy)


# ============================================================================
# Task 9.22: Elicitation timeout
# ============================================================================


@pytest.mark.unit
def test_elicitation_timeout_serialization() -> None:
    """PendingDeferredCall with timeout field serializes and deserializes correctly."""
    original = PendingDeferredCall(
        tool_call_id="tc-timeout-1",
        tool_name="mcp_tool",
        deferred_kind="elicitation",
        deferred_strategy="block",
        timeout=timedelta(seconds=300),
        elicitation_message="Enter OTP",
        elicitation_schema={"type": "object"},
        elicitation_mode="form",
    )
    assert original.timeout == timedelta(seconds=300)
    adapter: TypeAdapter[list[PendingDeferredCall]] = TypeAdapter(list[PendingDeferredCall])
    serialized = adapter.dump_python([original])
    deserialized_list = adapter.validate_python(serialized)
    assert len(deserialized_list) == 1
    result = deserialized_list[0]
    assert result.timeout is not None
    assert result.timeout == timedelta(seconds=300)
    assert result.deferred_kind == "elicitation"
    assert result.elicitation_message == "Enter OTP"
