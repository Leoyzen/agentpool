"""ACP (Agent Client Protocol) session management for agentpool.

This module provides session lifecycle management, state tracking, and coordination
between agents and ACP clients through the JSON-RPC protocol.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field
import hashlib
import re
from typing import TYPE_CHECKING, Any, Literal

import anyio
from exxec.acp_provider import ACPExecutionEnvironment
import logfire
from pydantic_ai import UsageLimitExceeded
from slashed import CommandStore
from tokonomics.model_discovery.model_info import ModelInfo

from acp.agent.acp_requests import ACPRequests
from acp.agent.notifications import ACPNotifications
from acp.filesystem import ACPFileSystem
from acp.schema import AvailableCommand, ClientCapabilities
from acp.schema.mcp import AcpMcpServer
from agentpool import Agent
from agentpool.agents.acp_agent import ACPAgent
from agentpool.agents.events.events import ToastInfo
from agentpool.agents.modes import ConfigOptionChanged, ModeInfo
from agentpool.commands.base import NodeCommand
from agentpool.log import get_logger
from agentpool.mcp_server.config_snapshot import McpConfigEntry, McpConfigSnapshot
from agentpool_config.commands import BaseCommandConfig
from agentpool_server.acp_server.converters import (
    convert_acp_mcp_server_to_config,
    from_acp_content,
)
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider


if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic_ai import UserContent
    from slashed import BaseCommand

    from acp import Client, RequestPermissionRequest, RequestPermissionResponse
    from acp.schema import (
        ContentBlock,
        Implementation,
        McpServer,
        StopReason,
        Usage,
    )
    from agentpool.agents.base_agent import BaseAgent, StateUpdate
    from agentpool.common_types import PathReference
    from agentpool.host.context import HostContext
    from agentpool_server.acp_server.acp_agent import AgentPoolACPAgent
    from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge
    from agentpool_server.acp_server.session_manager import ACPSessionManager

logger = get_logger(__name__)

# Maximum length for internal provider name keys (kept as local constant
# after removing from uri_resolver — provider segment concept was removed in D9).
_MAX_PROVIDER_NAME_LENGTH = 63
SLASH_PATTERN = re.compile(r"^/([\w-]+)(?:\s+(.*))?$")

# Zed-specific instructions for code references
ZED_CLIENT_PROMPT = """\
## Code References

When referencing code locations in responses, use markdown links with `file://` URLs:

- **File**: `[filename](file:///absolute/path/to/file.py)`
- **Line range**: `[filename#L10-25](file:///absolute/path/to/file.py#L10:25)`
- **Single line**: `[filename#L10](file:///absolute/path/to/file.py#L10:10)`
- **Directory**: `[dirname/](file:///absolute/path/to/dir/)`

Line range format is `#L<start>:<end>` (1-based, inclusive).

Use these clickable references instead of inline code blocks when pointing to specific \
code locations. For showing actual code content, still use fenced code blocks.

## Zed-specific URLs

In addition to `file://` URLs, these `zed://` URLs work in the agent context:

- **File reference**: `[text](zed:///agent/file?path=/absolute/path/to/file.py)`
- **Selection**: `[text](zed:///agent/selection?path=/absolute/path/to/file.py#L10:25)`
- **Symbol**: `[text](zed:///agent/symbol/function_name?path=/absolute/path/to/file.py#L10:25)`
- **Directory**: `[text](zed:///agent/directory?path=/absolute/path/to/dir)`

