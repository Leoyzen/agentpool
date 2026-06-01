"""Unit tests for BaseAgent public APIs get_active_run_context() and is_turn_active()."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

import pytest
from pydantic_ai.models.test import TestModel

from agentpool.agents.base_agent import BaseAgent, _current_run_ctx_var
from agentpool.agents.context import AgentRunContext


# ---------------------------------------------------------------------------
# Minimal concrete subclass for isolated unit tests
# ---------------------------------------------------------------------------


class _TestAgent(BaseAgent):
    """Minimal concrete agent for testing BaseAgent APIs."""

    @property
    def model_name(self) -> str | None:
        return "test-model"

    async def set_model(self, model: str) -> None:
        pass

    async def _stream_events(
        self,
        run_ctx: AgentRunContext,
        prompts: list[Any],
        *,
        user_msg: Any,
        message_history: Any,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: Any | None = None,
        deps: Any | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[Any]:
        async for _ in []:
            yield

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
def agent() -> _TestAgent:
    """Create a minimal test agent instance."""
    return _TestAgent(name="test-agent")


# ---------------------------------------------------------------------------
# get_active_run_context — no active turn
# ---------------------------------------------------------------------------


def test_get_active_run_context_returns_none_when_idle(agent: _TestAgent) -> None:
    """When no turn has started, get_active_run_context() returns None."""
    assert agent.get_active_run_context() is None


# ---------------------------------------------------------------------------
# get_active_run_context — _active_run_ctx
# ---------------------------------------------------------------------------


def test_get_active_run_context_returns_active_run_ctx(agent: _TestAgent) -> None:
    """When _active_run_ctx is set, it is returned."""
    ctx = AgentRunContext()
    agent._active_run_ctx = ctx

    result = agent.get_active_run_context()

    assert result is ctx


# ---------------------------------------------------------------------------
# get_active_run_context — _background_run_ctx
# ---------------------------------------------------------------------------


def test_get_active_run_context_returns_background_run_ctx(agent: _TestAgent) -> None:
    """When only _background_run_ctx is set, it is returned."""
    ctx = AgentRunContext()
    agent._background_run_ctx = ctx

    result = agent.get_active_run_context()

    assert result is ctx


# ---------------------------------------------------------------------------
# get_active_run_context — _current_run_ctx (ContextVar)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_run_context_returns_current_run_ctx(agent: _TestAgent) -> None:
    """When _current_run_ctx (ContextVar) is set in the current task, it is returned."""
    ctx = AgentRunContext()
    token = _current_run_ctx_var.set(ctx)
    try:
        result = agent.get_active_run_context()
        assert result is ctx
    finally:
        _current_run_ctx_var.reset(token)


# ---------------------------------------------------------------------------
# get_active_run_context — precedence order
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_run_context_prefers_current_over_active(agent: _TestAgent) -> None:
    """_current_run_ctx takes precedence over _active_run_ctx."""
    current_ctx = AgentRunContext()
    active_ctx = AgentRunContext()
    agent._active_run_ctx = active_ctx
    token = _current_run_ctx_var.set(current_ctx)
    try:
        result = agent.get_active_run_context()
        assert result is current_ctx
    finally:
        _current_run_ctx_var.reset(token)


def test_get_active_run_context_prefers_active_over_background(agent: _TestAgent) -> None:
    """_active_run_ctx takes precedence over _background_run_ctx."""
    active_ctx = AgentRunContext()
    background_ctx = AgentRunContext()
    agent._active_run_ctx = active_ctx
    agent._background_run_ctx = background_ctx

    result = agent.get_active_run_context()

    assert result is active_ctx


@pytest.mark.asyncio
async def test_get_active_run_context_prefers_current_over_background(agent: _TestAgent) -> None:
    """_current_run_ctx takes precedence over _background_run_ctx."""
    current_ctx = AgentRunContext()
    background_ctx = AgentRunContext()
    agent._background_run_ctx = background_ctx
    token = _current_run_ctx_var.set(current_ctx)
    try:
        result = agent.get_active_run_context()
        assert result is current_ctx
    finally:
        _current_run_ctx_var.reset(token)


# ---------------------------------------------------------------------------
# is_turn_active — boolean correctness
# ---------------------------------------------------------------------------


def test_is_turn_active_false_when_idle(agent: _TestAgent) -> None:
    """is_turn_active() returns False when no turn is running."""
    assert agent.is_turn_active() is False


def test_is_turn_active_true_with_active_run_ctx(agent: _TestAgent) -> None:
    """is_turn_active() returns True when _active_run_ctx is set."""
    agent._active_run_ctx = AgentRunContext()
    assert agent.is_turn_active() is True


def test_is_turn_active_true_with_background_run_ctx(agent: _TestAgent) -> None:
    """is_turn_active() returns True when _background_run_ctx is set."""
    agent._background_run_ctx = AgentRunContext()
    assert agent.is_turn_active() is True


@pytest.mark.asyncio
async def test_is_turn_active_true_with_current_run_ctx(agent: _TestAgent) -> None:
    """is_turn_active() returns True when _current_run_ctx (ContextVar) is set."""
    ctx = AgentRunContext()
    token = _current_run_ctx_var.set(ctx)
    try:
        assert agent.is_turn_active() is True
    finally:
        _current_run_ctx_var.reset(token)


# ---------------------------------------------------------------------------
# is_turn_active — cleanup after turn ends
# ---------------------------------------------------------------------------


def test_is_turn_active_false_after_clearing_active_run_ctx(agent: _TestAgent) -> None:
    """After clearing _active_run_ctx, is_turn_active() returns False."""
    agent._active_run_ctx = AgentRunContext()
    assert agent.is_turn_active() is True

    agent._active_run_ctx = None
    assert agent.is_turn_active() is False


def test_is_turn_active_false_after_clearing_background_run_ctx(agent: _TestAgent) -> None:
    """After clearing _background_run_ctx, is_turn_active() returns False."""
    agent._background_run_ctx = AgentRunContext()
    assert agent.is_turn_active() is True

    agent._background_run_ctx = None
    assert agent.is_turn_active() is False


# ---------------------------------------------------------------------------
# Integration-style: during an actual agent run
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_is_turn_active_during_run_stream() -> None:
    """is_turn_active() returns True while inside a run_stream execution.

    We verify this by providing a tool that inspects the agent state mid-turn.
    """
    from agentpool import Agent
    from agentpool.agents.events import StreamCompleteEvent

    turn_active_values: list[bool] = []

    async def check_turn() -> str:
        """Tool that records is_turn_active() mid-stream."""
        from agentpool.agents.context import AgentContext

        # We need to get the agent from the tool context.
        # In pydantic-ai tools, we can use RunContext or AgentContext.
        # Since we're testing BaseAgent APIs, we use AgentContext.
        # But in practice, for a simple test, we can capture the agent via closure.
        # However, the native agent tools don't have direct access to the agent
        # instance unless we use AgentContext.
        # For simplicity, we'll use a closure over the agent reference.
        turn_active_values.append(agent.is_turn_active())
        return "ok"

    model = TestModel(custom_output_text="Test response")
    async with Agent(name="native-test", model=model, tools=[check_turn]) as agent:
        async for event in agent.run_stream("trigger tool"):
            if isinstance(event, StreamCompleteEvent):
                break

    # The tool ran during the stream, so at least one True value was recorded
    assert any(turn_active_values), "is_turn_active() should be True during run_stream"


@pytest.mark.asyncio
async def test_get_active_run_context_during_run_stream() -> None:
    """get_active_run_context() returns a non-None context while inside run_stream."""
    from agentpool import Agent
    from agentpool.agents.events import StreamCompleteEvent

    contexts: list[AgentRunContext | None] = []

    async def capture_ctx() -> str:
        """Tool that records get_active_run_context() mid-stream."""
        contexts.append(agent.get_active_run_context())
        return "ok"

    model = TestModel(custom_output_text="Test response")
    async with Agent(name="native-test", model=model, tools=[capture_ctx]) as agent:
        async for event in agent.run_stream("trigger tool"):
            if isinstance(event, StreamCompleteEvent):
                break

    # At least one context captured during the stream should be non-None
    assert any(ctx is not None for ctx in contexts), (
        "get_active_run_context() should return non-None during run_stream"
    )


# ---------------------------------------------------------------------------
# Edge case: concurrent tasks see different ContextVar values
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_active_run_context_isolation_between_tasks(agent: _TestAgent) -> None:
    """Two concurrent tasks setting different _current_run_ctx values are isolated."""
    ctx_a = AgentRunContext()
    ctx_b = AgentRunContext()

    async def task_a() -> AgentRunContext | None:
        token = _current_run_ctx_var.set(ctx_a)
        try:
            await asyncio.sleep(0.01)
            return agent.get_active_run_context()
        finally:
            _current_run_ctx_var.reset(token)

    async def task_b() -> AgentRunContext | None:
        token = _current_run_ctx_var.set(ctx_b)
        try:
            await asyncio.sleep(0.01)
            return agent.get_active_run_context()
        finally:
            _current_run_ctx_var.reset(token)

    result_a, result_b = await asyncio.gather(task_a(), task_b())

    assert result_a is ctx_a
    assert result_b is ctx_b
