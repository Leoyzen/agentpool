"""Tests for BaseAgent.create_turn() and Agent.create_turn()."""

from __future__ import annotations

from pydantic_ai.models.test import TestModel
import pytest

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import AgentRunContext
from agentpool.agents.native_agent.agent import Agent
from agentpool.agents.native_agent.turn import NativeTurn


@pytest.mark.unit
def test_agent_create_turn_returns_native_turn() -> None:
    """Given a native Agent, create_turn() returns a NativeTurn instance."""
    agent = Agent(model=TestModel(), name="test_agent")
    run_ctx = AgentRunContext()
    prompts: list[str] = ["Hello"]

    turn = agent.create_turn(
        prompts=prompts,
        run_ctx=run_ctx,
        message_history=[],
    )

    assert isinstance(turn, NativeTurn)


@pytest.mark.unit
def test_create_turn_is_abstract() -> None:
    """create_turn is abstract on BaseAgent and must be overridden by subclasses."""
    assert "create_turn" in BaseAgent.__abstractmethods__
