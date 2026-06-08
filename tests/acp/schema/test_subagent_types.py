"""TDD tests for ACP subagent schema types (RFC-0042 Phase 1)."""

from __future__ import annotations

import pytest

from acp.schema import (
    SubagentCapabilities,
    SubagentInfo,
    SubagentRunInfo,
    ToolCallKind,
)
from acp.schema.agent_responses import (
    ForkSessionResponse,
    LoadSessionResponse,
    NewSessionResponse,
    ResumeSessionResponse,
)
from acp.schema.session_state import SessionInfo
from acp.schema.session_updates import ToolCallProgress, ToolCallStart
from acp.schema.tool_call import ToolCallStatus


# =============================================================================
# PromptDelegation tests (T11)
# =============================================================================


def test_prompt_delegation_importable() -> None:
    """PromptDelegation must be importable from acp.schema."""
    from acp.schema import PromptDelegation

    assert PromptDelegation is not None


def test_prompt_delegation_auto_policy() -> None:
    """PromptDelegation should accept 'auto' policy."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="auto")
    assert d.policy == "auto"
    assert d.subagent_id is None
    assert d.run_mode is None


def test_prompt_delegation_disable_policy() -> None:
    """PromptDelegation should accept 'disable' policy."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="disable")
    assert d.policy == "disable"


def test_prompt_delegation_prefer_policy() -> None:
    """PromptDelegation should accept 'prefer' policy."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="prefer")
    assert d.policy == "prefer"


def test_prompt_delegation_require_policy() -> None:
    """PromptDelegation should accept 'require' policy."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="require")
    assert d.policy == "require"


def test_prompt_delegation_with_subagent_id() -> None:
    """PromptDelegation should accept subagent_id."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="require", subagent_id="sub_001")
    assert d.subagent_id == "sub_001"


def test_prompt_delegation_with_run_mode_foreground() -> None:
    """PromptDelegation should accept foreground run_mode."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="prefer", run_mode="foreground")
    assert d.run_mode == "foreground"


def test_prompt_delegation_with_run_mode_background() -> None:
    """PromptDelegation should accept background run_mode."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="prefer", run_mode="background")
    assert d.run_mode == "background"


def test_prompt_delegation_full() -> None:
    """PromptDelegation should accept all fields together."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="require", subagent_id="sub_001", run_mode="background")
    assert d.policy == "require"
    assert d.subagent_id == "sub_001"
    assert d.run_mode == "background"


def test_prompt_delegation_json_roundtrip() -> None:
    """PromptDelegation should serialize and deserialize correctly."""
    from acp.schema import PromptDelegation

    d = PromptDelegation(policy="prefer", subagent_id="sub_002", run_mode="foreground")
    json_data = d.model_dump(mode="json")
    assert json_data["policy"] == "prefer"
    assert json_data["subagent_id"] == "sub_002"
    assert json_data["run_mode"] == "foreground"

    restored = PromptDelegation.model_validate(json_data)
    assert restored.policy == "prefer"
    assert restored.subagent_id == "sub_002"
    assert restored.run_mode == "foreground"


def test_prompt_request_accepts_delegation() -> None:
    """PromptRequest should accept an optional delegation field."""
    from acp.schema import PromptDelegation, PromptRequest
    from acp.schema.content_blocks import TextContentBlock

    delegation = PromptDelegation(policy="auto")
    req = PromptRequest(
        session_id="sess_001",
        prompt=[TextContentBlock(text="Hello")],
        delegation=delegation,
    )
    assert req.delegation is not None
    assert req.delegation.policy == "auto"


def test_prompt_request_delegation_none_by_default() -> None:
    """PromptRequest delegation should default to None."""
    from acp.schema import PromptRequest
    from acp.schema.content_blocks import TextContentBlock

    req = PromptRequest(
        session_id="sess_001",
        prompt=[TextContentBlock(text="Hello")],
    )
    assert req.delegation is None


def test_prompt_request_with_delegation_json_roundtrip() -> None:
    """PromptRequest with delegation should serialize and deserialize."""
    from acp.schema import PromptDelegation, PromptRequest
    from acp.schema.content_blocks import TextContentBlock

    delegation = PromptDelegation(policy="require", subagent_id="sub_001", run_mode="background")
    req = PromptRequest(
        session_id="sess_001",
        prompt=[TextContentBlock(text="Hello")],
        delegation=delegation,
    )
    json_data = req.model_dump(mode="json")
    assert json_data["delegation"]["policy"] == "require"
    assert json_data["delegation"]["subagent_id"] == "sub_001"
    assert json_data["delegation"]["run_mode"] == "background"

    restored = PromptRequest.model_validate(json_data)
    assert restored.delegation is not None
    assert restored.delegation.policy == "require"
    assert restored.delegation.subagent_id == "sub_001"
    assert restored.delegation.run_mode == "background"


