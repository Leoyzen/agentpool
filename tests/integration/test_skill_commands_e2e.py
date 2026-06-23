"""End-to-end tests for skill slash commands across all protocols.

These tests verify the complete flow of skill discovery, registration,
and exposure across ACP, AG-UI, and OpenCode protocols.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import pytest
from upathtools import UPath

from agentpool.skills.command import SkillCommand
from agentpool.skills.command_registry import SkillCommandRegistry
from agentpool.skills.registry import SkillsRegistry
from agentpool.skills.skill import Skill
from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
from agentpool_server.agui_server.skill_tools import AGUISkillBridge
from agentpool_server.opencode_server.skill_bridge import OpenCodeSkillBridge

if TYPE_CHECKING:
    pass

# Import AvailableCommand at runtime for isinstance checks
from acp.schema.slash_commands import AvailableCommand


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def skills_dir() -> Path:
    """Return path to test skills directory."""
    return Path(__file__).parent.parent / "data" / "test_skills"


@pytest.fixture
def skills_dir_upath(skills_dir: Path) -> UPath:
    """Return UPath to test skills directory."""
    return UPath(str(skills_dir))


@pytest.fixture
async def skill_registry(skills_dir_upath: UPath) -> AsyncGenerator[SkillsRegistry]:
    """Create a SkillsRegistry loaded with test skills from filesystem."""
    registry = SkillsRegistry(skills_dirs=[skills_dir_upath])
    await registry.discover_skills()
    yield registry


@pytest.fixture
async def command_registry(
    skill_registry: SkillsRegistry,
) -> AsyncGenerator[SkillCommandRegistry]:
    """Create a SkillCommandRegistry initialized with skills."""
    registry = SkillCommandRegistry(skills_registry=skill_registry)
    await registry.initialize()
    yield registry


@pytest.fixture
def acp_bridge() -> ACPSkillBridge:
    """Create an ACP skill bridge."""
    return ACPSkillBridge()


@pytest.fixture
def agui_bridge() -> AGUISkillBridge:
    """Create an AG-UI skill bridge."""
    return AGUISkillBridge()


@pytest.fixture
def opencode_bridge() -> OpenCodeSkillBridge:
    """Create an OpenCode skill bridge."""
    return OpenCodeSkillBridge()


@pytest.fixture
def mock_skill() -> Skill:
    """Create a mock skill for testing."""
    return Skill(
        name="mock-skill",
        description="A mock skill for testing",
        skill_path=UPath("/tmp/mock-skill"),
        license="MIT",
        compatibility="1.0.0",
        allowed_tools="bash,read",
    )


@pytest.fixture
def mock_skill_command(mock_skill: Skill) -> SkillCommand:
    """Create a mock skill command for testing."""
    return SkillCommand(
        name="mock-skill",
        description="A mock skill for testing",
        skill=mock_skill,
    )


# =============================================================================
# Test Class: TestSkillDiscovery
# =============================================================================


@pytest.mark.integration
class TestSkillDiscovery:
    """Test skill discovery from filesystem."""

    async def test_skills_loaded_from_directory(self, skills_dir: Path) -> None:
        """Test that skills are discovered and loaded from filesystem."""
        registry = SkillsRegistry(skills_dirs=[UPath(str(skills_dir))])
        await registry.discover_skills()

        # Verify all test skills were loaded
        skill_names = registry.list_items()
        assert "hello-world" in skill_names
        assert "test-with-args" in skill_names
        assert "test-lifecycle" in skill_names
        assert len(skill_names) == 3

    async def test_skills_loaded_with_correct_metadata(
        self, skill_registry: SkillsRegistry
    ) -> None:
        """Test that skills are loaded with correct metadata from SKILL.md."""
        hello_skill = skill_registry.get("hello-world")

        assert hello_skill.name == "hello-world"
        assert "greeting" in hello_skill.description.lower()
        assert hello_skill.license == "MIT"
        assert hello_skill.compatibility == "1.0.0"
        assert hello_skill.allowed_tools == "bash, read"

        args_skill = skill_registry.get("test-with-args")
        assert args_skill.name == "test-with-args"
        assert args_skill.license == "Apache-2.0"
        assert args_skill.metadata.get("category") == "testing"

    async def test_skills_available_in_all_protocols(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that discovered skills are available for all protocol bridges."""
        # Create bridges
        acp = ACPSkillBridge()
        agui = AGUISkillBridge()
        opencode = OpenCodeSkillBridge()

        # Subscribe to command changes
        command_registry.on_command_change(acp.handle_change)
        command_registry.on_command_change(agui.handle_change)
        command_registry.on_command_change(opencode.handle_change)

        # Verify all bridges have the skills
        acp_commands = acp.get_available_commands()
        agui_tools = agui.get_tools()
        opencode_commands = opencode.get_commands()

        command_names = {cmd.name for cmd in acp_commands}
        tool_names = {tool.name.removeprefix("skill__") for tool in agui_tools}
        open_names = {cmd.name.removeprefix("skill:") for cmd in opencode_commands}

        expected_skills = {"hello-world", "test-with-args", "test-lifecycle"}

        assert command_names == expected_skills
        assert tool_names == expected_skills
        assert open_names == expected_skills
        assert len(acp_commands) == 3
        assert len(agui_tools) == 3
        assert len(opencode_commands) == 3

    async def test_cross_protocol_consistency(self, command_registry: SkillCommandRegistry) -> None:
        """Test that skill names and descriptions are consistent across protocols."""
        # Create bridges
        acp = ACPSkillBridge()
        agui = AGUISkillBridge()
        opencode = OpenCodeSkillBridge()

        # Subscribe to command changes
        command_registry.on_command_change(acp.handle_change)
        command_registry.on_command_change(agui.handle_change)
        command_registry.on_command_change(opencode.handle_change)

        # Get commands from all bridges
        acp_commands = {cmd.name: cmd for cmd in acp.get_available_commands()}
        agui_tools = {tool.name.removeprefix("skill__"): tool for tool in agui.get_tools()}
        open_commands = {cmd.name.removeprefix("skill:"): cmd for cmd in opencode.get_commands()}

        # Verify descriptions match
        for skill_name in ["hello-world", "test-with-args", "test-lifecycle"]:
            skill = command_registry.get(skill_name)
            assert skill is not None

            # ACP description
            assert acp_commands[skill_name].description == skill.description

            # AG-UI description
            assert agui_tools[skill_name].description == skill.description

            # OpenCode description
            assert open_commands[skill_name].description == skill.description


