"""Unit tests for ACPAgent.create_turn()."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from acp import InitializeRequest
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.acp_agent.turn import ACPTurn
from agentpool.agents.context import AgentRunContext


@pytest.mark.unit
def test_acp_agent_create_turn_returns_acp_turn() -> None:
    """Given an ACPAgent with mocked API, create_turn() returns an ACPTurn."""
    init_request = MagicMock(spec=InitializeRequest)
    agent = ACPAgent(command="test-cmd", init_request=init_request)
    agent._api = MagicMock()
    agent._sdk_session_id = "test-session-id"

    run_ctx = AgentRunContext(session_id="test-run-ctx")
    turn = agent.create_turn(
        prompts=["hello"],
        run_ctx=run_ctx,
        message_history=[],
    )

    assert isinstance(turn, ACPTurn)
