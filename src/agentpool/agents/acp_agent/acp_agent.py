"""ACP Agent - MessageNode wrapping an external ACP subprocess.

This module provides an agent implementation that communicates with external
ACP (Agent Client Protocol) servers via stdio, enabling integration of any
ACP-compatible agent into the agentpool pool.

The ACPAgent class acts as an ACP client, spawning an ACP server subprocess
and communicating with it via JSON-RPC over stdio. This allows:
- Integration of external ACP-compatible agents (like claude-code-acp)
- Composition with native agents via connections, teams, etc.
- Full ACP protocol support including file operations and terminals

Example:
    ```python
    from agentpool.models.acp_agents import ACPAgentConfig

    config = ACPAgentConfig(
        command="claude-code-acp",
        name="claude_coder",
        cwd="/path/to/project",
    )
    agent = ACPAgent.from_config(config)
    async with agent:
        result = await agent.run("Write a hello world program")
        print(result.content)
    ```
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import datetime
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self
import uuid

import anyio
from pydantic import HttpUrl
from pydantic_ai import (
    UserContent,
)

from acp import InitializeRequest
from acp.agent import ACPAgentAPI
from agentpool.agents.acp_agent.adapter import ACPClientAdapter
from agentpool.agents.acp_agent.session_state import ACPState
from agentpool.agents.acp_agent.turn import ACPTurn
from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.events import (
    RunStartedEvent,
)
from agentpool.agents.exceptions import (
    AgentNotInitializedError,
    UnknownCategoryError,
    UnknownModeError,
)
from agentpool.log import get_logger
from agentpool.utils.subprocess_utils import SubprocessError, run_with_process_monitor


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
    from types import TracebackType

    from anyio.abc import Process
    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from pydantic_ai import UserContent
    from pydantic_ai.messages import ModelMessage
    from slashed import BaseCommand
    from tokonomics.model_discovery.model_info import ModelInfo

    from acp.client.connection import ClientSideConnection
    from acp.conductor import Conductor
    from acp.schema import Implementation, RequestPermissionRequest, RequestPermissionResponse
    from acp.schema.capabilities import AgentCapabilities
    from acp.schema.mcp import McpServer
    from agentpool.agents.acp_agent.client_handler import ACPClientHandler
    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import AnyEventHandlerType
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.mcp_server import ToolBridge
    from agentpool.messaging import ChatMessage, MessageHistory
    from agentpool.models.acp_agents import BaseACPAgentConfig
    from agentpool.orchestrator.turn import Turn
    from agentpool.sessions import SessionData
    from agentpool.tools.factory import ToolsetFactory
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

logger = get_logger(__name__)


def get_updated_at(date_str: str | None) -> datetime:
    from agentpool.utils.time_utils import get_now

    updated_at = get_now()
    if date_str:
        with contextlib.suppress(ValueError, AttributeError):
            updated_at = datetime.fromisoformat(date_str)
    return updated_at


class _TerminalConnectionAdapter:
    """Wraps ClientSideConnection to cache init response for Conductor."""

    def __init__(self, connection: ClientSideConnection, init_response: Any) -> None:
        self._connection = connection
        self._init_response = init_response

    async def send_request(self, method: str, params: Any = None) -> Any:
        if method == "initialize":
            return self._init_response
        return await self._connection.send_request(method, params)

    async def send_notification(self, method: str, params: Any = None) -> None:
        if params is None:
            params = {}
        await self._connection.ext_notification(method, params)

    async def close(self) -> None:
        await self._connection.close()


class ACPAgent[TDeps = None](BaseAgent[TDeps, str]):
    """MessageNode that wraps an external ACP agent subprocess.

    This allows integrating any ACP-compatible agent into the agentpool
    pool, enabling composition with native agents via connections, teams, etc.
    """

    AGENT_TYPE: ClassVar = "acp"

    def __init__(
        self,
        *,
        # Required
        command: str,
        args: list[str] | None = None,
        # Identity
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        provider_type: str = "acp",
        # Environment
        cwd: str | None = None,
        env_vars: dict[str, str] | None = None,
        execution_env: ExecutionEnvironment | None = None,
        client_execution_env: ExecutionEnvironment | None = None,
        # ACP initialization
        init_request: InitializeRequest | None = None,
        # Tools
        tool_factories: list[ToolsetFactory] | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        # Runtime options
        deps_type: type[TDeps] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        auto_approve: bool = False,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
        session_id: str | None = None,
        # Conductor
        proxy_chain: list[Any] | None = None,
    ) -> None:
        super().__init__(
            name=name or command,
            description=description,
            deps_type=deps_type,
            display_name=display_name,
            mcp_servers=mcp_servers,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
            env=execution_env,
            input_provider=input_provider,
            event_handlers=event_handlers,
            commands=commands,
            hooks=hooks,
        )
        # Permission handling
        self.auto_approve = auto_approve
        # Command
        self._command = command
        self._args = args or []
        # Environment
        self._cwd = cwd
        self._env_vars = env_vars or {}
        self._client_env = client_execution_env
        # ACP initialization
        self._init_request = init_request or InitializeRequest.create_for_package("agentpool")
        # Tools
        self._tool_factories = tool_factories or []
        self._extra_toolsets: list[Any] = []
        # Provider type for model messages
        self._provider_type = provider_type
        # ACP-specific state
        self.acp_permission_callback: (
            Callable[[RequestPermissionRequest], Awaitable[RequestPermissionResponse]] | None
        ) = None
        self._process: Process | None = None
        self._connection: ClientSideConnection | None = None
        self._api: ACPAgentAPI | None = None
        self._client_handler: ACPClientHandler | None = None
        self._agent_info: Implementation | None = None
        self._caps: AgentCapabilities | None = None
        self._sdk_session_id: str | None = session_id
        self._state: ACPState | None = None
        self._extra_mcp_servers: list[McpServer] = []
        self._sessions_cache: list[SessionData] | None = None
        # ToolBridge lazily created in _setup_toolsets() when tools exist
        self._tool_bridge: ToolBridge | None = None
        # Track the prompt task for cancellation
        self._prompt_task: asyncio.Task[Any] | None = None
        # Conductor
        self._proxy_chain = proxy_chain
        self._conductor: Conductor | None = None
        self._init_response: Any = None

    @classmethod
    def from_config(
        cls,
        config: BaseACPAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        deps_type: type[TDeps] | None = None,
        agent_pool: AgentPool[Any] | None = None,
    ) -> Self:
        """Create an ACPAgent from a config object."""
        # Merge config-level handlers with provided handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]
        return cls(
            command=config.get_command() or "",
            args=config.get_args(),
            # Identity
            name=config.name,
            description=config.description,
            display_name=config.display_name,
            provider_type=config.type,
            # Environment
            cwd=config.cwd,
            env_vars=config.env_vars,
            execution_env=config.get_execution_environment(),
            client_execution_env=config.get_client_execution_environment(),
            # ACP initialization
            init_request=InitializeRequest.create_for_package(
                "agentpool",
                allow_terminal=config.allow_terminal,
                allow_file_operations=config.allow_file_operations,
            ),
            # Tools
            tool_factories=config.get_tool_factories(),
            mcp_servers=config.mcp_servers,
            # Runtime options
            event_handlers=merged_handlers or None,
            event_configs=list(config.triggers),
            input_provider=input_provider,
            agent_pool=agent_pool,
            deps_type=deps_type,
            auto_approve=config.auto_approve,
            hooks=config.hooks.get_agent_hooks() if config.hooks else None,
            # Conductor
            proxy_chain=config.proxy_chain,
        )

    @property
    def client_env(self) -> ExecutionEnvironment:
        """Execution environment for handling subprocess requests.

        This is used by ACPClientHandler for file/terminal operations requested
        by the subprocess. Falls back to the agent's main env if not explicitly set.
        """
        return self._client_env if self._client_env is not None else self.env

    async def _setup_toolsets(self) -> None:
        """Initialize toolsets and start bridge if needed."""
        from acp.schema import HttpMcpServer
        from agentpool.mcp_server import create_tool_bridge
        from agentpool.tools.base import Tool
        from agentpool.tools.factory import StaticToolsetFactory

        if not self._tool_factories:
            return

        all_tools: list[Any] = []
        self._extra_toolsets = []

        for factory in self._tool_factories:
            match factory:
                case StaticToolsetFactory(tools=factory_tools):
                    all_tools.extend(factory_tools)
                case _:
                    cap = await factory.create_capability()
                    if cap is not None:
                        self._extra_toolsets.append(cap)

        if not all_tools:
            return

        # Register tools with the node's tool manager for bridge discovery
        for tool in all_tools:
            if isinstance(tool, Tool):
                self.tools.register_tool(tool)

        # Lazily create and start the tool bridge
        self._tool_bridge = create_tool_bridge(node=self)
        await self._tool_bridge.start()

        url = HttpUrl(self._tool_bridge.url)
        mcp_config = HttpMcpServer(name=self._tool_bridge.resolved_server_name, url=url)
        self._extra_mcp_servers.append(mcp_config)

    async def _setup_conductor(self) -> None:
        """Set up Conductor for proxy chain execution.

        When proxy_chain is configured, Conductor manages the subprocess
        and proxy chain. ACPAgent wires its own connection/api to the
        Conductor's connection after initialization.
        """
        from acp.conductor import Conductor

        # Create and enter Conductor — it spawns the subprocess and
        # sets up the proxy chain.
        self._conductor = Conductor(
            name=self.name,
            command=self._command,
            args=self._args,
            cwd=self._cwd,
            env=dict(self._env_vars),
            proxy_chain=self._proxy_chain or [],
            client_handler=self._client_handler,
            agent_hooks=self.hooks if self.hooks else None,
        )
        await self._conductor.__aenter__()

        # Wire ACPAgent's connection/api to Conductor's connection
        # so that ACPTurn uses the proxy-chained connection.
        if self._conductor.connection is not None:
            self._connection = self._conductor.connection
            from acp.agent.acp_agent_api import ACPAgentAPI

            self._api = ACPAgentAPI(self._connection)

    async def __aenter__(self) -> Self:
        """Start subprocess and initialize ACP connection."""
        await super().__aenter__()
        await self._setup_toolsets()

        if self._proxy_chain:
            # Proxy chain mode: Conductor manages subprocess + proxy chain.
            # ACPAgent wires its connection/api to Conductor's connection.
            await self._setup_conductor()
            # Initialize and create session using Conductor's connection.
            assert self._conductor is not None
            assert self._conductor.process is not None
            self._process = self._conductor.process
            process = self._process
            try:
                await run_with_process_monitor(
                    process, self._initialize, context="ACP initialization"
                )
                await run_with_process_monitor(
                    process, self._create_session, context="ACP session creation"
                )
            except SubprocessError as e:
                await self._cleanup()
                raise RuntimeError(str(e)) from e
            except Exception:
                await self._cleanup()
                raise
        else:
            # Direct mode: ACPAgent manages its own subprocess.
            process = await self._start_process()
            try:
                await run_with_process_monitor(
                    process, self._initialize, context="ACP initialization"
                )
                # Load existing session or create new one
                if session_to_load := self._sdk_session_id:
                    self._sdk_session_id = None
                    result = await run_with_process_monitor(
                        process,
                        lambda: self.load_session(session_to_load),
                        context="ACP session load",
                    )
                    if result is None:
                        self.log.warning(
                            "Failed to load session, creating new one",
                            session_id=session_to_load,
                        )
                        await run_with_process_monitor(
                            process, self._create_session, context="ACP session creation"
                        )
                else:
                    await run_with_process_monitor(
                        process, self._create_session, context="ACP session creation"
                    )
            except SubprocessError as e:
                raise RuntimeError(str(e)) from e
        await anyio.sleep(0.3)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up subprocess and connection."""
        await self._cleanup()
        await super().__aexit__(exc_type, exc_val, exc_tb)

    async def _start_process(self) -> Process:
        """Start the ACP server subprocess."""
        args = self._args
        cmd = [self._command, *args]
        self.log.info("Starting ACP subprocess", command=cmd)
        env = {**os.environ, **self._env_vars}
        cwd = str(self._cwd) if self._cwd else None
        self._process = await anyio.open_process(cmd, env=env, cwd=cwd)
        if not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Failed to create subprocess pipes")
        return self._process

    async def _initialize(self) -> None:
        """Initialize the ACP connection."""
        from acp.client.connection import ClientSideConnection
        from agentpool.agents.acp_agent.client_handler import ACPClientHandler

        if not self._process or not self._process.stdin or not self._process.stdout:
            raise RuntimeError("Process not started")

        self._state = ACPState(session_id="")
        self._client_handler = ACPClientHandler(self, self._state, self._input_provider)
        self._connection = ClientSideConnection(
            to_client=self._client_handler,
            input_stream=self._process.stdin,
            output_stream=self._process.stdout,
        )
        self._api = ACPAgentAPI(self._connection)
        init_response = await self._connection.initialize(self._init_request)
        self._init_response = init_response
        self._agent_info = init_response.agent_info
        self._caps = init_response.agent_capabilities
        self.log.info("ACP connection initialized", agent_info=self._agent_info)

    async def _create_session(self) -> None:
        """Create a new ACP session with configured MCP servers."""
        from agentpool.agents.acp_agent.acp_converters import mcp_config_to_acp
        from agentpool.agents.acp_agent.helpers import filter_servers_by_capabilities

        if not self._api:
            raise AgentNotInitializedError

        # Collect all MCP servers (extra + from mcp_servers list)
        all_servers = self._extra_mcp_servers[:]
        # Add servers from mcp_servers (converted to ACP format)
        if self.mcp.servers:
            all_servers.extend([mcp_config_to_acp(config) for config in self.mcp.servers])
        mcp_servers = filter_servers_by_capabilities(all_servers, self._caps, logger=self.log)
        response = await self._api.new_session(self._cwd, mcp_servers=mcp_servers or None)
        self._sdk_session_id = response.session_id
        if self._state:
            self._state.session_id = self._sdk_session_id
            if response.config_options:
                self._state.config_options = list(response.config_options)
            if response.models:
                self._state.models = response.models
                self._state.current_model_id = response.models.current_model_id
            self._state.modes = response.modes
        model = self._state.current_model_id if self._state else None
        self.log.info("ACP session created", session_id=self._sdk_session_id, model=model)

    async def _cleanup(self) -> None:
        """Clean up resources."""
        if self._conductor is not None:
            await self._conductor.__aexit__(None, None, None)
            self._conductor = None
        if self._tool_bridge is not None:
            await self._tool_bridge.stop()
            self._tool_bridge = None
        self._extra_toolsets.clear()
        self._extra_mcp_servers.clear()
        if self._client_handler:
            await self._client_handler.cleanup()
            self._client_handler = None
        if self._connection:
            try:
                await self._connection.close()
            except Exception:
                self.log.exception("Error closing ACP connection")
            self._connection = None
            self._api = None

        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()
            except Exception:
                self.log.exception("Error terminating ACP process")
            self._process = None

    async def _stream_events(
        self,
        run_ctx: AgentRunContext,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        deps: TDeps | None = None,
        wait_for_connections: bool | None = None,
        store_history: bool = True,
    ) -> AsyncIterator[RichAgentStreamEvent[str]]:
        """Stream events by delegating to ACPTurn.execute() via create_turn().

        This is a thin wrapper preserved for backward compatibility.
        The actual execution logic lives in ACPTurn.execute().
        """
        if input_provider is not None and self._client_handler:
            self._client_handler._input_provider = input_provider
        if not self._api or not self._sdk_session_id or not self._state:
            raise AgentNotInitializedError

        assert session_id is not None

        # Handle ephemeral execution (fork session if store_history=False)
        acp_session_id = self._sdk_session_id
        if not store_history and self._sdk_session_id:
            cwd = self._cwd or str(Path.cwd())
            fork_response = await self._api.fork_session(self._sdk_session_id, cwd)
            acp_session_id = fork_response.session_id
            self.log.debug("Forked session", parent=self._sdk_session_id, fork=acp_session_id)

        # Delegate to ACPTurn.execute() via create_turn()
        assert self._api is not None
        assert self._client_handler is not None
        turn = self.create_turn(
            prompts=prompts,
            run_ctx=run_ctx,
            message_history=message_history,  # type: ignore[arg-type]
        )

        run_id = str(uuid.uuid4())
        yield RunStartedEvent(
            session_id=session_id,
            run_id=run_id,
            agent_name=self.name,
            parent_session_id=parent_session_id,
        )

        async for event in turn.execute():
            yield event

        if turn._final_message is not None:
            self._final_message = turn._final_message
        if turn._message_history:
            self._message_history = turn._message_history

    @property
    def model_name(self) -> str | None:
        """Get the model name in a consistent format."""
        return model_id if self._state and (model_id := self._state.current_model_id) else None

    async def set_model(self, model: str) -> None:
        """Update the model for the current session via ACP protocol."""
        await self._set_mode(model, "model")

    async def set_auto_approve(self, auto_approve: bool) -> None:
        """Set auto-approve mode for permission requests.

        Args:
            auto_approve: If True, automatically approve all permission requests.
                         If False, forward to callback/input_provider.
        """
        self.auto_approve = auto_approve
        self.log.info("Auto-approve mode changed", auto_approve=auto_approve)

    def create_turn(
        self,
        prompts: list[UserContent],
        run_ctx: AgentRunContext,
        message_history: list[ModelMessage],
    ) -> Turn:
        """Create an ACPTurn for single-cycle execution.

        Args:
            prompts: Pre-converted prompt strings for this turn.
            run_ctx: Per-run isolated context.
            message_history: Incoming message history.

        Returns:
            An ACPTurn instance for single-cycle execution.
        """
        assert self._api is not None
        assert self._client_handler is not None
        str_prompts: list[str] = [str(p) if not isinstance(p, str) else p for p in prompts]
        return ACPTurn(
            acp_client=ACPClientAdapter(self._api, self._client_handler),
            prompts=str_prompts,
            run_ctx=run_ctx,
            message_history=message_history,
            session_id=self._sdk_session_id or run_ctx.session_id,
            agent_name=self.name,
            hooks=self.hooks,
            env=self.env,
        )

    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        """Send CancelNotification to remote ACP server and mark run as cancelled.

        Args:
            run_ctx: Optional per-run context for the stream to interrupt
        """
        if self._api and self._sdk_session_id:
            try:
                await self._api.cancel(self._sdk_session_id)
                self.log.info("Sent cancel notification to ACP server")
            except Exception:
                self.log.exception("Failed to send cancel notification to ACP server")
        if run_ctx is not None:
            run_ctx.cancelled = True
            self.log.info("Marked run as cancelled")

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models from the ACP session state."""
        from tokonomics.model_discovery.model_info import ModelInfo

        if not self._state or not self._state.models:
            return None
        return [
            ModelInfo(id=m.model_id, name=m.name, description=m.description)
            for m in self._state.models.available_models
        ]

    async def get_modes(self) -> list[ModeCategory]:
        """Get available modes from the ACP session state."""
        from agentpool.agents.acp_agent.acp_converters import get_modes

        if not self._state:
            return []

        return get_modes(
            self._state.config_options,
            available_modes=self._state.modes,
            available_models=self._state.models,
        )

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        """Forward mode change to remote ACP server."""
        if not self._api or not self._sdk_session_id or not self._state:
            raise RuntimeError("Not connected to ACP server")
        available_modes = await self.get_modes()
        if matching_category := next((c for c in available_modes if c.id == category_id), None):
            valid_ids = {m.id for m in matching_category.available_modes}
            if mode_id not in valid_ids:
                raise UnknownModeError(mode_id, sorted(valid_ids))
        else:
            available_cats = {c.id for c in available_modes}
            raise UnknownCategoryError(category_id, sorted(available_cats))
        if self._state.config_options:
            assert category_id
            response = await self._api.set_session_config_option(
                self._sdk_session_id, category_id, mode_id
            )
            if response and response.config_options:
                self._state.config_options = list(response.config_options)
        elif category_id == "mode":
            await self._api.set_session_mode(self._sdk_session_id, mode_id)
            if self._state.modes:
                self._state.modes.current_mode_id = mode_id
        elif category_id == "model":
            if await self._api.set_session_model(self._sdk_session_id, mode_id):
                self._state.current_model_id = mode_id
                self.log.info("Model changed via legacy set_session_model")
            else:
                raise RuntimeError("Remote ACP agent does not support model changes.")
        else:
            raise UnknownCategoryError(category_id, ["mode", "model"])
        await self.update_state(config_id=category_id, value_id=mode_id)

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from the remote ACP server."""
        from agentpool.sessions.models import SessionData

        if not self._api:
            raise RuntimeError("Not connected to ACP server")
        try:
            response = await self._api.list_sessions(cwd)
        except Exception:
            self.log.exception("Failed to list sessions from ACP server")
            return []
        else:
            result: list[SessionData] = []
            for acp_session in response.sessions:
                updated_at = get_updated_at(acp_session.updated_at)
                meta = acp_session.field_meta or {}
                created_at = updated_at
                if meta_created := meta.get("created_at"):
                    created_at = get_updated_at(meta_created)
                session_data = SessionData(
                    session_id=acp_session.session_id,
                    agent_name=self.name,
                    cwd=acp_session.cwd,
                    created_at=created_at,
                    last_active=updated_at,
                    pool_id=meta.get("pool_id"),
                    project_id=meta.get("project_id"),
                    parent_id=meta.get("parent_id"),
                    version=meta.get("version", "1"),
                    metadata={"title": acp_session.title} if acp_session.title else {},
                )
                result.append(session_data)
            self._sessions_cache = result
            if limit is not None:
                result = result[:limit]
            return result

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session from the remote ACP server."""
        from agentpool.agents.acp_agent.acp_converters import (
            acp_notifications_to_messages,
            mcp_config_to_acp,
        )
        from agentpool.agents.acp_agent.helpers import filter_servers_by_capabilities
        from agentpool.sessions.models import SessionData
        from agentpool.utils.time_utils import get_now

        if not self._api:
            self.log.error("Cannot load session: not connected to ACP server")
            return None

        if not self._state:
            self.log.error("Cannot load session: state not initialized")
            return None
        all_servers = self._extra_mcp_servers[:]
        if self.mcp.servers:
            all_servers.extend([mcp_config_to_acp(config) for config in self.mcp.servers])
        mcp_servers = filter_servers_by_capabilities(all_servers, self._caps, logger=self.log)
        cwd = self._cwd or str(Path.cwd())
        try:
            self._state.start_load()
            response = await self._api.load_session(session_id, cwd, mcp_servers or None)
            # Allow pending session/update notifications from replay to be processed
            # before finishing load capture. Notifications may be in flight when
            # the JSON-RPC response arrives.
            await asyncio.sleep(0)
            raw_updates = self._state.finish_load()

            self._sdk_session_id = session_id
            self._state.session_id = session_id

            if response.config_options:
                self._state.config_options = list(response.config_options)
            if response.models:
                self._state.models = response.models
                self._state.current_model_id = response.models.current_model_id
            if response.modes:
                self._state.modes = response.modes

            if raw_updates:
                messages = acp_notifications_to_messages(
                    raw_updates,
                    session_id=session_id,
                    agent_name=self.name,
                    model_name=self.model_name,
                )
                self.conversation.chat_messages.clear()
                self.conversation.chat_messages.extend(messages)
                self.log.info("Restored session", session_id=session_id, msg_count=len(messages))
            else:
                self.log.debug("No conversation history to restore", session_id=session_id)

            self.log.info("Session loaded from ACP server", session_id=session_id)

            def find_in_cache(sid: str) -> SessionData | None:
                if self._sessions_cache is None:
                    return None
                return next((s for s in self._sessions_cache if s.session_id == sid), None)

            if session_info := find_in_cache(session_id):
                return session_info

            try:
                await self.list_sessions()
                if session_info := find_in_cache(session_id):
                    return session_info
            except Exception:  # noqa: BLE001
                self.log.debug("Could not fetch session metadata", session_id=session_id)
            now = get_now()
            return SessionData(
                session_id=session_id,
                agent_name=self.name,
                cwd=cwd,
                last_active=now,
                created_at=now,
            )

        except Exception:
            if self._state:
                self._state.finish_load()
            self.log.exception("Failed to load session from ACP server")
            return None


if __name__ == "__main__":

    async def main() -> None:
        async with ACPAgent(command="claude-code-acp") as agent:
            async for event in agent.run_stream("hello"):
                print(event)

    asyncio.run(main())
