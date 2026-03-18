"""Comprehensive unit tests for skill commands components.

This module provides unit tests for:
- SkillCommand creation and properties
- SkillCommandRegistry operations (register, get, remove, has_commands)
- SkillCommandWrapper initialization
"""

from __future__ import annotations

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.skill import Skill
from agentpool.tools.exceptions import ToolError


@pytest.fixture
def sample_skill() -> Skill:
    """Create a sample Skill for testing."""
    return Skill(
        name="test-skill",
        description="A test skill for unit testing",
        skill_path=UPath("/tmp/test-skill"),
    )


@pytest.fixture
def sample_command(sample_skill: Skill) -> SkillCommand:
    """Create a sample SkillCommand for testing."""
    return SkillCommand(
        name="test-cmd",
        description="Test command description",
        skill=sample_skill,
    )


class TestSkillCommandCreation:
    """Tests for SkillCommand creation and basic properties."""

    def test_creation_with_valid_skill(self, sample_skill: Skill) -> None:
        """Test that SkillCommand can be created with a valid skill."""
        command = SkillCommand(
            name="my-command",
            description="My command description",
            skill=sample_skill,
        )

        assert command.name == "my-command"
        assert command.description == "My command description"
        assert command.skill == sample_skill

    def test_properties_accessible(self, sample_skill: Skill) -> None:
        """Test that all properties are accessible."""
        command = SkillCommand(
            name="test-name",
            description="Test description",
            skill=sample_skill,
            input_hint="Test input hint",
            category="test-category",
        )

        # All basic properties should be accessible
        _ = command.name
        _ = command.description
        _ = command.skill
        _ = command.input_hint
        _ = command.category

        # Verify values
        assert command.name == "test-name"
        assert command.description == "Test description"
        assert command.input_hint == "Test input hint"
        assert command.category == "test-category"
        assert command.skill.name == "test-skill"


class TestSkillCommandDefaults:
    """Tests for SkillCommand default values."""

    def test_default_input_hint(self, sample_skill: Skill) -> None:
        """Test that default input_hint is 'Arguments for skill'."""
        command = SkillCommand(
            name="test",
            description="Test",
            skill=sample_skill,
        )

        assert command.input_hint == "Arguments for skill"

    def test_default_category(self, sample_skill: Skill) -> None:
        """Test that default category is 'skill'."""
        command = SkillCommand(
            name="test",
            description="Test",
            skill=sample_skill,
        )

        assert command.category == "skill"

    def test_defaults_when_partially_specified(self, sample_skill: Skill) -> None:
        """Test that unspecified fields use defaults while specified ones use values."""
        command = SkillCommand(
            name="test",
            description="Test",
            skill=sample_skill,
            category="custom-category",
            # input_hint not specified - should use default
        )

        assert command.input_hint == "Arguments for skill"  # Default
        assert command.category == "custom-category"  # Specified


class TestSkillCommandFrozen:
    """Tests for frozen dataclass immutability."""

    def test_cannot_mutate_name(self, sample_command: SkillCommand) -> None:
        """Test that name cannot be mutated after creation."""
        with pytest.raises(AttributeError):
            sample_command.name = "new-name"  # type: ignore[misc]

    def test_cannot_mutate_description(self, sample_command: SkillCommand) -> None:
        """Test that description cannot be mutated after creation."""
        with pytest.raises(AttributeError):
            sample_command.description = "new-description"  # type: ignore[misc]

    def test_cannot_mutate_skill(self, sample_command: SkillCommand) -> None:
        """Test that skill cannot be mutated after creation."""
        other_skill = Skill(
            name="other",
            description="Other skill",
            skill_path=UPath("/tmp/other"),
        )
        with pytest.raises(AttributeError):
            sample_command.skill = other_skill  # type: ignore[misc]

    def test_cannot_mutate_input_hint(self, sample_command: SkillCommand) -> None:
        """Test that input_hint cannot be mutated after creation."""
        with pytest.raises(AttributeError):
            sample_command.input_hint = "new-hint"  # type: ignore[misc]

    def test_cannot_mutate_category(self, sample_command: SkillCommand) -> None:
        """Test that category cannot be mutated after creation."""
        with pytest.raises(AttributeError):
            sample_command.category = "new-category"  # type: ignore[misc]


