"""Unit tests for NativeTurn.

Tests the pydantic-ai iter/next/stream cycle wrapper, including:
- Normal execution with TestModel
- Terminal tool detection and early stop
- RunAbortedError graceful handling
- asyncio.CancelledError re-raising
- message_history and final_message property lifecycle
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic_ai.models.test import TestModel

from agentpool import Agent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.events.events import RunErrorEvent, ToolCallCompleteEvent
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.tasks.exceptions import RunAbortedError


if TYPE_CHECKING:
    from agentpool.tools.base import Tool


def _make_mock_agentlet_raising(exc: BaseException) -> MagicMock:
    """Create a mock agentlet whose iter() async CM raises *exc* in __aenter__."""
    mock_agentlet = MagicMock()
    mock_run = AsyncMock()
    mock_run.__aenter__ = AsyncMock(side_effect=exc)
    mock_run.__aexit__ = AsyncMock(return_value=None)
    mock_agentlet.iter = MagicMock(return_value=mock_run)
    return mock_agentlet


# ---------------------------------------------------------------------------
# Normal cycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_normal_cycle_yields_events_and_sets_properties() -> None:
    """Normal iter/next/stream cycle yields events and sets message_history/final_message."""
    agent = Agent(
        name="test-normal",
        model=TestModel(custom_output_text="Hello world"),
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["Hello"],
            run_ctx=run_ctx,
            message_history=[],
        )
        events: list[Any] = []
        async for event in turn.execute():
            events.append(event)

        # Should have yielded some events (pydantic-ai stream events pass through EventMapper)
        assert len(events) > 0, "Expected at least one event from normal cycle"

        # Properties should be available after execute() completes
        assert len(turn.message_history) > 0
        assert turn.final_message is not None
        assert "Hello world" in turn.final_message.content


# ---------------------------------------------------------------------------
# Terminal tool
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_terminal_tool_stops_execution() -> None:
    """Terminal tool detection stops the iter/next loop early."""
    def terminal_tool() -> str:
        """A terminal tool."""
        return "terminal result"

    agent = Agent(
        name="test-terminal",
        model=TestModel(call_tools=["terminal_tool"], custom_output_text="done"),
        tools=[terminal_tool],
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["Call the tool"],
            run_ctx=run_ctx,
            message_history=[],
        )

        def fake_is_terminal(tool: Tool[Any]) -> bool:
            return tool.name == "terminal_tool"

        events: list[Any] = []
        with patch(
            "agentpool.agents.native_agent.turn.is_terminal_tool",
            side_effect=fake_is_terminal,
        ):
            async for event in turn.execute():
                events.append(event)

        # Terminal tool name should be set on run_ctx
        assert run_ctx.terminal_tool_name == "terminal_tool"

        # A ToolCallCompleteEvent for the terminal tool should have been yielded
        complete_events = [
            e for e in events
            if isinstance(e, ToolCallCompleteEvent) and e.tool_name == "terminal_tool"
        ]
        assert len(complete_events) == 1, (
            "Expected exactly one ToolCallCompleteEvent for terminal_tool"
        )


# ---------------------------------------------------------------------------
# RunAbortedError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_aborted_error_graceful_stop() -> None:
    """RunAbortedError causes graceful stop without propagating exception."""
    agent = Agent(
        name="test-abort",
        model=TestModel(custom_output_text="hello"),
    )
    async with agent:
        mock_agentlet = _make_mock_agentlet_raising(RunAbortedError("test abort"))

        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        events: list[Any] = []
        with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
            async for event in turn.execute():
                events.append(event)

        # No RunErrorEvent should be yielded for RunAbortedError
        assert not any(isinstance(e, RunErrorEvent) for e in events), (
            "RunAbortedError should not produce RunErrorEvent"
        )


# ---------------------------------------------------------------------------
# CancelledError
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_error_is_reraised() -> None:
    """asyncio.CancelledError is re-raised, not swallowed."""
    agent = Agent(
        name="test-cancel",
        model=TestModel(custom_output_text="hello"),
    )
    async with agent:
        mock_agentlet = _make_mock_agentlet_raising(asyncio.CancelledError())

        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        with patch.object(agent, "get_agentlet", AsyncMock(return_value=mock_agentlet)):
            with pytest.raises(asyncio.CancelledError):
                async for _ in turn.execute():
                    pass


# ---------------------------------------------------------------------------
# Property lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.asyncio
async def test_properties_raise_before_execute() -> None:
    """message_history and final_message raise RuntimeError before execute() completes."""
    agent = Agent(
        name="test-props",
        model=TestModel(custom_output_text="hello"),
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        with pytest.raises(RuntimeError, match="message_history is not available"):
            _ = turn.message_history

        with pytest.raises(RuntimeError, match="final_message is not available"):
            _ = turn.final_message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_message_history_and_final_message_after_execute() -> None:
    """message_history and final_message are populated after execute() completes."""
    agent = Agent(
        name="test-props-after",
        model=TestModel(custom_output_text="final response"),
    )
    async with agent:
        run_ctx = AgentRunContext(session_id="test-session")
        turn = NativeTurn(
            agent=agent,
            prompts=["test"],
            run_ctx=run_ctx,
            message_history=[],
        )

        # Consume all events
        async for _ in turn.execute():
            pass

        # message_history should contain pydantic-ai messages
        history = turn.message_history
        assert len(history) > 0

        # final_message should contain the response text
        msg = turn.final_message
        assert msg is not None
        assert msg.role == "assistant"
        assert msg.name == "test-props-after"
        assert "final response" in msg.content
        assert msg.session_id == "test-session"
