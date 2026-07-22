"""Example: Custom capability that registers slash commands via CommandResource.

This demonstrates the end-to-end flow:
1. A custom capability implements ``CommandResource`` to publish commands.
2. ``CommandBridge.discover_commands()`` finds them via ``ExtensionRegistry``.
3. Protocol servers (ACP, OpenCode) expose them as slash commands to clients.
4. When invoked, ``CommandBridge.execute()`` calls the ``CommandEntry.handler``.

To use: register this capability in your AgentPool config or programmatically::

    from examples.custom_command_capability import WeatherCommandCapability

    pool.extension_registry.register(
        WeatherCommandCapability(),
        scope=Scope(level=ScopeLevel.POOL),
    )

The ``/weather`` command will then appear in ACP and OpenCode clients.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from agentpool.capabilities.resource_protocols import (
    CommandEntry,
    CommandResource,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from agentpool.capabilities.agent_context import AgentContext
    from agentpool.capabilities.change_event import ChangeEvent


@dataclass
class WeatherCommandCapability(CommandResource):
    """A minimal custom capability that provides a ``/weather`` slash command.

    This capability does NOT provide tools or instructions — it only
    implements ``CommandResource`` to publish a command with a handler.
    ``CommandBridge`` discovers it via ``ExtensionRegistry.get_command_resources()``
    and protocol servers expose it to clients.
    """

    name: str = "weather-cmd"
    _commands: list[CommandEntry] | None = None

    def __post_init__(self) -> None:
        """Build the command entries with handlers."""
        self._commands = [
            CommandEntry(
                name="weather",
                description="Get the current weather for a city",
                skill_uri="weather://command",
                source="custom",
                handler=self._weather_handler,
            ),
        ]

    @staticmethod
    async def _weather_handler(
        input_text: str,
        ctx: AgentContext,
    ) -> str:
        """Handle the ``/weather`` command.

        Args:
            input_text: The user's input after the command name (e.g., "San Francisco").
            ctx: The agent context (provides access to host, registry, etc.).

        Returns:
            A weather report string.
        """
        city = input_text.strip() or "Unknown"
        # In a real implementation, you would call a weather API here.
        # The handler has access to ``ctx.host`` for MCP tools, storage, etc.
        return f"🌤️ Weather for {city}: Sunny, 72°F (22°C)"

    # --- CommandResource protocol ---

    async def list_commands(self) -> Sequence[CommandEntry]:
        """Return all commands provided by this capability."""
        return self._commands or []

    async def get_command(self, name: str) -> CommandEntry | None:
        """Look up a command by name."""
        for entry in self._commands or []:
            if entry.name == name:
                return entry
        return None

    # --- Optional: ChangeObservable ---

    def on_change(self) -> AsyncIterator[ChangeEvent] | None:
        """Emit change events when the command list changes.

        For a static capability, return ``None`` (no changes expected).
        For a dynamic capability, yield ``ChangeEvent(kind="commands_changed")``
        when the command list is updated.
        """
        return None
