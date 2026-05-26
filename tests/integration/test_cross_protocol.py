"""Cross-protocol integration validation (Task 17)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from agentpool.agents.modes import ModeCategory, ModeInfo
from agentpool_server.acp_server.acp_agent import get_agent_role_config_option
from agentpool_server.opencode_server.routes.config_routes import list_modes


class TestCrossProtocolAlignment:
    """Verify ACP and OpenCode protocols reflect aligned agent state."""

    async def test_agent_role_appears_when_multiple_modes(self):
        """agent_role config option appears when /mode returns multiple modes."""
        # Setup: agent with multiple modes
        agent = MagicMock()
        agent.name = "agent_a"
        agent.agent_pool = MagicMock()
        agent_b = MagicMock()
        agent_b.name = "agent_b"
        agent.agent_pool.all_agents = {"agent_a": agent, "agent_b": agent_b}
        agent.get_modes = AsyncMock(
            return_value=[
                ModeCategory(
                    id="mode",
                    name="Mode",
                    available_modes=[
                        ModeInfo(id="default", name="Default"),
                        ModeInfo(id="advanced", name="Advanced"),
                    ],
                    current_mode_id="default",
                    category="mode",
                )
            ]
        )

        # ACP: get agent_role config option
        agent_role_opt = get_agent_role_config_option(agent)
        assert agent_role_opt is not None
        assert agent_role_opt.id == "agent_role"

        # OpenCode: get modes
        state = MagicMock()
        state.agent = agent
        modes = await list_modes(state)  # type: ignore[arg-type]
        assert len(modes) == 2

    async def test_agent_role_hidden_when_single_mode(self):
        """agent_role config option hidden when /mode returns single mode."""
        agent = MagicMock()
        agent.name = "solo"
        agent.agent_pool = MagicMock()
        agent.agent_pool.all_agents = {"solo": agent}
        agent.get_modes = AsyncMock(
            return_value=[
                ModeCategory(
                    id="mode",
                    name="Mode",
                    available_modes=[ModeInfo(id="default", name="Default")],
                    current_mode_id="default",
                    category="mode",
                )
            ]
        )

        # ACP: no agent_role for single agent
        agent_role_opt = get_agent_role_config_option(agent)
        assert agent_role_opt is None

        # OpenCode: single mode (returns mode.id, not mode.name)
        state = MagicMock()
        state.agent = agent
        modes = await list_modes(state)  # type: ignore[arg-type]
        assert len(modes) == 1
        assert modes[0].name == "default"

    async def test_cross_protocol_model_alignment(self):
        """Both protocols reflect same underlying model state."""
        from llmling_models_config import StringModelConfig

        from agentpool_server.acp_server.provider_router import ProviderRouter
        from agentpool_server.shared.model_utils import build_model_state_for_acp

        # Manifest with configured variants
        manifest = MagicMock()
        manifest.model_variants = {
            "fast": StringModelConfig(identifier="openai:gpt-4o-mini"),
        }
        pool = MagicMock()
        pool.manifest = manifest
        agent = MagicMock()
        agent.name = "test"
        agent.model_name = "fast"
        agent.agent_pool = pool
        agent.get_available_models = AsyncMock(return_value=[])

        router = ProviderRouter(manifest)  # type: ignore[arg-type]
        acp_state = await build_model_state_for_acp(agent, router)  # type: ignore[arg-type]

        assert acp_state is not None
        model_ids = {m.model_id for m in acp_state.available_models}
        assert "fast" in model_ids
        assert acp_state.current_model_id == "fast"