# =============================================================================
# Test Class: TestACPEndToEnd
# =============================================================================


@pytest.mark.integration
class TestACPEndToEnd:
    """End-to-end tests for ACP protocol skill command exposure."""

    async def test_acp_server_exposes_skill_commands(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that ACP server exposes skills as AvailableCommand objects."""
        bridge = ACPSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        commands = bridge.get_available_commands()

        assert len(commands) == 3
        for cmd in commands:
            assert isinstance(cmd, AvailableCommand)
            assert cmd.name in ["hello-world", "test-with-args", "test-lifecycle"]
            assert cmd.description is not None
            assert len(cmd.description) > 0

    async def test_acp_capabilities_include_skills(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that ACP capabilities include all discovered skills."""
        bridge = ACPSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        commands = bridge.get_available_commands()

        # Verify structure matches ACP spec
        for cmd in commands:
            assert hasattr(cmd, "name")
            assert hasattr(cmd, "description")
            assert hasattr(cmd, "input")
            # Input should have hint
            assert cmd.input is not None
            assert cmd.input.root is not None

    async def test_acp_commands_have_correct_format(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that ACP commands follow the correct format."""
        bridge = ACPSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        commands = bridge.get_available_commands()
        command_dict = {cmd.name: cmd for cmd in commands}

        # Test hello-world command format
        hello_cmd = command_dict["hello-world"]
        assert hello_cmd.name == "hello-world"
        assert "greeting" in hello_cmd.description.lower()
        assert hello_cmd.input is not None
        assert hello_cmd.input.root.hint is not None

    async def test_acp_skill_lifecycle_updates(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that ACP bridge receives live updates on skill changes."""
        bridge = ACPSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Initial state
        assert len(bridge.get_available_commands()) == 3

        # Add new command
        mock_skill = Skill(
            name="dynamic-skill",
            description="A dynamically added skill",
            skill_path=UPath("/tmp/dynamic"),
        )
        mock_command = SkillCommand(
            name="dynamic-skill",
            description="A dynamically added skill",
            skill=mock_skill,
        )
        command_registry.register("dynamic-skill", mock_command)

        # Verify bridge received update
        commands = bridge.get_available_commands()
        assert len(commands) == 4
        command_names = {cmd.name for cmd in commands}
        assert "dynamic-skill" in command_names

        # Remove command
        del command_registry["dynamic-skill"]

        # Verify bridge received removal
        commands = bridge.get_available_commands()
        assert len(commands) == 3
        command_names = {cmd.name for cmd in commands}
        assert "dynamic-skill" not in command_names


# =============================================================================
# Test Class: TestAGUIEndToEnd
# =============================================================================


@pytest.mark.integration
class TestAGUIEndToEnd:
    """End-to-end tests for AG-UI protocol skill tool exposure."""

    async def test_agui_tools_include_skills(self, command_registry: SkillCommandRegistry) -> None:
        """Test that AG-UI exposes skills as Tools with proper format."""
        bridge = AGUISkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        tools = bridge.get_tools()

        assert len(tools) == 3
        for tool in tools:
            assert tool.name.startswith("skill__")
            skill_name = tool.name.removeprefix("skill__")
            assert skill_name in ["hello-world", "test-with-args", "test-lifecycle"]

    async def test_agui_tool_format_is_correct(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that AG-UI tools follow OpenAI function format."""
        bridge = AGUISkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        tools = bridge.get_tools()
        tool_dict = {tool.name: tool for tool in tools}

        # Verify structure
        hello_tool = tool_dict["skill__hello-world"]
        assert hello_tool.name == "skill__hello-world"
        assert "greeting" in hello_tool.description.lower()

        # Verify parameters schema
        assert hello_tool.parameters["type"] == "object"
        assert "properties" in hello_tool.parameters
        assert "arguments" in hello_tool.parameters["properties"]
        assert "required" in hello_tool.parameters
        assert "arguments" in hello_tool.parameters["required"]

    async def test_agui_skills_have_prefix(self, command_registry: SkillCommandRegistry) -> None:
        """Test that all AG-UI skill tools have the skill__ prefix."""
        bridge = AGUISkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        tools = bridge.get_tools()

        for tool in tools:
            assert tool.name.startswith("skill__"), f"Tool {tool.name} missing skill__ prefix"
            # Should have exactly one double underscore
            assert "__" in tool.name
            # Should not have triple underscore
            assert "___" not in tool.name

    async def test_agui_handler_lookup(self, command_registry: SkillCommandRegistry) -> None:
        """Test that AG-UI handler can look up skills by tool name."""
        bridge = AGUISkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Test valid lookups
        adapter = bridge.get_handler("skill__hello-world")
        assert adapter is not None
        assert adapter.skill_cmd.name == "hello-world"

        adapter = bridge.get_handler("skill__test-with-args")
        assert adapter is not None
        assert adapter.skill_cmd.name == "test-with-args"

        # Test invalid lookups
        assert bridge.get_handler("hello-world") is None  # Missing prefix
        assert bridge.get_handler("skill__nonexistent") is None  # Non-existent
        assert bridge.get_handler("other__prefix") is None  # Wrong prefix

    async def test_agui_skill_lifecycle_updates(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that AG-UI bridge receives live updates on skill changes."""
        bridge = AGUISkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Initial state
        assert len(bridge.get_tools()) == 3

        # Add new skill
        mock_skill = Skill(
            name="agui-dynamic",
            description="Dynamic AG-UI skill",
            skill_path=UPath("/tmp/agui-dynamic"),
        )
        mock_command = SkillCommand(
            name="agui-dynamic",
            description="Dynamic AG-UI skill",
            skill=mock_skill,
        )
        command_registry.register("agui-dynamic", mock_command)

        # Verify update
        tools = bridge.get_tools()
        assert len(tools) == 4
        tool_names = {tool.name for tool in tools}
        assert "skill__agui-dynamic" in tool_names

        # Remove skill
        del command_registry["agui-dynamic"]

        # Verify removal
        tools = bridge.get_tools()
        assert len(tools) == 3
        tool_names = {tool.name for tool in tools}
        assert "skill__agui-dynamic" not in tool_names


# =============================================================================
# Test Class: TestOpenCodeEndToEnd
# =============================================================================


@pytest.mark.integration
class TestOpenCodeEndToEnd:
    """End-to-end tests for OpenCode protocol skill command exposure."""

    async def test_opencode_commands_registered(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that OpenCode server registers skills as slashed commands."""
        bridge = OpenCodeSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        commands = bridge.get_commands()

        assert len(commands) == 3
        for cmd in commands:
            assert cmd.name in ["hello-world", "test-with-args", "test-lifecycle"]

    async def test_opencode_command_format(self, command_registry: SkillCommandRegistry) -> None:
        """Test that OpenCode commands follow slashed command format."""
        bridge = OpenCodeSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        commands = bridge.get_commands()
        command_dict = {cmd.name: cmd for cmd in commands}

        # Verify hello-world format
        hello_cmd = command_dict["hello-world"]
        assert hello_cmd.name == "hello-world"
        assert "greeting" in hello_cmd.description.lower()
        assert hello_cmd.category == "skill"

    async def test_opencode_commands_executable(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that OpenCode commands are callable with execute method."""
        from slashed import CommandContext

        bridge = OpenCodeSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Get a command
        cmd = bridge.get_command("hello-world")
        assert cmd is not None
        # Command should have execute capability (either method or be callable)
        assert hasattr(cmd, "execute") or callable(cmd)

    async def test_opencode_command_lookup(self, command_registry: SkillCommandRegistry) -> None:
        """Test OpenCode command lookup with and without prefix."""
        bridge = OpenCodeSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Lookup with prefix
        cmd_with_prefix = bridge.get_command("skill:hello-world")
        assert cmd_with_prefix is not None

        # Lookup without prefix
        cmd_no_prefix = bridge.get_command("hello-world")
        assert cmd_no_prefix is not None
        assert cmd_with_prefix == cmd_no_prefix

        # Non-existent command
        assert bridge.get_command("nonexistent") is None

    async def test_opencode_skill_lifecycle_updates(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that OpenCode bridge receives live updates on skill changes."""
        bridge = OpenCodeSkillBridge()
        command_registry.on_command_change(bridge.handle_change)

        # Initial state
        assert len(bridge.get_commands()) == 3

        # Add new skill
        mock_skill = Skill(
            name="opencode-dynamic",
            description="Dynamic OpenCode skill",
            skill_path=UPath("/tmp/opencode-dynamic"),
        )
        mock_command = SkillCommand(
            name="opencode-dynamic",
            description="Dynamic OpenCode skill",
            skill=mock_skill,
        )
        command_registry.register("opencode-dynamic", mock_command)

        # Verify update
        commands = bridge.get_commands()
        assert len(commands) == 4

        # Verify with prefix
        assert bridge.get_command("skill:opencode-dynamic") is not None

        # Remove skill
        del command_registry["opencode-dynamic"]

        # Verify removal
        commands = bridge.get_commands()
        assert len(commands) == 3
        assert bridge.get_command("opencode-dynamic") is None


# =============================================================================
# Test Class: TestSkillLifecycle
# =============================================================================


@pytest.mark.integration
class TestSkillLifecycle:
    """End-to-end tests for skill lifecycle management."""

    async def test_skill_add_lifecycle(self, skills_dir_upath: UPath) -> None:
        """Test the complete lifecycle when adding a skill."""
        # Step 1: Create components
        skills_registry = SkillsRegistry(skills_dirs=[skills_dir_upath])
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        acp_bridge = ACPSkillBridge()
        agui_bridge = AGUISkillBridge()
        opencode_bridge = OpenCodeSkillBridge()

        # Step 2: Wire up bridges
        command_registry.on_command_change(acp_bridge.handle_change)
        command_registry.on_command_change(agui_bridge.handle_change)
        command_registry.on_command_change(opencode_bridge.handle_change)

        # Step 3: Discover skills (simulating filesystem discovery)
        await skills_registry.discover_skills()
        await command_registry.initialize(wait=True)

        # Step 4: Verify all protocols have the skills
        assert len(skills_registry.list_items()) == 3
        assert len(command_registry.list_items()) == 3
        assert len(acp_bridge.get_available_commands()) == 3
        assert len(agui_bridge.get_tools()) == 3
        assert len(opencode_bridge.get_commands()) == 3

    async def test_skill_remove_lifecycle(self, command_registry: SkillCommandRegistry) -> None:
        """Test the complete lifecycle when removing a skill."""
        # Create bridges
        acp_bridge = ACPSkillBridge()
        agui_bridge = AGUISkillBridge()
        opencode_bridge = OpenCodeSkillBridge()

        # Subscribe bridges
        command_registry.on_command_change(acp_bridge.handle_change)
        command_registry.on_command_change(agui_bridge.handle_change)
        command_registry.on_command_change(opencode_bridge.handle_change)

        # Initial state
        assert len(command_registry.list_items()) == 3

        # Remove a skill
        del command_registry["hello-world"]

        # Verify removal propagated to all bridges
        assert len(command_registry.list_items()) == 2
        assert "hello-world" not in command_registry.list_items()

        acp_commands = {cmd.name for cmd in acp_bridge.get_available_commands()}
        assert "hello-world" not in acp_commands

        agui_tools = {tool.name.removeprefix("skill__") for tool in agui_bridge.get_tools()}
        assert "hello-world" not in agui_tools

        opencode_commands = {
            cmd.name.removeprefix("skill:") for cmd in opencode_bridge.get_commands()
        }
        assert "hello-world" not in opencode_commands

    async def test_skill_update_propagates(self, command_registry: SkillCommandRegistry) -> None:
        """Test that updating a skill propagates to all protocol bridges."""
        # Create bridges
        acp_bridge = ACPSkillBridge()
        agui_bridge = AGUISkillBridge()
        opencode_bridge = OpenCodeSkillBridge()

        # Subscribe bridges
        command_registry.on_command_change(acp_bridge.handle_change)
        command_registry.on_command_change(agui_bridge.handle_change)
        command_registry.on_command_change(opencode_bridge.handle_change)

        # Get original description
        original_cmd = command_registry.get("hello-world")
        assert original_cmd is not None
        original_desc = original_cmd.description

        # Update skill with new description
        updated_skill = Skill(
            name="hello-world",
            description="Updated description for testing",
            skill_path=UPath("/tmp/hello-world"),
        )
        updated_command = SkillCommand(
            name="hello-world",
            description="Updated description for testing",
            skill=updated_skill,
        )
        command_registry.register("hello-world", updated_command, replace=True)

        # Verify update propagated to ACP
        acp_commands = {cmd.name: cmd for cmd in acp_bridge.get_available_commands()}
        assert acp_commands["hello-world"].description == "Updated description for testing"

        # Verify update propagated to AG-UI
        agui_tools = {tool.name.removeprefix("skill__"): tool for tool in agui_bridge.get_tools()}
        assert agui_tools["hello-world"].description == "Updated description for testing"

    async def test_multiple_skills_batch_operations(self) -> None:
        """Test batch operations with multiple skills."""
        # Create registries
        skills_registry = SkillsRegistry()
        command_registry = SkillCommandRegistry(skills_registry=skills_registry)
        acp_bridge = ACPSkillBridge()

        command_registry.on_command_change(acp_bridge.handle_change)

        # Add multiple skills at once
        skills_data = [
            ("batch-1", "First batch skill"),
            ("batch-2", "Second batch skill"),
            ("batch-3", "Third batch skill"),
        ]

        for name, desc in skills_data:
            skill = Skill(name=name, description=desc, skill_path=UPath(f"/tmp/{name}"))
            cmd = SkillCommand(name=name, description=desc, skill=skill)
            command_registry.register(name, cmd)

        # Verify all added
        assert len(command_registry.list_items()) == 3
        assert len(acp_bridge.get_available_commands()) == 3

        # Remove multiple
        del command_registry["batch-1"]
        del command_registry["batch-2"]

        # Verify removals
        assert len(command_registry.list_items()) == 1
        assert len(acp_bridge.get_available_commands()) == 1
        assert command_registry.list_items() == ["batch-3"]

    async def test_skill_registration_replace_behavior(self) -> None:
        """Test that skill registration with replace=True updates without error."""
        registry = SkillsRegistry()
        skill = Skill(
            name="idempotent-skill",
            description="A test skill",
            skill_path=UPath("/tmp/test"),
        )

        # Register initially
        registry.register("idempotent-skill", skill)

        # Register with replace=True should update without error
        skill_updated = Skill(
            name="idempotent-skill",
            description="Updated description",
            skill_path=UPath("/tmp/test"),
        )
        registry.register("idempotent-skill", skill_updated, replace=True)

        # Should only be one entry with updated description
        assert len(registry) == 1
        assert registry.list_items() == ["idempotent-skill"]
        assert registry.get("idempotent-skill").description == "Updated description"


# =============================================================================
# Test Class: TestCrossProtocolConsistency
# =============================================================================


@pytest.mark.integration
class TestCrossProtocolConsistency:
    """Tests to verify consistency across all protocol bridges."""

    async def test_all_protocols_have_same_skill_set(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that all protocols expose the exact same set of skills."""
        # Create all bridges
        acp = ACPSkillBridge()
        agui = AGUISkillBridge()
        opencode = OpenCodeSkillBridge()

        # Subscribe all
        command_registry.on_command_change(acp.handle_change)
        command_registry.on_command_change(agui.handle_change)
        command_registry.on_command_change(opencode.handle_change)

        # Get skill names from each protocol
        acp_names = {cmd.name for cmd in acp.get_available_commands()}
        agui_names = {tool.name.removeprefix("skill__") for tool in agui.get_tools()}
        open_names = {cmd.name.removeprefix("skill:") for cmd in opencode.get_commands()}

        # All should match
        assert acp_names == agui_names == open_names
        assert acp_names == {"hello-world", "test-with-args", "test-lifecycle"}

    async def test_skill_ordering_consistency(self, command_registry: SkillCommandRegistry) -> None:
        """Test that skill ordering is consistent (alphabetical or insertion order)."""
        # Create bridges
        acp = ACPSkillBridge()
        agui = AGUISkillBridge()

        command_registry.on_command_change(acp.handle_change)
        command_registry.on_command_change(agui.handle_change)

        # Get ordered lists
        acp_names = [cmd.name for cmd in acp.get_available_commands()]
        agui_names = [tool.name.removeprefix("skill__") for tool in agui.get_tools()]

        # Should be consistent ordering
        assert acp_names == agui_names

    async def test_protocol_specific_naming_conventions(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test that each protocol uses its naming convention correctly."""
        acp = ACPSkillBridge()
        agui = AGUISkillBridge()
        opencode = OpenCodeSkillBridge()

        command_registry.on_command_change(acp.handle_change)
        command_registry.on_command_change(agui.handle_change)
        command_registry.on_command_change(opencode.handle_change)

        # ACP: No prefix, exact skill name
        for cmd in acp.get_available_commands():
            assert "skill" not in cmd.name.lower() or "hello" in cmd.name
            assert "__" not in cmd.name
            assert ":" not in cmd.name

        # AG-UI: skill__ prefix
        for tool in agui.get_tools():
            assert tool.name.startswith("skill__")
            assert "___" not in tool.name  # No triple underscore

        # OpenCode: no prefix (plain skill name)
        for cmd in opencode.get_commands():
            assert "__" not in cmd.name
            assert ":" not in cmd.name


# =============================================================================
# Test Class: TestErrorHandling
# =============================================================================


@pytest.mark.integration
class TestErrorHandling:
    """Tests for error handling in skill command system."""

    async def test_empty_registry_behavior(self) -> None:
        """Test behavior with empty command registry."""
        empty_registry = SkillCommandRegistry()
        await empty_registry.initialize()

        acp = ACPSkillBridge()
        agui = AGUISkillBridge()
        opencode = OpenCodeSkillBridge()

        empty_registry.on_command_change(acp.handle_change)
        empty_registry.on_command_change(agui.handle_change)
        empty_registry.on_command_change(opencode.handle_change)

        # Should all be empty but not error
        assert acp.get_available_commands() == []
        assert agui.get_tools() == []
        assert opencode.get_commands() == []

    async def test_invalid_skill_name_handling(self) -> None:
        """Test handling of skills with invalid names."""
        registry = SkillsRegistry()

        # Try to create skill with invalid name (will fail validation)
        with pytest.raises(ValueError):
            Skill(
                name="Invalid Name With Spaces",  # Invalid: has spaces
                description="Test",
                skill_path=UPath("/tmp/test"),
            )

        with pytest.raises(ValueError):
            Skill(
                name="Invalid-",  # Invalid: ends with hyphen
                description="Test",
                skill_path=UPath("/tmp/test"),
            )

        with pytest.raises(ValueError):
            Skill(
                name="-Invalid",  # Invalid: starts with hyphen
                description="Test",
                skill_path=UPath("/tmp/test"),
            )

    async def test_skill_lookup_error_handling(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test error handling when looking up non-existent skills."""
        from agentpool.tools.exceptions import ToolError

        # Non-existent skill check using membership
        assert "nonexistent-skill" not in command_registry.list_items()

        # AG-UI handler should return None for non-existent
        agui = AGUISkillBridge()
        command_registry.on_command_change(agui.handle_change)
        assert agui.get_handler("skill__nonexistent") is None

        # OpenCode should return None for non-existent
        opencode = OpenCodeSkillBridge()
        command_registry.on_command_change(opencode.handle_change)
        assert opencode.get_command("nonexistent") is None

        # Verify ToolError is raised for direct access to non-existent key
        with pytest.raises(ToolError):
            _ = command_registry["nonexistent-skill"]


# =============================================================================
# Test Class: TestSkillInstructionsLoading
# =============================================================================


@pytest.mark.integration
class TestSkillInstructionsLoading:
    """Tests for skill instructions loading functionality."""

    async def test_skill_instructions_lazy_loading(self, skill_registry: SkillsRegistry) -> None:
        """Test that skill instructions are lazy-loaded from SKILL.md."""
        skill = skill_registry.get("hello-world")

        # Initial state: instructions not loaded yet
        assert skill.instructions is None

        # Load instructions
        instructions = skill.load_instructions()

        # Should now have content
        assert skill.instructions is not None
        assert len(instructions) > 0
        assert "greeting" in instructions.lower()

        # Subsequent loads should return cached value
        instructions2 = skill.load_instructions()
        assert instructions2 == instructions

    async def test_instructions_from_skill_command(
        self, command_registry: SkillCommandRegistry
    ) -> None:
        """Test accessing instructions through skill command."""
        cmd = command_registry.get("hello-world")
        assert cmd is not None

        # Access via skill reference
        instructions = cmd.skill.load_instructions()
        assert len(instructions) > 0
        assert "greeting" in instructions.lower()

    async def test_instructions_available_in_all_skills(
        self, skill_registry: SkillsRegistry
    ) -> None:
        """Test that all discovered skills have loadable instructions."""
        for skill_name in skill_registry.list_items():
            skill = skill_registry.get(skill_name)
            instructions = skill.load_instructions()
            assert len(instructions) > 0, f"Skill {skill_name} has no instructions"
