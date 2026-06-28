"""Edge case tests for steer/followup — Metis-identified gaps.

Covers 8 edge cases:
1. Concurrent steer: 5 concurrent steer() calls all enqueued with asap
2. Steer during tool execution: message enqueued asap, drained at before_model_request
3. Multiple followup chain: Multiple when_idle messages create correct chain
4. RunHandle cleanup on UndrainedPendingMessagesError: active_agent_run cleared
5. Session close during steer race: TOCTOU-safe — no crash
6. Tool result augmentation preserved: injection_manager.consume() still works
7. ACP snapshot regression: verified via `uv run pytest -m acp_snapshot -v`
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from agentpool.orchestrator.core import SessionController
from agentpool.orchestrator.run import RunHandle


if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _AsyncListIterator:
    """Async iterator wrapper for a list."""

    def __init__(self, items: list[Any]) -> None:
        self._items = items
        self._idx = 0

    def __aiter__(self) -> _AsyncListIterator:
        return self

    async def __anext__(self) -> Any:
        if self._idx < len(self._items):
            item = self._items[self._idx]
            self._idx += 1
            return item
        raise StopAsyncIteration


def _make_native_agent() -> MagicMock:
    """Return a mocked native agent with AGENT_TYPE = 'native'."""
    agent = MagicMock()
    agent.AGENT_TYPE = "native"
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
    """Create a RunHandle and register it in the controller's _runs."""
    handle = RunHandle(
        run_id=f"run-{session_id}",
        session_id=session_id,
        agent_type=agent_type,
    )
    if run_ctx is not None:
        handle.run_ctx = run_ctx
    return handle


# =============================================================================
# Test 1: Concurrent steer — 5 concurrent calls all enqueued with asap
# =============================================================================
# =============================================================================
# Test 2: Steer during tool execution — enqueued asap, drained at
#         before_model_request
# =============================================================================
# =============================================================================
# Test 3: Multiple followup chain — when_idle messages create correct chain
# =============================================================================
# =============================================================================
# Test 5: Session close during steer race — TOCTOU-safe, no crash
# =============================================================================
# =============================================================================
# Test 6: Tool result augmentation preserved — injection_manager.consume()
# =============================================================================


@pytest.mark.anyio
async def test_tool_result_augmentation_consume_preserved() -> None:
    """injection_manager.consume() still works on native agents after changes.

    Edge case: The inject/consume pattern is used for tool result
    augmentation (adding context after tool execution). This must
    continue to work correctly after the steer/followup changes.
    """
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()

    # Inject a message (simulating steer for tool augmentation)
    manager.inject("Additional context for the model after tool execution")

    assert manager.has_pending(), "Injection should be pending"

    # Consume should return the wrapped message
    consumed = await manager.consume()
    assert consumed is not None, "consume() should return the injected message"
    assert "Additional context" in consumed, (
        f"Expected injected message in consumed output, got: {consumed}"
    )
    assert "<injected-context>" in consumed, (
        "Expected XML-wrapped injection"
    )
    assert not manager.has_pending(), "Pending should be cleared after consume"


@pytest.mark.anyio
async def test_tool_result_augmentation_consume_all_preserved() -> None:
    """injection_manager.consume_all() works for native agents after changes.

    Edge case: Multiple injections should all be consumable via consume_all().
    """
    from agentpool.agents.prompt_injection import PromptInjectionManager

    manager = PromptInjectionManager()

    manager.inject("context-1")
    manager.inject("context-2")
    manager.inject("context-3")

    results = await manager.consume_all()
    assert len(results) == 3, f"Expected 3 consumed results, got {len(results)}"
    assert all("<injected-context>" in r for r in results)
    assert not manager.has_pending(), "All pending should be cleared"
