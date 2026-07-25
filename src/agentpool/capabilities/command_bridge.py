"""CommandBridge — unified command discovery, execution, and change watching.

The :class:`CommandBridge` connects the :class:`ExtensionRegistry` (which tracks
capabilities at four scope levels) to protocol servers (ACP, OpenCode) that need
to expose slash commands to clients.

Architecture
------------

1. **Discovery** — ``discover_commands(scope)`` queries
   :meth:`ExtensionRegistry.get_command_resources` and aggregates
   :meth:`CommandResource.list_commands` from all visible capabilities.
   Commands are de-duplicated by name with most-specific-scope-first priority
   (TURN → AGENT → SESSION → POOL).

2. **Execution** — ``execute(name, input, ctx)`` looks up the command in a
   cached name→entry index (built during discovery) and invokes
   :attr:`CommandEntry.handler`.

3. **Change watching** — ``watch_changes(scope)`` wraps
   :meth:`ExtensionRegistry.merge_change_streams` and filters for
   ``"commands_changed"``, ``"skills_changed"``, and ``"prompts_changed"``
   events.

4. **Per-session lifecycle** — Each protocol session constructs its own
   ``CommandBridge`` with ``Scope(level=ScopeLevel.SESSION, session_id=...)``.
   The bridge references the registry but does not own its lifecycle.

5. **Protocol conversion** — ``entry_to_slashed_command(entry)`` converts a
   :class:`CommandEntry` to a :class:`slashed.Command` for protocol bridges
   that use the ``slashed`` command store.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import logfire


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import Any

    from slashed import Command as SlashedCommand

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.change_event import ChangeEvent
    from agentpool.capabilities.extension_registry import ExtensionRegistry, Scope
    from agentpool.capabilities.resource_protocols import CommandEntry


class CommandNotFoundError(Exception):
    """Raised when a command name is not found in the CommandBridge index."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Command not found: {name!r}")
        self.name = name


