"""Tests for ACP capabilities schema."""

from __future__ import annotations

import pytest

from acp.schema.capabilities import AgentCapabilities
from acp.schema.slash_commands import AvailableCommand


class TestAgentCapabilitiesSlashCommands:
    """Test suite for slash_commands field in AgentCapabilities."""

    def test_default_empty_list(self):
        """Default value should be empty list (backward compatible)."""
        caps = AgentCapabilities()
        assert caps.slash_commands == []

    def test_accepts_empty_list_explicitly(self):
        """AgentCapabilities accepts explicit empty list."""
        caps = AgentCapabilities(slash_commands=[])
        assert caps.slash_commands == []

    def test_accepts_list_of_commands(self):
        """AgentCapabilities accepts list of AvailableCommand."""
        command = AvailableCommand.create(
            name="test_cmd",
            description="Test command",
            input_hint="Provide input",
        )
        caps = AgentCapabilities(slash_commands=[command])
        assert len(caps.slash_commands) == 1
        assert caps.slash_commands[0].name == "test_cmd"
        assert caps.slash_commands[0].description == "Test command"

    def test_multiple_commands(self):
        """AgentCapabilities accepts multiple commands."""
        cmd1 = AvailableCommand.create(name="cmd1", description="First command")
        cmd2 = AvailableCommand.create(name="cmd2", description="Second command")
        caps = AgentCapabilities(slash_commands=[cmd1, cmd2])
        assert len(caps.slash_commands) == 2
        assert caps.slash_commands[0].name == "cmd1"
        assert caps.slash_commands[1].name == "cmd2"

    def test_json_serialization_includes_field(self):
        """JSON serialization includes slash_commands field."""
        caps = AgentCapabilities(slash_commands=[])
        json_data = caps.model_dump(mode="json")
        assert "slash_commands" in json_data
        assert json_data["slash_commands"] == []

    def test_json_serialization_with_commands(self):
        """JSON serialization works with commands."""
        command = AvailableCommand.create(name="my_cmd", description="My command")
        caps = AgentCapabilities(slash_commands=[command])
        json_data = caps.model_dump(mode="json")
        assert "slash_commands" in json_data
        assert len(json_data["slash_commands"]) == 1
        assert json_data["slash_commands"][0]["name"] == "my_cmd"
        assert json_data["slash_commands"][0]["description"] == "My command"

    def test_json_deserialization_without_field(self):
        """Backward compatibility: old JSON without slash_commands works."""
        json_data = {
            "load_session": False,
            "mcp_capabilities": {"http": False, "sse": False},
            "prompt_capabilities": {"audio": False, "embedded_context": False, "image": False},
            "session_capabilities": {},
        }
        caps = AgentCapabilities.model_validate(json_data)
        assert caps.slash_commands == []

    def test_json_deserialization_with_empty_list(self):
        """JSON deserialization with explicit empty list works."""
        json_data = {
            "load_session": False,
            "slash_commands": [],
        }
        caps = AgentCapabilities.model_validate(json_data)
        assert caps.slash_commands == []

    def test_json_deserialization_with_commands(self):
        """JSON deserialization with commands works."""
        json_data = {
            "load_session": False,
            "slash_commands": [
                {"name": "cmd1", "description": "Command 1"},
                {"name": "cmd2", "description": "Command 2", "input": {"hint": "hint text"}},
            ],
        }
        caps = AgentCapabilities.model_validate(json_data)
        assert len(caps.slash_commands) == 2
        assert caps.slash_commands[0].name == "cmd1"
        assert caps.slash_commands[1].name == "cmd2"
        assert caps.slash_commands[1].input is not None
        assert caps.slash_commands[1].input.root.hint == "hint text"

    def test_create_method_accepts_slash_commands(self):
        """create() method accepts slash_commands parameter."""
        command = AvailableCommand.create(name="create_plan", description="Create a plan")
        caps = AgentCapabilities.create(slash_commands=[command])
        assert len(caps.slash_commands) == 1
        assert caps.slash_commands[0].name == "create_plan"

    def test_create_method_default_empty_list(self):
        """create() method defaults to empty list when not provided."""
        caps = AgentCapabilities.create()
        assert caps.slash_commands == []

    def test_field_is_not_none_type(self):
        """slash_commands is list type, not optional None."""
        caps = AgentCapabilities()
        # Should be list, not None
        assert caps.slash_commands is not None
        assert isinstance(caps.slash_commands, list)
