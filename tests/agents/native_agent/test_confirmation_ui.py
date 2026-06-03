"""Tests for confirmation UI integration with capability-based toolsets.

These tests verify that AgentContext.handle_confirmation() correctly bridges
to InputProvider.get_tool_confirmation(), and that Tool.requires_confirmation
is properly propagated to pydantic-ai's requires_approval flag for capability-
based toolsets.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentContext
from agentpool.tools.base import Tool


@pytest.fixture
def mock_agent() -> Agent[Any]:
    """Create an agent with heavily mocked internals for confirmation testing."""
    model = TestModel(custom_output_text="test")
    agent = Agent(name="confirmation-test-agent", model=model)
    return agent


@pytest.fixture
def mock_input_provider() -> MagicMock:
    """Create a mock InputProvider that returns 'allow' by default."""
    provider = MagicMock()
    provider.get_tool_confirmation = AsyncMock(return_value="allow")
    return provider


@pytest.fixture
def confirmation_tool() -> Tool[Any]:
    """Create a tool that requires confirmation."""

    def tool_with_confirm(text: str) -> str:
        """Tool requiring confirmation."""
        return f"Confirmed tool got: {text}"

    return Tool.from_callable(tool_with_confirm, requires_confirmation=True)


@pytest.fixture
def no_confirmation_tool() -> Tool[Any]:
    """Create a tool that does not require confirmation."""

    def tool_without_confirm(text: str) -> str:
        """Tool not requiring confirmation."""
        return f"Regular tool got: {text}"

    return Tool.from_callable(tool_without_confirm, requires_confirmation=False)


# ---------------------------------------------------------------------------
# Test: Approval flow through InputProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_ui_approval(
    mock_agent: Agent[Any],
    mock_input_provider: MagicMock,
    confirmation_tool: Tool[Any],
) -> None:
    """Test approval flow through InputProvider with capability-based tools."""
    # Setup mock InputProvider that approves
    mock_agent._input_provider = mock_input_provider

    # Create AgentContext with the mock provider
    ctx = mock_agent.get_context(input_provider=mock_input_provider)

    # Call handle_confirmation with a tool requiring confirmation
    result = await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    # Verify InputProvider.get_tool_confirmation was called
    mock_input_provider.get_tool_confirmation.assert_called_once()
    call_args = mock_input_provider.get_tool_confirmation.call_args
    assert call_args[0][0] is ctx  # First arg is the AgentContext
    assert call_args[0][1] == confirmation_tool.description  # Second arg is description

    # Verify approval result is returned
    assert result == "allow"


# ---------------------------------------------------------------------------
# Test: Denial flow through InputProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_ui_denial(
    mock_agent: Agent[Any],
    confirmation_tool: Tool[Any],
) -> None:
    """Test denial flow through InputProvider."""
    # Setup mock InputProvider that denies (returns 'skip')
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(return_value="skip")
    mock_agent._input_provider = mock_provider

    # Create AgentContext with the mock provider
    ctx = mock_agent.get_context(input_provider=mock_provider)

    # Call handle_confirmation
    result = await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    # Verify InputProvider was called
    mock_provider.get_tool_confirmation.assert_called_once()

    # Verify skip result is returned (tool should not execute)
    assert result == "skip"


# ---------------------------------------------------------------------------
# Test: Timeout during confirmation
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_ui_timeout(
    mock_agent: Agent[Any],
    confirmation_tool: Tool[Any],
) -> None:
    """Test timeout during confirmation."""
    # Setup mock InputProvider that raises TimeoutError
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(
        side_effect=TimeoutError("Confirmation timed out")
    )
    mock_agent._input_provider = mock_provider

    # Create AgentContext with the mock provider
    ctx = mock_agent.get_context(input_provider=mock_provider)

    # Verify that timeout error propagates appropriately
    with pytest.raises(TimeoutError, match="Confirmation timed out"):
        await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    # Verify InputProvider was called
    mock_provider.get_tool_confirmation.assert_called_once()


# ---------------------------------------------------------------------------
# Test: requires_confirmation propagated to pydantic-ai requires_approval
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_requires_confirmation_propagated_to_pydantic_ai(
    confirmation_tool: Tool[Any],
) -> None:
    """Tool.requires_confirmation is propagated to pydantic-ai Tool.requires_approval."""
    pa_tool = confirmation_tool.to_pydantic_ai()
    assert pa_tool.requires_approval is True


@pytest.mark.unit
def test_no_confirmation_not_propagated_to_pydantic_ai(
    no_confirmation_tool: Tool[Any],
) -> None:
    """Tool without requires_confirmation does not set requires_approval."""
    pa_tool = no_confirmation_tool.to_pydantic_ai()
    assert pa_tool.requires_approval is False


# ---------------------------------------------------------------------------
# Test: tool_confirmation_mode='never' bypasses InputProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_never_mode_bypasses_provider(
    mock_agent: Agent[Any],
    mock_input_provider: MagicMock,
    confirmation_tool: Tool[Any],
) -> None:
    """tool_confirmation_mode='never' bypasses InputProvider entirely."""
    mock_agent.tool_confirmation_mode = "never"
    ctx = mock_agent.get_context(input_provider=mock_input_provider)

    result = await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    # InputProvider should NOT be called
    mock_input_provider.get_tool_confirmation.assert_not_called()
    assert result == "allow"


# ---------------------------------------------------------------------------
# Test: per_tool mode with non-confirmation tool bypasses InputProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_per_tool_mode_no_confirmation_bypasses(
    mock_agent: Agent[Any],
    mock_input_provider: MagicMock,
    no_confirmation_tool: Tool[Any],
) -> None:
    """per_tool mode with non-confirmation tool bypasses InputProvider."""
    mock_agent.tool_confirmation_mode = "per_tool"
    ctx = mock_agent.get_context(input_provider=mock_input_provider)

    result = await ctx.handle_confirmation(no_confirmation_tool, {"text": "hello"})

    # InputProvider should NOT be called
    mock_input_provider.get_tool_confirmation.assert_not_called()
    assert result == "allow"


# ---------------------------------------------------------------------------
# Test: AgentContext fields populated for confirmation call
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_context_populated(
    mock_agent: Agent[Any],
    mock_input_provider: MagicMock,
    confirmation_tool: Tool[Any],
) -> None:
    """AgentContext passed to InputProvider has tool execution fields set."""
    mock_agent._input_provider = mock_input_provider
    ctx = mock_agent.get_context(
        input_provider=mock_input_provider,
        tool_name=confirmation_tool.name,
        tool_input={"text": "hello"},
        tool_call_id="call-123",
    )

    await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    # Verify the context passed to get_tool_confirmation has tool info
    passed_ctx = mock_input_provider.get_tool_confirmation.call_args[0][0]
    assert isinstance(passed_ctx, AgentContext)
    assert passed_ctx.tool_name == confirmation_tool.name
    assert passed_ctx.tool_input == {"text": "hello"}
    assert passed_ctx.tool_call_id == "call-123"


# ---------------------------------------------------------------------------
# Test: abort_run confirmation result
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_ui_abort_run(
    mock_agent: Agent[Any],
    confirmation_tool: Tool[Any],
) -> None:
    """Test abort_run confirmation result from InputProvider."""
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(return_value="abort_run")
    mock_agent._input_provider = mock_provider

    ctx = mock_agent.get_context(input_provider=mock_provider)
    result = await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    assert result == "abort_run"


# ---------------------------------------------------------------------------
# Test: abort_chain confirmation result
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_confirmation_ui_abort_chain(
    mock_agent: Agent[Any],
    confirmation_tool: Tool[Any],
) -> None:
    """Test abort_chain confirmation result from InputProvider."""
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(return_value="abort_chain")
    mock_agent._input_provider = mock_provider

    ctx = mock_agent.get_context(input_provider=mock_provider)
    result = await ctx.handle_confirmation(confirmation_tool, {"text": "hello"})

    assert result == "abort_chain"
