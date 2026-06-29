"""Tests for v2 SessionUpdate types and three-state patch fields."""

from __future__ import annotations

import pytest

from acp.schema.content_blocks import TextContentBlock
from acp_v2.schema import (
    AgentMessage,
    AgentMessageChunk,
    StateUpdate,
    ToolCallUpdate,
    UserMessage,
    UserMessageChunk,
)
from acp_v2.schema._unset import _UNSET, is_unset


class TestUnsetSentinel:
    """Verify the UNSET sentinel behaves correctly."""

    @pytest.mark.unit
    def test_unset_is_falsy(self) -> None:
        assert not _UNSET
        assert not is_unset(None)
        assert is_unset(_UNSET)

    @pytest.mark.unit
    def test_unset_is_singleton(self) -> None:
        from acp_v2.schema._unset import UnsetType
        assert UnsetType() is _UNSET


class TestWholeMessageUpsert:
    """Verify whole-message upsert serialization."""

    @pytest.mark.unit
    def test_agent_message_with_content(self) -> None:
        msg = AgentMessage(
            message_id="m1",
            content=[TextContentBlock(text="Hello")],
        )
        dumped = msg.model_dump(by_alias=True, exclude_none=True)
        assert dumped["sessionUpdate"] == "agent_message"
        assert dumped["messageId"] == "m1"
        assert len(dumped["content"]) == 1

    @pytest.mark.unit
    def test_agent_message_without_content_is_unset(self) -> None:
        msg = AgentMessage(message_id="m1")
        dumped = msg.model_dump(by_alias=True, exclude_none=True)
        assert "content" not in dumped

    @pytest.mark.unit
    def test_user_message_serialization(self) -> None:
        msg = UserMessage(
            message_id="u1",
            content=[TextContentBlock(text="Question")],
        )
        dumped = msg.model_dump(by_alias=True, exclude_none=True)
        assert dumped["sessionUpdate"] == "user_message"
        assert dumped["messageId"] == "u1"


class TestChunksRequireMessageId:
    """Verify v2 chunks require messageId (unlike v1 where it was optional)."""

    @pytest.mark.unit
    def test_agent_message_chunk_requires_message_id(self) -> None:
        with pytest.raises(Exception):
            AgentMessageChunk(content=TextContentBlock(text="hi"))  # type: ignore[call-arg]

    @pytest.mark.unit
    def test_agent_message_chunk_with_message_id(self) -> None:
        chunk = AgentMessageChunk(
            message_id="m1",
            content=TextContentBlock(text="hi"),
        )
        dumped = chunk.model_dump(by_alias=True, exclude_none=True)
        assert dumped["messageId"] == "m1"

    @pytest.mark.unit
    def test_user_message_chunk_with_message_id(self) -> None:
        chunk = UserMessageChunk(
            message_id="u1",
            content=TextContentBlock(text="q"),
        )
        dumped = chunk.model_dump(by_alias=True, exclude_none=True)
        assert dumped["messageId"] == "u1"


class TestToolCallUpdate:
    """Verify unified tool_call_update upsert behavior."""

    @pytest.mark.unit
    def test_tool_call_update_with_title(self) -> None:
        tc = ToolCallUpdate(
            tool_call_id="tc1",
            title="Reading file",
            kind="read",
            status="pending",
        )
        dumped = tc.model_dump(by_alias=True, exclude_none=True)
        assert dumped["sessionUpdate"] == "tool_call_update"
        assert dumped["toolCallId"] == "tc1"
        assert dumped["title"] == "Reading file"
        assert dumped["kind"] == "read"
        assert dumped["status"] == "pending"

    @pytest.mark.unit
    def test_tool_call_update_patch_only_status(self) -> None:
        tc = ToolCallUpdate(
            tool_call_id="tc1",
            status="in_progress",
        )
        dumped = tc.model_dump(by_alias=True, exclude_none=True)
        assert "title" not in dumped
        assert "kind" not in dumped
        assert dumped["status"] == "in_progress"


class TestStateUpdate:
    """Verify state_update notification types."""

    @pytest.mark.unit
    def test_running_state(self) -> None:
        su = StateUpdate(state="running")
        dumped = su.model_dump(by_alias=True, exclude_none=True)
        assert dumped["sessionUpdate"] == "state_update"
        assert dumped["state"] == "running"

    @pytest.mark.unit
    def test_idle_with_stop_reason(self) -> None:
        su = StateUpdate(state="idle", stop_reason="end_turn")
        dumped = su.model_dump(by_alias=True, exclude_none=True)
        assert dumped["state"] == "idle"
        assert dumped["stopReason"] == "end_turn"

    @pytest.mark.unit
    def test_requires_action(self) -> None:
        su = StateUpdate(state="requires_action")
        dumped = su.model_dump(by_alias=True, exclude_none=True)
        assert dumped["state"] == "requires_action"
        assert "stopReason" not in dumped