class TestSkillCommandRegistryRegister:
    """Tests for SkillCommandRegistry register operations."""

    def test_register_adds_command(self) -> None:
        """Test that register() adds command to registry."""
        registry = SkillCommandRegistry()
        skill = Skill(
            name="test-skill",
            description="Test skill",
            skill_path=UPath("/tmp/test"),
        )
        command = SkillCommand(
            name="test-cmd",
            description="Test command",
            skill=skill,
        )

        registry.register("test-cmd", command)

        assert "test-cmd" in registry
        assert registry.get("test-cmd") is command

    def test_register_multiple_commands(self) -> None:
        """Test that multiple commands can be registered."""
        registry = SkillCommandRegistry()
        skill = Skill(
            name="test-skill",
            description="Test skill",
            skill_path=UPath("/tmp/test"),
        )
        cmd1 = SkillCommand(name="cmd1", description="Command 1", skill=skill)
        cmd2 = SkillCommand(name="cmd2", description="Command 2", skill=skill)

        registry.register("cmd1", cmd1)
        registry.register("cmd2", cmd2)

        assert registry.get("cmd1") is cmd1
        assert registry.get("cmd2") is cmd2
        assert len(registry) == 2


class TestSkillCommandRegistryGet:
    """Tests for SkillCommandRegistry get operations."""

    def test_get_retrieves_command(self, sample_command: SkillCommand) -> None:
        """Test that get() retrieves registered command."""
        registry = SkillCommandRegistry()
        registry.register("my-cmd", sample_command)

        retrieved = registry.get("my-cmd")

        assert retrieved is sample_command
        assert retrieved.name == "test-cmd"

    def test_get_nonexistent_raises_error(self) -> None:
        """Test that get() raises error for nonexistent command."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Item not found: nonexistent"):
            registry.get("nonexistent")


class TestSkillCommandRegistryHasCommands:
    """Tests for SkillCommandRegistry has_commands property."""

    def test_has_commands_false_when_empty(self) -> None:
        """Test that has_commands returns False when no commands registered."""
        registry = SkillCommandRegistry()

        assert registry.has_commands is False

    def test_has_commands_true_with_commands(self, sample_command: SkillCommand) -> None:
        """Test that has_commands returns True when commands are registered."""
        registry = SkillCommandRegistry()
        registry.register("test", sample_command)

        assert registry.has_commands is True

    def test_has_commands_false_after_removal(self, sample_command: SkillCommand) -> None:
        """Test that has_commands returns False after all commands removed."""
        registry = SkillCommandRegistry()
        registry.register("test", sample_command)
        assert registry.has_commands is True

        del registry["test"]
        assert registry.has_commands is False


class TestSkillCommandRegistryContains:
    """Tests for SkillCommandRegistry __contains__ operator."""

    def test_contains_returns_true_for_registered(self, sample_command: SkillCommand) -> None:
        """Test that 'in' operator returns True for registered command."""
        registry = SkillCommandRegistry()
        registry.register("registered-cmd", sample_command)

        assert "registered-cmd" in registry

    def test_contains_returns_false_for_unregistered(self) -> None:
        """Test that 'in' operator returns False for unregistered command."""
        registry = SkillCommandRegistry()

        assert "unregistered-cmd" not in registry

    def test_contains_after_removal(self, sample_command: SkillCommand) -> None:
        """Test that 'in' returns False after command is removed."""
        registry = SkillCommandRegistry()
        registry.register("temp-cmd", sample_command)
        assert "temp-cmd" in registry

        del registry["temp-cmd"]
        assert "temp-cmd" not in registry


class TestSkillCommandRegistryDelItem:
    """Tests for SkillCommandRegistry __delitem__ operation."""

    def test_delitem_removes_command(self, sample_command: SkillCommand) -> None:
        """Test that __delitem__ removes command from registry."""
        registry = SkillCommandRegistry()
        registry.register("to-remove", sample_command)
        assert "to-remove" in registry

        del registry["to-remove"]

        assert "to-remove" not in registry
        assert len(registry) == 0

    def test_delitem_raises_for_nonexistent(self) -> None:
        """Test that __delitem__ raises error for nonexistent command."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Item not found: nonexistent"):
            del registry["nonexistent"]


