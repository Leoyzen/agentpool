"""Unit tests for advanced read tools (Phase 5)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.messages import ToolReturn
import pytest

from agentpool.capabilities.agent_db import AgentDBCapability
from agentpool.capabilities.agent_db.schema_advanced import build_advanced_read_tools


pytestmark = pytest.mark.unit


def _get_tool(tools: list[Any], name: str) -> Any:
    """Find a tool by name from the list returned by build_advanced_read_tools."""
    return next(t for t in tools if t.__name__ == name)


def _make_ctx(session_id: str | None = "test-session") -> MagicMock:
    """Create a mock RunContext with session_id on deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = session_id
    return ctx


# ---- TestGenerateTextbook ----


class TestGenerateTextbook:
    """Tests for agentdb_generate_textbook."""

    async def test_generate_textbook_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Generate a 3-layer textbook from knowledge items."""
        # Mock query_applicability results: we need BOM, entity_rel, and entity reads
        bom_markdown = (
            "# BOM SY75C\n\n"
            "| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |\n"
            "|------|---------|--------|--------|------|-----------|------------|\n"
            "| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |\n"
        )
        caused_by_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/component/k3v_k3v63dt\n"
            "  relation: caused_by\n"
            "  weight: 0.9\n"
        )
        manifests_as_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/symptom/no_pressure\n"
            "  relation: manifests_as\n"
            "  weight: 0.85\n"
        )
        addresses_yaml = (
            "- source: wiki/opl/fix_pump\n"
            "  target: wiki/fault/pump_failure\n"
            "  relation: addresses\n"
            "  weight: 0.8\n"
        )
        confirmed_by_yaml = ""
        repaired_by_yaml = ""

        async def read_side_effect(uri: str) -> str:
            mapping: dict[str, str] = {
                "viking://catalog/sany/excavator/sy75c/bom.md": bom_markdown,
                "viking://graph/entity_rel/caused_by.yaml": caused_by_yaml,
                "viking://graph/entity_rel/manifests_as.yaml": manifests_as_yaml,
                "viking://graph/entity_rel/addresses.yaml": addresses_yaml,
                "viking://graph/entity_rel/confirmed_by.yaml": confirmed_by_yaml,
                "viking://graph/entity_rel/repaired_by.yaml": repaired_by_yaml,
                "viking://wiki/domain/excavator": (
                    "---\ntitle: 挖掘机\ntype: domain\n---\n\n## 概述\n\n挖掘机知识域。\n"
                ),
                "viking://wiki/fault/pump_failure": (
                    "---\ntitle: 泵故障\ntype: fault\ncredibility: high\n"
                    "version: 2\nstatus: active\n---\n\n"
                    "## 故障描述\n\n主泵压力不足。\n\n"
                    "## 排查步骤\n\n1. 检查先导压力\n"
                ),
                "viking://wiki/symptom/no_pressure": (
                    "---\ntitle: 无压力\ntype: symptom\ncredibility: high\n"
                    "version: 1\nstatus: active\n---\n\n"
                    "## 症状描述\n\n系统无压力。\n\n"
                    "## pruning_rules\n\n- 条件1\n"
                ),
                "viking://wiki/opl/fix_pump": (
                    "---\ntitle: 修复泵\ntype: opl\ncredibility: medium\n"
                    "version: 1\nstatus: active\n---\n\n"
                    "## 修复方案\n\n更换主泵。\n"
                ),
                "viking://wiki/component/k3v_k3v63dt.md": (
                    "---\ntitle: 主泵\ntype: component\ncredibility: high\n"
                    "version: 1\nstatus: active\n---\nAbstract."
                ),
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)

        async def abstract_side_effect(uri: str) -> str:
            abstracts: dict[str, str] = {
                "viking://wiki/fault/pump_failure": (
                    "---\ntitle: 泵故障\ncredibility: high\nversion: 2\n"
                    "status: active\n---\nAbstract."
                ),
                "viking://wiki/symptom/no_pressure": (
                    "---\ntitle: 无压力\ncredibility: high\nversion: 1\n"
                    "status: active\n---\nAbstract."
                ),
                "viking://wiki/opl/fix_pump": (
                    "---\ntitle: 修复泵\ncredibility: medium\nversion: 1\n"
                    "status: active\n---\nAbstract."
                ),
                "viking://wiki/component/k3v_k3v63dt.md": (
                    "---\ntitle: 主泵\ncredibility: high\nversion: 1\n"
                    "status: active\n---\nAbstract."
                ),
            }
            return abstracts.get(uri, "")

        mock_client.abstract = AsyncMock(side_effect=abstract_side_effect)

        tools = build_advanced_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_generate_textbook")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity_node="sany/excavator/sy75c",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert "domain_layer" in data
        assert "pruning_layer" in data
        assert "variant_layer" in data
        assert "assembled_content" in data
        assert "evidence_chain" in data
        assert isinstance(data["evidence_chain"], list)


# ---- TestGetCoverageReport ----


class TestGetCoverageReport:
    """Tests for agentdb_get_coverage_report."""

    async def test_get_coverage_report_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Generate a coverage report for known symptoms."""
        bom_markdown = (
            "# BOM SY75C\n\n"
            "| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |\n"
            "|------|---------|--------|--------|------|-----------|------------|\n"
            "| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |\n"
        )
        caused_by_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/component/k3v_k3v63dt\n"
            "  relation: caused_by\n"
            "  weight: 0.9\n"
        )
        manifests_as_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/symptom/no_pressure\n"
            "  relation: manifests_as\n"
            "  weight: 0.85\n"
        )
        addresses_yaml = ""
        confirmed_by_yaml = ""
        repaired_by_yaml = ""

        async def read_side_effect(uri: str) -> str:
            mapping: dict[str, str] = {
                "viking://catalog/sany/excavator/sy75c/bom.md": bom_markdown,
                "viking://graph/entity_rel/caused_by.yaml": caused_by_yaml,
                "viking://graph/entity_rel/manifests_as.yaml": manifests_as_yaml,
                "viking://graph/entity_rel/addresses.yaml": addresses_yaml,
                "viking://graph/entity_rel/confirmed_by.yaml": confirmed_by_yaml,
                "viking://graph/entity_rel/repaired_by.yaml": repaired_by_yaml,
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)

        async def abstract_side_effect(uri: str) -> str:
            abstracts: dict[str, str] = {
                "viking://wiki/fault/pump_failure": (
                    "---\ntitle: 泵故障\ncredibility: high\nversion: 2\n"
                    "status: active\n---\nAbstract."
                ),
                "viking://wiki/symptom/no_pressure": (
                    "---\ntitle: 无压力\ncredibility: high\nversion: 1\n"
                    "status: active\n---\nAbstract."
                ),
                "viking://wiki/component/k3v_k3v63dt.md": (
                    "---\ntitle: 主泵\ncredibility: high\nversion: 1\n"
                    "status: active\n---\nAbstract."
                ),
            }
            return abstracts.get(uri, "")

        mock_client.abstract = AsyncMock(side_effect=abstract_side_effect)

        tools = build_advanced_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_coverage_report")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity_node="sany/excavator/sy75c",
            symptoms_checked=["wiki/symptom/no_pressure", "wiki/symptom/unknown_symptom"],
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert "symptoms" in data
        assert len(data["symptoms"]) == 2
        # The known symptom should be covered
        covered_symptoms = [s for s in data["symptoms"] if s.get("covered")]
        assert len(covered_symptoms) >= 1
        # The unknown symptom should not be covered
        uncovered = [s for s in data["symptoms"] if not s.get("covered")]
        assert len(uncovered) >= 1
        assert "faults" in data
        assert "recommendations" in data


