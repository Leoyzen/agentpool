"""Unit tests for schema-aware write tools (Phase 4)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.messages import ToolReturn
import pytest

from agentpool.capabilities.agent_db import AgentDBCapability
from agentpool.capabilities.agent_db.schema_write import build_schema_write_tools
from agentpool.capabilities.viking import VikingCapability


pytestmark = pytest.mark.unit


def _get_tool(tools: list[Any], name: str) -> Any:
    """Find a tool by name from the list returned by build_schema_write_tools."""
    return next(t for t in tools if t.__name__ == name)


def _make_ctx(session_id: str | None = "test-session") -> MagicMock:
    """Create a mock RunContext with session_id on deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = session_id
    return ctx


@pytest.fixture
def agent_db_cap_write(mock_viking: VikingCapability) -> Any:
    """Create an AgentDBCapability with write mode."""
    return AgentDBCapability(
        viking=mock_viking,
        allowed_prefixes=(
            "viking://raw/",
            "viking://wiki/",
            "viking://catalog/",
            "viking://tickets/",
            "viking://graph/",
        ),
        mode="write",
    )


# ---- TestCreateEntity ----


class TestCreateEntity:
    """Tests for agentdb_create_entity."""

    async def test_create_entity_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap_write: AgentDBCapability,
    ) -> None:
        """Create an OPL proposal for a new entity."""
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        tools = build_schema_write_tools(agent_db_cap_write)
        tool = _get_tool(tools, "agentdb_create_entity")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            entity_type="fault",
            entity_data={"title": "新故障", "description": "新故障描述"},
            opl_proposal_id="opl-001",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["opl_proposal_uri"] == "viking://tickets/opl_proposal/opl-001.md"
        assert data["status"] == "created"
        mock_client.write.assert_called_once()

    async def test_create_entity_validation_error(
        self,
        mock_client: AsyncMock,
        agent_db_cap_write: AgentDBCapability,
    ) -> None:
        """Pass invalid entity_type, verify ValidationError returned."""
        tools = build_schema_write_tools(agent_db_cap_write)
        tool = _get_tool(tools, "agentdb_create_entity")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            entity_type="invalid_type",
            entity_data={"title": "test"},
            opl_proposal_id="opl-002",
        )

        assert isinstance(result, ToolReturn)
        assert (
            "invalid" in str(result.return_value).lower()
            or "error" in str(result.return_value).lower()
        )
        mock_client.write.assert_not_called()


# ---- TestUpdateEntity ----


class TestUpdateEntity:
    """Tests for agentdb_update_entity."""

    async def test_update_entity_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap_write: AgentDBCapability,
    ) -> None:
        """Create an OPL proposal to modify an existing entity."""
        existing_content = (
            "---\ntitle: 泵故障\ntype: fault\nversion: 2\n---\n\n## 故障描述\n\n旧描述。\n"
        )
        mock_client.read = AsyncMock(return_value=existing_content)
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        tools = build_schema_write_tools(agent_db_cap_write)
        tool = _get_tool(tools, "agentdb_update_entity")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            entity_uri="viking://wiki/fault/pump_failure.md",
            changes={"description": "新描述"},
            opl_proposal_id="opl-003",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["opl_proposal_uri"] == "viking://tickets/opl_proposal/opl-003.md"
        assert data["status"] == "created"
        assert data["base_version"] == 2
        mock_client.write.assert_called_once()


# ---- Mode gating test ----


def test_build_schema_write_tools_empty_in_read_mode(
    agent_db_cap: AgentDBCapability,
) -> None:
    """build_schema_write_tools returns empty list in read mode."""
    tools = build_schema_write_tools(agent_db_cap)
    assert tools == []