class TestSkillCommandRegistryOnCommandChange:
    """Tests for SkillCommandRegistry on_command_change callback registration."""

    def test_callback_registration(self, sample_command: SkillCommand) -> None:
        """Test that on_command_change registers callback."""
        registry = SkillCommandRegistry()
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # Initial callback should be called with existing commands (none yet)
        assert len(events) == 0

        # Register a command - callback should be called
        registry.register("test", sample_command)
        assert len(events) == 1
        assert events[0] == ("test", sample_command)

    def test_callback_receives_remove_notification(self, sample_command: SkillCommand) -> None:
        """Test that callback receives notification on command removal."""
        registry = SkillCommandRegistry()
        registry.register("test", sample_command)
        events: list[tuple[str, SkillCommand | None]] = []

        def handler(name: str, command: SkillCommand | None) -> None:
            events.append((name, command))

        registry.on_command_change(handler)

        # Initial notification of existing command
        assert len(events) == 1

        # Remove command - callback should receive None
        del registry["test"]
        assert len(events) == 2
        assert events[1] == ("test", None)

    def test_multiple_callbacks_registered(self, sample_command: SkillCommand) -> None:
        """Test that multiple callbacks can be registered."""
        registry = SkillCommandRegistry()
        events1: list[tuple[str, SkillCommand | None]] = []
        events2: list[tuple[str, SkillCommand | None]] = []

        def handler1(name: str, command: SkillCommand | None) -> None:
            events1.append((name, command))

        def handler2(name: str, command: SkillCommand | None) -> None:
            events2.append((name, command))

        registry.on_command_change(handler1)
        registry.on_command_change(handler2)

        registry.register("test", sample_command)

        assert len(events1) == 1
        assert len(events2) == 1
        assert events1[0] == ("test", sample_command)
        assert events2[0] == ("test", sample_command)


class TestSkillCommandWrapperInit:
    """Tests for SkillCommandWrapper initialization."""

    def test_wrapper_initializes_with_skill_command(self, sample_command: SkillCommand) -> None:
        """Test that SkillCommandWrapper initializes with a SkillCommand."""
        from agentpool_server.opencode_server.skill_bridge import SkillCommandWrapper

        wrapper = SkillCommandWrapper(sample_command)

        assert wrapper._skill_cmd == sample_command

    def test_wrapper_exposes_skill_name(self, sample_command: SkillCommand) -> None:
        """Test that wrapper exposes command name with prefix."""
        from agentpool_server.opencode_server.skill_bridge import SkillCommandWrapper

        wrapper = SkillCommandWrapper(sample_command)

        assert "test-cmd" in wrapper.name
        assert wrapper.name == "skill:test-cmd"

    def test_wrapper_exposes_description(self, sample_command: SkillCommand) -> None:
        """Test that wrapper exposes description."""
        from agentpool_server.opencode_server.skill_bridge import SkillCommandWrapper

        wrapper = SkillCommandWrapper(sample_command)

        assert wrapper.description == "Test command description"

    def test_wrapper_exposes_category(self, sample_command: SkillCommand) -> None:
        """Test that wrapper exposes category."""
        from agentpool_server.opencode_server.skill_bridge import SkillCommandWrapper

        wrapper = SkillCommandWrapper(sample_command)

        assert wrapper.category == "skill"
