"""Skill command registry for managing skill-based commands."""

from __future__ import annotations

from collections.abc import Callable
import time
from typing import TYPE_CHECKING, Any

import logfire

from agentpool.log import get_logger
from agentpool.tools.exceptions import ToolError
from agentpool.utils.baseregistry import BaseRegistry


logger = get_logger(__name__)

if TYPE_CHECKING:
    from agentpool.skills.command import SkillCommand
    from agentpool.skills.registry import SkillsRegistry
    from agentpool.skills.skill import Skill

CommandChangeHandler = Callable[[str, "SkillCommand | None"], None]
"""Handler type for command change notifications.

Called with (name, command) when a command is added,
or (name, None) when a command is removed.
"""


class SkillCommandRegistry(BaseRegistry[str, "SkillCommand"]):
    """Registry for skill commands that watches SkillsRegistry changes.

    This registry maintains a mapping of skill names to their command
    representations, automatically syncing with SkillsRegistry when
    skills are added or removed. Also connects to skill_provider for
    dynamic skill discovery from MCP servers and other sources.
    """

    def __init__(
        self,
        skills_registry: SkillsRegistry | None = None,
        skill_provider: Any | None = None,
    ) -> None:
        """Initialize registry.

        Args:
            skills_registry: Optional SkillsRegistry to watch for changes.
                If None, registry works in standalone mode.
            skill_provider: Optional skill provider (e.g., AggregatingResourceProvider)
                to watch for skill changes from MCP servers and other sources.
        """
        super().__init__()
        self._skills_registry = skills_registry
        self._skill_provider = skill_provider
        self._command_change_handlers: list[CommandChangeHandler] = []
        logger.debug("Initializing skill command registry")

    @property
    def has_skills(self) -> bool:
        """Check if a SkillsRegistry is connected."""
        return self._skills_registry is not None

    @property
    def has_commands(self) -> bool:
        """Check if any commands are registered."""
        return len(self) > 0

    @property
    def _error_class(self) -> type[ToolError]:
        """Error class for registry operations."""
        return ToolError

    def _validate_item(self, item: Any) -> SkillCommand:
        """Validate item is a SkillCommand."""
        from agentpool.skills.command import SkillCommand

        if not isinstance(item, SkillCommand):
            msg = f"Expected SkillCommand, got {type(item).__name__}"
            raise ToolError(msg)
        return item

    def on_command_change(self, callback: CommandChangeHandler) -> None:
        """Register callback for command changes.

        New callbacks are immediately notified of all existing commands.

        Args:
            callback: Called with (name, command) on add, (name, None) on remove.
        """
        # Notify of existing state
        for name, command in self._items.items():
            callback(name, command)
        # Store for future changes
        self._command_change_handlers.append(callback)

    @logfire.instrument("skill_command_register", extract_args=True)
    def register(self, key: str, item: SkillCommand | Any, replace: bool = False) -> None:
        """Register command and broadcast to handlers."""
        super().register(key, item, replace)
        validated_item = self._items[key]
        for handler in self._command_change_handlers:
            handler(key, validated_item)
        logger.info(
            "Skill command registered",
            command_name=key,
            replace=replace,
            total_commands=len(self._items),
        )

    @logfire.instrument("skill_command_remove")
    def __delitem__(self, key: str) -> None:
        """Remove command and broadcast to handlers."""
        if key in self._items:
            for handler in self._command_change_handlers:
                handler(key, None)
            del self._items[key]
            logger.info("Skill command removed", command_name=key, total_commands=len(self._items))
        else:
            raise self._error_class(f"Item not found: {key}")

    async def initialize(self) -> None:
        """Initialize by syncing with SkillsRegistry and subscribing to events.

        This method:
        1. Syncs existing skills from SkillsRegistry and/or skill_provider
        2. Subscribes to future skill change events from SkillsRegistry
        3. Subscribes to skill provider changes if skill_provider is set

        Should be called after SkillsRegistry has loaded its initial skills.
        """
        # Always sync commands (from both registry and provider)
        await self._sync_commands()

        if self._skills_registry is not None:
            self._subscribe_to_registry()

        # Subscribe to skill provider changes for dynamic skill discovery
        if self._skill_provider is not None:
            self._subscribe_to_skill_provider()

    def _subscribe_to_registry(self) -> None:
        """Subscribe to SkillsRegistry change events."""
        if self._skills_registry is None:
            return
        self._skills_registry.on_skill_added(self._on_skill_added)
        self._skills_registry.on_skill_removed(self._on_skill_removed)

    def _subscribe_to_skill_provider(self) -> None:
        """Subscribe to skill provider change events.

        Connects to the skills_changed signal on the skill provider
        to receive updates when skills are added/removed from MCP servers
        or other dynamic sources.
        """
        if self._skill_provider is None:
            return
        self._skill_provider.skills_changed.connect(self._on_skill_provider_changed)
        logger.debug("Subscribed to skill provider changes")

    async def _on_skill_provider_changed(self, event: Any) -> None:
        """Handle skill provider change events.

        Args:
            event: The resource change event from the provider.
        """
        from agentpool.resource_providers.base import ResourceChangeEvent

        if isinstance(event, ResourceChangeEvent) and event.resource_type == "skills":
            logger.debug(
                "Skill provider change detected, refreshing commands",
                provider=event.provider_name,
            )
            # Re-sync all skills respecting priority (local skills override MCP skills)
            await self._sync_commands()

    async def _sync_from_skill_provider(self) -> None:
        """Sync skills from the skill provider.

        Fetches all skills from the aggregating skill provider and
        updates the command registry accordingly.
        """
        from agentpool.skills.command import SkillCommand

        if self._skill_provider is None:
            return

        start_time = time.time()
        try:
            skills = await self._skill_provider.get_skills()
            count = 0
            for skill in skills:
                # Build skill URI using the actual provider name from skill metadata
                # The provider name in metadata is the real source (e.g., "local" or MCP server name)
                provider_name = skill.metadata.get("provider") if skill.metadata else None
                if provider_name is None:
                    provider_name = self._skill_provider.name
                skill_uri = f"skill://{provider_name}/{skill.name}"
                command = SkillCommand(
                    name=skill.name,
                    description=skill.description,
                    skill=skill,
                    skill_uri=skill_uri,
                )
                self.register(skill.name, command, replace=True)
                count += 1

            duration_ms = (time.time() - start_time) * 1000
            logger.info(
                "Synced commands from skill provider",
                count=count,
                duration_ms=round(duration_ms, 2),
                total_commands=len(self._items),
            )
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.warning(
                "Failed to sync commands from skill provider",
                error=str(e),
                duration_ms=round(duration_ms, 2),
            )

    def _on_skill_added(self, name: str, skill: Skill) -> None:
        """Handle skill added from SkillsRegistry.

        Creates a SkillCommand and registers it.
        """
        from agentpool.skills.command import SkillCommand

        command = SkillCommand(
            name=skill.name,
            description=skill.description,
            skill=skill,
        )
        self.register(name, command, replace=True)

    def _on_skill_removed(self, name: str, _skill: Skill | None) -> None:
        """Handle skill removed from SkillsRegistry."""
        if name in self:
            del self[name]

    @logfire.instrument("skill_commands_sync")
    async def _sync_commands(self) -> None:
        """Sync existing SkillsRegistry and skill_provider commands to this registry.

        Local skills have priority over MCP skills - they are registered last
        and will replace any MCP skills with the same name.
        """
        from agentpool.skills.command import SkillCommand

        start_time = time.time()
        count = 0

        # 1. Sync from skill_provider (MCP-based skills) first
        # These will be overridden by local skills if names conflict
        if self._skill_provider is not None:
            try:
                provider_skills = await self._skill_provider.get_skills()
                for skill in provider_skills:
                    try:
                        command = SkillCommand(
                            name=skill.name,
                            description=skill.description,
                            skill=skill,
                        )
                        self.register(skill.name, command, replace=True)
                        count += 1
                    except Exception as e:
                        logger.warning(
                            "Failed to register provider skill command",
                            name=skill.name,
                            error=str(e),
                        )
            except Exception as e:
                logger.warning("Failed to sync from skill_provider", error=str(e))

        # 2. Sync from SkillsRegistry (local filesystem skills) last
        # These take priority and will override MCP skills with the same name
        if self._skills_registry is not None:
            try:
                for name in self._skills_registry.list_items():
                    try:
                        skill = self._skills_registry.get(name)
                        command = SkillCommand(
                            name=skill.name,
                            description=skill.description,
                            skill=skill,
                        )
                        self.register(name, command, replace=True)
                        count += 1
                    except Exception as e:
                        logger.warning("Failed to register skill command", name=name, error=str(e))
            except Exception as e:
                logger.warning("Failed to sync from SkillsRegistry", error=str(e))

        duration_ms = (time.time() - start_time) * 1000
        logger.info(
            "Synced commands from SkillsRegistry and skill_provider",
            count=count,
            duration_ms=round(duration_ms, 2),
            total_commands=len(self._items),
        )
