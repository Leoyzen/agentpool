"""Tests for /mode and /config/mcp-servers routes."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from agentpool.agents.modes import ModeCategory, ModeInfo
from agentpool.common_types import MCPServerStatus
from agentpool_server.opencode_server.routes.config_routes import (
    get_mcp_servers,
    list_modes,
)


class TestListModes:
    """Test dynamic /mode route."""

    async def test_mode_returns_dynamic_modes(self):
        """/mode returns modes from agent.get_modes()."""
        agent = MagicMock()
        agent.get_modes = AsyncMock(
            return_value=[
                ModeCategory(
                    id="mode",
                    name="Mode",
                    available_modes=[
                        ModeInfo(id="default", name="Default"),
                        ModeInfo(id="accept_edits", name="Accept Edits"),
                    ],
                    current_mode_id="default",
                    category="mode",
                )
            ]
        )
        state = MagicMock()
        state.agent = agent

        result = await list_modes(state)  # type: ignore[arg-type]

        assert len(result) == 2
        names = {m.name for m in result}
        assert "default" in names
        assert "accept_edits" in names

    async def test_mode_fallback_on_error(self):
        """/mode returns default when get_modes() raises."""
        agent = MagicMock()
        agent.get_modes = AsyncMock(side_effect=RuntimeError("boom"))
        state = MagicMock()
        state.agent = agent

        result = await list_modes(state)  # type: ignore[arg-type]

        assert len(result) == 1
        assert result[0].name == "default"

    async def test_mode_empty_modes(self):
        """/mode returns default when no mode category found."""
        agent = MagicMock()
        agent.get_modes = AsyncMock(return_value=[])
        state = MagicMock()
        state.agent = agent

        result = await list_modes(state)  # type: ignore[arg-type]

        assert len(result) == 1
        assert result[0].name == "default"

    async def test_mode_no_mode_category(self):
        """/mode returns default when category has no mode id."""
        agent = MagicMock()
        agent.get_modes = AsyncMock(
            return_value=[
                ModeCategory(
                    id="model",
                    name="Model",
                    available_modes=[
                        ModeInfo(id="gpt-4o", name="GPT-4o"),
                    ],
                    current_mode_id="gpt-4o",
                    category="model",
                )
            ]
        )
        state = MagicMock()
        state.agent = agent

        result = await list_modes(state)  # type: ignore[arg-type]

        assert len(result) == 1
        assert result[0].name == "default"


class TestGetMcpServers:
    """Test GET /config/mcp-servers route."""

    async def test_returns_mcp_server_statuses(self):
        """/config/mcp-servers returns statuses from agent.get_mcp_server_info()."""
        mock_status = MCPServerStatus(
            name="test-server",
            status="connected",
            server_type="stdio",
            display_name="Test Server",
        )
        agent = MagicMock()
        agent.get_mcp_server_info = AsyncMock(return_value={"test-server": mock_status})
        state = MagicMock()
        state.agent = agent

        result = await get_mcp_servers(state)  # type: ignore[arg-type]

        assert "test-server" in result
        entry = result["test-server"]
        assert entry.name == "test-server"
        assert entry.status == "connected"
        assert entry.display_name == "Test Server"
        assert entry.server_type == "stdio"

    async def test_returns_empty_dict_when_agent_is_none(self):
        """/config/mcp-servers returns empty dict when agent is None."""
        state = MagicMock()
        state.agent = None

        result = await get_mcp_servers(state)  # type: ignore[arg-type]

        assert result == {}

    async def test_includes_server_type_for_multiple_servers(self):
        """/config/mcp-servers includes server_type for each server."""
        statuses = {
            "srv-1": MCPServerStatus(
                name="srv-1",
                status="connected",
                server_type="stdio",
                display_name="Stdio Server",
            ),
            "srv-2": MCPServerStatus(
                name="srv-2",
                status="error",
                server_type="sse",
                display_name="SSE Server",
                error="Connection refused",
            ),
        }
        agent = MagicMock()
        agent.get_mcp_server_info = AsyncMock(return_value=statuses)
        state = MagicMock()
        state.agent = agent

        result = await get_mcp_servers(state)  # type: ignore[arg-type]

        assert len(result) == 2
        assert result["srv-1"].server_type == "stdio"
        assert result["srv-2"].server_type == "sse"
        assert result["srv-2"].error == "Connection refused"
