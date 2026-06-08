"""Tests for ACP server converters."""

from __future__ import annotations

import pytest

from agentpool.sessions import SessionData
from agentpool_server.acp_server.converters import to_session_info


async def test_to_session_info_populates_hierarchy_fields() -> None:
    """Test to_session_info includes hierarchy fields in meta."""
    session_data = SessionData(
        session_id="ses_abc123",
        agent_name="test_agent",
        cwd="/tmp/test",
        metadata={
            "title": "Test Session",
            "parent_tool_call_id": "tc_xyz789",
            "subagent_id": "sub_agent_1",
        },
    )

    info = to_session_info(session_data)

    assert info.session_id == "ses_abc123"
    assert info.cwd == "/tmp/test"
    assert info.title == "Test Session"
    assert info.meta is not None
    assert info.meta.get("parent_tool_call_id") == "tc_xyz789"
    assert info.meta.get("subagent_id") == "sub_agent_1"


async def test_to_session_info_without_hierarchy_fields() -> None:
    """Test to_session_info works when hierarchy fields are absent."""
    session_data = SessionData(
        session_id="ses_def456",
        agent_name="test_agent",
        cwd="/tmp/other",
    )

    info = to_session_info(session_data)

    assert info.session_id == "ses_def456"
    assert info.meta is None or info.meta.get("parent_tool_call_id") is None
    assert info.meta is None or info.meta.get("subagent_id") is None


async def test_to_session_info_preserves_other_metadata() -> None:
    """Test to_session_info preserves non-hierarchy metadata in meta."""
    session_data = SessionData(
        session_id="ses_ghi789",
        agent_name="test_agent",
        metadata={
            "custom_key": "custom_value",
            "parent_tool_call_id": "tc_123",
        },
    )

    info = to_session_info(session_data)

    assert info.meta is not None
    assert info.meta.get("custom_key") == "custom_value"
    assert info.meta.get("parent_tool_call_id") == "tc_123"
