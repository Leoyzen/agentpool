"""Unit tests for schema-aware read tools (Phase 2)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai.messages import ToolReturn
import pytest

from agentpool.capabilities.agent_db import AgentDBCapability
from agentpool.capabilities.agent_db.schema_read import build_schema_read_tools
from agentpool.capabilities.viking import VikingCapability


pytestmark = pytest.mark.unit


def _get_tool(tools: list[Any], name: str) -> Any:
    """Find a tool by name from the list returned by build_schema_read_tools."""
    return next(t for t in tools if t.__name__ == name)


def _make_ctx(session_id: str | None = "test-session") -> MagicMock:
    """Create a mock RunContext with session_id on deps."""
    ctx = MagicMock()
    ctx.deps = MagicMock()
    ctx.deps.session_id = session_id
    return ctx


# ---- TestResolveIdentity ----


class TestResolveIdentity:
    """Tests for agentdb_resolve_identity."""

    async def test_resolve_by_marketing_model(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Resolve identity by matching a marketing model name in the catalog tree."""
        # Mock nested catalog directory structure
        # viking://catalog/ → ["sany/"]
        # viking://catalog/sany/ → ["excavator/"]
        # viking://catalog/sany/excavator/ → ["sy75c/"]
        mock_client.ls = AsyncMock(
            side_effect=[
                ["sany/"],  # catalog root
                ["excavator/"],  # sany/
                ["sy75c/"],  # sany/excavator/
            ]
        )
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_resolve_identity")

        ctx = _make_ctx()
        result = await tool(ctx, marketing_model="SY75C")

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["node_path"] == "sany/excavator/sy75c"
        assert data["catalog_uri"] == "viking://catalog/sany/excavator/sy75c/"
        assert data["bom_uri"] == "viking://catalog/sany/excavator/sy75c/bom.md"
        assert data["domain_uri"] == "viking://wiki/domain/excavator"
        assert data["level"] == "model"

    async def test_resolve_by_serial_number(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Resolve identity by serial number (normalized matching)."""
        mock_client.ls = AsyncMock(
            side_effect=[
                ["doosan/"],
                ["excavator/"],
                ["dx55/"],
            ]
        )
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_resolve_identity")

        ctx = _make_ctx()
        result = await tool(ctx, serial_number="DX-55")

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["node_path"] == "doosan/excavator/dx55"

    async def test_resolve_not_found(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Return error when identity is not found in catalog."""
        mock_client.ls = AsyncMock(return_value=[])
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_resolve_identity")

        ctx = _make_ctx()
        result = await tool(ctx, marketing_model="nonexistent")

        assert isinstance(result, ToolReturn)
        assert "not found" in str(result.return_value).lower()

    async def test_resolve_no_search_term(self, agent_db_cap: AgentDBCapability) -> None:
        """Return error when no search term is provided."""
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_resolve_identity")

        ctx = _make_ctx()
        result = await tool(ctx)

        assert isinstance(result, ToolReturn)
        assert "at least one" in str(result.return_value).lower()


# ---- TestTraverseBom ----


class TestTraverseBom:
    """Tests for agentdb_traverse_bom."""

    async def test_traverse_bom_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Parse a BOM markdown table and return component list."""
        bom_markdown = """# BOM SY75C

| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |
|------|---------|--------|--------|------|-----------|------------|
| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |
| 发动机系统 | 发动机 | isuzu:4le2 | 4LE2-5678 | 1 | diesel_engine | None |
"""
        mock_client.read = AsyncMock(return_value=bom_markdown)
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_traverse_bom")

        ctx = _make_ctx()
        result = await tool(ctx, identity_node="sany/excavator/sy75c")

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["device"] == "sany/excavator/sy75c"
        assert len(data["components"]) == 2
        comp = data["components"][0]
        assert comp["component_id"] == "k3v:k3v63dt"
        assert comp["component_name"] == "主泵"
        assert comp["system"] == "液压系统"
        assert comp["material_no"] == "K3V63DT-1234"
        assert comp["quantity"] == "1"
        assert comp["class_ref"] == "axial_piston_pump"
        assert comp["ecu_family"] is None
        assert comp["component_uri"] == "viking://wiki/component/k3v_k3v63dt.md"
        assert comp["children"] == []

    async def test_traverse_bom_system_filter(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Filter BOM components by system name."""
        bom_markdown = """# BOM SY75C

| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |
|------|---------|--------|--------|------|-----------|------------|
| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |
| 发动机系统 | 发动机 | isuzu:4le2 | 4LE2-5678 | 1 | diesel_engine | None |
"""
        mock_client.read = AsyncMock(return_value=bom_markdown)
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_traverse_bom")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity_node="sany/excavator/sy75c",
            system="液压系统",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert len(data["components"]) == 1
        assert data["components"][0]["system"] == "液压系统"

    async def test_traverse_bom_not_found(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Return error message when BOM file is empty or missing."""
        mock_client.read = AsyncMock(return_value="")
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_traverse_bom")

        ctx = _make_ctx()
        result = await tool(ctx, identity_node="sany/excavator/nonexistent")

        assert isinstance(result, ToolReturn)
        assert "not found" in str(result.return_value).lower()


# ---- TestGetFaultSymptomGraph ----


class TestGetFaultSymptomGraph:
    """Tests for agentdb_get_fault_symptom_graph."""

    async def test_get_graph_by_fault(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Build a fault-symptom graph filtered by a specific fault URI."""
        manifests_as_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/symptom/no_pressure\n"
            "  relation: manifests_as\n"
            "  weight: 0.9\n"
        )
        caused_by_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/component/k3v_k3v63dt\n"
            "  relation: caused_by\n"
            "  weight: 0.85\n"
        )
        leads_to_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/fault/valve_stuck\n"
            "  relation: leads_to\n"
            "  weight: 0.7\n"
        )
        co_occurs_yaml = (
            "- source: wiki/fault/pump_failure\n"
            "  target: wiki/fault/leak\n"
            "  relation: co_occurs_with\n"
            "  weight: 0.5\n"
        )

        def read_side_effect(uri: str) -> str:
            mapping = {
                "viking://graph/entity_rel/manifests_as.yaml": manifests_as_yaml,
                "viking://graph/entity_rel/caused_by.yaml": caused_by_yaml,
                "viking://graph/entity_rel/leads_to.yaml": leads_to_yaml,
                "viking://graph/entity_rel/co_occurs_with.yaml": co_occurs_yaml,
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_fault_symptom_graph")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            fault_uri="wiki/fault/pump_failure",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert "wiki/fault/pump_failure" in data["faults"]
        assert "wiki/symptom/no_pressure" in data["symptoms"]
        assert "wiki/component/k3v_k3v63dt" in data["components"]
        assert len(data["edges"]) > 0
        # co_occurs_with: "wiki/fault/leak" is a fault but not in the filtered faults set
        assert "wiki/fault/leak" in data["co_occurring_faults"]

    async def test_get_graph_empty(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Return empty graph when all entity-rel files are empty/missing."""
        mock_client.read = AsyncMock(return_value="")
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_fault_symptom_graph")

        ctx = _make_ctx()
        result = await tool(ctx)

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["faults"] == []
        assert data["symptoms"] == []
        assert data["components"] == []
        assert data["edges"] == []
        assert data["co_occurring_faults"] == []

    async def test_get_graph_no_filter(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Build full graph without any filter."""
        manifests_as_yaml = (
            "- source: wiki/fault/f1\n"
            "  target: wiki/symptom/s1\n"
            "  relation: manifests_as\n"
            "  weight: 0.9\n"
        )
        caused_by_yaml = (
            "- source: wiki/fault/f1\n"
            "  target: wiki/component/c1\n"
            "  relation: caused_by\n"
            "  weight: 0.85\n"
        )

        def read_side_effect(uri: str) -> str:
            mapping = {
                "viking://graph/entity_rel/manifests_as.yaml": manifests_as_yaml,
                "viking://graph/entity_rel/caused_by.yaml": caused_by_yaml,
                "viking://graph/entity_rel/leads_to.yaml": "",
                "viking://graph/entity_rel/co_occurs_with.yaml": "",
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_fault_symptom_graph")

        ctx = _make_ctx()
        result = await tool(ctx)

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert "wiki/fault/f1" in data["faults"]
        assert "wiki/symptom/s1" in data["symptoms"]
        assert "wiki/component/c1" in data["components"]


# ---- TestGetEffectiveKnowledge ----


class TestGetEffectiveKnowledge:
    """Tests for agentdb_get_effective_knowledge."""

    async def test_no_variant(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Return base wiki content when no variant file exists."""
        wiki_content = """---
title: 泵故障
version: 2
---

## 故障描述

主泵压力不足。

## 排查步骤

1. 检查先导压力
"""
        mock_client.read = AsyncMock(return_value=wiki_content)
        mock_client.ls = AsyncMock(return_value=[])
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_effective_knowledge")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity="sany/excavator/sy75c",
            uri="viking://wiki/fault/pump_failure.md",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["has_variant"] is False
        assert data["variant_uri"] is None
        assert data["merge_operations"] == []
        assert data["base_version"] == 2
        assert "故障描述" in data["content"]

    async def test_with_variant_override(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Merge wiki body with a variant file that overrides a section."""
        wiki_content = """---
title: 泵故障
version: 2
---

## 故障描述

通用描述。

## 排查步骤

通用步骤。
"""
        variant_content = """---
knowledge: wiki/fault/pump_failure.md
version: 1
---

## 故障描述

SY75C 特有描述。
"""
        # Mock: first read is wiki, second read is variant file
        read_calls: list[str] = []

        async def read_side_effect(uri: str) -> str:
            read_calls.append(uri)
            if uri == "viking://wiki/fault/pump_failure.md":
                return wiki_content
            if uri == "viking://catalog/sany/excavator/sy75c/variant/pump_failure_variant.md":
                return variant_content
            return ""

        mock_client.read = AsyncMock(side_effect=read_side_effect)
        mock_client.ls = AsyncMock(
            return_value=[
                {"name": "pump_failure_variant.md", "is_dir": False},
            ]
        )
        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_get_effective_knowledge")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity="sany/excavator/sy75c",
            uri="viking://wiki/fault/pump_failure.md",
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["has_variant"] is True
        assert data["variant_uri"] is not None
        assert "variant" in data["variant_uri"]
        assert len(data["merge_operations"]) > 0
        assert "SY75C 特有描述" in data["content"]
        assert data["base_version"] == 2


# ---- TestQueryApplicability ----


class TestQueryApplicability:
    """Tests for agentdb_query_applicability."""

    async def test_basic_applicability(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Query applicable knowledge entities for a device."""
        bom_markdown = """# BOM SY75C

| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |
|------|---------|--------|--------|------|-----------|------------|
| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |
"""
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

        def read_side_effect(uri: str) -> str:
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

        # Mock abstract calls for each entity
        async def abstract_side_effect(uri: str) -> str:
            abstracts: dict[str, str] = {
                "viking://wiki/fault/pump_failure": "---\ntitle: 泵故障\ntype: fault\ncredibility: high\nversion: 2\nstatus: active\n---\nAbstract.",
                "viking://wiki/symptom/no_pressure": "---\ntitle: 无压力\ntype: symptom\ncredibility: high\nversion: 1\nstatus: active\n---\nAbstract.",
                "viking://wiki/opl/fix_pump": "---\ntitle: 修复泵\ntype: opl\ncredibility: medium\nversion: 1\nstatus: active\n---\nAbstract.",
                "viking://wiki/component/k3v_k3v63dt.md": "---\ntitle: 主泵\ntype: component\ncredibility: high\nversion: 1\nstatus: active\n---\nAbstract.",
            }
            return abstracts.get(uri, "")

        mock_client.abstract = AsyncMock(side_effect=abstract_side_effect)

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_query_applicability")

        ctx = _make_ctx()
        result = await tool(ctx, identity_node="sany/excavator/sy75c")

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data["identity_node"] == "sany/excavator/sy75c"
        assert len(data["items"]) > 0
        types_in_items = {item["type"] for item in data["items"]}
        assert "fault" in types_in_items
        assert "symptom" in types_in_items
        assert "opl" in types_in_items
        assert data["coverage"]["fault"] >= 1
        assert data["coverage"]["symptom"] >= 1
        assert data["coverage"]["opl"] >= 1

    async def test_filter_by_type(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Filter applicability results by knowledge type."""
        bom_markdown = """# BOM SY75C

| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |
|------|---------|--------|--------|------|-----------|------------|
| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |
"""
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

        def read_side_effect(uri: str) -> str:
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
                "viking://wiki/fault/pump_failure": "---\ntitle: 泵故障\ncredibility: high\nversion: 2\nstatus: active\n---\nAbstract.",
                "viking://wiki/symptom/no_pressure": "---\ntitle: 无压力\ncredibility: high\nversion: 1\nstatus: active\n---\nAbstract.",
                "viking://wiki/opl/fix_pump": "---\ntitle: 修复泵\ncredibility: medium\nversion: 1\nstatus: active\n---\nAbstract.",
                "viking://wiki/component/k3v_k3v63dt.md": "---\ntitle: 主泵\ncredibility: high\nversion: 1\nstatus: active\n---\nAbstract.",
            }
            return abstracts.get(uri, "")

        mock_client.abstract = AsyncMock(side_effect=abstract_side_effect)

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_query_applicability")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity_node="sany/excavator/sy75c",
            knowledge_types=["fault"],
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        types_in_items = {item["type"] for item in data["items"]}
        assert types_in_items == {"fault"}
        assert data["coverage"]["fault"] >= 1
        assert data["coverage"]["symptom"] == 0

    async def test_exclude_disputed(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Exclude entities with low credibility or disputed status."""
        bom_markdown = """# BOM SY75C

| 系统 | 组件名称 | 组件ID | 物料号 | 数量 | class_ref | ecu_family |
|------|---------|--------|--------|------|-----------|------------|
| 液压系统 | 主泵 | k3v:k3v63dt | K3V63DT-1234 | 1 | axial_piston_pump | None |
"""
        caused_by_yaml = (
            "- source: wiki/fault/good_fault\n"
            "  target: wiki/component/k3v_k3v63dt\n"
            "  relation: caused_by\n"
            "  weight: 0.9\n"
            "- source: wiki/fault/bad_fault\n"
            "  target: wiki/component/k3v_k3v63dt\n"
            "  relation: caused_by\n"
            "  weight: 0.5\n"
        )
        manifests_as_yaml = ""
        addresses_yaml = ""
        confirmed_by_yaml = ""
        repaired_by_yaml = ""

        def read_side_effect(uri: str) -> str:
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
                "viking://wiki/fault/good_fault": "---\ntitle: Good\ncredibility: high\nversion: 1\nstatus: active\n---\nA.",
                "viking://wiki/fault/bad_fault": "---\ntitle: Bad\ncredibility: low\nversion: 1\nstatus: active\n---\nB.",
                "viking://wiki/component/k3v_k3v63dt.md": "---\ntitle: Pump\ncredibility: high\nversion: 1\nstatus: active\n---\nC.",
            }
            return abstracts.get(uri, "")

        mock_client.abstract = AsyncMock(side_effect=abstract_side_effect)

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_query_applicability")

        ctx = _make_ctx()
        result = await tool(
            ctx,
            identity_node="sany/excavator/sy75c",
            exclude_disputed=True,
        )

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        uris = {item["uri"] for item in data["items"]}
        assert "viking://wiki/fault/good_fault" in uris
        assert "viking://wiki/fault/bad_fault" not in uris


# ---- Tool count test ----


def test_build_schema_read_tools_returns_expected_tools(
    agent_db_cap: AgentDBCapability,
) -> None:
    """build_schema_read_tools returns expected tool functions."""
    tools = build_schema_read_tools(agent_db_cap)
    names = {t.__name__ for t in tools}
    expected = {
        "agentdb_resolve_identity",
        "agentdb_traverse_bom",
        "agentdb_get_fault_symptom_graph",
        "agentdb_get_effective_knowledge",
        "agentdb_query_applicability",
    }
    expected.add("agentdb_list_pending")
    assert names == expected
    assert len(tools) == len(expected)


# ---- Visibility test ----


async def test_schema_read_tool_visibility_blocked(
    mock_viking: VikingCapability,
) -> None:
    """Schema-read tools respect URI prefix visibility."""
    from agentpool.capabilities.agent_db import AgentDBCapability

    cap = AgentDBCapability(
        viking=mock_viking,
        allowed_prefixes=("viking://wiki/",),  # Only wiki allowed
        mode="read",
    )
    tools = build_schema_read_tools(cap)
    bom_tool = _get_tool(tools, "agentdb_traverse_bom")

    ctx = _make_ctx()
    # BOM is under viking://catalog/ which is not allowed
    result = await bom_tool(ctx, identity_node="sany/excavator/sy75c")

    assert isinstance(result, ToolReturn)
    assert "denied" in str(result.return_value).lower()


# ---- TestListPending ----


class TestListPending:
    """Tests for agentdb_list_pending QT query tool."""

    async def test_list_pending_basic(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """List pending QTs across tickets/opa/, tickets/ops/, tickets/opl_proposal/."""
        opa_content = "---\ntype: opa\ntitle: OPA-001\nticket_status: open\nexpert_owner: expert_a\ncreated_at: 2026-01-15\ndescription: Test OPA\n---\nBody."
        ops_content = "---\ntype: ops\ntitle: OPS-001\nticket_status: reviewing\nexpert_owner: expert_b\ncreated_at: 2026-02-01\ndescription: Test OPS\n---\nBody."
        opl_content = "---\ntype: opl_proposal\ntitle: OPL-001\nticket_status: approved\nexpert_owner: expert_a\ncreated_at: 2026-01-10\ndescription: Test OPL\n---\nBody."

        # Mock ls for tickets/ subdirectories
        ls_calls: list[str] = []

        async def ls_side_effect(uri: str) -> list[Any]:
            ls_calls.append(uri)
            if uri == "viking://tickets/":
                return [
                    {"name": "opa/", "is_dir": True},
                    {"name": "ops/", "is_dir": True},
                    {"name": "opl_proposal/", "is_dir": True},
                ]
            if uri == "viking://tickets/opa/":
                return [{"name": "opa-001.md", "is_dir": False}]
            if uri == "viking://tickets/ops/":
                return [{"name": "ops-001.md", "is_dir": False}]
            if uri == "viking://tickets/opl_proposal/":
                return [{"name": "opl-001.md", "is_dir": False}]
            return []

        mock_client.ls = AsyncMock(side_effect=ls_side_effect)

        async def read_side_effect(uri: str) -> str:
            mapping: dict[str, str] = {
                "viking://tickets/opa/opa-001.md": opa_content,
                "viking://tickets/ops/ops-001.md": ops_content,
                "viking://tickets/opl_proposal/opl-001.md": opl_content,
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_list_pending")

        ctx = _make_ctx()
        result = await tool(ctx)

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert isinstance(data, list)
        assert len(data) == 3
        qt_types = {item["qt_type"] for item in data}
        assert qt_types == {"opa", "ops", "opl_proposal"}
        statuses = {item["ticket_status"] for item in data}
        assert "open" in statuses
        assert "reviewing" in statuses
        assert "approved" in statuses

    async def test_list_pending_filter_by_type(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Filter pending QTs by qt_type parameter."""

        async def ls_side_effect(uri: str) -> list[Any]:
            if uri == "viking://tickets/":
                return [
                    {"name": "opa/", "is_dir": True},
                    {"name": "ops/", "is_dir": True},
                    {"name": "opl_proposal/", "is_dir": True},
                ]
            if uri == "viking://tickets/opa/":
                return [{"name": "opa-001.md", "is_dir": False}]
            if uri == "viking://tickets/ops/":
                return [{"name": "ops-001.md", "is_dir": False}]
            if uri == "viking://tickets/opl_proposal/":
                return [{"name": "opl-001.md", "is_dir": False}]
            return []

        mock_client.ls = AsyncMock(side_effect=ls_side_effect)

        async def read_side_effect(uri: str) -> str:
            mapping: dict[str, str] = {
                "viking://tickets/opa/opa-001.md": "---\ntype: opa\ntitle: OPA-001\nticket_status: open\ncreated_at: 2026-01-15\n---\nBody.",
                "viking://tickets/ops/ops-001.md": "---\ntype: ops\ntitle: OPS-001\nticket_status: open\ncreated_at: 2026-02-01\n---\nBody.",
                "viking://tickets/opl_proposal/opl-001.md": "---\ntype: opl_proposal\ntitle: OPL-001\nticket_status: approved\ncreated_at: 2026-01-10\n---\nBody.",
            }
            return mapping.get(uri, "")

        mock_client.read = AsyncMock(side_effect=read_side_effect)

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_list_pending")

        ctx = _make_ctx()
        result = await tool(ctx, qt_type="opa")

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert len(data) == 1
        assert data[0]["qt_type"] == "opa"
        assert data[0]["title"] == "OPA-001"

    async def test_list_pending_empty(
        self,
        mock_client: AsyncMock,
        agent_db_cap: AgentDBCapability,
    ) -> None:
        """Return empty list when no QTs exist."""
        mock_client.ls = AsyncMock(return_value=[])
        mock_client.read = AsyncMock(return_value="")

        tools = build_schema_read_tools(agent_db_cap)
        tool = _get_tool(tools, "agentdb_list_pending")

        ctx = _make_ctx()
        result = await tool(ctx)

        assert isinstance(result, ToolReturn)
        data = json.loads(result.return_value)
        assert data == []
