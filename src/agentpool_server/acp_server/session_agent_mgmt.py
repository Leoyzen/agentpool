"""Agent registration and lifecycle mixin for ACPSession.

Extracted from session.py as part of the session-debt-cleanup file split.
Contains agent switching, command registration (manifest, skill, MCP prompt),
skill change watching, and slash command execution methods.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING, Any

import anyio
import logfire

from agentpool import Agent
from agentpool.commands.base import NodeCommand
from agentpool.log import get_logger
from agentpool_config.commands import BaseCommandConfig


if TYPE_CHECKING:
    from collections.abc import Callable

    from slashed import CommandStore

    from acp.schema import AvailableCommand
    from agentpool.capabilities.agent_context import AgentContext as CapabilityAgentContext
    from agentpool.capabilities.command_bridge import CommandBridge


logger = get_logger(__name__)


class ACPSessionAgentMgmtMixin:
    """Mixin providing agent registration and lifecycle methods for ACPSession.

    Contains agent switching, command registration (manifest commands, skill
    commands, MCP prompts, prompt hub), skill change watching, and slash
    command execution.

    All attributes are provided by the main :class:`ACPSession` dataclass.
    Type annotations are declared under ``TYPE_CHECKING`` to avoid being
    treated as dataclass fields.
    """

    if TYPE_CHECKING:
        session_id: str
        agent: Any  # BaseAgent[Any, Any]
        acp_agent: Any  # AgentPoolACPAgent
        cwd: str
        manager: Any  # ACPSessionManager | None
        log: Any
        command_store: CommandStore
        input_provider: Any  # ACPInputProvider
        acp_env: Any  # ACPExecutionEnvironment
        notifications: Any  # ACPNotifications
        fs: Any  # ACPFileSystem
        _skill_bridge: Any  # ACPSkillBridge
        _skill_change_task: asyncio.Task[None] | None
        _skill_register_lock: asyncio.Lock
        _command_bridge: CommandBridge | None
        _update_callbacks: list[Callable[[], None]]
        _remote_commands: list[AvailableCommand]
        client_info: Any  # Implementation | None

        @property
        def host_context(self) -> Any: ...
        def get_cwd_context(self) -> str: ...
        def _notify_command_update(self) -> None: ...
        def get_acp_commands(self) -> list[AvailableCommand]: ...
        async def send_available_commands_update(self) -> None: ...
        async def _send_toast(
            self,
            message: str,
            level: str = "error",
            *,
            duration: int | None = None,
            action: dict[str, str] | None = None,
        ) -> None: ...
        async def _on_state_updated(self, state: Any) -> None: ...

    def _register_manifest_commands(self) -> None:
        """Register global commands from manifest to command_store.

        Loads commands defined in manifest.commands (like static commands)
        and registers them as slashed commands in the session's command_store
        so they are included in available_commands_update notifications to ACP clients.
        """
        ctx = self.host_context
        commands = ctx.manifest.get_command_configs()
        if commands is None:
            self.log.debug("No manifest commands to register")
            return

        cmd_count = 0
        for cmd_name, cmd_config in commands.items():
            try:
                # Convert CommandConfig to slashed Command
                slashed_cmd = cmd_config.get_slashed_command(category="manifest")
                # Register in session's command_store
                self.command_store.register_command(slashed_cmd)
                cmd_count += 1
                self.log.debug(
                    "Registered manifest command",
                    name=cmd_name,
                    type=cmd_config.type,
                )
            except Exception:
                self.log.exception(
                    "Failed to register manifest command",
                    name=cmd_name,
                    config_type=type(cmd_config).__name__
                    if isinstance(cmd_config, BaseCommandConfig)
                    else "unknown",
                )

        if cmd_count > 0:
            # Schedule update to notify client of new commands
            self._notify_command_update()
            self.log.info("Registered manifest commands", count=cmd_count)

    async def _register_skill_commands(self) -> None:
        """Register commands from CommandBridge and skill commands as slash commands.

        Discovers commands from ALL CommandResource capabilities via
        ``CommandBridge.discover_commands()``, converts them to
        ``SlashedCommand`` objects, and registers them in ``command_store``
        with ``replace=True``. Also registers skill commands via
        ``ACPSkillBridge`` for backward compatibility. When a command name
        exists in both ``CommandBridge`` and ``ACPSkillBridge``, the
        ``CommandBridge`` version is preferred (more comprehensive).
        Also removes stale skill commands that are no longer present.
        """
        from agentpool.skills.command import SkillCommand

        # --- Phase 1: Discover commands from CommandBridge ---
        bridge_names: set[str] = set()
        if self._command_bridge is not None:
            from agentpool.capabilities.command_bridge import CommandBridge

            try:
                entries = await self._command_bridge.discover_commands()
            except Exception:
                self.log.exception("Failed to discover commands from CommandBridge")
                entries = []

            for entry in entries:
                slashed_cmd = CommandBridge.entry_to_slashed_command(entry, self._command_bridge)
                if slashed_cmd is not None:
                    self.command_store.register_command(slashed_cmd, replace=True)
                    bridge_names.add(entry.name)
                    self.log.debug(
                        "Registered CommandBridge command in command_store",
                        name=entry.name,
                        source=entry.source,
                    )

        # --- Phase 2: Register skill commands via ACPSkillBridge (backward compat) ---
        ctx = self.host_context
        skills_registry = ctx.skills_registry
        skills = skills_registry.list_skills()

        # Build current set of invocable skill commands
        new_cmds: list[SkillCommand] = []
        for skill in skills:
            if not skill.user_invocable:
                continue
            new_cmds.append(
                SkillCommand(
                    name=skill.name,
                    description=skill.description,
                    skill=skill,
                    skill_uri=f"skill://{skill.name}",
                )
            )
        new_names = {cmd.name for cmd in new_cmds}

        # Remove stale commands no longer in the registry
        old_names = self._skill_bridge.get_command_names()
        stale_names = old_names - new_names
        for stale in stale_names:
            self._skill_bridge.handle_change(stale, None)
            # Don't unregister from command_store if CommandBridge has it
            if stale not in bridge_names:
                self.command_store.unregister_command(stale)
            self.log.debug("Unregistered stale skill command", name=stale)

        # Add/update commands through the bridge
        for cmd in new_cmds:
            self._skill_bridge.handle_change(cmd.name, cmd)

        # Register all bridge commands in command_store with replace=True,
        # but skip ones already registered by CommandBridge (de-duplication)
        for slashed_cmd in self._skill_bridge.get_commands():
            if slashed_cmd.name in bridge_names:
                self.log.debug(
                    "Skipping skill command (CommandBridge version preferred)",
                    name=slashed_cmd.name,
                )
                continue
            self.command_store.register_command(slashed_cmd, replace=True)
            self.log.debug(
                "Registered skill command in command_store",
                name=slashed_cmd.name,
            )

        if new_cmds or stale_names or bridge_names:
            self._notify_command_update()
            self.log.info(
                "Synced commands",
                bridge_count=len(bridge_names),
                added=len(new_cmds),
                removed=len(stale_names),
            )

    def _start_skill_change_watcher(self) -> None:
        """Start watching for command/skill/prompt changes via CommandBridge."""
        if self._command_bridge is None:
            return
        self._skill_change_task = asyncio.create_task(
            self._watch_skill_changes(), name=f"command_watcher_{self.session_id}"
        )

    async def _watch_skill_changes(self) -> None:
        """Watch for command/skill/prompt change events and rebuild commands.

        Consumes ``CommandBridge.watch_changes()`` which filters for
        ``"commands_changed"``, ``"skills_changed"``, and
        ``"prompts_changed"`` events from the ``ExtensionRegistry``.
        When any of these events arrive, rebuilds the command list and
        sends an ``AvailableCommandsUpdate`` to the client.
        """
        if self._command_bridge is None:
            self.log.debug("No CommandBridge — change watcher disabled")
            return

        try:
            async for event in self._command_bridge.watch_changes():
                self.log.info(
                    "Command/skill/prompt change detected, rebuilding commands",
                    kind=event.kind,
                )
                try:
                    async with self._skill_register_lock:
                        await self._register_skill_commands()
                    await self.send_available_commands_update()
                except Exception:
                    self.log.exception("Failed to rebuild commands after change")
        except asyncio.CancelledError:
            self.log.debug("Command change watcher cancelled")
            raise
        except Exception:
            self.log.exception("Command change watcher error")

    async def init_client_skills(self) -> None:
        """Discover and load skills from client-side .claude/skills directory."""
        try:
            await self.host_context.skills_registry.add_skills_directory(
                ".claude/skills", fs=self.fs
            )
            skills = self.host_context.skills_registry.list_skills()
            self.log.info("Collected client-side skills", skill_count=len(skills))
            # Bridge newly discovered skills into command_store
            await self._register_skill_commands()
            await self.send_available_commands_update()
        except Exception as e:
            self.log.exception("Failed to discover client-side skills", error=e)

    async def switch_active_agent(self, agent_name: str) -> None:
        """Switch to a different agent in the pool.

        Creates a new session-level agent for the target name via SessionPool.
        Pool-level agents were removed — all agents are now session-scoped.
        """
        # Validate agent exists in config (not runtime instances)
        available = list(self.host_context.manifest.agents.keys())
        if agent_name not in available:
            raise ValueError(f"Agent {agent_name!r} not found. Available: {available}")

        old_agent_name = self.agent.name

        # Disconnect old agent's signal
        with suppress(Exception):
            self.agent.state_updated.disconnect(self._on_state_updated)

        # Remove session-specific mutations from old agent before switching
        if isinstance(self.agent, Agent) and self.get_cwd_context in self.agent.sys_prompts.prompts:
            self.agent.sys_prompts.prompts.remove(self.get_cwd_context)  # pyright: ignore[reportArgumentType]

        # Create new session agent via SessionPool (pool-level agents removed)
        ctx = self.host_context
        if ctx.session_pool is not None:
            # Invalidate cache so get_or_create_session_agent creates a fresh agent
            ctx.session_pool.sessions._session_agents.pop(self.session_id, None)
            self.agent = await ctx.session_pool.sessions.get_or_create_session_agent(
                self.session_id, agent_name=agent_name, input_provider=self.input_provider
            )
        else:
            msg = "SessionPool is required for agent switching"
            raise RuntimeError(msg)

        # Re-apply session-specific mutations
        self.agent.env = self.acp_env
        self.agent._input_provider = self.input_provider
        if isinstance(self.agent, Agent):
            self.agent.sys_prompts.prompts.append(self.get_cwd_context)  # pyright: ignore[reportArgumentType]

        # Reconnect signal
        with suppress(Exception):
            self.agent.state_updated.disconnect(self._on_state_updated)
        self.agent.state_updated.connect(self._on_state_updated)

        self.log.info("Switched agents", from_agent=old_agent_name, to_agent=agent_name)
        # Persist the agent switch via session manager
        if self.manager:
            await self.manager.update_session_agent(self.session_id, agent_name)
        await self.send_available_commands_update()

    async def _register_mcp_prompts_as_commands(self) -> None:
        """Register MCP prompts as slash commands."""
        if all_prompts := await self.agent.list_prompts():
            for prompt in all_prompts:
                command = prompt.create_mcp_command(self.agent.staged_content)
                self.command_store.register_command(command)
            self._notify_command_update()
            self.log.info("Registered MCP prompts as commands", prompt_count=len(all_prompts))
            await self.send_available_commands_update()  # Send updated command list to client

    async def _register_prompt_hub_commands(self) -> None:
        """Register prompt hub prompts as slash commands."""
        manager = self.host_context.prompt_manager
        cmd_count = 0
        all_prompts = await manager.list_prompts()
        for provider_name, prompt_names in all_prompts.items():
            if not prompt_names:  # Skip empty providers
                continue
            for prompt_name in prompt_names:
                command = manager.create_prompt_hub_command(
                    provider_name,
                    prompt_name,
                    self.agent.staged_content,
                )
                self.command_store.register_command(command)
                cmd_count += 1

        if cmd_count > 0:
            self._notify_command_update()
            self.log.info("Registered hub prompts as slash commands", cmd_count=cmd_count)
            await self.send_available_commands_update()  # Send updated command list to client

    def _build_command_agent_context(self) -> CapabilityAgentContext:
        """Construct a capabilities.AgentContext from the session's current state.

        This is used for ``CommandBridge.execute()`` which expects a
        :class:`~agentpool.capabilities.agent_context.AgentContext` (not
        the ``agents.AgentContext`` used by ``command_store``).

        Returns:
            A minimal ``AgentContext`` with the session's host context,
            extension registry, and scope information.
        """
        from agentpool.capabilities.agent_context import AgentContext as CapabilityAgentContext
        from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
        from agentpool.host.context import RunScope
        from agentpool.host.registry import AgentRegistry
        from agentpool.orchestrator.session_controller import SessionState

        hctx = self.host_context
        registry = AgentRegistry()
        delegation: RunLoopDelegationService = RunLoopDelegationService(
            registry=registry,
            host=hctx,
            session_id=self.session_id,
        )
        session = SessionState(
            session_id=self.session_id,
            agent_name=self.agent.name,
        )
        scope = RunScope(session_id=self.session_id)
        return CapabilityAgentContext(
            agent_registry=registry,
            delegation=delegation,
            session=session,
            scope=scope,
            host=hctx,
            extension_registry=hctx.extension_registry,
        )

    @logfire.instrument(r"Execute Slash Command {command_text}")
    async def execute_slash_command(self, command_text: str) -> None:
        """Execute any slash command with unified handling.

        Routes execution through ``CommandBridge.execute()`` first. If
        ``CommandNotFoundError`` is raised, falls back to the existing
        ``command_store.execute_command()`` path (manifest commands,
        debug commands, etc.). If ``CommandNotExecutableError`` is raised,
        sends an error toast to the client.

        Args:
            command_text: Full command text (including slash)
        """
        from agentpool_server.acp_server.session import SLASH_PATTERN

        if match := SLASH_PATTERN.match(command_text.strip()):
            command_name = match.group(1)
            args = match.group(2) or ""
        else:
            logger.warning("Invalid slash command", command=command_text)
            return

        # Check if command supports current node type
        if (
            (cmd := self.command_store.get_command(command_name))
            and isinstance(cmd, NodeCommand)
            and not cmd.supports_node(self.agent)
        ):
            error_msg = f"❌ Command `/{command_name}` is not available for this node type"
            await self.notifications.send_agent_text(error_msg)
            return

        # --- Phase 1: Try CommandBridge.execute() first ---
        if self._command_bridge is not None:
            from agentpool.capabilities.command_bridge import (
                CommandNotExecutableError,
                CommandNotFoundError,
            )

            try:
                agent_ctx = self._build_command_agent_context()
                result = await self._command_bridge.execute(command_name, args, agent_ctx)
            except CommandNotFoundError:
                pass  # Fall through to command_store fallback
            except CommandNotExecutableError:
                await self._send_toast(
                    message=f"Command `/{command_name}` is not executable",
                    level="error",
                )
                await anyio.sleep(0.05)
                return
            else:
                await self.notifications.send_agent_text(result)
                await anyio.sleep(0.05)  # Allow network buffers to flush
                return

        # --- Phase 2: Fallback to command_store.execute_command() ---
        agent_context = self.agent.get_context(data=self)
        cmd_ctx = self.command_store.create_context(
            data=agent_context,
            output_writer=self.notifications.send_agent_text,
        )

        command_str = f"{command_name} {args}".strip()
        try:
            await self.command_store.execute_command(command_str, cmd_ctx)
        except Exception as e:
            logger.exception("Command execution failed")
            # Send error as toast instead of polluting chat history
            await self._send_toast(
                message=f"Command error: {e}",
                level="error",
            )
            await anyio.sleep(0.05)  # Allow network buffers to flush
