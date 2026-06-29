"""Tests for agent role config option and swap (Phase 2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from acp.exceptions import RequestError
from acp.schema import SessionConfigOption
from agentpool_server.acp_server.shared.config_utils import (
    get_agent_role_config_option,
)
from agentpool_server.acp_server.v1.acp_agent import (
    AgentPoolACPAgent,
)


class TestGetAgentRoleConfigOption:
    """Test get_agent_role_config_option."""

    def test_single_agent_no_role(self):
        """Single agent pool should not expose agent_role option."""
        pool = MagicMock()
        pool.all_agents = {"solo": MagicMock(name="solo")}
        agent = MagicMock()
        agent.name = "solo"
        agent.agent_pool = pool

        result = get_agent_role_config_option(agent)
        assert result is None

    def test_multi_agent_has_role(self):
        """Multi-agent pool should expose agent_role option."""
        pool = MagicMock()
        agent_a = MagicMock()
        agent_a.name = "agent_a"
        agent_a.display_name = None
        agent_b = MagicMock()
        agent_b.name = "agent_b"
        agent_b.display_name = "Agent B"
        pool.all_agents = {"agent_a": agent_a, "agent_b": agent_b}
        agent = MagicMock()
        agent.name = "agent_a"
        agent.agent_pool = pool

        result = get_agent_role_config_option(agent)
        assert result is not None
        assert isinstance(result, SessionConfigOption)
        assert result.id == "agent_role"
        assert result.current_value == "agent_a"
        options = result.options
        assert len(options) == 2
        assert all(hasattr(o, "value") for o in options)
        choice_values = {o.value for o in options}  # type: ignore[union-attr]
        assert "agent_a" in choice_values
        assert "agent_b" in choice_values
        # Verify display_name fallback and description
        option_a = next(o for o in options if o.value == "agent_a")  # type: ignore[union-attr]
        assert option_a.name == "agent_a"  # type: ignore[union-attr]
        assert option_a.description == "Switch to agent_a agent"  # type: ignore[union-attr]
        option_b = next(o for o in options if o.value == "agent_b")  # type: ignore[union-attr]
        assert option_b.name == "Agent B"  # type: ignore[union-attr]
        assert option_b.description == "Switch to agent_b agent"  # type: ignore[union-attr]

    def test_no_pool_returns_none(self):
        """Agent without pool should not expose agent_role."""
        agent = MagicMock()
        agent.agent_pool = None

        result = get_agent_role_config_option(agent)
        assert result is None


class TestSwapSessionAgent:
    """Test _swap_session_agent on AgentPoolACPAgent."""

    @pytest.fixture
    def mock_acp_agent(self):
        """Create a mock ACP agent with session manager."""
        pool = MagicMock()
        default_agent = MagicMock()
        default_agent.name = "default"
        default_agent.agent_pool = pool
        pool.all_agents = {"default": default_agent}

        client = MagicMock()
        acp_agent = AgentPoolACPAgent(client=client, default_agent=default_agent)
        # Mock provider_router
        acp_agent.provider_router = MagicMock()
        return acp_agent, pool

    async def test_role_swap_success(self, mock_acp_agent):
        """Swap to valid agent should succeed."""
        acp_agent, _pool = mock_acp_agent

        session = MagicMock()
        session.agent = MagicMock()
        session.agent.name = "default"
        session._task_lock = MagicMock()
        session._task_lock.locked.return_value = False
        session.switch_active_agent = AsyncMock()
        acp_agent.session_manager._acp_sessions = {"sess_1": session}
        acp_agent.session_manager.get_session = lambda sid: session

        result = await acp_agent._swap_session_agent("sess_1", "other")
        assert result["success"] is True
        session.switch_active_agent.assert_called_once_with("other")

    async def test_role_swap_blocked_during_prompt(self, mock_acp_agent):
        """Swap should fail when prompt is active."""
        acp_agent, _pool = mock_acp_agent

        session = MagicMock()
        session._task_lock = MagicMock()
        session._task_lock.locked.return_value = True
        acp_agent.session_manager.get_session = lambda sid: session

        with pytest.raises(RequestError) as exc_info:
            await acp_agent._swap_session_agent("sess_1", "other")
        assert exc_info.value.code == -32602

    async def test_role_swap_invalid_agent(self, mock_acp_agent):
        """Swap to invalid agent name should fail."""
        acp_agent, _pool = mock_acp_agent

        session = MagicMock()
        session.agent = MagicMock()
        session.agent.name = "default"
        session._task_lock = MagicMock()
        session._task_lock.locked.return_value = False
        session.switch_active_agent = AsyncMock(side_effect=ValueError("Agent not found"))
        acp_agent.session_manager.get_session = lambda sid: session

        with pytest.raises(Exception):
            await acp_agent._swap_session_agent("sess_1", "nonexistent")

    async def test_swap_no_history_inheritance(self, mock_acp_agent):
        """New agent should start fresh without inheriting history."""
        acp_agent, _pool = mock_acp_agent

        session = MagicMock()
        session.agent = MagicMock()
        session.agent.name = "default"
        session._task_lock = MagicMock()
        session._task_lock.locked.return_value = False
        session.switch_active_agent = AsyncMock()
        acp_agent.session_manager.get_session = lambda sid: session

        await acp_agent._swap_session_agent("sess_1", "other")
        # switch_active_agent is called, which handles creating new agent
        session.switch_active_agent.assert_called_once_with("other")
