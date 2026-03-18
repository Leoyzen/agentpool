"""Comprehensive integration tests for ACP skill commands bridge.

This module provides extensive test coverage for the ACPSkillBridge class,
which converts SkillCommand instances to ACP AvailableCommand format for
exposure as slash commands via the ACP protocol.

Test Classes:
    - TestSkillCommandConversion: Tests for conversion from SkillCommand to AvailableCommand
    - TestACPSkillBridgeLifecycle: Tests for bridge lifecycle and management operations
    - TestIntegrationWithRegistry: Tests for integration with SkillCommandRegistry
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest
from upathtools import UPath

from acp.schema.slash_commands import AvailableCommand, AvailableCommandInput, CommandInputHint
from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import CommandChangeHandler, SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge

if TYPE_CHECKING:
    pass


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def temp_skill_dir(tmp_path: UPath) -> UPath:
    """Create a temporary directory for skill testing."""
    return UPath(tmp_path)


@pytest.fixture
def sample_skill(temp_skill_dir: UPath) -> Skill:
    """Create a real Skill instance for testing.

    Creates a skill with a valid SKILL.md file in a temporary directory.
    """
    skill_content = """---
name: test-skill
description: A test skill for integration testing
---

# Test Skill

This is a test skill for ACP integration testing.
"""
    skill_file = temp_skill_dir / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    return Skill.from_skill_dir(temp_skill_dir)


@pytest.fixture
def sample_skill_with_long_description(temp_skill_dir: UPath) -> Skill:
    """Create a skill with a very long description."""
    long_description = "A" * 500 + " test skill with very long description"
    skill_content = f"""---
name: long-desc-skill
description: {long_description}
---

# Long Description Skill

This skill has a very long description.
"""
    skill_file = temp_skill_dir / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    return Skill.from_skill_dir(temp_skill_dir)


@pytest.fixture
def sample_skill_with_special_chars(temp_skill_dir: UPath) -> Skill:
    """Create a skill with special characters in description."""
    # Use literal block scalar to avoid YAML quote escaping issues
    skill_content = """---
name: special-chars-skill
description: |
  Special chars: <>&"' and unicode: café, naïve, résumé
---

# Special Characters Skill

