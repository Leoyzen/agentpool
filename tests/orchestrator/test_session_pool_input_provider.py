"""Tests for SessionPool input_provider propagation.

Verifies that input_provider is correctly forwarded through SessionPool
run_stream -> process_prompt -> _run_turn -> get_or_create_session_agent
so that elicitation does NOT fall back to StdlibInputProvider.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunStartedEvent, StreamCompleteEvent
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.core import SessionPool
from agentpool.ui.base import InputProvider


pytestmark = pytest.mark.unit


class FakeInputProvider(InputProvider):
    """A fake input provider for testing propagation."""

    async def get_tool_confirmation(
        self, context: Any, tool_description: str = ""
    ) -> Any:
        return "allow"

    async def get_elicitation(
        self, params: Any
    ) -> Any:
        return {"action": "accept", "content": {}}


@pytest.fixture
def mock_pool() -> MagicMock:
    """Return a mocked AgentPool with SessionPool enabled."""
    pool = MagicMock()
    pool.main_agent = MagicMock()
    pool.main_agent.name = "main-agent"
    pool.manifest = MagicMock()
    pool.manifest.agents = {}
    pool.manifest.opencode = MagicMock()
    pool.manifest.opencode.use_session_pool = True
    return pool


@pytest.fixture
def session_pool(mock_pool: MagicMock) -> SessionPool:
    """Return a SessionPool backed by the mock pool."""
    return SessionPool(pool=mock_pool)


@pytest.fixture
def mock_agent() -> MagicMock:
    """Return a mocked BaseAgent that captures kwargs passed to _run_stream_once."""
    agent = MagicMock()
    agent.get_active_run_context.return_value = None
    agent.AGENT_TYPE = "native"

    async def _fake_stream(
        run_ctx: AgentRunContext,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Yield a RunStartedEvent then a StreamCompleteEvent."""
        session_id = kwargs.get("session_id", "default")
        yield RunStartedEvent(session_id=session_id, run_id="run-1")
        msg = ChatMessage(content="test response", role="assistant")
        yield StreamCompleteEvent(message=msg)

    agent._run_stream_once = _fake_stream
    return agent


