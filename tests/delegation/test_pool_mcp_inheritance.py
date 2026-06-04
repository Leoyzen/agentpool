from __future__ import annotations

import pytest

from agentpool import AgentPool, AgentsManifest, NativeAgentConfig


def _provider_names(agent: object) -> set[str]:
    tools = getattr(agent, "tools")
    return {provider.name for provider in tools.external_providers}


@pytest.mark.asyncio
async def test_agent_can_opt_out_of_pool_mcp_inheritance() -> None:
    manifest = AgentsManifest(
        agents={
            "default_agent": NativeAgentConfig(name="default_agent", model="test"),
            "isolated_agent": NativeAgentConfig(
                name="isolated_agent",
                model="test",
                inherit_pool_mcp_servers=False,
            ),
        },
    )

    async with AgentPool(manifest) as pool:
        default_agent = pool.nodes["default_agent"]
        isolated_agent = pool.nodes["isolated_agent"]

        assert "pool_mcp_aggregated" in _provider_names(default_agent)
        assert "pool_mcp_aggregated" not in _provider_names(isolated_agent)