# =============================================================================
# ToolCallKind tests
# =============================================================================


def test_tool_call_kind_includes_subagent() -> None:
    """ToolCallKind Literal must include 'subagent'."""
    from typing import get_args

    kinds = get_args(ToolCallKind)
    assert "subagent" in kinds


def test_tool_call_kind_subagent_is_valid_value() -> None:
    """'subagent' should be assignable to ToolCallKind."""
    kind: ToolCallKind = "subagent"
    assert kind == "subagent"


def test_tool_kind_definitions_match() -> None:
    """ACP ToolCallKind and AgentPool ToolKind must have identical members."""
    from acp.schema.tool_call import ToolCallKind
    from agentpool.tools.base import ToolKind

    assert set(ToolCallKind.__args__) == set(ToolKind.__args__)


# =============================================================================
# SubagentRunInfo tests
# =============================================================================


def test_subagent_run_info_defaults() -> None:
    """SubagentRunInfo should create with required fields only."""
    info = SubagentRunInfo(subagent_id="sub_001", name="coder")
    assert info.subagent_id == "sub_001"
    assert info.name == "coder"
    assert info.description is None
    assert info.status is None
    assert info.depth is None


def test_subagent_run_info_full() -> None:
    """SubagentRunInfo should accept all fields."""
    info = SubagentRunInfo(
        subagent_id="sub_001",
        name="coder",
        description="A coding subagent",
        status="running",
        depth=1,
    )
    assert info.description == "A coding subagent"
    assert info.status == "running"
    assert info.depth == 1


def test_subagent_run_info_json_roundtrip() -> None:
    """SubagentRunInfo should serialize/deserialize correctly."""
    info = SubagentRunInfo(
        subagent_id="sub_001",
        name="coder",
        description="A coding subagent",
        status="completed",
        depth=2,
    )
    json_data = info.model_dump(mode="json")
    assert json_data["subagent_id"] == "sub_001"
    assert json_data["name"] == "coder"
    assert json_data["description"] == "A coding subagent"
    assert json_data["status"] == "completed"
    assert json_data["depth"] == 2

    restored = SubagentRunInfo.model_validate(json_data)
    assert restored.subagent_id == "sub_001"
    assert restored.name == "coder"


def test_subagent_run_info_depth_must_be_non_negative() -> None:
    """SubagentRunInfo depth must be >= 0."""
    with pytest.raises(ValueError):
        SubagentRunInfo(subagent_id="sub_001", name="coder", depth=-1)


# =============================================================================
# SessionInfo hierarchy tests
# =============================================================================


def test_session_info_hierarchy_defaults() -> None:
    """SessionInfo hierarchy fields should default to None."""
    info = SessionInfo(session_id="sess_001", cwd="/tmp")
    assert info.parent_session_id is None
    assert info.child_session_ids is None
    assert info.depth is None


def test_session_info_with_parent() -> None:
    """SessionInfo should accept parent_session_id."""
    info = SessionInfo(
        session_id="sess_002",
        cwd="/tmp",
        parent_session_id="sess_001",
    )
    assert info.parent_session_id == "sess_001"


def test_session_info_with_children() -> None:
    """SessionInfo should accept child_session_ids."""
    info = SessionInfo(
        session_id="sess_001",
        cwd="/tmp",
        child_session_ids=["sess_002", "sess_003"],
    )
    assert info.child_session_ids == ["sess_002", "sess_003"]


def test_session_info_with_depth() -> None:
    """SessionInfo should accept depth."""
    info = SessionInfo(session_id="sess_002", cwd="/tmp", depth=1)
    assert info.depth == 1


def test_session_info_json_roundtrip() -> None:
    """SessionInfo should serialize hierarchy fields."""
    info = SessionInfo(
        session_id="sess_002",
        cwd="/tmp",
        parent_session_id="sess_001",
        child_session_ids=["sess_003"],
        depth=1,
    )
    json_data = info.model_dump(mode="json")
    assert json_data["parent_session_id"] == "sess_001"
    assert json_data["child_session_ids"] == ["sess_003"]
    assert json_data["depth"] == 1

    restored = SessionInfo.model_validate(json_data)
    assert restored.parent_session_id == "sess_001"
    assert restored.child_session_ids == ["sess_003"]
    assert restored.depth == 1