class TestSessionPoolRunStreamInputProvider:
    """RED FLAG: input_provider must be forwarded through SessionPool.run_stream()."""
    @pytest.mark.anyio
    async def test_run_stream_without_input_provider_does_not_crash(
        self,
        session_pool: SessionPool,
        mock_pool: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """run_stream() without input_provider should still work (backward compat)."""
        session_id = "test-session"
        mock_pool.get_agent.return_value = mock_agent

        # Count events to ensure stream completes
        event_count = 0
        async for _event in session_pool.run_stream(session_id, "hello"):
            event_count += 1

        assert event_count > 0, "Stream should yield events even without input_provider"

    @pytest.mark.anyio
    async def test_process_prompt_forwards_kwargs_to_run_turn(
        self,
        session_pool: SessionPool,
        mock_pool: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """process_prompt() must forward **kwargs to the turn runner.

        This tests the middle layer: process_prompt -> run_turn/_run_turn.
        """
        fake_provider = FakeInputProvider()
        session_id = "test-session"
        mock_pool.get_agent.return_value = mock_agent

        captured_kwargs: dict[str, Any] | None = None
        original_run_loop = session_pool.turns.run_loop

        async def _capturing_run_loop(
            sid: str,
            *prompts: Any,
            **kwargs: Any,
        ) -> None:
            nonlocal captured_kwargs
            captured_kwargs = kwargs
            await original_run_loop(sid, *prompts, **kwargs)

        session_pool.turns.run_loop = _capturing_run_loop  # type: ignore[method-assign]

        await session_pool.process_prompt(
            session_id, "hello", input_provider=fake_provider
        )

        assert captured_kwargs is not None, "run_loop was never called"
        assert "input_provider" in captured_kwargs, (
            f"input_provider missing from run_loop kwargs: {captured_kwargs.keys()}"
        )
        assert captured_kwargs["input_provider"] is fake_provider, (
            f"Expected FakeInputProvider in run_loop, got {captured_kwargs['input_provider']}"
        )

        assert captured_kwargs is not None, "run_loop was never called"
        assert "input_provider" in captured_kwargs, (
            f"input_provider missing from run_loop kwargs: {captured_kwargs.keys()}"
        )
        assert captured_kwargs["input_provider"] is fake_provider, (
            f"Expected FakeInputProvider in run_loop, got {captured_kwargs['input_provider']}"
        )

        assert captured_kwargs is not None, "run_turn was never called"
        assert "input_provider" in captured_kwargs, (
            f"input_provider missing from run_turn kwargs: {captured_kwargs.keys()}"
        )
        assert captured_kwargs["input_provider"] is fake_provider, (
            f"Expected FakeInputProvider in run_turn, got {captured_kwargs['input_provider']}"
        )


class TestRunTurnInputProvider:
    """Tests for _run_turn forwarding input_provider to agent creation and stream."""

    @pytest.mark.anyio
    async def test_run_turn_passes_input_provider_to_get_or_create_session_agent(
        self,
        session_pool: SessionPool,
        mock_pool: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """_run_turn must pass input_provider to get_or_create_session_agent."""
        fake_provider = FakeInputProvider()
        session_id = "test-session"
        mock_pool.get_agent.return_value = mock_agent

        captured_agent_kwargs: dict[str, Any] | None = None
        original_get_agent = session_pool.sessions.get_or_create_session_agent

        async def _capturing_get_agent(
            sid: str,
            agent_name: str | None = None,
            input_provider: Any | None = None,
        ) -> MagicMock:
            nonlocal captured_agent_kwargs
            captured_agent_kwargs = {"input_provider": input_provider}
            return mock_agent

        session_pool.sessions.get_or_create_session_agent = _capturing_get_agent

        # Directly call _run_turn (internal, but we test it)
        from agentpool.orchestrator.core import SessionState

        # Ensure session exists
        session_pool.sessions._sessions[session_id] = SessionState(
            session_id=session_id,
            agent_name="main-agent",
        )

        await session_pool.turns._run_turn_unlocked(
            session_id, "hello", input_provider=fake_provider
        )

        assert captured_agent_kwargs is not None, (
            "get_or_create_session_agent was never called"
        )
        assert captured_agent_kwargs["input_provider"] is fake_provider, (
            f"Expected FakeInputProvider, got {captured_agent_kwargs['input_provider']}"
        )

    @pytest.mark.anyio
    async def test_run_turn_passes_input_provider_to_run_stream_once(
        self,
        session_pool: SessionPool,
        mock_pool: MagicMock,
        mock_agent: MagicMock,
    ) -> None:
        """_run_turn must pass input_provider to agent._run_stream_once."""
        fake_provider = FakeInputProvider()
        session_id = "test-session"
        mock_pool.get_agent.return_value = mock_agent

        captured_stream_kwargs: dict[str, Any] | None = None
        original_stream = mock_agent._run_stream_once

        async def _capturing_stream(
            run_ctx: AgentRunContext,
            *prompts: Any,
            **kwargs: Any,
        ) -> AsyncIterator[Any]:
            nonlocal captured_stream_kwargs
            captured_stream_kwargs = kwargs
            async for event in original_stream(run_ctx, *prompts, **kwargs):
                yield event

        mock_agent._run_stream_once = _capturing_stream

        from agentpool.orchestrator.core import SessionState

        session_pool.sessions._sessions[session_id] = SessionState(
            session_id=session_id,
            agent_name="main-agent",
        )

        await session_pool.turns._run_turn_unlocked(
            session_id, "hello", input_provider=fake_provider
        )

        assert captured_stream_kwargs is not None, (
            "_run_stream_once was never called"
        )
        assert "input_provider" in captured_stream_kwargs, (
            f"input_provider missing from _run_stream_once kwargs: {captured_stream_kwargs.keys()}"
        )
        assert captured_stream_kwargs["input_provider"] is fake_provider, (
            f"Expected FakeInputProvider in _run_stream_once, got {captured_stream_kwargs['input_provider']}"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
