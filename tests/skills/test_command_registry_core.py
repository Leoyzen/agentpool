"""Core tests for SkillCommandRegistry."""

from __future__ import annotations

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill
from agentpool.tools.exceptions import ToolError


def create_test_skill(name: str = "test-skill", description: str = "A test skill") -> Skill:
    """Create a minimal test skill."""
    return Skill(
        name=name,
        description=description,
        skill_path=UPath("/tmp/test-skill"),
    )


def create_test_command(
    name: str = "test-command", description: str = "A test command"
) -> SkillCommand:
    """Create a minimal test command."""
    skill = create_test_skill(name, description)
    return SkillCommand(
        name=name,
        description=description,
        skill=skill,
    )


class TestRegistryWithSkillsSource:
    """Tests for registry with SkillsRegistry connected."""

    def test_has_skills_returns_true_with_registry(self) -> None:
        """Test that has_skills returns True when SkillsRegistry is provided."""
        skills_registry = SkillsRegistry()
        registry = SkillCommandRegistry(skills_registry=skills_registry)

        assert registry.has_skills is True


class TestRegistryWithoutSkillsSource:
    """Tests for registry without SkillsRegistry (standalone mode)."""

    def test_has_skills_returns_false_without_registry(self) -> None:
        """Test that has_skills returns False when no SkillsRegistry is provided."""
        registry = SkillCommandRegistry()

        assert registry.has_skills is False

    def test_has_skills_returns_false_with_none(self) -> None:
        """Test that has_skills returns False when None is explicitly passed."""
        registry = SkillCommandRegistry(skills_registry=None)

        assert registry.has_skills is False


class TestHasCommandsProperty:
    """Tests for has_commands property."""

    def test_has_commands_returns_false_when_empty(self) -> None:
        """Test that has_commands returns False when no commands registered."""
        registry = SkillCommandRegistry()

        assert registry.has_commands is False
        assert len(registry) == 0

    def test_has_commands_returns_true_when_commands_registered(self) -> None:
        """Test that has_commands returns True when commands are registered."""
        registry = SkillCommandRegistry()
        command = create_test_command("cmd1")

        registry.register("cmd1", command)

        assert registry.has_commands is True
        assert len(registry) == 1

    def test_has_commands_returns_false_after_removing_all(self) -> None:
        """Test that has_commands returns False after all commands removed."""
        registry = SkillCommandRegistry()
        command = create_test_command("cmd1")

        registry.register("cmd1", command)
        assert registry.has_commands is True

        del registry["cmd1"]
        assert registry.has_commands is False


class TestValidateItem:
    """Tests for _validate_item method."""

    def test_validate_item_accepts_skillcommand(self) -> None:
        """Test that _validate_item accepts a valid SkillCommand."""
        registry = SkillCommandRegistry()
        command = create_test_command()

        validated = registry._validate_item(command)

        assert validated is command

    def test_validate_item_raises_on_string(self) -> None:
        """Test that _validate_item raises ToolError for string input."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Expected SkillCommand, got str"):
            registry._validate_item("not a command")

    def test_validate_item_raises_on_dict(self) -> None:
        """Test that _validate_item raises ToolError for dict input."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Expected SkillCommand, got dict"):
            registry._validate_item({"name": "invalid"})

    def test_validate_item_raises_on_none(self) -> None:
        """Test that _validate_item raises ToolError for None input."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Expected SkillCommand, got NoneType"):
            registry._validate_item(None)

    def test_validate_item_raises_on_skill(self) -> None:
        """Test that _validate_item raises ToolError for Skill input."""
        registry = SkillCommandRegistry()
        skill = create_test_skill()

        with pytest.raises(ToolError, match="Expected SkillCommand, got Skill"):
            registry._validate_item(skill)

    def test_validate_item_raises_on_int(self) -> None:
        """Test that _validate_item raises ToolError for integer input."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Expected SkillCommand, got int"):
            registry._validate_item(123)


class TestGracefulDegradation:
    """Tests for graceful degradation without SkillsRegistry."""

    def test_registry_works_without_skills_registry(self) -> None:
        """Test that registry functions normally without a SkillsRegistry."""
        registry = SkillCommandRegistry()
        command = create_test_command("standalone-cmd")

        # Should be able to register
        registry.register("standalone-cmd", command)
        assert "standalone-cmd" in registry
        assert registry.has_commands is True

        # Should be able to retrieve
        retrieved = registry.get("standalone-cmd")
        assert retrieved is command

        # Should be able to list
        assert registry.list_items() == ["standalone-cmd"]

        # Should be able to delete
        del registry["standalone-cmd"]
        assert "standalone-cmd" not in registry

    def test_register_via_dict_syntax_without_skills(self) -> None:
        """Test dict-style registration works without SkillsRegistry."""
        registry = SkillCommandRegistry()
        command = create_test_command("dict-cmd")

        registry["dict-cmd"] = command

        assert registry["dict-cmd"] is command


