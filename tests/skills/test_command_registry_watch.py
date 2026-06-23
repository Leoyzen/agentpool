"""Tests for SkillCommandRegistry SkillsRegistry integration and event watching."""

from __future__ import annotations

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill


def create_test_skill(name: str = "test-skill", description: str = "A test skill") -> Skill:
    """Create a minimal test skill."""
    return Skill(
        name=name,
        description=description,
        skill_path=UPath("/tmp/test-skill"),
    )


class TestInitializeWithoutSkillsRegistry:
    """Tests for initialize() with no SkillsRegistry attached."""

    @pytest.mark.asyncio
    async def test_initialize_noop_without_skills_registry(self) -> None:
        """Test that initialize() does nothing when no SkillsRegistry is set."""
        registry = SkillCommandRegistry()

        # Should complete without errors
        await registry.initialize(wait=True)

        assert registry.has_skills is False
        assert registry.has_commands is False

    @pytest.mark.asyncio
    async def test_initialize_noop_with_explicit_none(self) -> None:
        """Test that initialize() handles explicit None."""
        registry = SkillCommandRegistry(skills_registry=None)

        # Should complete without errors
        await registry.initialize(wait=True)

        assert registry.has_skills is False
        assert registry.has_commands is False


class TestInitializeSyncsExistingSkills:
    """Tests for initial sync of existing SkillsRegistry commands."""

    @pytest.mark.asyncio
    async def test_initialize_syncs_single_skill(self) -> None:
        """Test that initialize() syncs a single existing skill."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("existing-skill", "An existing skill")
        skills_registry.register("existing-skill", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        await command_registry.initialize(wait=True)

        assert "existing-skill" in command_registry
        assert command_registry.has_commands is True
        command = command_registry.get("existing-skill")
        assert command.name == "existing-skill"
        assert command.description == "An existing skill"
        assert command.skill is skill

    @pytest.mark.asyncio
    async def test_initialize_syncs_multiple_skills(self) -> None:
        """Test that initialize() syncs multiple existing skills."""
        skills_registry = SkillsRegistry()
        skill1 = create_test_skill("skill-1", "First skill")
        skill2 = create_test_skill("skill-2", "Second skill")
        skill3 = create_test_skill("skill-3", "Third skill")

        skills_registry.register("skill-1", skill1)
        skills_registry.register("skill-2", skill2)
        skills_registry.register("skill-3", skill3)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        await command_registry.initialize(wait=True)

        assert len(command_registry) == 3
        assert "skill-1" in command_registry
        assert "skill-2" in command_registry
        assert "skill-3" in command_registry

    @pytest.mark.asyncio
    async def test_initialize_syncs_to_empty_registry(self) -> None:
        """Test that initialize() works with empty SkillsRegistry."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        await command_registry.initialize(wait=True)

        assert len(command_registry) == 0
        assert command_registry.has_commands is False


