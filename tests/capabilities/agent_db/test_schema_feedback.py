"""Unit tests for feedback tools (Phase 5)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.messages import ToolReturn
import pytest

from agentpool.capabilities.agent_db import AgentDBCapability
from agentpool.capabilities.agent_db.schema_feedback import build_feedback_tools


pytestmark = pytest.mark.unit


def _get_tool(tools: list[Any], name: str) -> Any:
    """Find a tool by name from the list returned by build_feedback_tools."""
    return next(t for t in tools if t.__name__ == name)


def _make_ctx(session_id: str | None = "test-session") -> MagicMock:
    """Create a mock RunContext with session_id on deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = session_id
    return ctx


# ---- TestCreateSimplifiedFeedback ----


class TestCreateSimplifiedFeedback:
    """Tests for agentdb_create_simplified_feedback."""

    async def test_feedback_observation(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Create an OPS from observation feedback."""
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        tools = build_feedback_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_create_simplified_feedback")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            feedback_type="observation",
            identity_node="sany/excavator/sy75c",
            symptom_uri="viking://wiki/symptom/no_pressure",
            observation="现场观察到压力异常下降",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["feedback_type"] == "observation"
        assert "qt_uri" in data
        assert data["status"] == "created"
        mock_client.write.assert_called_once()
        written_content = mock_client.write.call_args.args[1]
        assert "type: ops" in written_content
        assert "source: agent_proactive" in written_content

    async def test_feedback_experience(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Create an OPL proposal from experience feedback."""
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        tools = build_feedback_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_create_simplified_feedback")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            feedback_type="experience",
            identity_node="sany/excavator/sy75c",
            pattern="更换主泵后压力恢复",
            cases=["案例1: SY75C-001", "案例2: SY75C-002"],
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["feedback_type"] == "experience"
        assert data["status"] == "created"
        written_content = mock_client.write.call_args.args[1]
        assert "type: opl_proposal" in written_content
        assert "proposal_type: create" in written_content

    async def test_feedback_contradiction(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Create an OPA from contradiction feedback."""
        mock_client.write = AsyncMock(return_value={"status": "ok"})

        tools = build_feedback_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_create_simplified_feedback")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            feedback_type="contradiction",
            identity_node="sany/excavator/sy75c",
            entity_uri="viking://wiki/fault/pump_failure",
            contradiction="现有排查步骤与实际不符",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["feedback_type"] == "contradiction"
        assert data["status"] == "created"
        written_content = mock_client.write.call_args.args[1]
        assert "type: opa" in written_content
        assert "source: agent_quality_check" in written_content

    async def test_feedback_missing_fields(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Pass observation without symptom_uri, verify ValidationError."""
        tools = build_feedback_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_create_simplified_feedback")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            feedback_type="observation",
            identity_node="sany/excavator/sy75c",
            observation="some observation",
        )

        assert isinstance(result, ToolReturn)
        assert (
            "validation" in str(result.return_value).lower()
            or "required" in str(result.return_value).lower()
        )
        mock_client.write.assert_not_called()


# ---- Tool count test ----


def test_build_feedback_tools_returns_1_tool(
    agent_db_cap: AgentDBCapability,
) -> None:
    """build_feedback_tools returns exactly 1 tool function."""
    tools = build_feedback_tools(agent_db_cap)
    assert len(tools) == 1
    assert tools[0].__name__ == "agentdb_create_simplified_feedback"