class TestRegisterAndRetrieve:
    """Tests for register and retrieve operations."""

    def test_register_and_retrieve_command(self) -> None:
        """Test that commands can be registered and retrieved."""
        registry = SkillCommandRegistry()
        command = create_test_command("my-cmd", "My command")

        registry.register("my-cmd", command)
        retrieved = registry.get("my-cmd")

        assert retrieved is command
        assert retrieved.name == "my-cmd"
        assert retrieved.description == "My command"

    def test_register_multiple_commands(self) -> None:
        """Test that multiple commands can be registered."""
        registry = SkillCommandRegistry()
        command1 = create_test_command("cmd1", "First command")
        command2 = create_test_command("cmd2", "Second command")

        registry.register("cmd1", command1)
        registry.register("cmd2", command2)

        assert registry.get("cmd1") is command1
        assert registry.get("cmd2") is command2
        assert len(registry) == 2

    def test_register_duplicate_raises_without_replace(self) -> None:
        """Test that registering duplicate key raises error without replace flag."""
        registry = SkillCommandRegistry()
        command1 = create_test_command("duplicate")
        command2 = create_test_command("duplicate")

        registry.register("duplicate", command1)

        with pytest.raises(ToolError, match="Item already registered: duplicate"):
            registry.register("duplicate", command2)

    def test_register_duplicate_with_replace(self) -> None:
        """Test that registering with replace=True updates the command."""
        registry = SkillCommandRegistry()
        skill = create_test_skill("original")
        skill2 = create_test_skill("replacement")
        command1 = SkillCommand(name="duplicate", description="Original", skill=skill)
        command2 = SkillCommand(name="duplicate", description="Replacement", skill=skill2)

        registry.register("duplicate", command1)
        registry.register("duplicate", command2, replace=True)

        assert registry.get("duplicate") is command2
        assert registry.get("duplicate").description == "Replacement"


class TestUnregisterCommand:
    """Tests for unregistering commands."""

    def test_unregister_existing_command(self) -> None:
        """Test that existing commands can be unregistered."""
        registry = SkillCommandRegistry()
        command = create_test_command("to-remove")

        registry.register("to-remove", command)
        assert "to-remove" in registry

        del registry["to-remove"]
        assert "to-remove" not in registry

    def test_unregister_nonexistent_raises_error(self) -> None:
        """Test that unregistering nonexistent command raises error."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError, match="Item not found: nonexistent"):
            del registry["nonexistent"]

    def test_unregister_via_delitem(self) -> None:
        """Test del syntax for unregistering."""
        registry = SkillCommandRegistry()
        command = create_test_command("del-cmd")

        registry.register("del-cmd", command)
        del registry["del-cmd"]

        assert "del-cmd" not in registry


class TestErrorClass:
    """Tests for _error_class property."""

    def test_error_class_is_toolerror(self) -> None:
        """Test that _error_class returns ToolError."""
        registry = SkillCommandRegistry()

        assert registry._error_class is ToolError

    def test_error_used_for_missing_item(self) -> None:
        """Test that ToolError is raised for missing items."""
        registry = SkillCommandRegistry()

        with pytest.raises(ToolError):
            registry.get("missing")


class TestIterationAndContainment:
    """Tests for iteration and containment operations."""

    def test_contains_for_registered_command(self) -> None:
        """Test 'in' operator for registered commands."""
        registry = SkillCommandRegistry()
        command = create_test_command("contained")

        registry.register("contained", command)

        assert "contained" in registry
        assert "not-contained" not in registry

    def test_iteration_over_keys(self) -> None:
        """Test iteration over registry keys."""
        registry = SkillCommandRegistry()
        command1 = create_test_command("iter-1")
        command2 = create_test_command("iter-2")

        registry.register("iter-1", command1)
        registry.register("iter-2", command2)

        keys = list(registry)
        assert len(keys) == 2
        assert "iter-1" in keys
        assert "iter-2" in keys

    def test_list_items_returns_keys(self) -> None:
        """Test list_items returns all registered keys."""
        registry = SkillCommandRegistry()
        command1 = create_test_command("list-1")
        command2 = create_test_command("list-2")

        registry.register("list-1", command1)
        registry.register("list-2", command2)

        items = registry.list_items()
        assert "list-1" in items
        assert "list-2" in items


class TestRegistryRepresentation:
    """Tests for registry string representation."""

    def test_repr_contains_class_name(self) -> None:
        """Test that repr contains the class name."""
        registry = SkillCommandRegistry()

        repr_str = repr(registry)
        assert "SkillCommandRegistry" in repr_str