class CommandNotExecutableError(Exception):
    """Raised when a command entry has no handler (display-only)."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Command {name!r} is not executable (no handler)")
        self.name = name


class CommandBridge:
    """Bridge between ExtensionRegistry and protocol servers for slash commands.

    Constructed per-session with a :class:`Scope` at SESSION level. Discovers
    commands from all visible :class:`CommandResource` capabilities, caches
    them in a name→entry index for O(1) execution lookup, and watches for
    changes via :meth:`ExtensionRegistry.merge_change_streams`.

    Attributes:
        _registry: The ExtensionRegistry to query.
        _scope: The scope to query at (typically SESSION level).
        _commands: Cached list of discovered CommandEntry objects.
        _index: Cached dict[str, CommandEntry] for O(1) name lookup.
    """

    def __init__(
        self,
        registry: ExtensionRegistry,
        scope: Scope,
    ) -> None:
        """Initialize the CommandBridge.

        Args:
            registry: The ExtensionRegistry to query for CommandResource
                capabilities.
            scope: The scope at which to discover commands. Typically
                ``Scope(level=ScopeLevel.SESSION, session_id=...)``.
        """
        self._registry = registry
        self._scope = scope
        self._commands: list[CommandEntry] = []
        self._index: dict[str, CommandEntry] = {}

    @logfire.instrument("command_bridge.discover_commands")
    async def discover_commands(self) -> list[CommandEntry]:
        """Discover all commands visible at the bridge's scope.

        Queries :meth:`ExtensionRegistry.get_command_resources` and aggregates
        ``list_commands()`` from all results. De-duplicates by name with
        most-specific-scope-first priority (TURN → AGENT → SESSION → POOL).
        Builds and caches a ``dict[str, CommandEntry]`` index for O(1) lookup.

        Returns:
            List of unique CommandEntry objects (de-duplicated by name).
        """
        resources = self._registry.get_command_resources(self._scope)

        # Aggregate commands from all CommandResource capabilities.
        # get_command_resources returns in scope-specificity order
        # (TURN → AGENT → SESSION → POOL), so the first occurrence of each
        # name wins during de-duplication.
        seen: set[str] = set()
        commands: list[CommandEntry] = []
        index: dict[str, CommandEntry] = {}

        for cap in resources:
            try:
                cap_commands = await cap.list_commands()
            except Exception:
                logger = _get_logger()
                logger.exception(
                    "Failed to list commands from capability",
                    capability=type(cap).__name__,
                )
                continue

            for entry in cap_commands:
                if entry.name in seen:
                    logger = _get_logger()
                    logger.debug(
                        "Duplicate command name, keeping first (more specific scope)",
                        name=entry.name,
                    )
                    continue
                seen.add(entry.name)
                commands.append(entry)
                index[entry.name] = entry

        self._commands = commands
        self._index = index
        return commands

    @logfire.instrument("command_bridge.execute {name}")
    async def execute(
        self,
        name: str,
        input: str,  # noqa: A002
        ctx: AgentContext,
    ) -> str:
        """Execute a command by name via its handler.

        Looks up the :class:`CommandEntry` from the cached name→entry index.
        Invokes ``entry.handler(input, ctx)`` if the handler is non-None.

        Args:
            name: The command name to execute.
            input: The raw input text (arguments) for the command.
            ctx: The agent context for this execution.

        Returns:
            The string result from the command handler.

        Raises:
            CommandNotFoundError: If no command with ``name`` exists in the
                cached index.
            CommandNotExecutableError: If the command entry has no handler
                (``handler is None``).
        """
        entry = self._index.get(name)
        if entry is None:
            raise CommandNotFoundError(name)
        if entry.handler is None:
            raise CommandNotExecutableError(name)
        # Exceptions from handler() propagate without wrapping.
        return await entry.handler(input, ctx)

    async def watch_changes(self) -> AsyncIterator[ChangeEvent]:
        """Watch for command list changes via ExtensionRegistry.

        Wraps :meth:`ExtensionRegistry.merge_change_streams` and filters for
        ``"commands_changed"``, ``"skills_changed"``, and
        ``"prompts_changed"`` events. Other event kinds (e.g.,
        ``"tools_changed"``, ``"resources_changed"``) are filtered out.

        Returns:
            An async iterator yielding filtered ChangeEvent objects. If
            ``merge_change_streams`` returns ``None``, returns an empty async
            iterator.

        Yields:
            ChangeEvent: A change event with a relevant kind.
        """
        stream = self._registry.merge_change_streams(self._scope)
        if stream is None:
            return
        async for event in stream:
            if event.kind in ("commands_changed", "skills_changed", "prompts_changed"):
                yield event

    @staticmethod
    def entry_to_slashed_command(
        entry: CommandEntry,
        bridge: CommandBridge,
    ) -> SlashedCommand | None:
        """Convert a CommandEntry to a slashed Command.

        Creates a :class:`slashed.Command` whose executor calls
        :meth:`CommandBridge.execute` with the entry's name. Returns ``None``
        for display-only entries (``handler is None``).

        This method MUST NOT modify ``create_skill_command()`` in
        ``skill_bridge.py`` — it is a separate conversion path for
        ``CommandEntry``-based commands.

        Args:
            entry: The CommandEntry to convert.
            bridge: The CommandBridge to use for execution.

        Returns:
            A SlashedCommand if the entry has a handler, ``None`` otherwise.
        """
        if entry.handler is None:
            return None

        from slashed import Command as SlashedCommand

        async def execute_entry(
            ctx: Any,
            args: list[str],
            kwargs: dict[str, str],
        ) -> None:
            """Execute the command entry via CommandBridge."""
            input_text = " ".join(args)
            # Extract AgentContext from the command context's data field.
            agent_ctx: AgentContext | None = None
            if hasattr(ctx, "data") and ctx.data is not None:
                agent_ctx = ctx.data
            if agent_ctx is None:
                msg = "No AgentContext available in command context"
                raise RuntimeError(msg)
            result = await bridge.execute(entry.name, input_text, agent_ctx)
            if hasattr(ctx, "print"):
                await ctx.print(result)

        return SlashedCommand.from_raw(
            execute_entry,
            name=entry.name,
            description=entry.description,
            category="capability",
        )


def _get_logger() -> Any:
    """Get the module logger (deferred to avoid import-time side effects)."""
    from agentpool.log import get_logger

    return get_logger(__name__)
