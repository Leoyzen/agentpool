"""Tests for OpenCode message models.

Tests the MessageWithParts class and related models.
"""

from __future__ import annotations

from agentpool_server.opencode_server.models import (
    MessagePath,
    MessageTime,
    MessageWithParts,
    TimeCreated,
)


class TestMessageWithPartsRole:
    """Tests for MessageWithParts.role property."""

    def test_user_message_has_role_user(self):
        """User message should have role='user'."""
        msg = MessageWithParts.user(
            message_id="test-user-1",
            session_id="test-session",
            time=TimeCreated(created=1234567890),
            agent_name="test-agent",
        )

        assert msg.role == "user"
        assert msg.info.role == "user"

    def test_assistant_message_has_role_assistant(self):
        """Assistant message should have role='assistant'."""
        msg = MessageWithParts.assistant(
            message_id="test-assistant-1",
            session_id="test-session",
            time=MessageTime(created=1234567890),
            agent_name="test-agent",
            model_id="gpt-4",
            parent_id="",
            provider_id="openai",
            path=MessagePath(cwd="/test", root="/test"),
        )

        assert msg.role == "assistant"
        assert msg.info.role == "assistant"

    def test_role_property_matches_info_role(self):
        """role property should match info.role for both message types."""
        user_msg = MessageWithParts.user(
            message_id="user-1",
            session_id="session-1",
            time=TimeCreated(created=1000),
            agent_name="agent",
        )

        assistant_msg = MessageWithParts.assistant(
            message_id="assistant-1",
            session_id="session-1",
            time=MessageTime(created=1000),
            agent_name="agent",
            model_id="gpt-4",
            parent_id="parent-1",
            provider_id="openai",
            path=MessagePath(cwd="/home", root="/home"),
        )

        # Verify role property delegates to info.role
        assert user_msg.role == user_msg.info.role
        assert assistant_msg.role == assistant_msg.info.role


class TestMessageWithPartsFactories:
    """Tests for MessageWithParts factory methods."""

    def test_user_factory_creates_user_message(self):
        """MessageWithParts.user() should create a user message."""
        msg = MessageWithParts.user(
            message_id="msg-1",
            session_id="session-1",
            time=TimeCreated(created=1234567890000),
            agent_name="test-agent",
        )

        assert msg.info.id == "msg-1"
        assert msg.info.session_id == "session-1"
        assert msg.info.agent == "test-agent"
        assert msg.role == "user"
        assert msg.parts == []

    def test_assistant_factory_creates_assistant_message(self):
        """MessageWithParts.assistant() should create an assistant message."""
        from agentpool_server.opencode_server.models import AssistantMessage

        msg = MessageWithParts.assistant(
            message_id="msg-1",
            session_id="session-1",
            time=MessageTime(created=1234567890000, completed=1234567900000),
            agent_name="test-agent",
            model_id="gpt-4o",
            parent_id="parent-1",
            provider_id="openai",
            path=MessagePath(cwd="/workspace", root="/workspace"),
            mode="default",
            cost=0.001,
            finish="stop",
        )

        # Verify common fields
        assert msg.info.id == "msg-1"
        assert msg.info.session_id == "session-1"
        assert msg.info.agent == "test-agent"
        assert msg.role == "assistant"
        assert msg.parts == []

        # Verify assistant-specific fields (narrow type first)
        assert isinstance(msg.info, AssistantMessage)
        info: AssistantMessage = msg.info
        assert info.model_id == "gpt-4o"
        assert info.parent_id == "parent-1"
        assert info.provider_id == "openai"


class TestMessageWithPartsParts:
    """Tests for MessageWithParts parts manipulation."""

    def test_add_text_part(self):
        """Should be able to add text parts to a message."""
        msg = MessageWithParts.user(
            message_id="msg-1",
            session_id="session-1",
            time=TimeCreated(created=1234567890000),
            agent_name="agent",
        )

        part = msg.add_text_part("Hello, world!")

        assert len(msg.parts) == 1
        assert msg.parts[0] == part
        assert part.text == "Hello, world!"
        assert part.message_id == "msg-1"
        assert part.session_id == "session-1"

    def test_add_multiple_parts(self):
        """Should be able to add multiple parts to a message."""
        msg = MessageWithParts.assistant(
            message_id="msg-1",
            session_id="session-1",
            time=MessageTime(created=1234567890000),
            agent_name="agent",
            model_id="gpt-4",
            parent_id="",
            provider_id="openai",
            path=MessagePath(cwd="/test", root="/test"),
        )

        msg.add_step_start_part()
        msg.add_text_part("Processing...")

        assert len(msg.parts) == 2