class TestInitializeSubscribesToEvents:
    """Tests that initialize() subscribes to SkillsRegistry events."""

    @pytest.mark.asyncio
    async def test_initialize_subscribes_to_add_events(self) -> None:
        """Test that initialize() subscribes to on_skill_added."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        await command_registry.initialize(wait=True)

        # Add a skill after initialization
        skill = create_test_skill("runtime-skill", "Added at runtime")
        skills_registry.register("runtime-skill", skill)

        # Command should be auto-created
        assert "runtime-skill" in command_registry
        command = command_registry.get("runtime-skill")
        assert command.name == "runtime-skill"
        assert command.description == "Added at runtime"

    @pytest.mark.asyncio
    async def test_initialize_subscribes_to_remove_events(self) -> None:
        """Test that initialize() subscribes to on_skill_removed."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("removable-skill", "Will be removed")
        skills_registry.register("removable-skill", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        assert "removable-skill" in command_registry

        # Remove skill from SkillsRegistry
        del skills_registry["removable-skill"]

        # Command should be auto-removed
        assert "removable-skill" not in command_registry


class TestRuntimeSkillAddition:
    """Tests for runtime skill addition via event handlers."""

    @pytest.mark.asyncio
    async def test_runtime_skill_addition_creates_command(self) -> None:
        """Test that adding skill at runtime creates a command."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        skill = create_test_skill("new-skill", "A new skill added at runtime")
        skills_registry.register("new-skill", skill)

        assert "new-skill" in command_registry
        command = command_registry.get("new-skill")
        assert isinstance(command, SkillCommand)
        assert command.skill is skill

    @pytest.mark.asyncio
    async def test_multiple_runtime_additions(self) -> None:
        """Test adding multiple skills at runtime."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        for i in range(5):
            skill = create_test_skill(f"dynamic-skill-{i}", f"Dynamic skill {i}")
            skills_registry.register(f"dynamic-skill-{i}", skill)

        assert len(command_registry) == 5
        for i in range(5):
            assert f"dynamic-skill-{i}" in command_registry


class TestRuntimeSkillRemoval:
    """Tests for runtime skill removal via event handlers."""

    @pytest.mark.asyncio
    async def test_runtime_skill_removal_deletes_command(self) -> None:
        """Test that removing skill at runtime deletes its command."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("to-remove", "Will be removed")
        skills_registry.register("to-remove", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        assert "to-remove" in command_registry

        del skills_registry["to-remove"]

        assert "to-remove" not in command_registry
        assert len(command_registry) == 0

    @pytest.mark.asyncio
    async def test_removing_nonexistent_skill_no_error(self) -> None:
        """Test that removing skill not in command_registry is handled gracefully."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("only-in-registry", "Only in SkillsRegistry")
        skills_registry.register("only-in-registry", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        # Manually remove from command_registry first
        del command_registry["only-in-registry"]
        assert "only-in-registry" not in command_registry

        # Now removing from SkillsRegistry should not crash
        del skills_registry["only-in-registry"]

        # Should still not be in command_registry
        assert "only-in-registry" not in command_registry


class TestReplaceExistingSkills:
    """Tests for replacing existing skills with replace=True."""

    @pytest.mark.asyncio
    async def test_sync_with_replace_updates_existing(self) -> None:
        """Test that _sync_commands uses replace=True to update existing."""
        skills_registry = SkillsRegistry()
        original_skill = create_test_skill("replaceable", "Original description")
        skills_registry.register("replaceable", original_skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        # Manually register a different command with same name
        replacement_skill = create_test_skill("replaceable", "Updated description")

        # Registering same name again via SkillsRegistry should trigger update
        skills_registry.register("replaceable", replacement_skill, replace=True)

        # Command should be updated
        command = command_registry.get("replaceable")
        assert command.description == "Updated description"

    @pytest.mark.asyncio
    async def test_sync_does_not_cause_duplicate_key_error(self) -> None:
        """Test that syncing twice doesn't cause duplicate key errors."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("duplicate-test", "Test skill")
        skills_registry.register("duplicate-test", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        # First initialize
        await command_registry.initialize(wait=True)
        assert "duplicate-test" in command_registry

        # Register another skill
        skill2 = create_test_skill("another-skill", "Another skill")
        skills_registry.register("another-skill", skill2)

        # Initialize again - should not raise error
        await command_registry.initialize(wait=True)

        assert "duplicate-test" in command_registry
        assert "another-skill" in command_registry


class TestCommandChangeBroadcasts:
    """Tests that command change broadcasts occur from runtime updates."""

    @pytest.mark.asyncio
    async def test_addition_broadcasts_to_handlers(self) -> None:
        """Test that skill addition broadcasts command change."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        broadcasts: list[tuple[str, SkillCommand | None]] = []

        def on_change(name: str, command: SkillCommand | None) -> None:
            broadcasts.append((name, command))

        command_registry.on_command_change(on_change)
        await command_registry.initialize(wait=True)

        # Clear broadcasts from initialization
        broadcasts.clear()

        # Add skill at runtime
        skill = create_test_skill("broadcast-skill", "Broadcast test")
        skills_registry.register("broadcast-skill", skill)

        assert len(broadcasts) == 1
        assert broadcasts[0][0] == "broadcast-skill"
        assert broadcasts[0][1] is not None
        assert broadcasts[0][1].name == "broadcast-skill"

    @pytest.mark.asyncio
    async def test_removal_broadcasts_to_handlers(self) -> None:
        """Test that skill removal broadcasts command change."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("remove-broadcast", "Will be removed")
        skills_registry.register("remove-broadcast", skill)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)

        broadcasts: list[tuple[str, SkillCommand | None]] = []

        def on_change(name: str, command: SkillCommand | None) -> None:
            broadcasts.append((name, command))

        await command_registry.initialize(wait=True)
        command_registry.on_command_change(on_change)

        # Clear broadcasts from registration
        broadcasts.clear()

        # Remove skill
        del skills_registry["remove-broadcast"]

        assert len(broadcasts) == 1
        assert broadcasts[0][0] == "remove-broadcast"
        assert broadcasts[0][1] is None


class TestEventHandlerEdgeCases:
    """Tests for edge cases in event handling."""

    @pytest.mark.asyncio
    async def test_subscribe_to_registry_is_protected(self) -> None:
        """Test that _subscribe_to_registry handles None gracefully."""
        command_registry = SkillCommandRegistry(skills_registry=None)

        # Should not raise error
        command_registry._subscribe_to_registry()

    @pytest.mark.asyncio
    async def test_sync_commands_handles_none(self) -> None:
        """Test that _sync_commands handles None gracefully."""
        command_registry = SkillCommandRegistry(skills_registry=None)

        # Should not raise error
        await command_registry._sync_commands()

    @pytest.mark.asyncio
    async def test_on_skill_removed_handles_missing_command(self) -> None:
        """Test that _on_skill_removed handles missing command."""
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        # Call handler directly with non-existent name
        command_registry._on_skill_removed("non-existent", None)

        # Should not raise error
        assert "non-existent" not in command_registry


class TestIntegrationScenarios:
    """Integration tests for realistic scenarios."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_scenario(self) -> None:
        """Test complete lifecycle: init, add, replace, remove."""
        skills_registry = SkillsRegistry()

        # Pre-populate with initial skills
        skill1 = create_test_skill("persistent", "Always present")
        skills_registry.register("persistent", skill1)

        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        await command_registry.initialize(wait=True)

        # Initial state
        assert "persistent" in command_registry

        # Add new skill
        skill2 = create_test_skill("transient", "Will be removed")
        skills_registry.register("transient", skill2)
        assert "transient" in command_registry

        # Replace existing skill
        skill1_replaced = create_test_skill("persistent", "Updated description")
        skills_registry.register("persistent", skill1_replaced, replace=True)
        assert command_registry.get("persistent").description == "Updated description"

        # Remove skill
        del skills_registry["transient"]
        assert "transient" not in command_registry
        assert "persistent" in command_registry

    @pytest.mark.asyncio
    async def test_standalone_mode_no_events(self) -> None:
        """Test that standalone mode (no SkillsRegistry) doesn't subscribe."""
        command_registry = SkillCommandRegistry()

        # Should complete without errors
        await command_registry.initialize(wait=True)

        # Manually register a command
        skill = create_test_skill("manual", "Manually registered")
        command = SkillCommand(name="manual", description="Manual", skill=skill)
        command_registry.register("manual", command)

        assert "manual" in command_registry

    @pytest.mark.asyncio
    async def test_multiple_commands_same_skill_source(self) -> None:
        """Test that multiple command registries can share a SkillsRegistry."""
        skills_registry = SkillsRegistry()
        skill = create_test_skill("shared", "Shared skill")
        skills_registry.register("shared", skill)

        registry1 = SkillCommandRegistry(skills_registry=skills_registry)
        registry2 = SkillCommandRegistry(skills_registry=skills_registry)

        await registry1.initialize(wait=True)
        await registry2.initialize(wait=True)

        assert "shared" in registry1
        assert "shared" in registry2

        # Add new skill
        skill2 = create_test_skill("new", "New skill")
        skills_registry.register("new", skill2)

        assert "new" in registry1
        assert "new" in registry2
