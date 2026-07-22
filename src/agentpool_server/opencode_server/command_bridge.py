"""OpenCode command bridge for exposing CommandResource commands as slashed Commands.

The :class:`OpenCodeCommandBridge` wraps :class:`CommandBridge` to provide
OpenCode-specific command conversion. It discovers commands from ALL
``CommandResource`` capabilities registered in the ``ExtensionRegistry``,
converts them to ``slashed.Command`` instances, and exposes them alongside
existing skill and MCP prompt commands.

This class is separate from :class:`OpenCodeSkillBridge` and does NOT modify
``create_skill_command()`` — skill-based commands continue to flow through the
existing ``SkillCommand`` → ``SlashedCommand`` path.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from slashed import Command as SlashedCommand

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.change_event import ChangeEvent
    from agentpool.capabilities.command_bridge import CommandBridge
    from agentpool.capabilities.extension_registry import ExtensionRegistry


class OpenCodeCommandBridge:
    """Bridge wrapping :class:`CommandBridge` for OpenCode-specific command conversion.

    Constructed per-server (not per-session) with a SESSION-level scope. Discovers
    commands from all :class:`CommandResource` capabilities via the inner
    :class:`CommandBridge`, converts each :class:`CommandEntry` to a
    :class:`slashed.Command`, and delegates execution to the inner bridge.

    This class does NOT replace :class:`OpenCodeSkillBridge` — both coexist.
    Skill commands flow through the existing ``SkillCommand`` path; capability
    commands flow through this bridge.
    """

    def __init__(self, registry: ExtensionRegistry, session_id: str) -> None:
        """Initialize the OpenCode command bridge.

        Args:
            registry: The ExtensionRegistry to query for CommandResource
                capabilities.
            session_id: The session ID for scoping command discovery.
        """
        from agentpool.capabilities.command_bridge import CommandBridge
        from agentpool.capabilities.extension_registry import Scope, ScopeLevel

        self._bridge: CommandBridge = CommandBridge(
            registry=registry,
            scope=Scope(level=ScopeLevel.SESSION, session_id=session_id),
        )

    @logfire.instrument("opencode_command_bridge.discover_commands")
    async def discover_commands(self) -> list[SlashedCommand]:
        """Discover all commands and convert to SlashedCommand instances.

        Calls the inner :meth:`CommandBridge.discover_commands` to collect
        :class:`CommandEntry` objects from all visible ``CommandResource``
        capabilities, then converts each entry via
        :meth:`CommandBridge.entry_to_slashed_command`. Entries without a
        handler (display-only) are filtered out.

        Returns:
            List of :class:`SlashedCommand` instances from capability commands.
        """
        from agentpool.capabilities.command_bridge import CommandBridge

        entries = await self._bridge.discover_commands()
        commands: list[SlashedCommand] = []
        for entry in entries:
            slashed = CommandBridge.entry_to_slashed_command(entry, self._bridge)
            if slashed is not None:
                commands.append(slashed)
        return commands

    @logfire.instrument("opencode_command_bridge.execute {name}")
    async def execute(self, name: str, input: str, ctx: AgentContext) -> str:  # noqa: A002
        """Execute a command by name via the inner CommandBridge.

        Delegates to :meth:`CommandBridge.execute`. Raises
        :class:`CommandNotFoundError` if the command is not in the bridge's
        index, or :class:`CommandNotExecutableError` if the entry has no handler.

        Args:
            name: The command name to execute.
            input: The raw input text (arguments) for the command.
            ctx: The agent context for this execution.

        Returns:
            The string result from the command handler.
        """
        return await self._bridge.execute(name, input, ctx)

    async def watch_changes(self) -> AsyncIterator[ChangeEvent]:
        """Watch for command list changes via the inner CommandBridge.

        Delegates to :meth:`CommandBridge.watch_changes`, which filters for
        ``"commands_changed"``, ``"skills_changed"``, and
        ``"prompts_changed"`` events.

        Yields:
            ChangeEvent: A change event with a relevant kind.
        """
        async for event in self._bridge.watch_changes():
            yield event

    def get_command_bridge(self) -> CommandBridge:
        """Return the inner CommandBridge for direct access.

        Returns:
            The wrapped :class:`CommandBridge` instance.
        """
        return self._bridge