Query params must be URL-encoded (spaces → `%20`). Paths must be absolute.
"""


def get_all_commands() -> Sequence[BaseCommand]:
    """Return empty command list to align with OpenCode behavior.

    All built-in framework commands are hidden to keep ACP consistent
    with OpenCode, which does not register agentpool_commands at all.
    """
    return []


def _is_slash_command(text: str) -> bool:
    """Check if text starts with a slash command."""
    return bool(SLASH_PATTERN.match(text.strip()))


def split_commands(
    contents: Sequence[UserContent | PathReference],
    command_store: CommandStore,
) -> tuple[list[str], list[UserContent | PathReference]]:
    """Split content into local slash commands and pass-through content.

    Only commands that exist in the local command_store are extracted.
    Remote commands (from nested ACP agents) stay in non_command_content
    so they flow through to the agent and reach the nested server.
    """
    commands: list[str] = []
    non_command_content: list[UserContent | PathReference] = []
    for item in contents:
        # Check if this is a LOCAL command we handle
        if (
            isinstance(item, str)
            and _is_slash_command(item)
            and (match := SLASH_PATTERN.match(item.strip()))
            and command_store.get_command(match.group(1))
        ):
            commands.append(item.strip())
        else:
            # Not a local command - pass through (may be remote command or regular text)
            non_command_content.append(item)
    return commands, non_command_content


def infer_stop_reason(error_msg: str) -> StopReason:
    """Infers the reason for stopping the session based on the error message."""
    if "request_limit" in error_msg:
        return "max_turn_requests"
    if any(limit in error_msg for limit in ["tokens_limit", "token_limit"]):
        return "max_tokens"
    # Tool call limits don't have a direct ACP stop reason, treat as refusal
    if "tool_calls_limit" in error_msg or "tool call" in error_msg:
        return "refusal"
    return "max_tokens"  # Default to max_tokens for other usage limits


@dataclass
class ACPSession:
    """Individual ACP session state and management.

    Manages the lifecycle and state of a single ACP session, including:
    - Agent instance and conversation state
    - Working directory and environment
    - MCP server connections
    - File system bridge for client operations
    - Tool execution and streaming updates
    """

    session_id: str
    """Unique session identifier"""

    agent: BaseAgent[Any, Any]
    """Currently active agent instance.

    The agent carries its own pool reference via agent.host_context,
    which is used for agent switching and pool-level operations.
    """

    cwd: str
    """Working directory for the session"""

    client: Client
    """External library Client interface for operations"""

    acp_agent: AgentPoolACPAgent
    """ACP agent instance for capability tools"""

    mcp_servers: Sequence[McpServer] | None = None
    """Optional MCP server configurations"""

    client_capabilities: ClientCapabilities = field(default_factory=ClientCapabilities)
    """Client capabilities for tool registration"""

    client_info: Implementation | None = None
    """Client implementation info (name, version, title)"""

    manager: ACPSessionManager | None = None
    """Session manager for managing sessions. Used for session management commands."""

    subagent_display_mode: Literal["legacy", "zed", "qwen"] = "legacy"
    """How to display subagent output:
    - 'legacy': Default display mode using tool_box semantics
    - 'zed': Zed-compatible display mode
    """

    raw_input_mode: Literal["dict", "skip", "json_str"] = "dict"
    """How to emit tool call raw_input:
    - 'dict': Parse args as dict (default)
    - 'skip': Omit raw_input until tool call is complete
    - 'json_str': Emit raw_input as a JSON string
    """

    checkpoint_enabled: bool = False
    """Whether durable elicitation checkpointing is enabled for this session.

    When True, the ACPInputProvider.supports_durable_elicitation property
    returns True, enabling the two-level interception path that checkpoints
    elicitation requests for crash recovery.
    """

    def __post_init__(self) -> None:
        """Initialize session state and set up providers."""
        self.mcp_servers = self.mcp_servers or []
        self.log = logger.bind(session_id=self.session_id)
        self._task_lock = asyncio.Lock()
        self._cancelled = False
        self._current_converter: ACPEventConverter | None = None
        self.last_usage: Usage | None = None
        self.fs = ACPFileSystem(
            self.client,
            session_id=self.session_id,
            client_capabilities=self.client_capabilities,
        )
        self.command_store = CommandStore(commands=get_all_commands())
        self.command_store._initialize_sync()
        self._update_callbacks: list[Callable[[], None]] = []
        self._remote_commands: list[AvailableCommand] = []

        # Skill bridge: converts SkillCommand → SlashedCommand for command_store
        from agentpool_server.acp_server.commands.skill_commands import ACPSkillBridge

        self._skill_bridge: ACPSkillBridge = ACPSkillBridge()
        self._skill_change_task: asyncio.Task[None] | None = None
        self._skill_register_lock = asyncio.Lock()

        # CRITICAL: Initialize requests and acp_env BEFORE agent mutation
        self.notifications = ACPNotifications(client=self.client, session_id=self.session_id)
        self.requests = ACPRequests(client=self.client, session_id=self.session_id)
        self.input_provider = ACPInputProvider(self)
        self.acp_env = ACPExecutionEnvironment(fs=self.fs, requests=self.requests, cwd=self.cwd)

        # Inject Zed-specific instructions if client is Zed
        if self.client_info and self.client_info.name and "zed" in self.client_info.name.lower():
            self.agent.staged_content.add_text(ZED_CLIENT_PROMPT)

        # Only mutate THIS session's agent, not all pool agents
        self.agent.env = self.acp_env
        # CRITICAL: Set the real input provider (overrides temp None from creation)
        self.agent._input_provider = self.input_provider
        if isinstance(self.agent, Agent):
            self.agent.sys_prompts.prompts.append(self.get_cwd_context)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]
            # Wire ACP MCP connection manager for per-session cleanup tracking.
            # Without this, MCPManager.cleanup_session() can never delegate
            # to AcpMcpConnectionManager.cleanup_session(), leaking per-session
            # ACP stream pairs and reverse-index entries.
            self.agent.mcp._acp_mcp_manager = self.acp_agent._mcp_manager
        if isinstance(self.agent, ACPAgent):

            async def permission_callback(
                params: RequestPermissionRequest,
            ) -> RequestPermissionResponse:
                forwarded = params.model_copy(update={"session_id": self.session_id})
                return await self.requests.client.request_permission(forwarded)

            self.agent.acp_permission_callback = permission_callback

        # Subscribe to state changes for THIS agent only
        # Defense: disconnect first (idempotent) to prevent duplicate connections
        with suppress(Exception):
            self.agent.state_updated.disconnect(self._on_state_updated)
        self.agent.state_updated.connect(self._on_state_updated)
        # Register global commands from manifest.commands (e.g., static commands like start_eval)
        self._register_manifest_commands()

        # Register pool-level skills as slash commands
        self._register_skill_commands()

        # Subscribe to dynamic skill changes from ExtensionRegistry
        self._start_skill_change_watcher()

        self.log.info("Created ACP session", current_agent=self.agent.name)

    @property
    def is_busy(self) -> bool:
        """Whether the session is currently processing a prompt.

        Returns:
            True if the task lock is held (active prompt processing).
        """
        return self._task_lock.locked()

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

    def _register_skill_commands(self) -> None:
        """Register pool-level and client-side skills as slash commands.

        Builds SkillCommand objects from the skills registry, feeds them
        through ACPSkillBridge, and registers the resulting SlashedCommand
        objects in command_store with replace=True for idempotent updates.
        """
        from agentpool.skills.command import SkillCommand

        ctx = self.host_context
        skills_registry = ctx.skills_registry
        skills = skills_registry.list_skills()
        cmd_count = 0
        for skill in skills:
            if not skill.user_invocable:
                continue
            skill_cmd = SkillCommand(
                name=skill.name,
                description=skill.description,
                skill=skill,
                skill_uri=f"skill://{skill.name}",
            )
            self._skill_bridge.handle_change(skill_cmd.name, skill_cmd)
            cmd_count += 1

        # Register all bridge commands in command_store with replace=True
        for slashed_cmd in self._skill_bridge.get_commands():
            self.command_store.register_command(slashed_cmd, replace=True)
            self.log.debug(
                "Registered skill command in command_store",
                name=slashed_cmd.name,
            )

        if cmd_count > 0:
            self._notify_command_update()
            self.log.info("Registered skill commands", count=cmd_count)

    def _start_skill_change_watcher(self) -> None:
        """Start watching for dynamic skill changes from ExtensionRegistry."""
        ctx = self.host_context
        if ctx.extension_registry is None:
            return
        self._skill_change_task = asyncio.create_task(
            self._watch_skill_changes(), name=f"skill_watcher_{self.session_id}"
        )

    async def _watch_skill_changes(self) -> None:
        """Watch for skill change events and rebuild skill commands.

        Subscribes to ExtensionRegistry.merge_change_streams() for the
        POOL scope. When a skills_changed event arrives, rebuilds skill
        commands and sends an update to the client.
        """
        from agentpool.capabilities.extension_registry import Scope, ScopeLevel

        ctx = self.host_context
        if ctx.extension_registry is None:
            return

        stream = ctx.extension_registry.merge_change_streams(
            Scope(level=ScopeLevel.POOL)
        )
        if stream is None:
            self.log.debug("No skill change streams to watch")
            return

        try:
            async for event in stream:
                if event.kind != "skills_changed":
                    continue
                self.log.info("Skill change detected, rebuilding skill commands")
                try:
                    async with self._skill_register_lock:
                        self._register_skill_commands()
                    await self.send_available_commands_update()
                except Exception:
                    self.log.exception("Failed to rebuild skill commands after change")
        except asyncio.CancelledError:
            self.log.debug("Skill change watcher cancelled")
            raise
        except Exception:
            self.log.exception("Skill change watcher error")

    async def _on_state_updated(self, state: StateUpdate) -> None:
        """Handle state update signal from agent - forward to ACP client."""
        from acp.schema import (
            AvailableCommandsUpdate,
            ConfigOptionUpdate,
            CurrentModelUpdate,
            CurrentModeUpdate,
        )
        from agentpool_server.acp_server.acp_agent import get_session_config_options

        update: CurrentModeUpdate | CurrentModelUpdate | ConfigOptionUpdate
        match state:
            case ModeInfo(id=mode_id):
                update = CurrentModeUpdate(current_mode_id=mode_id)
                self.log.debug("Forwarding mode change to client", mode_id=mode_id)
            case ModelInfo(id=model_id):
                update = CurrentModelUpdate(current_model_id=model_id)
                self.log.debug("Forwarding model change to client", model_id=model_id)
            case AvailableCommandsUpdate(available_commands=cmds):
                # Store remote commands and send merged list
                self._remote_commands = list(cmds)
                await self.send_available_commands_update()
                self.log.debug("Merged and sent commands update to client")
                return
            case ToastInfo():
                self.log.debug("Received ToastInfo, ignoring")
                return
            case ConfigOptionChanged(config_id=config_id, value_id=value_id):
                # Get full config_options from agent (required by ACP protocol)
                config_options = await get_session_config_options(self.agent)
                # Update the changed option's current_value
                if opt := next((i for i in config_options if i.id == config_id), None):
                    opt.current_value = value_id
                # Convert our core type to ACP type with full config_options
                update = ConfigOptionUpdate(
                    config_id=config_id,
                    value_id=value_id,
                    config_options=config_options,
                )
                self.log.debug("Config option change", config_id=config_id, value_id=value_id)
                # For permissions, also send legacy CurrentModeUpdate (still needed)
                if config_id == "permissions":
                    await self.notifications.update_session_mode(value_id)
                    self.log.debug("Also sent legacy mode update", mode_id=value_id)
        await self.notifications.send_update(update)

    async def initialize(self) -> None:
        """Initialize async resources. Must be called after construction."""
        # Prevent _detect_os_type() from sending terminal/create requests
        # when the client does not support terminal capability.
        # _detect_os_type() runs uname -s / ver via terminal, which fails
        # for clients declaring terminal=false in their capabilities.
        if not self.client_capabilities.terminal:
            import platform

            self.acp_env._os_type = platform.system()  # type: ignore[assignment]
        await self.acp_env.__aenter__()

    def _make_provider_name(self, display_name: str) -> str:
        """Build a provider name that fits within the 63-char DNS-label limit.

        Truncates the session_id (via SHA-256 prefix) when the full name
        would exceed ``MAX_PROVIDER_NAME_LENGTH``.

        Args:
            display_name: The MCP server display name to embed.

        Returns:
            A provider name guaranteed to pass ``_validate_provider_name``.
        """
        prefix = "session_"
        suffix = f"_{display_name}"
        budget = _MAX_PROVIDER_NAME_LENGTH - len(prefix) - len(suffix)
        if budget >= len(self.session_id):
            return f"{prefix}{self.session_id}{suffix}"
        # Truncate session_id to fit — use SHA-256 prefix for collision resistance
        safe_budget = max(0, budget)
        truncated = hashlib.sha256(self.session_id.encode()).hexdigest()[:safe_budget]
        return f"{prefix}{truncated}{suffix}"

    async def initialize_mcp_servers(self) -> None:
        """Initialize MCP servers if any are configured.

        Session-level MCP servers are converted to :class:`McpConfigEntry`
        objects and merged into the session's MCP config snapshot via
        :meth:`MCPManager.update_session_snapshot`.  For ACP-transport
        servers, the transport is registered via
        :meth:`MCPManager.add_acp_transport` so that snapshot-aware
        capability building can reuse it.
        """
        if not self.mcp_servers:
            return
        self.log.info("Initializing MCP servers", server_count=len(self.mcp_servers))

        entries: list[McpConfigEntry] = []

        async def _init_server(server: McpServer) -> None:
            try:
                with anyio.fail_after(30):
                    cfg = convert_acp_mcp_server_to_config(server)

                    # ACP-transport MCP servers need a live connection to the
                    # client before the transport can be created.
                    if isinstance(server, AcpMcpServer):
                        self.log.info(
                            "Connecting ACP MCP server via mcp/connect",
                            server_name=server.name,
                        )
                        connection_id, session_key = await self.acp_agent.connect_acp_mcp_server(
                            server, self.session_id
                        )
                        conn = self.acp_agent._mcp_manager.get_connection(connection_id)
                        if conn is None:
                            raise RuntimeError(  # noqa: TRY301
                                f"AcpMcpConnection not found for {connection_id}"
                            )
                        from agentpool_server.acp_server.acp_mcp_transport import (
                            AcpMcpTransport,
                        )

                        transport = AcpMcpTransport(conn, timeout=600.0)
                        if isinstance(self.agent, Agent):
                            # Register the ACP transport on the MCPManager's
                            # session context so that get_capabilities() can
                            # find it and child sessions can inherit it via
                            # copy_pre_created_transports().
                            await self.agent.mcp.add_acp_transport(
                                self.session_id,
                                cfg.client_id,
                                transport,
                                connection_id,
                                session_key,
                            )
                        self.log.info(
                            "Added session ACP MCP server",
                            server_name=cfg.name,
                            session_id=self.session_id,
                        )
                    else:
                        self.log.info(
                            "Added session MCP server",
                            server_name=cfg.name,
                            session_id=self.session_id,
                        )

                    entries.append(McpConfigEntry(server_config=cfg, source="session"))
            except TimeoutError:
                self.log.warning(
                    "MCP server initialization timed out",
                    server_name=server.name,
                )
            except Exception:
                self.log.exception(
                    "Failed to setup MCP server",
                    server_name=server.name,
                )

        await asyncio.gather(*[_init_server(s) for s in self.mcp_servers])

        # Merge new session configs into the agent's MCP snapshot, deduplicating
        # by client_id so that re-initialisation does not duplicate entries.
        if entries and isinstance(self.agent, Agent):
            ctx = self.agent.mcp.get_session_context(self.session_id)
            existing = ctx.snapshot if ctx is not None else None
            existing_session = existing.session_configs if existing is not None else ()
            seen_ids: set[str] = {e.server_config.client_id for e in existing_session}
            merged: list[McpConfigEntry] = list(existing_session)
            for entry in entries:
                if entry.server_config.client_id not in seen_ids:
                    merged.append(entry)
                    seen_ids.add(entry.server_config.client_id)
            new_snapshot = (existing or McpConfigSnapshot()).with_session_configs(tuple(merged))
            # Sync the updated snapshot to the MCPManager's session context
            # so that get_capabilities(session_id) can discover ACP MCP configs
            # and child sessions can inherit them via copy_pre_created_transports().
            self.agent.mcp.update_session_snapshot(self.session_id, new_snapshot)
            self.log.info(
                "Updated agent MCP snapshot with session configs",
                session_config_count=len(merged),
                session_id=self.session_id,
            )

        # Register MCP prompts as commands after all servers are added
        try:
            await self._register_mcp_prompts_as_commands()
        except Exception:
            self.log.exception("Failed to register MCP prompts as commands")

    async def init_client_skills(self) -> None:
        """Discover and load skills from client-side .claude/skills directory."""
        try:
            await self.host_context.skills_registry.add_skills_directory(
                ".claude/skills", fs=self.fs
            )
            skills = self.host_context.skills_registry.list_skills()
            self.log.info("Collected client-side skills", skill_count=len(skills))
            # Bridge newly discovered skills into command_store
            self._register_skill_commands()
            await self.send_available_commands_update()
        except Exception as e:
            self.log.exception("Failed to discover client-side skills", error=e)

    @property
    def host_context(self) -> HostContext:
        """Get the host context from the current agent."""
        ctx = self.agent.host_context
        if ctx is None:
            msg = "Agent has no associated pool"
            raise RuntimeError(msg)
        return ctx

    def get_cwd_context(self) -> str:
        """Get current working directory context for prompts."""
        return f"Working directory: {self.cwd}" if self.cwd else ""

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
            self.agent.sys_prompts.prompts.remove(self.get_cwd_context)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

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
            self.agent.sys_prompts.prompts.append(self.get_cwd_context)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

        # Reconnect signal
        with suppress(Exception):
            self.agent.state_updated.disconnect(self._on_state_updated)
        self.agent.state_updated.connect(self._on_state_updated)

        self.log.info("Switched agents", from_agent=old_agent_name, to_agent=agent_name)
        # Persist the agent switch via session manager
        if self.manager:
            await self.manager.update_session_agent(self.session_id, agent_name)
        await self.send_available_commands_update()

    async def cancel(self) -> None:
        """Cancel the current prompt turn.

        This actively interrupts the running agent by calling its interrupt() method,
        which handles protocol-specific cancellation (e.g., sending CancelNotification
        for ACP agents, etc.).

        Note:
            Tool call cleanup is handled in process_prompt() to avoid race conditions
            with the converter state being modified from multiple async contexts.
        """
        self._cancelled = True
        self.log.info("Session cancelled, interrupting agent")
        try:  # Actively interrupt the agent's stream
            await self.agent.interrupt()
        except Exception:
            self.log.exception("Failed to interrupt agent")

    def is_cancelled(self) -> bool:
        """Check if the session is cancelled."""
        return self._cancelled

    async def process_prompt(self, content_blocks: Sequence[ContentBlock]) -> StopReason:  # noqa: PLR0911
        """Process a prompt request and stream responses.

        Args:
            content_blocks: List of content blocks from the prompt request

        Returns:
            Stop reason
        """
        self._cancelled = False
        fs = self.agent.env.get_fs()
        contents = [from_acp_content(i, fs=fs) for i in content_blocks]
        self.log.debug("Converted content", content=contents)
        if not contents:
            self.log.warning("Empty prompt received")
            return "refusal"
        commands, non_command_content = split_commands(contents, self.command_store)
        async with self._task_lock:
            if commands:  # Process commands if found
                for command in commands:
                    self.log.info("Processing slash command", command=command)
                    await self.execute_slash_command(command)

                # If only commands and no staged content, end turn
                if not non_command_content and len(self.agent.staged_content) == 0:
                    return "end_turn"

            self.log.debug("Processing prompt", content_items=len(non_command_content))
            event_count = 0
            # Derive turn-complete support from client capabilities
            client_supports_turn_complete = (
                bool(self.client_capabilities.turn_complete)
                if self.client_capabilities is not None
                else False
            )
            # Create a new event converter for this prompt
            converter = ACPEventConverter(
                subagent_display_mode=self.subagent_display_mode,
                raw_input_mode=self.raw_input_mode,
                client_supports_turn_complete=client_supports_turn_complete,
            )
            self._current_converter = converter  # Track for cancellation

            # Route through SessionPool for unified session management.
            # MCP tools are handled via McpConfigSnapshot → get_capabilities() →
            # MCPToolset, not through agent.tools.providers.
            agent_ctx = self.agent.host_context
            session_pool = agent_ctx.session_pool if agent_ctx is not None else None
            try:
                if session_pool is not None:
                    stream = session_pool.run_stream(
                        self.session_id,
                        *non_command_content,
                        input_provider=self.input_provider,
                        deps=self,
                    )
                else:
                    raise RuntimeError(  # noqa: TRY301
                        f"SessionPool is required for prompt processing "
                        f"in session {self.session_id}"
                    )

                async for event in stream:
                    if self._cancelled:
                        self.log.info("Cancelled during event loop, cleaning up tool calls")
                        # Send cancellation notifications for any pending tool calls
                        # This happens in the same async context as the converter
                        async for cancel_update in converter.cancel_pending_tools():
                            await self.notifications.send_update(cancel_update)
                        # CRITICAL: Allow time for client to process tool completion notifications
                        # before sending PromptResponse. Without this delay, the client may receive
                        # and process the PromptResponse before the tool notifications, causing UI
                        # state desync where subsequent prompts appear stuck/unresponsive.
                        # This is needed because even though send() awaits the write, the client
                        # may process messages asynchronously or out of order.
                        await anyio.sleep(0.05)
                        self._current_converter = None
                        return "cancelled"

                    event_count += 1
                    async for update in converter.convert(event):
                        await self.notifications.send_update(update)
                    # Yield control to allow notifications to be sent immediately
                    await anyio.sleep(0.01)
                self.log.info("Streaming finished", events_processed=event_count)
            except asyncio.CancelledError:
                # Task was cancelled (e.g., via interrupt()) - return proper stop reason
                # This is critical: CancelledError doesn't inherit from Exception,
                # so we must catch it explicitly to send the PromptResponse
                self.log.info("Stream cancelled via CancelledError, cleaning up tool calls")
                # Send cancellation notifications for any pending tool calls
                async for cancel_update in converter.cancel_pending_tools():
                    await self.notifications.send_update(cancel_update)
                # CRITICAL: Allow time for client to process tool completion notifications
                # before sending PromptResponse. See comment in cancellation branch above.
                await anyio.sleep(0.05)
                self._current_converter = None
                return "cancelled"
            except UsageLimitExceeded as e:
                self.log.info("Usage limit exceeded", error=str(e))
                return infer_stop_reason(str(e))
            except Exception as e:
                self._current_converter = None  # Clear converter reference
                self.log.exception("Error during streaming")
                # Send error as toast notification instead of polluting chat history
                await self._send_toast(
                    message=f"Agent error: {e}",
                    level="error",
                )
                await anyio.sleep(0.05)  # Allow network buffers to flush
                return "end_turn"
            else:
                # Title generation is now handled automatically by log_session
                self.last_usage = converter.last_usage
                self._current_converter = None  # Clear converter reference
                return "end_turn"

    async def _send_toast(
        self,
        message: str,
        level: str = "error",
        *,
        duration: int | None = None,
        action: dict[str, str] | None = None,
    ) -> None:
        """Send a toast notification via ExtNotification.

        Uses _agentpool/toast ext notification instead of polluting chat
        history with error messages disguised as agent text.

        Args:
            message: Toast message text.
            level: Severity level (error, warning, info, success).
            duration: Display duration in ms; None for persistent.
            action: Optional action button {label, command}.
        """
        if self._cancelled:
            return
        try:
            await self.notifications.send_ext_notification(
                method="_agentpool/toast",
                params={
                    "message": message,
                    "level": level,
                    "duration": duration,
                    "action": action,
                },
            )
        except Exception:
            self.log.exception("Failed to send toast notification")

    async def close(self) -> None:
        """Close the session and cleanup resources."""
        try:
            # Cancel skill change watcher
            if self._skill_change_task is not None:
                self._skill_change_task.cancel()
                with suppress(asyncio.CancelledError):
                    await self._skill_change_task
                self._skill_change_task = None

            # Cleanup MCP session-scoped resources (toolset cache, connection
            # pool, ACP connection manager) before tearing down the agent env.
            # Must run BEFORE acp_env.__aexit__ so the agent context is still live.
            try:
                await self.agent.mcp.cleanup_session(self.session_id)
            except Exception:
                self.log.exception("Failed to cleanup MCP session", session_id=self.session_id)

            await self.acp_env.__aexit__(None, None, None)

            # Disconnect state_updated signal to prevent stale callbacks
            with suppress(Exception):
                self.agent.state_updated.disconnect(self._on_state_updated)

            # Clean up sys_prompts from THIS session's agent only
            if isinstance(self.agent, Agent) and (
                self.get_cwd_context in self.agent.sys_prompts.prompts
            ):
                self.agent.sys_prompts.prompts.remove(self.get_cwd_context)  # pyright: ignore[reportArgumentType]  # ty: ignore[invalid-argument-type]

            # Note: Individual agents are managed by the pool's lifecycle
            # The pool will handle agent cleanup when it's closed
            self.log.info("Closed ACP session")
        except Exception:
            self.log.exception("Error closing session")

    async def send_available_commands_update(self) -> None:
        """Send current available commands to client.

        Merges local commands from command_store with any remote commands
        from nested ACP agents.
        """
        commands = [*self.get_acp_commands(), *self._remote_commands]
        try:
            await self.notifications.update_commands(commands)
        except Exception:
            self.log.exception("Failed to send available commands update")

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

    def _notify_command_update(self) -> None:
        """Notify all registered callbacks about command updates."""
        for callback in self._update_callbacks:
            try:
                callback()
            except Exception:
                logger.exception("Command update callback failed")

    def get_acp_commands(self) -> list[AvailableCommand]:
        """Convert all slashed commands to ACP format."""
        # Filter commands by node compatibility
        cmds = []
        for cmd in self.command_store.list_commands():
            # Check if command supports current node type
            if isinstance(cmd, NodeCommand) and not cmd.supports_node(self.agent):
                continue
            available_cmd = AvailableCommand.create(
                name=cmd.name,
                description=cmd.description,
                input_hint=cmd.usage,
            )
            cmds.append(available_cmd)
        return cmds

    @logfire.instrument(r"Execute Slash Command {command_text}")
    async def execute_slash_command(self, command_text: str) -> None:
        """Execute any slash command with unified handling.

        Args:
            command_text: Full command text (including slash)
            session: ACP session context
        """
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

        # Create context with session data
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

    def register_update_callback(self, callback: Callable[[], None]) -> None:
        """Register callback for command updates."""
        self._update_callbacks.append(callback)
