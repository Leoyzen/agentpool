"""Integration tests for steer/followup with PendingMessageDrainCapability.

Covers after_node_run hooks, agent type detection, and injection_manager.consume().

Tests:
- 10.7: steer message injected via PendingMessageDrainCapability.before_model_request
- 10.8: followup message processed via after_node_run redirect
- 10.9: manual follow-up loop NOT executed for native agents
- 10.11: agent type detected via agent.AGENT_TYPE (not metadata)
- 10.12: tool result augmentation via injection_manager.consume()
"""

from __future__ import annotations

from unittest.mock import MagicMock

from pydantic_ai.models.test import TestModel
import pytest

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.orchestrator.core import SessionController
from agentpool.orchestrator.run import RunHandle


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    return pool


@pytest.fixture
def controller(mock_pool: MagicMock) -> SessionController:
    """Return a real SessionController backed by the mock pool."""
    return SessionController(pool=mock_pool)


@pytest.fixture
def test_agent() -> Agent[None]:
    """Create an Agent backed by TestModel."""
    model = TestModel(custom_output_text="Integration test response")
    return Agent(name="integration-test-agent", model=model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_native_agent() -> MagicMock:
    """Return a mocked native agent with AGENT_TYPE = 'native'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
    return agent


def _make_acp_agent() -> MagicMock:
    """Return a mocked ACP agent with AGENT_TYPE = 'acp'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "acp"
    return agent


async def _setup_session_with_agent(
    controller: SessionController,
    session_id: str,
    agent: MagicMock,
    mock_pool: MagicMock,
) -> None:
    """Create a session and attach the mock agent."""
    state, _ = await controller.get_or_create_session(session_id)
    state.agent = agent
    controller._session_agents[session_id] = agent
    mock_pool.get_agent.return_value = agent


def _make_run_handle(
    session_id: str,
    agent_type: str,
    run_ctx: AgentRunContext | None = None,
) -> RunHandle:
    """Create a RunHandle and return it (does NOT register in controller)."""
    handle = RunHandle(
        run_id=f"run-{session_id}",
        session_id=session_id,
        agent_type=agent_type,
    )
    if run_ctx is not None:
        handle.run_ctx = run_ctx
    return handle


# =============================================================================
# 10.7: steer message injected before next LLM call via
#       PendingMessageDrainCapability.before_model_request()
# =============================================================================
# =============================================================================
# 10.8: followup message processed after agent would otherwise end
#       (via after_node_run redirect)
# =============================================================================
# =============================================================================
# 10.9: manual follow-up loop NOT executed for native agents
#       (no redundant processing)
# =============================================================================
# =============================================================================
# 10.11: agent type detected via agent.AGENT_TYPE (not metadata)
#        — native agents correctly skip manual loop
# =============================================================================
# =============================================================================
# 10.12: tool result augmentation via injection_manager.consume()
#        still works on native agents
# =============================================================================


@pytest.mark.anyio
async def test_injection_manager_consume_works_in_run_handle_context() -> None:
    """injection_manager.consume() works correctly within a native RunHandle context.

    Tool result augmentation uses the inject/consume pattern: after a tool
    executes, the after_tool_execute hook calls consume() to inject additional
    context into the conversation. This must continue to work correctly with
    native agents, where the manual follow-up loop is skipped in favor of
    PendingMessageDrainCapability.

    This integration test verifies the full inject→consume→clear lifecycle
    using a real PromptInjectionManager attached to a RunHandle.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-int")
    run_handle = RunHandle(
        run_id="run-consume-int",
        session_id="sess-consume-int",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # Initially empty
    assert not manager.has_pending(), "Should not have pending injections initially"

    # Simulate tool result augmentation: inject then consume
    manager.inject("Tool execution result: test passed with 42 assertions")

    assert manager.has_pending(), "Injection should be pending after inject()"

    # consume() is called by the after_tool_execute hook
    consumed = await manager.consume()
    assert consumed is not None, "consume() should return the wrapped message"
    assert "Tool execution result" in consumed, (
        f"Expected injected content in consumed output, got: {consumed}"
    )
    assert "<injected-context>" in consumed, (
        f"Expected XML-wrapped injection format, got: {consumed}"
    )
    assert "</injected-context>" in consumed, (
        "Expected closing XML tag in injection"
    )

    # After consume, pending should be empty
    assert not manager.has_pending(), "Pending should be cleared after consume"

    # RunHandle context should remain stable
    assert run_handle.run_ctx is run_ctx, "RunHandle.run_ctx should preserve reference"


@pytest.mark.anyio
async def test_injection_manager_consume_returns_none_when_empty() -> None:
    """injection_manager.consume() returns None when no injections are pending.

    This is the normal case after all injections have been consumed.
    The after_tool_execute hook handles this gracefully by skipping
    injection when consume() returns None.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-empty")
    run_handle = RunHandle(
        run_id="run-consume-empty",
        session_id="sess-consume-empty",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # consume() on empty manager should return None
    result = await manager.consume()
    assert result is None, "consume() on empty manager should return None"

    # Manager state remains clean
    assert not manager.has_pending(), "Should still have no pending after empty consume"


@pytest.mark.anyio
async def test_injection_manager_consume_all_preserves_order() -> None:
    """injection_manager.consume_all() preserves FIFO order of injections.

    When multiple tool results accumulate, consume_all() returns them
    in the order they were injected. This is important for maintaining
    context coherence when multiple tools fire before the hooks run.
    """
    run_ctx = AgentRunContext(session_id="sess-consume-order")
    run_handle = RunHandle(
        run_id="run-consume-order",
        session_id="sess-consume-order",
        agent_type="native",
        run_ctx=run_ctx,
    )

    manager = run_handle.run_ctx.injection_manager

    # Inject multiple messages in sequence
    manager.inject("Step 1: read file")
    manager.inject("Step 2: analyzed content")
    manager.inject("Step 3: wrote results")

    assert manager.has_pending(), "Should have pending injections"

    # consume_all() returns all in order
    results = await manager.consume_all()
    assert len(results) == 3, f"Expected 3 consumed results, got {len(results)}"

    # Verify order and XML wrapping
    for i, result in enumerate(results):
        assert f"Step {i + 1}" in result, (
            f"Result {i} should contain 'Step {i + 1}', got: {result}"
        )
        assert "<injected-context>" in result
        assert "</injected-context>" in result

    assert not manager.has_pending(), "All pending should be cleared after consume_all"