This skill tests special character handling.
"""
    skill_file = temp_skill_dir / "SKILL.md"
    skill_file.write_text(skill_content, encoding="utf-8")

    return Skill.from_skill_dir(temp_skill_dir)


@pytest.fixture
def sample_command(sample_skill: Skill) -> SkillCommand:
    """Create a SkillCommand instance using the sample skill."""
    return SkillCommand(
        name=sample_skill.name,
        description=sample_skill.description,
        skill=sample_skill,
        input_hint="Provide test arguments",
        category="test",
    )


@pytest.fixture
def mock_skill() -> MagicMock:
    """Create a mock Skill object for simple tests."""
    skill = MagicMock()
    skill.name = "mock_skill"
    skill.description = "A mock skill for testing"
    return skill


@pytest.fixture
def mock_skill2() -> MagicMock:
    """Create a second mock Skill object."""
    skill = MagicMock()
    skill.name = "another_mock_skill"
    skill.description = "Another mock skill for testing"
    return skill


@pytest.fixture
def skill_command(mock_skill: MagicMock) -> SkillCommand:
    """Create a SkillCommand fixture using mock skill."""
    return SkillCommand(
        name="test_skill",
        description="A test skill for testing",
        skill=mock_skill,
        input_hint="Provide test arguments",
        category="test",
    )


@pytest.fixture
def skill_command2(mock_skill2: MagicMock) -> SkillCommand:
    """Create a second SkillCommand fixture."""
    return SkillCommand(
        name="another_skill",
        description="Another test skill",
        skill=mock_skill2,
        input_hint="Provide more arguments",
        category="test",
    )


@pytest.fixture
def bridge() -> ACPSkillBridge:
    """Create a fresh ACPSkillBridge instance."""
    return ACPSkillBridge()


@pytest.fixture
def skill_registry() -> SkillCommandRegistry:
    """Create a SkillCommandRegistry with SkillsRegistry."""
    skills_registry = SkillsRegistry()
    return SkillCommandRegistry(skills_registry=skills_registry)


# =============================================================================
# TestSkillCommandConversion
# =============================================================================


class TestSkillCommandConversion:
    """Tests for converting SkillCommand to ACP AvailableCommand format.

    These tests verify that the bridge correctly converts SkillCommand
    instances to ACP AvailableCommand format with proper handling of
    all fields and edge cases.
    """

    def test_conversion_creates_available_command(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify that bridge converts SkillCommand to AvailableCommand."""
        acp_cmd = bridge._to_acp_command(sample_command)

        assert isinstance(acp_cmd, AvailableCommand)
        assert acp_cmd.name == sample_command.name
        assert acp_cmd.description == sample_command.description

    def test_command_format_name_mapping(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify that command name is correctly mapped."""
        acp_cmd = bridge._to_acp_command(sample_command)

        assert acp_cmd.name == "test-skill"
        assert isinstance(acp_cmd.name, str)

    def test_command_format_description_mapping(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify that description is correctly mapped."""
        acp_cmd = bridge._to_acp_command(sample_command)

        assert acp_cmd.description == sample_command.description
        assert "test skill" in acp_cmd.description.lower()

    def test_long_descriptions_handled_correctly(
        self, bridge: ACPSkillBridge, sample_skill_with_long_description: Skill
    ) -> None:
        """Test edge case: very long descriptions are preserved."""
        long_command = SkillCommand(
            name=sample_skill_with_long_description.name,
            description=sample_skill_with_long_description.description,
            skill=sample_skill_with_long_description,
        )

        acp_cmd = bridge._to_acp_command(long_command)

        assert isinstance(acp_cmd, AvailableCommand)
        assert len(acp_cmd.description) > 500
        assert acp_cmd.description == long_command.description

    def test_special_characters_in_names_preserved(
        self, bridge: ACPSkillBridge, sample_skill_with_special_chars: Skill
    ) -> None:
        """Test edge case: special characters in description are preserved."""
        special_command = SkillCommand(
            name=sample_skill_with_special_chars.name,
            description=sample_skill_with_special_chars.description,
            skill=sample_skill_with_special_chars,
        )

        acp_cmd = bridge._to_acp_command(special_command)

        assert isinstance(acp_cmd, AvailableCommand)
        assert "<" in acp_cmd.description
        assert ">" in acp_cmd.description
        assert "&" in acp_cmd.description
        assert "café" in acp_cmd.description

    def test_conversion_creates_input_spec_with_hint(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify that input specification is created with hint."""
        acp_cmd = bridge._to_acp_command(sample_command)

        assert acp_cmd.input is not None
        assert isinstance(acp_cmd.input, AvailableCommandInput)

    def test_input_hint_correctly_set(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify that input hint is correctly set in AvailableCommand."""
        acp_cmd = bridge._to_acp_command(sample_command)

        assert acp_cmd.input is not None
        assert isinstance(acp_cmd.input.root, CommandInputHint)
        assert acp_cmd.input.root.hint == "Provide test arguments"

    def test_default_input_hint_when_not_specified(
        self, bridge: ACPSkillBridge, mock_skill: MagicMock
    ) -> None:
        """Verify default input hint is used when not specified."""
        cmd = SkillCommand(
            name="default_hint_cmd",
            description="A command with default hint",
            skill=mock_skill,
        )

        acp_cmd = bridge._to_acp_command(cmd)

        assert acp_cmd.input is not None
        assert acp_cmd.input.root.hint == "Arguments for skill"

    def test_custom_input_hint_used(self, bridge: ACPSkillBridge, mock_skill: MagicMock) -> None:
        """Verify custom input hint is used when specified."""
        cmd = SkillCommand(
            name="custom_hint_cmd",
            description="A command with custom hint",
            skill=mock_skill,
            input_hint="Custom hint text here",
        )

        acp_cmd = bridge._to_acp_command(cmd)

        assert acp_cmd.input is not None
        assert acp_cmd.input.root.hint == "Custom hint text here"


# =============================================================================
# TestACPSkillBridgeLifecycle
# =============================================================================


class TestACPSkillBridgeLifecycle:
    """Tests for ACPSkillBridge lifecycle and command management.

    These tests verify the bridge correctly handles adding, removing,
    and updating commands throughout its lifecycle.
    """

    def test_bridge_initialized_with_empty_commands(self) -> None:
        """Test that bridge is initialized with empty commands dictionary."""
        bridge = ACPSkillBridge()

        assert bridge._commands == {}
        assert bridge.get_available_commands() == []

    def test_handle_change_adds_command(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that handle_change adds command when command is not None."""
        bridge.handle_change("test_skill", skill_command)

        assert "test_skill" in bridge._commands
        assert len(bridge._commands) == 1
        assert isinstance(bridge._commands["test_skill"], AvailableCommand)

    def test_handle_change_removes_command(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that handle_change removes command when command is None."""
        # First add the command
        bridge.handle_change("test_skill", skill_command)
        assert "test_skill" in bridge._commands

        # Then remove it
        bridge.handle_change("test_skill", None)

        assert "test_skill" not in bridge._commands
        assert len(bridge._commands) == 0

    def test_handle_change_updates_command(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test add → remove → add lifecycle updates command correctly."""
        # Add initial command
        bridge.handle_change("test_skill", skill_command)
        initial_cmd = bridge._commands["test_skill"]
        assert initial_cmd.description == "A test skill for testing"

        # Remove command
        bridge.handle_change("test_skill", None)
        assert "test_skill" not in bridge._commands

        # Add updated command with same name
        modified_cmd = SkillCommand(
            name="test_skill",
            description="Updated description after re-add",
            skill=skill_command.skill,
            input_hint="Updated hint",
        )
        bridge.handle_change("test_skill", modified_cmd)

        # Verify updated command is stored
        updated_cmd = bridge._commands["test_skill"]
        assert updated_cmd.description == "Updated description after re-add"
        assert updated_cmd.input is not None
        assert updated_cmd.input.root.hint == "Updated hint"

    def test_get_available_commands_returns_list(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that get_available_commands returns a list of AvailableCommand."""
        bridge.handle_change("test_skill", skill_command)

        commands = bridge.get_available_commands()

        assert isinstance(commands, list)
        assert len(commands) == 1
        assert isinstance(commands[0], AvailableCommand)

    def test_get_available_commands_empty_initially(self) -> None:
        """Test that get_available_commands returns empty list initially."""
        bridge = ACPSkillBridge()

        commands = bridge.get_available_commands()

        assert commands == []
        assert isinstance(commands, list)

    def test_multiple_commands_managed(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand, skill_command2: SkillCommand
    ) -> None:
        """Test managing multiple commands at once."""
        # Create a third command with unique name
        mock_skill3 = MagicMock()
        mock_skill3.name = "third_skill"
        skill_command3 = SkillCommand(
            name="third_skill",
            description="Third test skill",
            skill=mock_skill3,
        )

        # Add multiple commands
        bridge.handle_change("test_skill", skill_command)
        bridge.handle_change("another_skill", skill_command2)
        bridge.handle_change("third_skill", skill_command3)

        commands = bridge.get_available_commands()

        assert len(commands) == 3
        names = {cmd.name for cmd in commands}
        assert names == {"test_skill", "another_skill", "third_skill"}

    def test_name_prefix_in_commands(
        self, bridge: ACPSkillBridge, sample_skill: Skill, sample_command: SkillCommand
    ) -> None:
        """Verify command name format includes skill name."""
        bridge.handle_change(sample_skill.name, sample_command)

        commands = bridge.get_available_commands()

        assert len(commands) == 1
        assert commands[0].name == "test-skill"
        # Skill names use hyphen format
        assert "-" in commands[0].name or commands[0].name.isalnum()

    def test_command_has_input_spec(
        self, bridge: ACPSkillBridge, sample_command: SkillCommand
    ) -> None:
        """Verify commands have proper input specification."""
        bridge.handle_change("test_skill", sample_command)

        commands = bridge.get_available_commands()

        assert len(commands) == 1
        cmd = commands[0]
        assert cmd.input is not None
        assert isinstance(cmd.input, AvailableCommandInput)
        assert isinstance(cmd.input.root, CommandInputHint)

    def test_handle_change_removes_nonexistent_command_safely(self, bridge: ACPSkillBridge) -> None:
        """Test that removing a non-existent command does not raise an error."""
        # Should not raise KeyError
        bridge.handle_change("nonexistent", None)

        assert len(bridge._commands) == 0

    def test_replace_existing_command(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that adding command with same name replaces existing."""
        bridge.handle_change("test_skill", skill_command)

        # Create modified command with same name
        modified_cmd = SkillCommand(
            name="test_skill",
            description="Modified description",
            skill=skill_command.skill,
            input_hint="Modified hint",
        )

        bridge.handle_change("test_skill", modified_cmd)

        commands = bridge.get_available_commands()
        assert len(commands) == 1
        assert commands[0].description == "Modified description"
        assert commands[0].input is not None
        assert commands[0].input.root.hint == "Modified hint"

    def test_get_available_commands_returns_copy(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that get_available_commands returns a copy of the list."""
        bridge.handle_change("test_skill", skill_command)

        commands1 = bridge.get_available_commands()
        commands2 = bridge.get_available_commands()

        # Should be equal but not the same object
        assert commands1 == commands2
        assert commands1 is not commands2

    def test_accessing_removed_command_returns_empty_list(
        self, bridge: ACPSkillBridge, skill_command: SkillCommand
    ) -> None:
        """Test that accessing removed command returns empty list."""
        bridge.handle_change("test_skill", skill_command)
        bridge.handle_change("test_skill", None)

        commands = bridge.get_available_commands()

        assert commands == []


# =============================================================================
# TestIntegrationWithRegistry
# =============================================================================


class TestIntegrationWithRegistry:
    """Tests for ACPSkillBridge integration with SkillCommandRegistry.

    These tests verify that the bridge properly integrates with the
    SkillCommandRegistry and receives updates when commands change.
    """

    def test_bridge_receives_registry_changes(self, skill_registry: SkillCommandRegistry) -> None:
        """Verify bridge receives commands added to SkillCommandRegistry."""
        bridge = ACPSkillBridge()

        # Register bridge as change handler
        skill_registry.on_command_change(bridge.handle_change)

        # Create a mock skill and command
        mock_skill = MagicMock()
        mock_skill.name = "registry_test_skill"

        cmd = SkillCommand(
            name="registry_test_skill",
            description="Test skill from registry",
            skill=mock_skill,
        )

        # Register command should trigger handler
        skill_registry.register("registry_test_skill", cmd)

        # Verify bridge received the command
        assert len(bridge.get_available_commands()) == 1
        assert bridge._commands["registry_test_skill"].name == "registry_test_skill"

    def test_commands_updated_on_runtime_change(self, skill_registry: SkillCommandRegistry) -> None:
        """Test runtime skill updates are reflected in bridge."""
        bridge = ACPSkillBridge()
        skill_registry.on_command_change(bridge.handle_change)

        # Add initial command
        mock_skill1 = MagicMock()
        mock_skill1.name = "runtime_skill"
        cmd1 = SkillCommand(
            name="runtime_skill",
            description="Initial version",
            skill=mock_skill1,
        )
        skill_registry.register("runtime_skill", cmd1)

        assert bridge._commands["runtime_skill"].description == "Initial version"

        # Update with new version (replace)
        mock_skill2 = MagicMock()
        mock_skill2.name = "runtime_skill"
        cmd2 = SkillCommand(
            name="runtime_skill",
            description="Updated version",
            skill=mock_skill2,
        )
        skill_registry.register("runtime_skill", cmd2, replace=True)

        # Verify updated
        assert bridge._commands["runtime_skill"].description == "Updated version"
        assert len(bridge.get_available_commands()) == 1

    def test_bridge_handles_removal_from_registry(
        self, skill_registry: SkillCommandRegistry
    ) -> None:
        """Test bridge handles command removal from registry."""
        bridge = ACPSkillBridge()
        skill_registry.on_command_change(bridge.handle_change)

        # Add command
        mock_skill = MagicMock()
        mock_skill.name = "removable_skill"
        cmd = SkillCommand(
            name="removable_skill",
            description="Will be removed",
            skill=mock_skill,
        )
        skill_registry.register("removable_skill", cmd)

        assert len(bridge.get_available_commands()) == 1

        # Remove command
        del skill_registry["removable_skill"]

        assert len(bridge.get_available_commands()) == 0
        assert "removable_skill" not in bridge._commands

    def test_bridge_receives_initial_state_on_registration(
        self, skill_registry: SkillCommandRegistry
    ) -> None:
        """Test bridge receives existing commands when registering handler."""
        # Add commands before bridge registration
        mock_skill1 = MagicMock()
        mock_skill1.name = "pre_existing_skill1"
        cmd1 = SkillCommand(
            name="pre_existing_skill1",
            description="Pre-existing skill 1",
            skill=mock_skill1,
        )
        skill_registry.register("pre_existing_skill1", cmd1)

        mock_skill2 = MagicMock()
        mock_skill2.name = "pre_existing_skill2"
        cmd2 = SkillCommand(
            name="pre_existing_skill2",
            description="Pre-existing skill 2",
            skill=mock_skill2,
        )
        skill_registry.register("pre_existing_skill2", cmd2)

        # Now create and register bridge
        bridge = ACPSkillBridge()
        skill_registry.on_command_change(bridge.handle_change)

        # Should receive all pre-existing commands
        commands = bridge.get_available_commands()
        assert len(commands) == 2
        names = {cmd.name for cmd in commands}
        assert names == {"pre_existing_skill1", "pre_existing_skill2"}

    def test_multiple_handlers_can_be_registered(
        self, skill_registry: SkillCommandRegistry
    ) -> None:
        """Test that multiple bridges/handlers can be registered."""
        bridge1 = ACPSkillBridge()
        bridge2 = ACPSkillBridge()

        skill_registry.on_command_change(bridge1.handle_change)
        skill_registry.on_command_change(bridge2.handle_change)

        # Add command
        mock_skill = MagicMock()
        mock_skill.name = "multi_handler_skill"
        cmd = SkillCommand(
            name="multi_handler_skill",
            description="Test with multiple handlers",
            skill=mock_skill,
        )
        skill_registry.register("multi_handler_skill", cmd)

        # Both bridges should have the command
        assert len(bridge1.get_available_commands()) == 1
        assert len(bridge2.get_available_commands()) == 1

    def test_handler_signature_matches_expected(self, bridge: ACPSkillBridge) -> None:
        """Verify handle_change method matches CommandChangeHandler signature."""
        # Should be callable as CommandChangeHandler
        handler: CommandChangeHandler = bridge.handle_change

        # Test with None (remove operation)
        handler("test", None)

        # Test with SkillCommand (add operation)
        mock_skill = MagicMock()
        mock_skill.name = "sig_test_skill"
        cmd = SkillCommand(
            name="sig_test_skill",
            description="Test signature",
            skill=mock_skill,
        )
        handler("sig_test_skill", cmd)

        assert "sig_test_skill" in bridge._commands

    def test_registry_integration_with_real_skill(
        self, skill_registry: SkillCommandRegistry, sample_skill: Skill
    ) -> None:
        """Test integration using a real Skill instance."""
        bridge = ACPSkillBridge()
        skill_registry.on_command_change(bridge.handle_change)

        # Create command with real skill
        cmd = SkillCommand(
            name=sample_skill.name,
            description=sample_skill.description,
            skill=sample_skill,
        )

        skill_registry.register(sample_skill.name, cmd)

        commands = bridge.get_available_commands()
        assert len(commands) == 1
        assert commands[0].name == sample_skill.name
        assert commands[0].description == sample_skill.description


# =============================================================================
# Legacy Tests (Preserved for backward compatibility)
# =============================================================================


class TestACPSkillBridge:
    """Original test class preserved for backward compatibility."""

    def test_bridge_initialized_with_empty_commands(self) -> None:
        """Test that bridge is initialized with empty commands dictionary."""
        bridge = ACPSkillBridge()

        assert bridge._commands == {}
        assert bridge.get_available_commands() == []

    def test_handle_change_adds_command(self, skill_command: SkillCommand) -> None:
        """Test that handle_change adds command when command is not None."""
        bridge = ACPSkillBridge()

        bridge.handle_change("test_skill", skill_command)

        assert "test_skill" in bridge._commands
        assert len(bridge._commands) == 1

    def test_handle_change_removes_command(self, skill_command: SkillCommand) -> None:
        """Test that handle_change removes command when command is None."""
        bridge = ACPSkillBridge()

        # First add the command
        bridge.handle_change("test_skill", skill_command)
        assert "test_skill" in bridge._commands

        # Then remove it
        bridge.handle_change("test_skill", None)

        assert "test_skill" not in bridge._commands
        assert len(bridge._commands) == 0

    def test_handle_change_removes_nonexistent_command_safely(self) -> None:
        """Test that removing a non-existent command does not raise an error."""
        bridge = ACPSkillBridge()

        # Should not raise KeyError
        bridge.handle_change("nonexistent", None)

        assert len(bridge._commands) == 0

    def test_get_available_commands_returns_list(self, skill_command: SkillCommand) -> None:
        """Test that get_available_commands returns a list of AvailableCommand."""
        bridge = ACPSkillBridge()
        bridge.handle_change("test_skill", skill_command)

        commands = bridge.get_available_commands()

        assert isinstance(commands, list)
        assert len(commands) == 1
        assert isinstance(commands[0], AvailableCommand)

    def test_multiple_commands_can_be_stored(
        self, skill_command: SkillCommand, skill_command2: SkillCommand
    ) -> None:
        """Test that multiple commands can be stored."""
        bridge = ACPSkillBridge()

        bridge.handle_change("test_skill", skill_command)
        bridge.handle_change("another_skill", skill_command2)

        commands = bridge.get_available_commands()

        assert len(commands) == 2
        names = {cmd.name for cmd in commands}
        assert names == {"test_skill", "another_skill"}

    def test_accessing_removed_command_returns_empty_list(
        self, skill_command: SkillCommand
    ) -> None:
        """Test that accessing removed command returns empty list."""
        bridge = ACPSkillBridge()

        bridge.handle_change("test_skill", skill_command)
        bridge.handle_change("test_skill", None)

        commands = bridge.get_available_commands()

        assert commands == []

    def test_conversion_preserves_name_and_description(self, skill_command: SkillCommand) -> None:
        """Test that conversion preserves name and description."""
        bridge = ACPSkillBridge()

        acp_cmd = bridge._to_acp_command(skill_command)

        assert acp_cmd.name == skill_command.name
        assert acp_cmd.description == skill_command.description

    def test_conversion_with_input_hint(self, skill_command: SkillCommand) -> None:
        """Test that command with input hint is converted correctly."""
        bridge = ACPSkillBridge()

        acp_cmd = bridge._to_acp_command(skill_command)

        assert acp_cmd.input is not None
        assert isinstance(acp_cmd.input, AvailableCommandInput)
        assert isinstance(acp_cmd.input.root, CommandInputHint)
        assert acp_cmd.input is not None
        assert acp_cmd.input.root.hint == skill_command.input_hint

    def test_conversion_default_input_hint(self, mock_skill: MagicMock) -> None:
        """Test that default input hint is used when not specified."""
        bridge = ACPSkillBridge()
        # Create command with default input_hint
        cmd = SkillCommand(
            name="default_cmd",
            description="A command with default hint",
            skill=mock_skill,
        )

        acp_cmd = bridge._to_acp_command(cmd)

        assert acp_cmd.input is not None
        assert acp_cmd.input is not None
        assert acp_cmd.input.root.hint == "Arguments for skill"  # Default value

    def test_handle_change_matches_command_change_handler_signature(
        self, skill_command: SkillCommand
    ) -> None:
        """Test that handle_change matches CommandChangeHandler signature."""
        bridge = ACPSkillBridge()

        # Verify the method can be used as a CommandChangeHandler
        handler: CommandChangeHandler = bridge.handle_change

        # Should accept (name, command) for add
        handler("test_skill", skill_command)
        assert "test_skill" in bridge._commands

        # Should accept (name, None) for remove
        handler("test_skill", None)
        assert "test_skill" not in bridge._commands

    def test_replace_existing_command(self, skill_command: SkillCommand) -> None:
        """Test that adding command with same name replaces existing."""
        bridge = ACPSkillBridge()

        bridge.handle_change("test_skill", skill_command)

        # Create modified command with same name
        modified_cmd = SkillCommand(
            name="test_skill",
            description="Modified description",
            skill=skill_command.skill,
            input_hint="Modified hint",
        )

        bridge.handle_change("test_skill", modified_cmd)

        commands = bridge.get_available_commands()
        assert len(commands) == 1
        assert commands[0].description == "Modified description"
        assert commands[0].input is not None
        assert commands[0].input.root.hint == "Modified hint"

    def test_get_available_commands_returns_copy(self, skill_command: SkillCommand) -> None:
        """Test that get_available_commands returns a copy of the list."""
        bridge = ACPSkillBridge()
        bridge.handle_change("test_skill", skill_command)

        commands1 = bridge.get_available_commands()
        commands2 = bridge.get_available_commands()

        # Should be equal but not the same object
        assert commands1 == commands2
        assert commands1 is not commands2
