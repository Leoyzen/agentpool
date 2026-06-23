"""Tests for skill module logging functionality."""

from __future__ import annotations

import logging

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill


@pytest.fixture
def test_skill() -> Skill:
    """Create a test skill."""
    return Skill(
        name="test-skill",
        description="A test skill",
        skill_path=UPath("/tmp/test-skill"),
    )


@pytest.fixture
def test_command(test_skill: Skill) -> SkillCommand:
    """Create a test command."""
    return SkillCommand(
        name="test-command",
        description="A test command",
        skill=test_skill,
    )


def check_log_message(caplog: pytest.LogCaptureFixture, level: int, message_pattern: str) -> bool:
    """Check if a log message pattern exists at the specified level.

    Structlog stores messages with format like:
    "[info     ] Skill command registered: %s   positional_args=('test-cmd',)"

    Args:
        caplog: The log capture fixture.
        level: The logging level to check.
        message_pattern: Pattern to search for in log messages.

    Returns:
        True if the pattern is found at the specified level.
    """
    # Access caplog.text to ensure records are populated
    _ = caplog.text

    for record in caplog.records:
        if record.levelno == level:
            msg_str = str(record.msg)
            if message_pattern in msg_str:
                return True
    return False


class TestCommandRegistryLogging:
    """Tests for SkillCommandRegistry logging."""

    def test_initialization_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        """Test that registry initialization logs at DEBUG level."""
        from agentpool.skills.command_registry import SkillCommandRegistry

        with caplog.at_level(logging.DEBUG):
            _registry = SkillCommandRegistry()

        assert check_log_message(caplog, logging.DEBUG, "Initializing skill command registry")

    def test_register_logs_info(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command registration logs at INFO level."""
        from agentpool.skills.command_registry import SkillCommandRegistry

        registry = SkillCommandRegistry()

        with caplog.at_level(logging.INFO):
            registry.register("test-cmd", test_command)

        # Check for base message (without interpolated value)
        assert check_log_message(caplog, logging.INFO, "Skill command registered")

    def test_remove_logs_info(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command removal logs at INFO level."""
        from agentpool.skills.command_registry import SkillCommandRegistry

        registry = SkillCommandRegistry()
        registry.register("test-cmd", test_command)

        with caplog.at_level(logging.INFO):
            del registry["test-cmd"]

        # Check for base message (without interpolated value)
        assert check_log_message(caplog, logging.INFO, "Skill command removed")

    @pytest.mark.asyncio
    async def test_sync_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_skill: Skill
    ) -> None:
        """Test that sync logs at DEBUG level."""
        from agentpool.skills.command_registry import SkillCommandRegistry

        skills_registry = SkillsRegistry()
        # Register the skill in the registry
        skills_registry.register("test-skill", test_skill)

        registry = SkillCommandRegistry(skills_registry=skills_registry)

        with caplog.at_level(logging.DEBUG):
            await registry.initialize(wait=True)

        assert check_log_message(caplog, logging.INFO, "Synced")
        # "initial commands from SkillsRegistry" log was removed in refactoring


class TestACPSkillBridgeLogging:
    """Tests for ACPSkillBridge logging."""

    def test_command_conversion_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command conversion logs at DEBUG level."""
        from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge

        bridge = ACPSkillBridge()

        with caplog.at_level(logging.DEBUG):
            bridge.handle_change("test-cmd", test_command)

        # Check for base message patterns (without interpolated values)
        assert check_log_message(caplog, logging.DEBUG, "Converting skill command")
        assert check_log_message(caplog, logging.DEBUG, "to ACP format")
        assert check_log_message(caplog, logging.DEBUG, "ACPSkillBridge has")

    def test_command_removal_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command removal logs at DEBUG level."""
        from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge

        bridge = ACPSkillBridge()
        bridge.handle_change("test-cmd", test_command)

        with caplog.at_level(logging.DEBUG):
            bridge.handle_change("test-cmd", None)

        assert check_log_message(caplog, logging.DEBUG, "ACPSkillBridge has")


class TestAGUISkillBridgeLogging:
    """Tests for AGUISkillBridge logging."""

    def test_command_conversion_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command conversion logs at DEBUG level."""
        from agentpool_server.agui_server.skill_tools import AGUISkillBridge

        bridge = AGUISkillBridge()

        with caplog.at_level(logging.DEBUG):
            bridge.handle_change("test-cmd", test_command)

        # Check for base message patterns (without interpolated values)
        assert check_log_message(caplog, logging.DEBUG, "Converting skill command")
        assert check_log_message(caplog, logging.DEBUG, "to AG-UI Tool")
        assert check_log_message(caplog, logging.DEBUG, "AGUISkillBridge has")

    def test_tool_removal_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that tool removal logs at DEBUG level."""
        from agentpool_server.agui_server.skill_tools import AGUISkillBridge

        bridge = AGUISkillBridge()
        bridge.handle_change("test-cmd", test_command)

        with caplog.at_level(logging.DEBUG):
            bridge.handle_change("test-cmd", None)

        assert check_log_message(caplog, logging.DEBUG, "AGUISkillBridge has")


class TestOpenCodeSkillBridgeLogging:
    """Tests for OpenCodeSkillBridge logging."""

    def test_create_logs_debug(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command creation logs at DEBUG level."""
        from agentpool_server.opencode_server.skill_bridge import create_skill_command

        with caplog.at_level(logging.DEBUG):
            _cmd = create_skill_command(test_command)

        assert check_log_message(caplog, logging.DEBUG, "SkillCommand")
        assert check_log_message(caplog, logging.DEBUG, "initialized")

    def test_command_wrap_logs_info(
        self, caplog: pytest.LogCaptureFixture, test_command: SkillCommand
    ) -> None:
        """Test that command wrapping logs at INFO level."""
        from agentpool_server.opencode_server.skill_bridge import OpenCodeSkillBridge

        bridge = OpenCodeSkillBridge()

        with caplog.at_level(logging.INFO):
            bridge.handle_change("test-cmd", test_command)

        assert check_log_message(caplog, logging.INFO, "Skill command wrapped")
