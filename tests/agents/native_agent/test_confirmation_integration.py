"""Integration tests for tool confirmation with capability-based toolsets.

These tests verify the full agent run flow when multiple tools require
confirmation. They mock pydantic-ai internals (not real models) to test
that Agent.run() correctly routes deferred approval requests through the
InputProvider and handles mixed approval/denial scenarios.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import (
    DeferredToolRequests,
    DeferredToolResults,
    RunContext,
    ToolApproved,
    ToolDenied,
)
from pydantic_graph import End

from agentpool import Agent
from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.tools.base import Tool


@pytest.fixture
def mock_agent() -> Agent[Any]:
    """Create an agent with mocked internals for integration testing."""
    model = TestModel(custom_output_text="test")
    agent = Agent(name="integration-test-agent", model=model)
    return agent


@pytest.fixture
def confirmation_tool_1() -> Tool[Any]:
    """First tool requiring confirmation."""

    def dangerous_read(path: str) -> str:
        """Read a file path. Requires confirmation."""
        return f"Contents of {path}"

    return Tool.from_callable(dangerous_read, requires_confirmation=True)


@pytest.fixture
def confirmation_tool_2() -> Tool[Any]:
    """Second tool requiring confirmation."""

    def dangerous_write(path: str, content: str) -> str:
        """Write to a file path. Requires confirmation."""
        return f"Wrote to {path}"

    return Tool.from_callable(dangerous_write, requires_confirmation=True)


@pytest.fixture
def sample_deferred_requests() -> DeferredToolRequests:
    """Create sample DeferredToolRequests with multiple approval requests."""
    return DeferredToolRequests(
        approvals=[
            ToolCallPart(
                tool_name="dangerous_read",
                args={"path": "/etc/passwd"},
                tool_call_id="tc-read-001",
            ),
            ToolCallPart(
                tool_name="dangerous_write",
                args={"path": "/etc/hosts", "content": "test"},
                tool_call_id="tc-write-002",
            ),
        ]
    )


def _create_mock_agentlet_from_caps(
    capabilities: list[Any],
    deferred_requests: DeferredToolRequests,
    final_text: str = "Done",
) -> MagicMock:
    """Create a mock pydantic-ai agentlet that simulates deferred approval flow.

    The mock agentlet will:
    1. Find the HandleDeferredToolCalls capability
    2. Call it with the deferred requests
    3. Yield an End node with a mock result
    """
    from pydantic_ai.capabilities import HandleDeferredToolCalls

    # Find HandleDeferredToolCalls capability
    deferred_cap = None
    for cap in capabilities:
        if isinstance(cap, HandleDeferredToolCalls):
            deferred_cap = cap
            break

    # Create mock result with proper usage info
    mock_result = MagicMock()
    mock_result.data = final_text
    mock_result.all_messages.return_value = []
    mock_result.response.provider_details.get.return_value = None
    mock_usage = MagicMock()
    mock_usage.input_tokens = 10
    mock_usage.output_tokens = 5
    mock_usage.total_tokens = 15
    mock_result.usage = mock_usage

    cap_instance = deferred_cap

    def mock_iter(
        prompts: list[Any],
        *,
        deps: Any = None,
        message_history: list[Any] | None = None,
        usage_limits: Any = None,
    ) -> Any:
        """Mock iter that invokes deferred tool handler and yields End."""

        class MockAgentRun:
            def __init__(self) -> None:
                self.result = mock_result
                self.ctx = RunContext(
                    deps=deps,
                    model=MagicMock(),
                    usage=MagicMock(),
                )

            def __aiter__(self) -> Any:
                return self

            async def __anext__(self) -> Any:
                # Invoke the deferred tool capability
                if cap_instance is not None:
                    run_ctx = RunContext(
                        deps=deps,
                        model=MagicMock(),
                        usage=MagicMock(),
                    )
                    await cap_instance.handle_deferred_tool_calls(
                        run_ctx, requests=deferred_requests
                    )
                # End iteration
                raise StopAsyncIteration

            async def __aenter__(self) -> Any:
                return self

            async def __aexit__(self, *args: Any) -> None:
                pass

            def all_messages(self) -> list[Any]:
                return []

        return MockAgentRun()

    mock_agentlet = MagicMock()
    mock_agentlet.iter = mock_iter
    return mock_agentlet


# ---------------------------------------------------------------------------
# Test: Multiple confirmation-required tools in same run
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_multiple_confirmation_tools_same_run(
    mock_agent: Agent[Any],
    confirmation_tool_1: Tool[Any],
    confirmation_tool_2: Tool[Any],
    sample_deferred_requests: DeferredToolRequests,
) -> None:
    """Test multiple confirmation-required tools all get approval prompts."""
    # Setup mock InputProvider that approves all
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(return_value="allow")
    mock_agent._input_provider = mock_provider

    # Add confirmation tools to agent
    mock_agent.tools.register_tool(confirmation_tool_1)
    mock_agent.tools.register_tool(confirmation_tool_2)

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        def side_effect(**kwargs: Any) -> MagicMock:
            capabilities = kwargs.get("capabilities", []) or []
            return _create_mock_agentlet_from_caps(
                capabilities, sample_deferred_requests
            )

        mock_pydantic_agent.side_effect = side_effect

        # Run the agent
        result = await mock_agent.run("Test prompt")

    # Verify InputProvider.get_tool_confirmation was called twice (once per tool)
    assert mock_provider.get_tool_confirmation.call_count == 2

    # Verify both tools got approval prompts with correct details
    call_args_list = mock_provider.get_tool_confirmation.call_args_list

    # First call should be for dangerous_read
    first_ctx = call_args_list[0][0][0]
    assert isinstance(first_ctx, AgentContext)
    assert first_ctx.tool_name == "dangerous_read"
    assert first_ctx.tool_input == {"path": "/etc/passwd"}

    # Second call should be for dangerous_write
    second_ctx = call_args_list[1][0][0]
    assert isinstance(second_ctx, AgentContext)
    assert second_ctx.tool_name == "dangerous_write"
    assert second_ctx.tool_input == {"path": "/etc/hosts", "content": "test"}


# ---------------------------------------------------------------------------
# Test: Mixed approval/denial in same run
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_mixed_approval_denial_same_run(
    mock_agent: Agent[Any],
    confirmation_tool_1: Tool[Any],
    confirmation_tool_2: Tool[Any],
    sample_deferred_requests: DeferredToolRequests,
) -> None:
    """Test approving some tools and denying others in same run."""
    # Setup mock InputProvider: approve first, deny second
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(
        side_effect=["allow", "skip"]
    )
    mock_agent._input_provider = mock_provider

    # Add confirmation tools to agent
    mock_agent.tools.register_tool(confirmation_tool_1)
    mock_agent.tools.register_tool(confirmation_tool_2)

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        def side_effect(**kwargs: Any) -> MagicMock:
            capabilities = kwargs.get("capabilities", []) or []
            return _create_mock_agentlet_from_caps(
                capabilities, sample_deferred_requests
            )

        mock_pydantic_agent.side_effect = side_effect

        # Run the agent
        result = await mock_agent.run("Test prompt")

    # Verify InputProvider.get_tool_confirmation was called twice
    assert mock_provider.get_tool_confirmation.call_count == 2

    # Verify first tool was approved
    first_ctx = mock_provider.get_tool_confirmation.call_args_list[0][0][0]
    assert first_ctx.tool_name == "dangerous_read"

    # Verify second tool was also presented for confirmation (even though denied)
    second_ctx = mock_provider.get_tool_confirmation.call_args_list[1][0][0]
    assert second_ctx.tool_name == "dangerous_write"


# ---------------------------------------------------------------------------
# Test: Never mode auto-approves all tools without InputProvider
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_never_mode_auto_approves_all_tools(
    mock_agent: Agent[Any],
    confirmation_tool_1: Tool[Any],
    confirmation_tool_2: Tool[Any],
    sample_deferred_requests: DeferredToolRequests,
) -> None:
    """tool_confirmation_mode='never' auto-approves without calling InputProvider."""
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(return_value="allow")
    mock_agent._input_provider = mock_provider
    mock_agent.tool_confirmation_mode = "never"

    mock_agent.tools.register_tool(confirmation_tool_1)
    mock_agent.tools.register_tool(confirmation_tool_2)

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        def side_effect(**kwargs: Any) -> MagicMock:
            capabilities = kwargs.get("capabilities", []) or []
            return _create_mock_agentlet_from_caps(
                capabilities, sample_deferred_requests
            )

        mock_pydantic_agent.side_effect = side_effect

        result = await mock_agent.run("Test prompt")

    # InputProvider should NOT be called in never mode
    mock_provider.get_tool_confirmation.assert_not_called()


# ---------------------------------------------------------------------------
# Test: abort_run stops all subsequent confirmations
# ---------------------------------------------------------------------------


@pytest.mark.unit
async def test_abort_run_stops_subsequent_confirmations(
    mock_agent: Agent[Any],
    confirmation_tool_1: Tool[Any],
    confirmation_tool_2: Tool[Any],
    sample_deferred_requests: DeferredToolRequests,
) -> None:
    """abort_run on first tool should still present it, then stop."""
    mock_provider = MagicMock()
    mock_provider.get_tool_confirmation = AsyncMock(side_effect=["abort_run"])
    mock_agent._input_provider = mock_provider

    mock_agent.tools.register_tool(confirmation_tool_1)
    mock_agent.tools.register_tool(confirmation_tool_2)

    with patch("agentpool.agents.native_agent.agent.PydanticAgent") as mock_pydantic_agent:
        def side_effect(**kwargs: Any) -> MagicMock:
            capabilities = kwargs.get("capabilities", []) or []
            return _create_mock_agentlet_from_caps(
                capabilities, sample_deferred_requests
            )

        mock_pydantic_agent.side_effect = side_effect

        result = await mock_agent.run("Test prompt")

    # InputProvider should be called at least once (for first tool)
    assert mock_provider.get_tool_confirmation.call_count >= 1

    # First call should be for dangerous_read
    first_ctx = mock_provider.get_tool_confirmation.call_args_list[0][0][0]
    assert first_ctx.tool_name == "dangerous_read"