def test_session_info_depth_must_be_non_negative() -> None:
    """SessionInfo depth must be >= 0."""
    with pytest.raises(ValueError):
        SessionInfo(session_id="sess_001", cwd="/tmp", depth=-1)


# =============================================================================
# SubagentCapabilities tests
# =============================================================================


def test_subagent_capabilities_defaults() -> None:
    """SubagentCapabilities should default to False."""
    caps = SubagentCapabilities()
    assert caps.streaming is False
    assert caps.tools is False
    assert caps.delegation is False


def test_subagent_capabilities_all_true() -> None:
    """SubagentCapabilities should accept all True."""
    caps = SubagentCapabilities(streaming=True, tools=True, delegation=True)
    assert caps.streaming is True
    assert caps.tools is True
    assert caps.delegation is True


def test_subagent_capabilities_json_roundtrip() -> None:
    """SubagentCapabilities should serialize with camelCase."""
    caps = SubagentCapabilities(streaming=True, tools=False, delegation=True)
    json_data = caps.model_dump(mode="json")
    assert json_data["streaming"] is True
    assert json_data["tools"] is False
    assert json_data["delegation"] is True

    restored = SubagentCapabilities.model_validate(json_data)
    assert restored.streaming is True
    assert restored.tools is False
    assert restored.delegation is True


# =============================================================================
# SubagentInfo tests
# =============================================================================


def test_subagent_info_defaults() -> None:
    """SubagentInfo should create with required fields only."""
    info = SubagentInfo(subagent_id="sub_001", name="coder")
    assert info.subagent_id == "sub_001"
    assert info.name == "coder"
    assert info.description is None
    assert info.capabilities is None


def test_subagent_info_with_capabilities() -> None:
    """SubagentInfo should accept capabilities."""
    caps = SubagentCapabilities(streaming=True, tools=True)
    info = SubagentInfo(
        subagent_id="sub_001",
        name="coder",
        description="A coding subagent",
        capabilities=caps,
    )
    assert info.description == "A coding subagent"
    assert info.capabilities is not None
    assert info.capabilities.streaming is True
    assert info.capabilities.tools is True


def test_subagent_info_json_roundtrip() -> None:
    """SubagentInfo should serialize."""
    info = SubagentInfo(
        subagent_id="sub_001",
        name="coder",
        description="A coding subagent",
        capabilities=SubagentCapabilities(streaming=True),
    )
    json_data = info.model_dump(mode="json")
    assert json_data["subagent_id"] == "sub_001"
    assert json_data["name"] == "coder"
    assert json_data["description"] == "A coding subagent"
    assert json_data["capabilities"]["streaming"] is True

    restored = SubagentInfo.model_validate(json_data)
    assert restored.subagent_id == "sub_001"
    assert restored.capabilities is not None
    assert restored.capabilities.streaming is True


# =============================================================================
# ToolCallStart / ToolCallProgress subagent field tests
# =============================================================================


def test_tool_call_start_with_subagent() -> None:
    """ToolCallStart should accept an optional subagent field."""
    subagent = SubagentRunInfo(subagent_id="sub_001", name="coder", status="running")
    start = ToolCallStart(
        tool_call_id="tc_001",
        title="Running subagent",
        subagent=subagent,
    )
    assert start.subagent is not None
    assert start.subagent.subagent_id == "sub_001"
    assert start.subagent.name == "coder"


def test_tool_call_start_subagent_none_by_default() -> None:
    """ToolCallStart subagent field should default to None."""
    start = ToolCallStart(tool_call_id="tc_001", title="Regular tool")
    assert start.subagent is None


def test_tool_call_progress_with_subagent() -> None:
    """ToolCallProgress should accept an optional subagent field."""
    subagent = SubagentRunInfo(subagent_id="sub_001", name="coder", status="completed")
    progress = ToolCallProgress(
        tool_call_id="tc_001",
        status="completed",
        subagent=subagent,
    )
    assert progress.subagent is not None
    assert progress.subagent.status == "completed"


