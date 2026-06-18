"""Tests for gating the manual follow-up loop in _run_turn_unlocked().

Native agents use PydanticAI's ``PendingMessageDrainCapability`` and must NOT
go through the manual ``flush_pending_to_queue()`` / ``while has_queued()`` loop.
Non-native agents still use the manual loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionController, TurnRunner

from .test_phase2_native_queue import _MockNonNativeAgent


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Mock native agent for testing
# ---------------------------------------------------------------------------


class _MockNativeAgent:
    """Minimal concrete native agent for routing tests."""

    AGENT_TYPE = "native"  # type: ignore[misc]
    name: str

    def __init__(self, name: str = "mock-native-agent") -> None:
        self.name = name

    @property
    def model_name(self) -> str | None:
        return "mock-model"

    async def set_model(self, model: str) -> None:
        pass

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: Any,
        session_id: str | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Minimal stream that yields a single StreamCompleteEvent."""
        yield RunStartedEvent(session_id=session_id or "default", run_id="run-1")
        yield StreamCompleteEvent(
            message=ChatMessage(content="mock native response", role="assistant", name=self.name)
        )

    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        pass

    async def get_available_models(self) -> list[Any] | None:
        return None

    async def get_modes(self) -> list[Any]:
        return []

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        pass

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[Any]:
        return []

    async def load_session(self, session_id: str) -> Any | None:
        return None


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
def turn_runner(controller: SessionController) -> TurnRunner:
    """Return a TurnRunner with auto-resume disabled for unit isolation."""
    return TurnRunner(session_controller=controller, enable_auto_resume=False)


# ---------------------------------------------------------------------------
# Test: Native agent skips manual follow-up loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_native_agent_skips_manual_follow_up_loop(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Native agent: flush_pending_to_queue() NOT called, manual loop NOT executed."""
    session_id = "native-manual-loop-test"
    await controller.get_or_create_session(session_id)

    agent = _MockNativeAgent(name="native-test-agent")
    controller._session_agents[session_id] = agent  # type: ignore[assignment]
    mock_pool.get_agent.return_value = agent  # type: ignore[attr-defined]

    with (
        patch.object(
            PromptInjectionManager,
            "flush_pending_to_queue",
            autospec=True,
        ) as mock_flush,
        patch.object(
            PromptInjectionManager,
            "has_queued",
            autospec=True,
            return_value=False,
        ) as mock_has_queued,
    ):
        await turn_runner._run_turn_unlocked(session_id, "hello")

    # Native agent: flush_pending_to_queue should NOT be called
    mock_flush.assert_not_called(), (
        f"Native agent should NOT call flush_pending_to_queue(), "
        f"but it was called {mock_flush.call_count} times"
    )

    # Native agent: has_queued should NOT be called (manual loop not executed)
    mock_has_queued.assert_not_called(), (
        f"Native agent should NOT call has_queued() (manual loop skipped), "
        f"but it was called {mock_has_queued.call_count} times"
    )


# ---------------------------------------------------------------------------
# Test: Non-native agent still executes manual follow-up loop
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_native_agent_executes_manual_follow_up_loop(
    controller: SessionController,
    turn_runner: TurnRunner,
    mock_pool: MagicMock,
) -> None:
    """Non-native agent: flush_pending_to_queue() called, manual loop executed."""
    session_id = "non-native-manual-loop-test"
    await controller.get_or_create_session(session_id)

    agent = _MockNonNativeAgent(name="non-native-test-agent")
    controller._session_agents[session_id] = agent  # type: ignore[assignment]
    mock_pool.get_agent.return_value = agent  # type: ignore[attr-defined]

    with (
        patch.object(
            PromptInjectionManager,
            "flush_pending_to_queue",
            autospec=True,
        ) as mock_flush,
        patch.object(
            PromptInjectionManager,
            "has_queued",
            autospec=True,
            return_value=False,
        ) as mock_has_queued,
    ):
        await turn_runner._run_turn_unlocked(session_id, "hello")

    # Non-native agent: flush_pending_to_queue should be called
    mock_flush.assert_called(), (
        f"Non-native agent should call flush_pending_to_queue(), "
        f"but it was NOT called"
    )

    # Non-native agent: has_queued should be called (manual loop check)
    mock_has_queued.assert_called(), (
        f"Non-native agent should call has_queued() for manual loop, "
        f"but it was NOT called"
    )