# ---- Tool count test ----


def test_build_advanced_read_tools_returns_expected_tools(
    agent_db_cap: AgentDBCapability,
) -> None:
    """build_advanced_read_tools returns expected tool functions."""
    tools = build_advanced_read_tools(agent_db_cap)
    names = {t.__name__ for t in tools}
    expected = {
        "agentdb_generate_textbook",
        "agentdb_get_coverage_report",
        "agentdb_derive_applicability",
    }
    assert names == expected
    assert len(tools) == len(expected)


# ---- TestDeriveApplicability ----


class TestDeriveApplicability:
    """Tests for agentdb_derive_applicability."""

    async def test_derive_global_scope(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Entity with no variant overrides → suggested_scope scope_type='global'."""
        entity_content = (
            "---\ntitle: 泵故障\ntype: fault\nversion: 2\n---\n\n## 故障描述\n\n通用描述。\n"
        )
        mock_client.read = AsyncMock(return_value=entity_content)
        mock_client.ls = AsyncMock(return_value=[])

        tools = build_advanced_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_derive_applicability")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            entity_uri="viking://wiki/fault/pump_failure.md",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["suggested_scope"]["scope_type"] == "global"
        assert data["existing_variants"] == []
        assert isinstance(data["alternatives"], list)

    async def test_derive_model_scope(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Entity with variant override for specific device → scope_type='catalog_model'."""
        entity_content = (
            "---\ntitle: 泵故障\ntype: fault\nversion: 2\n---\n\n## 故障描述\n\n通用描述。\n"
        )
        variant_content = (
            "---\nknowledge: wiki/fault/pump_failure.md\nversion: 1\n---\n\n"
            "## 故障描述\n\nSY75C 特有描述。\n"
        )

        async def read_side_effect(uri: str) -> str:
            if "viking://wiki/fault/pump_failure.md" in uri:
                return entity_content
            if "variant" in uri and uri.endswith(".md"):
                return variant_content
            return ""

        mock_client.read = AsyncMock(side_effect=read_side_effect)

        async def ls_side_effect(uri: str) -> list[Any]:
            if uri == "viking://catalog/":
                return [{"name": "sany/", "is_dir": True}]
            if uri == "viking://catalog/sany/":
                return [{"name": "excavator/", "is_dir": True}]
            if uri == "viking://catalog/sany/excavator/":
                return [{"name": "sy75c/", "is_dir": True}]
            if uri == "viking://catalog/sany/excavator/sy75c/variant/":
                return [{"name": "pump_failure_variant.md", "is_dir": False}]
            return []

        mock_client.ls = AsyncMock(side_effect=ls_side_effect)

        tools = build_advanced_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_derive_applicability")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            entity_uri="viking://wiki/fault/pump_failure.md",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["suggested_scope"]["scope_type"] == "catalog_model"
        assert len(data["existing_variants"]) >= 1
