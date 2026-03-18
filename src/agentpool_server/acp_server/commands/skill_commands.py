"""ACP skill commands bridge for exposing skills as ACP slash commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire

from acp.schema.slash_commands import AvailableCommand, AvailableCommandInput, CommandInputHint
from agentpool.log import get_logger


logger = get_logger(__name__)

if TYPE_CHECKING:
    from agentpool.skills.command import SkillCommand


class ACPSkillBridge:
    """Bridge class that maps SkillCommand to ACP AvailableCommand.

    This class exposes skills as ACP slash commands by converting
    SkillCommand instances to ACP AvailableCommand format. It maintains
    an internal dictionary of commands and provides methods for
    handling add/remove changes.

    Attributes:
        _commands: Dictionary mapping command names to AvailableCommand instances.
    """

    def __init__(self) -> None:
        """Initialize the bridge with an empty command store."""
        self._commands: dict[str, AvailableCommand] = {}

    @logfire.instrument("acp_skill_bridge_handle_change")
    def handle_change(self, name: str, command: SkillCommand | None) -> None:
        """Handle skill command add/remove changes.

        This method matches the CommandChangeHandler signature and is called
        when skills are added or removed from the SkillsRegistry.

        Args:
            name: The name of the command being changed.
            command: The SkillCommand instance if added, None if removed.
        """
        if command is None:
            self._commands.pop(name, None)
        else:
            logger.debug("Converting skill command %s to ACP format", name)
            self._commands[name] = self._to_acp_command(command)
        logger.debug("ACPSkillBridge has %d commands", len(self._commands))

    @logfire.instrument("acp_skill_bridge_convert_command")
    def _to_acp_command(self, skill_cmd: SkillCommand) -> AvailableCommand:
        """Convert SkillCommand to ACP AvailableCommand.

        Args:
            skill_cmd: The SkillCommand to convert.

        Returns:
            An AvailableCommand instance representing the skill in ACP format.
        """
        input_spec = AvailableCommandInput(root=CommandInputHint(hint=skill_cmd.input_hint))
        available_cmd = AvailableCommand(
            name=skill_cmd.name, description=skill_cmd.description, input=input_spec
        )
        logger.debug(
            "Converted skill command to ACP format",
            skill_name=skill_cmd.name,
            has_input_hint=bool(skill_cmd.input_hint),
        )
        return available_cmd

    def get_available_commands(self) -> list[AvailableCommand]:
        """Return list of available commands in ACP format.

        Returns:
            A list of AvailableCommand instances for all stored commands.
        """
        commands = list(self._commands.values())
        logger.debug(
            "Retrieved available ACP commands",
            command_count=len(commands),
            command_names=[cmd.name for cmd in commands],
        )
        return commands