def test_tool_call_progress_subagent_none_by_default() -> None:
    """ToolCallProgress subagent field should default to None."""
    progress = ToolCallProgress(tool_call_id="tc_001", status="in_progress")
    assert progress.subagent is None


def test_tool_call_start_subagent_json_serialization() -> None:
    """ToolCallStart should serialize subagent field."""
    subagent = SubagentRunInfo(subagent_id="sub_001", name="coder", depth=1)
    start = ToolCallStart(
        tool_call_id="tc_001",
        title="Running subagent",
        subagent=subagent,
    )
    json_data = start.model_dump(mode="json")
    assert json_data["subagent"]["subagent_id"] == "sub_001"
    assert json_data["subagent"]["name"] == "coder"
    assert json_data["subagent"]["depth"] == 1


# =============================================================================
# Lifecycle response available_subagents tests
# =============================================================================


def test_new_session_response_with_available_subagents() -> None:
    """NewSessionResponse should accept available_subagents."""
    subagents = [
        SubagentInfo(subagent_id="sub_001", name="coder"),
        SubagentInfo(subagent_id="sub_002", name="reviewer"),
    ]
    resp = NewSessionResponse(session_id="sess_001", available_subagents=subagents)
    assert resp.available_subagents is not None
    assert len(resp.available_subagents) == 2
    assert resp.available_subagents[0].name == "coder"


def test_new_session_response_available_subagents_none_by_default() -> None:
    """NewSessionResponse available_subagents should default to None."""
    resp = NewSessionResponse(session_id="sess_001")
    assert resp.available_subagents is None


def test_load_session_response_with_available_subagents() -> None:
    """LoadSessionResponse should accept available_subagents."""
    subagents = [SubagentInfo(subagent_id="sub_001", name="coder")]
    resp = LoadSessionResponse(available_subagents=subagents)
    assert resp.available_subagents is not None
    assert len(resp.available_subagents) == 1


def test_fork_session_response_with_available_subagents() -> None:
    """ForkSessionResponse should accept available_subagents."""
    subagents = [SubagentInfo(subagent_id="sub_001", name="coder")]
    resp = ForkSessionResponse(session_id="sess_002", available_subagents=subagents)
    assert resp.available_subagents is not None


def test_resume_session_response_with_available_subagents() -> None:
    """ResumeSessionResponse should accept available_subagents."""
    subagents = [SubagentInfo(subagent_id="sub_001", name="coder")]
    resp = ResumeSessionResponse(available_subagents=subagents)
    assert resp.available_subagents is not None


def test_new_session_response_subagents_json_serialization() -> None:
    """NewSessionResponse should serialize available_subagents field."""
    subagents = [
        SubagentInfo(
            subagent_id="sub_001",
            name="coder",
            capabilities=SubagentCapabilities(streaming=True),
        ),
    ]
    resp = NewSessionResponse(session_id="sess_001", available_subagents=subagents)
    json_data = resp.model_dump(mode="json")
    assert json_data["available_subagents"] is not None
    assert len(json_data["available_subagents"]) == 1
    assert json_data["available_subagents"][0]["subagent_id"] == "sub_001"
    assert json_data["available_subagents"][0]["capabilities"]["streaming"] is True


# =============================================================================
# Export tests
# =============================================================================


def test_all_new_types_exported_from_acp_schema() -> None:
    """All new subagent types must be importable from acp.schema."""
    from acp.schema import (
        PromptDelegation,
        SubagentCapabilities,
        SubagentInfo,
        SubagentRunInfo,
    )

    assert SubagentRunInfo is not None
    assert SubagentInfo is not None
    assert SubagentCapabilities is not None
    assert PromptDelegation is not None


def test_tool_call_start_has_subagent_field() -> None:
    """ToolCallStart model fields must include subagent."""
    fields = ToolCallStart.model_fields
    assert "subagent" in fields


def test_tool_call_progress_has_subagent_field() -> None:
    """ToolCallProgress model fields must include subagent."""
    fields = ToolCallProgress.model_fields
    assert "subagent" in fields


def test_session_info_has_hierarchy_fields() -> None:
    """SessionInfo model fields must include hierarchy fields."""
    fields = SessionInfo.model_fields
    assert "parent_session_id" in fields
    assert "child_session_ids" in fields
    assert "depth" in fields


def test_new_session_response_has_available_subagents_field() -> None:
    """NewSessionResponse model fields must include available_subagents."""
    fields = NewSessionResponse.model_fields
    assert "available_subagents" in fields
