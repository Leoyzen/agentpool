"""Shared session configuration helpers for ACP v1/v2.

These functions build ``SessionConfigOption`` lists from agent mode
categories and pool-level agent roles.  They are used by both the v1
and v2 ACP agent implementations, so they live here to avoid v2
importing from v1 (which would violate clean layering).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp.schema import SessionConfigOption, SessionConfigSelectOption
from agentpool.log import get_logger
from agentpool_server.acp_server.converters import to_session_config_option


if TYPE_CHECKING:
    from agentpool.agents.base_agent import BaseAgent

logger = get_logger(__name__)


async def get_session_config_options(agent: BaseAgent[Any, Any]) -> list[SessionConfigOption]:
    """Get SessionConfigOptions from an agent using its get_modes() method."""
    try:
        mode_categories = await agent.get_modes()
    except Exception:
        logger.exception("Failed to get modes from agent")
        return []
    options = [to_session_config_option(category) for category in mode_categories]
    # Append agent_role config option if pool has multiple agents
    if agent_role_opt := get_agent_role_config_option(agent):
        options.append(agent_role_opt)
    return options


def get_agent_role_config_option(
    agent: BaseAgent[Any, Any],
) -> SessionConfigOption | None:
    """Build agent_role config option if pool has more than one agent.

    Args:
        agent: The agent to check pool membership for.

    Returns:
        SessionConfigOption for agent_role, or None if pool has <= 1 agents.
    """
    pool = agent.agent_pool
    if pool is None or len(pool.all_agents) <= 1:
        return None

    choices = [
        SessionConfigSelectOption(
            value=a.name,
            name=a.display_name if isinstance(a.display_name, str) and a.display_name else a.name,
            description=f"Switch to {a.name} agent",
        )
        for a in pool.all_agents.values()
    ]
    return SessionConfigOption(
        id="agent_role",
        name="Agent Role",
        description="Switch between available agents",
        category="other",
        current_value=agent.name,
        options=choices,
    )
