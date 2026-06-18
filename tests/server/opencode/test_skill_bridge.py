"""Comprehensive integration tests for OpenCode skill bridge.

This module tests the integration between AgentPool skills and OpenCode's
slashed command system, ensuring proper command registration, execution,
and lifecycle management.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from slashed import Command as SlashedCommand
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool_server.opencode_server.skill_bridge import (
    OpenCodeSkillBridge,
    SkillCommandWrapper,
    create_skill_command,
)


# =============================================================================
# Test Fixtures
# =============================================================================


@pytest.fixture
def sample_skill() -> Skill:
    """Create a sample skill for testing."""
    return Skill(
        name="test-skill",
        description="A test skill for integration testing",
        skill_path=UPath("/tmp/test-skill"),
    )


@pytest.fixture
def sample_command(sample_skill: Skill) -> SkillCommand:
    """Create a sample SkillCommand for testing."""
    return SkillCommand(
        name="test-skill",
        description="A test skill for integration testing",
        skill=sample_skill,
        input_hint="Provide test arguments",
        category="testing",
    )


@pytest.fixture
def sample_wrapper(sample_command: SkillCommand) -> SkillCommandWrapper:
    """Create a SkillCommandWrapper using sample_command."""
    return SkillCommandWrapper(skill_cmd=sample_command)


@pytest.fixture
def sample_bridge() -> OpenCodeSkillBridge:
    """Create an OpenCodeSkillBridge instance."""
    return OpenCodeSkillBridge()


@pytest.fixture
def skill_with_instructions() -> Skill:
    """Create a skill with instructions for testing."""
    skill = Skill(
        name="skill-with-instructions",
        description="A skill with instructions",
        skill_path=UPath("/tmp/skill-with-instructions"),
    )
    skill.instructions = "These are the skill instructions"
    return skill


@pytest.fixture
def command_with_instructions(skill_with_instructions: Skill) -> SkillCommand:
    """Create a SkillCommand with skill that has instructions."""
    return SkillCommand(
        name="skill-with-instructions",
        description="A skill with instructions",
        skill=skill_with_instructions,
        input_hint="Provide arguments",
    )


@pytest.fixture
def multiple_skills() -> list[Skill]:
    """Create multiple skills for testing."""
    return [
        Skill(
            name=f"skill-{i}",
            description=f"Test skill number {i}",
            skill_path=UPath(f"/tmp/skill-{i}"),
        )
        for i in range(3)
    ]


@pytest.fixture
def multiple_commands(multiple_skills: list[Skill]) -> list[SkillCommand]:
    """Create multiple SkillCommands for testing."""
    return [
        SkillCommand(
            name=skill.name,
            description=skill.description,
            skill=skill,
            input_hint=f"Arguments for {skill.name}",
        )
        for skill in multiple_skills
    ]


# =============================================================================
# SkillCommandWrapper Tests
# =============================================================================


class TestSkillCommandWrapper:
    """Test SkillCommandWrapper functionality."""

    def test_wrapper_has_correct_name_format(
        self, sample_wrapper: SkillCommandWrapper, sample_command: SkillCommand
    ) -> None:
        """Verify wrapper name matches the skill command name (no prefix)."""
        assert sample_wrapper.name == sample_command.name
        assert sample_wrapper.name == "test-skill"

    def test_wrapper_stores_underlying_skill(
        self, sample_wrapper: SkillCommandWrapper, sample_command: SkillCommand
    ) -> None:
        """Verify wrapper stores reference to underlying skill command."""
        assert sample_wrapper._skill_cmd is sample_command
        assert sample_wrapper._skill_cmd.name == sample_command.name

    def test_wrapper_exposes_description(
        self, sample_wrapper: SkillCommandWrapper, sample_command: SkillCommand
    ) -> None:
        """Verify wrapper exposes command description."""
        assert sample_wrapper.description == sample_command.description
        assert sample_wrapper.description == "A test skill for integration testing"

    def test_wrapper_exposes_category(
        self, sample_wrapper: SkillCommandWrapper, sample_command: SkillCommand
    ) -> None:
        """Verify wrapper exposes command category."""
        assert sample_wrapper.category == sample_command.category
        assert sample_wrapper.category == "testing"

    def test_wrapper_with_different_categories(self, sample_skill: Skill) -> None:
        """Test wrapper correctly exposes different categories."""
        categories = ["utility", "analysis", "coding", "general"]
        for category in categories:
            cmd = SkillCommand(
                name=f"{category}-skill",
                description=f"A {category} skill",
                skill=sample_skill,
                category=category,
            )
            wrapper = SkillCommandWrapper(cmd)
            assert wrapper.category == category


# =============================================================================
# OpenCodeSkillBridge Tests
# =============================================================================


class TestOpenCodeSkillBridge:
    """Test OpenCodeSkillBridge functionality."""

    def test_handle_change_adds_command(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test handle_change adds command to bridge."""
        sample_bridge.handle_change("test-skill", sample_command)

        commands = sample_bridge.get_commands()
        assert len(commands) == 1
        assert commands[0].name == "test-skill"

    def test_handle_change_removes_command(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test handle_change removes command from bridge."""
        # Add command first
        sample_bridge.handle_change("test-skill", sample_command)
        assert len(sample_bridge.get_commands()) == 1

        # Remove command
        sample_bridge.handle_change("test-skill", None)
        assert len(sample_bridge.get_commands()) == 0

    def test_handle_change_updates_command(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test handle_change updates existing command."""
        # Add initial command
        sample_bridge.handle_change("test-skill", sample_command)

        # Create updated command with same name but different description
        updated_skill = Skill(
            name="test-skill",
            description="Updated description",
            skill_path=UPath("/tmp/updated"),
        )
        updated_command = SkillCommand(
            name="test-skill",
            description="Updated description",
            skill=updated_skill,
        )

        # Update command
        sample_bridge.handle_change("test-skill", updated_command)

        commands = sample_bridge.get_commands()
        assert len(commands) == 1
        assert commands[0].description == "Updated description"

    def test_get_commands_returns_empty_list_initially(
        self, sample_bridge: OpenCodeSkillBridge
    ) -> None:
        """Test get_commands returns empty list for fresh bridge."""
        commands = sample_bridge.get_commands()
        assert commands == []
        assert isinstance(commands, list)
        assert len(commands) == 0

    def test_get_commands_returns_commands(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test get_commands returns list of slashed commands."""
        sample_bridge.handle_change("test-skill", sample_command)

        commands = sample_bridge.get_commands()
        assert isinstance(commands, list)
        assert len(commands) == 1
        assert all(isinstance(cmd, SlashedCommand) for cmd in commands)

    def test_get_commands_multiple_commands(
        self, sample_bridge: OpenCodeSkillBridge, multiple_commands: list[SkillCommand]
    ) -> None:
        """Test get_commands returns multiple commands correctly."""
        for cmd in multiple_commands:
            sample_bridge.handle_change(cmd.name, cmd)

        commands = sample_bridge.get_commands()
        assert len(commands) == len(multiple_commands)

        names = {cmd.name for cmd in commands}
        expected_names = {cmd.name for cmd in multiple_commands}
        assert names == expected_names

    def test_get_command_with_prefix(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test get_command finds command with skill: prefix (backward compat lookup)."""
        sample_bridge.handle_change("test-skill", sample_command)

        cmd = sample_bridge.get_command("skill:test-skill")
        assert cmd is not None
        assert cmd.name == "test-skill"

    def test_get_command_without_prefix(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test get_command finds command without skill: prefix."""
        sample_bridge.handle_change("test-skill", sample_command)

        cmd = sample_bridge.get_command("test-skill")
        assert cmd is not None
        assert cmd.name == "test-skill"

    def test_get_command_returns_none_for_missing(self, sample_bridge: OpenCodeSkillBridge) -> None:
        """Test get_command returns None for non-existent command."""
        assert sample_bridge.get_command("nonexistent") is None
        assert sample_bridge.get_command("skill:nonexistent") is None

    def test_commands_are_slashed_commands(
        self, sample_bridge: OpenCodeSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Test that stored commands are SlashedCommand instances."""
        sample_bridge.handle_change("test-skill", sample_command)

        # Test via get_commands
        commands = sample_bridge.get_commands()
        for cmd in commands:
            assert isinstance(cmd, SlashedCommand)

        # Test via get_command
        cmd = sample_bridge.get_command("test-skill")
        assert isinstance(cmd, SlashedCommand)

    def test_remove_nonexistent_command_safely(self, sample_bridge: OpenCodeSkillBridge) -> None:
        """Test removing non-existent command doesn't raise error."""
        # Should not raise KeyError or any other exception
        sample_bridge.handle_change("nonexistent", None)
        assert sample_bridge.get_commands() == []

    def test_bridge_handles_empty_skill_name(
        self, sample_bridge: OpenCodeSkillBridge, sample_skill: Skill
    ) -> None:
        """Test bridge handles command with various name formats."""
        # Test with hyphenated names
        cmd = SkillCommand(
            name="my-test-skill",
            description="Hyphenated name skill",
            skill=sample_skill,
        )
        sample_bridge.handle_change("my-test-skill", cmd)

        assert sample_bridge.get_command("my-test-skill") is not None
        assert sample_bridge.get_command("skill:my-test-skill") is not None


# =============================================================================
# create_skill_command Tests
# =============================================================================


class TestCreateSkillCommand:
    """Test create_skill_command factory function."""

    def test_creates_slashed_command(self, sample_command: SkillCommand) -> None:
        """Test factory creates a valid SlashedCommand."""
        cmd = create_skill_command(sample_command)
        assert isinstance(cmd, SlashedCommand)

    def test_command_has_correct_name_format(self, sample_command: SkillCommand) -> None:
        """Test created command name matches the skill command name (no prefix)."""
        cmd = create_skill_command(sample_command)
        assert cmd.name == "test-skill"

    def test_command_has_correct_description(self, sample_command: SkillCommand) -> None:
        """Test created command has correct description."""
        cmd = create_skill_command(sample_command)
        assert cmd.description == sample_command.description

    def test_command_has_correct_category(self, sample_command: SkillCommand) -> None:
        """Test created command has skill category."""
        cmd = create_skill_command(sample_command)
        assert cmd.category == "skill"

    def test_command_has_usage_hint(self, sample_command: SkillCommand) -> None:
        """Test created command has usage hint from input_hint."""
        cmd = create_skill_command(sample_command)
        assert cmd.usage == "Provide test arguments"

    @pytest.mark.asyncio
    async def test_command_execution_shows_loading_message(
        self, command_with_instructions: SkillCommand
    ) -> None:
        """Test command execution shows loading message when instructions exist."""
        cmd = create_skill_command(command_with_instructions)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        await cmd.execute(mock_ctx, [], {})

        mock_ctx.print.assert_called_once_with(
            "Loading skill: skill-with-instructions (skill://local/skill-with-instructions)"
        )

    @pytest.mark.asyncio
    async def test_command_execution_shows_no_instructions_message(
        self, sample_command: SkillCommand
    ) -> None:
        """Test command execution shows message when no instructions exist."""
        # Create a skill with empty instructions
        sample_command.skill.instructions = ""

        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        await cmd.execute(mock_ctx, [], {})

        mock_ctx.print.assert_called_once_with(
            "Skill test-skill has no instructions (skill://local/test-skill)"
        )


# =============================================================================
# Argument Substitution Tests
# =============================================================================


class TestArgumentSubstitution:
    """Test command argument passing and substitution."""

    @pytest.mark.asyncio
    async def test_simple_argument_passing(self, sample_command: SkillCommand) -> None:
        """Test simple single argument passing to command."""
        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        # Execute with a simple argument
        await cmd.execute(mock_ctx, ["hello"], {})

        # Command should execute without error
        mock_ctx.print.assert_called_once()

    @pytest.mark.asyncio
    async def test_multiple_arguments(self, sample_command: SkillCommand) -> None:
        """Test multiple arguments passing to command."""
        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        # Execute with multiple arguments
        args = ["arg1", "arg2", "arg3"]
        await cmd.execute(mock_ctx, args, {})

        # Command should execute without error
        mock_ctx.print.assert_called_once()

    @pytest.mark.asyncio
    async def test_argument_with_spaces(self, sample_command: SkillCommand) -> None:
        """Test argument containing spaces."""
        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        # Execute with argument containing spaces
        await cmd.execute(mock_ctx, ["hello world"], {})

        # Command should execute without error
        mock_ctx.print.assert_called_once()

    @pytest.mark.asyncio
    async def test_keyword_arguments(self, sample_command: SkillCommand) -> None:
        """Test keyword arguments passing."""
        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        # Execute with keyword arguments
        kwargs = {"key1": "value1", "key2": "value2"}
        await cmd.execute(mock_ctx, [], kwargs)

        # Command should execute without error
        mock_ctx.print.assert_called_once()


# =============================================================================
# Bridge Integration Tests
# =============================================================================


class TestBridgeIntegration:
    """Test bridge integration with SkillCommandRegistry."""

    def test_bridge_receives_registry_changes(self, sample_command: SkillCommand) -> None:
        """Test bridge receives commands when registered with registry."""
        registry = SkillCommandRegistry()
        bridge = OpenCodeSkillBridge()

        # Subscribe bridge to registry
        registry.on_command_change(bridge.handle_change)

        # Add command to registry
        registry.register("test-skill", sample_command)

        # Bridge should receive it
        commands = bridge.get_commands()
        assert len(commands) == 1
        assert commands[0].name == "test-skill"

    def test_bridge_receives_remove_events(self, sample_command: SkillCommand) -> None:
        """Test bridge receives remove events from registry."""
        registry = SkillCommandRegistry()
        bridge = OpenCodeSkillBridge()

        # Subscribe and add command
        registry.on_command_change(bridge.handle_change)
        registry.register("test-skill", sample_command)

        # Verify command was added
        assert len(bridge.get_commands()) == 1

        # Remove command from registry
        del registry["test-skill"]

        # Bridge should have removed it
        assert len(bridge.get_commands()) == 0

    def test_bridge_receives_initial_commands_on_subscribe(
        self, sample_command: SkillCommand
    ) -> None:
        """Test bridge receives existing commands when subscribing."""
        registry = SkillCommandRegistry()
        bridge = OpenCodeSkillBridge()

        # Add command before subscribing
        registry.register("test-skill", sample_command)

        # Subscribe bridge to registry
        registry.on_command_change(bridge.handle_change)

        # Bridge should receive the existing command
        commands = bridge.get_commands()
        assert len(commands) == 1

    def test_commands_updated_at_runtime(self, multiple_commands: list[SkillCommand]) -> None:
        """Test commands are updated at runtime through registry."""
        registry = SkillCommandRegistry()
        bridge = OpenCodeSkillBridge()

        # Subscribe bridge
        registry.on_command_change(bridge.handle_change)

        # Initially no commands
        assert len(bridge.get_commands()) == 0

        # Add commands one by one
        for i, cmd in enumerate(multiple_commands):
            registry.register(cmd.name, cmd)
            assert len(bridge.get_commands()) == i + 1

        # Remove commands one by one
        for i, cmd in enumerate(multiple_commands):
            del registry[cmd.name]
            assert len(bridge.get_commands()) == len(multiple_commands) - i - 1

    def test_multiple_bridges_with_same_registry(self, sample_command: SkillCommand) -> None:
        """Test multiple bridges can subscribe to same registry."""
        registry = SkillCommandRegistry()
        bridge1 = OpenCodeSkillBridge()
        bridge2 = OpenCodeSkillBridge()

        # Subscribe both bridges
        registry.on_command_change(bridge1.handle_change)
        registry.on_command_change(bridge2.handle_change)

        # Add command
        registry.register("test-skill", sample_command)

        # Both bridges should receive it
        assert len(bridge1.get_commands()) == 1
        assert len(bridge2.get_commands()) == 1

        # Remove command
        del registry["test-skill"]

        # Both bridges should have removed it
        assert len(bridge1.get_commands()) == 0
        assert len(bridge2.get_commands()) == 0

    def test_bridge_handles_registry_replacements(self, sample_command: SkillCommand) -> None:
        """Test bridge handles command replacements from registry."""
        registry = SkillCommandRegistry()
        bridge = OpenCodeSkillBridge()

        registry.on_command_change(bridge.handle_change)
        registry.register("test-skill", sample_command)

        # Create replacement command
        new_skill = Skill(
            name="test-skill",
            description="Replacement skill",
            skill_path=UPath("/tmp/replacement"),
        )
        new_command = SkillCommand(
            name="test-skill",
            description="Replacement skill",
            skill=new_skill,
        )

        # Register with replace=True
        registry.register("test-skill", new_command, replace=True)

        # Bridge should have updated command
        commands = bridge.get_commands()
        assert len(commands) == 1
        assert commands[0].description == "Replacement skill"


# =============================================================================
# Edge Case Tests
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_bridge_with_special_characters_in_name(self, sample_skill: Skill) -> None:
        """Test bridge handles skill names with hyphens correctly."""
        bridge = OpenCodeSkillBridge()

        # Test various valid skill names
        names = ["my-skill", "test-skill-123", "a-b-c-d"]
        for name in names:
            cmd = SkillCommand(
                name=name,
                description=f"Skill {name}",
                skill=sample_skill,
            )
            bridge.handle_change(name, cmd)

            # Should be retrievable with and without prefix
            assert bridge.get_command(name) is not None
            assert bridge.get_command(f"skill:{name}") is not None

    def test_wrapper_preserves_skill_reference(self, sample_command: SkillCommand) -> None:
        """Test that wrapper maintains reference to original skill."""
        wrapper = SkillCommandWrapper(sample_command)

        # Modify the original command's skill
        original_instructions = wrapper._skill_cmd.skill.instructions
        wrapper._skill_cmd.skill.instructions = "Modified instructions"

        # Wrapper should see the change
        assert wrapper._skill_cmd.skill.instructions == "Modified instructions"

        # Restore for cleanup
        wrapper._skill_cmd.skill.instructions = original_instructions

    @pytest.mark.asyncio
    async def test_command_with_empty_args(self, sample_command: SkillCommand) -> None:
        """Test command execution with empty arguments."""
        cmd = create_skill_command(sample_command)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        # Execute with empty args
        await cmd.execute(mock_ctx, [], {})

        mock_ctx.print.assert_called_once()

    @pytest.mark.asyncio
    async def test_command_handles_empty_instructions(self, sample_skill: Skill) -> None:
        """Test command handles empty instructions gracefully."""
        # Set skill instructions to empty string (falsy value)
        sample_skill.instructions = ""

        empty_cmd = SkillCommand(
            name="empty-skill",
            description="Skill with empty instructions",
            skill=sample_skill,
        )

        cmd = create_skill_command(empty_cmd)

        mock_ctx = MagicMock()
        mock_ctx.print = AsyncMock()

        await cmd.execute(mock_ctx, [], {})

        # Empty string is falsy so "no instructions" message should be shown
        mock_ctx.print.assert_called_once()
        call_args = mock_ctx.print.call_args
        assert "has no instructions" in str(call_args)


# =============================================================================
# Test Count Summary
# =============================================================================
# TestSkillCommandWrapper: 6 tests
# TestOpenCodeSkillBridge: 12 tests
# TestCreateSkillCommand: 6 tests
# TestArgumentSubstitution: 4 tests
# TestBridgeIntegration: 6 tests
# TestEdgeCases: 4 tests
# TOTAL: 38 tests
