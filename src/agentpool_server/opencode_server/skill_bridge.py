"""OpenCode skill bridge for exposing skills as slashed Commands."""

from __future__ import annotations

import hashlib
import time
from typing import TYPE_CHECKING, Any

import logfire
from slashed import Command as SlashedCommand, CommandContext

from agentpool.log import get_logger


logger = get_logger(__name__)

if TYPE_CHECKING:
    from agentpool.skills.command import SkillCommand


class SkillCommandWrapper:
    """Wrapper exposing SkillCommand properties for OpenCode integration."""

    def __init__(self, skill_cmd: SkillCommand) -> None:
        self._skill_cmd = skill_cmd
        self.name = f"skill:{skill_cmd.name}"
        self.description = skill_cmd.description
        self.category = skill_cmd.category


def _hash_args(args: list[str], kwargs: dict[str, str]) -> str:
    """Hash arguments for privacy in logging.

    Args:
        args: The positional arguments.
        kwargs: The keyword arguments.

    Returns:
        A short hash prefix for tracking purposes.
    """
    content = str(args) + str(sorted(kwargs.items()))
    return hashlib.sha256(content.encode()).hexdigest()[:16]


def create_skill_command(skill_cmd: SkillCommand) -> SlashedCommand:
    """Create a slashed Command from a SkillCommand.

    Args:
        skill_cmd: The skill command to wrap.

    Returns:
        A slashed Command that loads and executes the skill.
    """
    logger.debug("SkillCommand %s initialized", skill_cmd.name)

    async def execute_skill(
        ctx: CommandContext[Any],
        args: list[str],
        kwargs: dict[str, str],
    ) -> None:
        """Execute the skill command."""
        start_time = time.time()
        args_hash = _hash_args(args, kwargs)

        with logfire.span(
            "skill_command_execute",
            skill_name=skill_cmd.name,
            protocol="opencode",
            args_hash=args_hash,
        ):
            logger.info(
                "Executing skill command",
                skill_name=skill_cmd.name,
                args_hash=args_hash,
                arg_count=len(args),
                kwarg_count=len(kwargs),
            )

            # Load skill instructions and pass to agent
            instructions = skill_cmd.skill.load_instructions()
            duration_ms = (time.time() - start_time) * 1000

            if instructions:
                await ctx.print(f"Loading skill: {skill_cmd.name}")
                logger.info(
                    "Skill command executed successfully",
                    skill_name=skill_cmd.name,
                    duration_ms=round(duration_ms, 2),
                    has_instructions=True,
                )
                # The actual skill loading happens via context injection
            else:
                await ctx.print(f"Skill {skill_cmd.name} has no instructions")
                logger.warning(
                    "Skill command executed but no instructions found",
                    skill_name=skill_cmd.name,
                    duration_ms=round(duration_ms, 2),
                )

    return SlashedCommand.from_raw(
        execute_skill,
        name=f"skill:{skill_cmd.name}",
        description=skill_cmd.description,
        category="skill",
        usage=skill_cmd.input_hint,
    )


class OpenCodeSkillBridge:
    """Bridge managing skill commands for OpenCode's slashed CommandStore."""

    def __init__(self) -> None:
        self._commands: dict[str, SlashedCommand] = {}

    @logfire.instrument("opencode_skill_bridge_handle_change")
    def handle_change(self, name: str, command: SkillCommand | None) -> None:
        """Handle skill command add/remove changes.

        Matches the CommandChangeHandler signature from SkillCommandRegistry.

        Args:
            name: The name of the skill command.
            command: The SkillCommand if adding, None if removing.
        """
        if command is None:
            self._commands.pop(name, None)
            logger.info(
                "Skill command removed from OpenCode bridge",
                skill_name=name,
                total_commands=len(self._commands),
            )
        else:
            self._commands[name] = create_skill_command(command)
            logger.info(
                "Skill command wrapped for OpenCode",
                skill_name=name,
                total_commands=len(self._commands),
            )

    def get_commands(self) -> list[SlashedCommand]:
        """Return all commands as slashed Commands."""
        commands = list(self._commands.values())
        logger.debug(
            "Retrieved OpenCode skill commands",
            command_count=len(commands),
            command_names=[cmd.name for cmd in commands],
        )
        return commands

    @logfire.instrument("opencode_skill_bridge_get_command")
    def get_command(self, name: str) -> SlashedCommand | None:
        """Get command by name (with or without 'skill:' prefix).

        Args:
            name: The command name to look up.

        Returns:
            The command if found, None otherwise.
        """
        skill_name = name.removeprefix("skill:") if name.startswith("skill:") else name
        command = self._commands.get(skill_name)
        logger.debug(
            "Retrieved OpenCode skill command",
            requested_name=name,
            skill_name=skill_name,
            found=command is not None,
        )
        return command
