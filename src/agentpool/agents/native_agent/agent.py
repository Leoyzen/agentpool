"""The main Agent. Can do all sort of crazy things."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import timedelta
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Self, TypedDict, TypeVar, overload
import warnings

import logfire
from pydantic_ai import (
    Agent as PydanticAgent,
)
from pydantic_ai.models import Model


try:
    from pydantic_ai import AgentRetries
    from pydantic_ai.capabilities import NativeTool, ProcessHistory
except ImportError:
    AgentRetries = None  # type: ignore[misc,assignment]
    from pydantic_ai.capabilities import (  # type: ignore[no-redef]
        BuiltinTool as NativeTool,
        HistoryProcessor as ProcessHistory,
    )

from agentpool.agents.base_agent import BaseAgent
from agentpool.agents.context import AgentContext
from agentpool.agents.events import (
    RunErrorEvent,
)
from agentpool.agents.exceptions import UnknownCategoryError, UnknownModeError
from agentpool.agents.native_agent.turn import NativeTurn
from agentpool.capabilities.function_toolset import FunctionToolsetCapability
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.storage import StorageManager
from agentpool.tools import Tool
from agentpool.tools.exceptions import ToolError
from agentpool.utils.result_utils import to_type


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Sequence
    from types import TracebackType

    from exxec import ExecutionEnvironment
    from pydantic_ai import AgentBuiltinTool, UsageLimits, UserContent
    from pydantic_ai.capabilities import AbstractCapability
    from pydantic_ai.messages import ModelMessage
    from pydantic_ai.models import Model
    from pydantic_ai.output import OutputSpec
    from pydantic_ai.settings import ModelSettings
    from slashed import BaseCommand
    from tokonomics.model_discovery import ProviderType
    from tokonomics.model_discovery.model_info import ModelInfo
    from toprompt import AnyPromptType
    from upathtools import JoinablePathLike

    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.agents.modes import ModeCategory
    from agentpool.common_types import (
        AnyEventHandlerType,
        EndStrategy,
        ModelType,
        ProcessorCallback,
        SessionIdType,
        StrPath,
        ToolType,
    )
    from agentpool.delegation import AgentPool
    from agentpool.hooks import AgentHooks
    from agentpool.mcp_server.config_snapshot import McpConfigEntry
    from agentpool.messaging import MessageNode
    from agentpool.models.agents import NativeAgentConfig, ToolMode
    from agentpool.orchestrator.turn import Turn
    from agentpool.prompts.prompts import PromptType
    from agentpool.sessions import SessionData
    from agentpool.tools.base import FunctionTool
    from agentpool.ui.base import InputProvider
    from agentpool_config.knowledge import Knowledge
    from agentpool_config.mcp_server import MCPServerConfig
    from agentpool_config.nodes import ToolConfirmationMode
    from agentpool_config.session import MemoryConfig, SessionQuery


logger = get_logger(__name__)
NoneType = type(None)


def _build_capability_from_config(config: Any) -> Any:
    """Build a capability instance from a config model.

    Delegates to ``agentpool_config.capabilities.build_capability()``,
    using late import to avoid a static config→core dependency.
    """
    from agentpool_config.capabilities import build_capability

    return build_capability(config)


TResult = TypeVar("TResult")
VALID_MODES = ["always", "never", "per_tool"]


class AgentKwargs(TypedDict, total=False):
    """Keyword arguments for configuring an Agent instance."""

    description: str | None
    model: ModelType
    system_prompt: str | Sequence[str]
    tools: Sequence[ToolType] | None
    toolsets: Sequence[AbstractCapability] | None
    mcp_servers: Sequence[str | MCPServerConfig] | None
    skills_paths: Sequence[JoinablePathLike] | None
    retries: int
    output_retries: int | None
    end_strategy: EndStrategy
    # context: AgentContext[Any] | None  # x
    session: SessionIdType | SessionQuery | MemoryConfig | bool
    input_provider: InputProvider | None
    event_handlers: Sequence[AnyEventHandlerType] | None
    env: ExecutionEnvironment | None

    hooks: AgentHooks | None
    model_settings: ModelSettings | None
    usage_limits: UsageLimits | None
    providers: Sequence[ProviderType] | None


class Agent[TDeps = None, OutputDataT = str](BaseAgent[TDeps, OutputDataT]):
    """The main agent class.

    Generically typed with: Agent[Type of Dependencies, Type of Result]
    """

    AGENT_TYPE: ClassVar = "native"

    def __init__(  # noqa: PLR0915
        # we dont use AgentKwargs here so that we can work with explicit ones in the ctor
        self,
        name: str = "agentpool",
        *,
        deps_type: type[TDeps] | None = None,
        model: ModelType,
        output_type: OutputSpec[OutputDataT] = str,  # type: ignore[assignment]
        # context: AgentContext[TDeps] | None = None,
        session: SessionIdType | SessionQuery | MemoryConfig | bool = None,
        system_prompt: AnyPromptType | Sequence[AnyPromptType] = (),
        description: str | None = None,
        display_name: str | None = None,
        tools: Sequence[ToolType] | None = None,
        toolsets: Sequence[AbstractCapability] | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        resources: Sequence[PromptType | str] = (),
        skills_paths: Sequence[JoinablePathLike] | None = None,
        retries: int = 1,
        output_retries: int | None = None,
        end_strategy: EndStrategy = "early",
        input_provider: InputProvider | None = None,
        parallel_init: bool = True,
        model_settings: ModelSettings | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        tool_mode: ToolMode | None = None,
        knowledge: Knowledge | None = None,
        agent_config: NativeAgentConfig | None = None,
        env: ExecutionEnvironment | StrPath | None = None,
        hooks: AgentHooks | None = None,
        tool_confirmation_mode: ToolConfirmationMode = "per_tool",
        builtin_tools: Sequence[AgentBuiltinTool] | None = None,
        usage_limits: UsageLimits | None = None,
        providers: Sequence[ProviderType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        metadata: dict[str, Any] | None = None,
        history_processors: Sequence[Callable[..., Any]] | None = None,
        capabilities: list[Any] | None = None,
    ) -> None:
        """Initialize agent.

        Args:
            name: Identifier for the agent (used for logging and lookups)
            deps_type: Type of dependencies to use
            model: The default model to use (defaults to GPT-5)
            output_type: The default output type to use (defaults to str)
            context: Agent context with configuration
            session: Memory configuration.
                - None: Default memory config
                - False: Disable message history (max_messages=0)
                - int: Max tokens for memory
                - str/UUID: Session identifier
                - MemoryConfig: Full memory configuration
                - MemoryProvider: Custom memory provider
                - SessionQuery: Session query

            system_prompt: System prompts for the agent
            description: Description of the Agent ("what it can do")
            display_name: Human-readable display name (falls back to name)
            tools: List of tools to register with the agent
            toolsets: List of toolset resource providers for the agent
            mcp_servers: MCP servers to connect to
            resources: Additional resources to load
            skills_paths: Local directories to search for agent-specific skills
            retries: Default number of retries for failed operations
            output_retries: Max retries for result validation (defaults to retries)
            end_strategy: Strategy for handling tool calls that are requested alongside
                          a final result
            input_provider: Provider for human input (tool confirmation / HumanProviders)
            parallel_init: Whether to initialize resources in parallel
            model_settings: Settings for the AI model
            event_handlers: Sequence of event handlers to register with the agent
            agent_pool: AgentPool instance for managing agent resources
            tool_mode: Tool execution mode (None or "codemode")
            knowledge: Knowledge sources for this agent
            agent_config: Agent configuration
            env: Execution environment for code/command execution and filesystem access
            hooks: AgentHooks instance for intercepting agent behavior at run and tool events
            tool_confirmation_mode: Tool confirmation mode
            builtin_tools: PydanticAI builtin tools (WebSearchTool, CodeExecutionTool, etc.)
            usage_limits: Per-request usage limits (applied to each run() call independently,
                not cumulative across the session)
            providers: Model providers for model discovery (e.g., ["openai", "anthropic"]).
                Defaults to ["models.dev"] if not specified.
            commands: Slash commands
            metadata: Arbitrary metadata for the agent (e.g., feature flags)
            history_processors: Callable history processors for message processing
            capabilities: Extra capability instances or configs to attach
        """
        from agentpool.agents.interactions import Interactions
        from agentpool.agents.native_agent.hook_manager import NativeAgentHookManager
        from agentpool.agents.sys_prompts import SystemPrompts
        from agentpool.models.manifest import AgentsManifest
        from agentpool.prompts.conversion_manager import ConversionManager
        from agentpool_commands.pool import CompactCommand
        from agentpool_config.session import MemoryConfig

        self.model_settings = model_settings
        self.config = agent_config
        self._direct_history_processors = None
        memory_cfg = (
            session if isinstance(session, MemoryConfig) else MemoryConfig.from_value(session)
        )
        # Collect MCP servers from config
        all_mcp_servers = list(mcp_servers) if mcp_servers else []
        if agent_config and agent_config.mcp_servers:
            all_mcp_servers.extend(agent_config.get_mcp_servers())
        # Add CompactCommand - only makes sense for Native Agent (has own history)
        # Other agents (ClaudeCode, ACP, AGUI) don't control their history directly
        all_commands = list(commands) if commands else []
        all_commands.append(CompactCommand())
        # Call base class with shared parameters
        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            deps_type=deps_type,
            enable_logging=memory_cfg.enable,
            mcp_servers=all_mcp_servers,
            agent_pool=agent_pool,
            event_configs=agent_config.triggers if agent_config else [],
            env=env,
            input_provider=input_provider,
            output_type=to_type(output_type),  # type: ignore[arg-type]
            event_handlers=event_handlers,
            commands=all_commands,
            hooks=hooks,
        )
        self.metadata = dict(metadata) if metadata else {}
        self.tool_confirmation_mode: ToolConfirmationMode = tool_confirmation_mode
        # Store builtin tools for pydantic-ai
        self._builtin_tools = list(builtin_tools) if builtin_tools else []
        # Initialize built-in tools and tool mode (replaces deprecated ToolManager)
        self._tool_mode = tool_mode
        self._builtin_provider = FunctionToolsetCapability(name="builtin")
        for tool in tools or []:
            self._builtin_provider.add_tool(tool)
        for toolset_provider in toolsets or []:
            self._external_capabilities.append(toolset_provider)
        aggregating_provider = self.mcp.get_aggregating_provider()
        self._external_capabilities.append(aggregating_provider)
        # Override conversation with Agent-specific MessageHistory (with storage, etc.)
        resources = list(resources)
        if knowledge:
            resources.extend(knowledge.get_resources())  # type: ignore[arg-type]
        manifest = agent_pool.manifest if agent_pool else AgentsManifest()
        storage = agent_pool.storage if agent_pool else StorageManager()
        self.conversation = MessageHistory(
            storage=storage,
            converter=ConversionManager(config=manifest.conversion),
            session_config=memory_cfg,
            resources=resources,
        )
        if isinstance(model, str):
            self._model, settings = self._resolve_model_string(model)
            if settings:
                self.model_settings = settings
        else:
            self._model = model
        self._retries = retries
        self._end_strategy: EndStrategy = end_strategy
        self._output_retries = output_retries
        self.parallel_init = parallel_init
        self._iteration_task: asyncio.Task[Any] | None = None
        self.talk = Interactions(self)
        # Set up system prompts
        all_prompts: list[AnyPromptType] = []
        if isinstance(system_prompt, (list, tuple)):
            all_prompts.extend(system_prompt)
        elif system_prompt:
            all_prompts.append(system_prompt)
        prompt_manager = self.host_context.prompt_manager if self.host_context else None
        self.sys_prompts = SystemPrompts(all_prompts, prompt_manager=prompt_manager)
        self._formatted_system_prompt: str | None = None  # Set in __aenter__
        self._hook_manager = NativeAgentHookManager(
            agent=self,
            agent_hooks=hooks,
        )
        logger.debug(
            "NativeAgent hooks initialized",
            agent_name=name,
            has_hooks=hooks is not None,
            hooks_repr=repr(hooks) if hooks else "None",
        )
        self._default_usage_limits = usage_limits
        self._providers = list(providers) if providers else None  # model discovery
        self._direct_history_processors = list(history_processors) if history_processors else None
        self._resolved_history_processors: list[Callable[..., Any]] | None = None
        self._extra_capabilities: list[Any] = capabilities or []

    def _build_pool_configs(self) -> tuple[McpConfigEntry, ...]:
        """Build MCP config entries from pool-level servers.

        Reads from the pool's MCPManager when the agent is part of a pool.
        When the agent is standalone (no pool), returns an empty tuple.

        Returns:
            Tuple of pool-scoped MCP config entries.
        """
        from agentpool.mcp_server.config_snapshot import McpConfigEntry

        if self.host_context is None:
            return ()
        pool_mcp = self.host_context.mcp
        return tuple(
            McpConfigEntry(server_config=server, source="pool")
            for server in pool_mcp.servers
            if server.enabled
        )

    def _build_agent_configs(self) -> tuple[McpConfigEntry, ...]:
        """Build MCP config entries from the agent's own servers.

        When the agent shares the pool's MCPManager (no own servers),
        returns an empty tuple. Otherwise reads from the agent's
        dedicated MCPManager.

        Returns:
            Tuple of agent-scoped MCP config entries.
        """
        from agentpool.mcp_server.config_snapshot import McpConfigEntry

        if self._mcp_shared:
            return ()
        return tuple(
            McpConfigEntry(server_config=server, source="agent")
            for server in self.mcp.servers
            if server.enabled
        )

    def _validate_processor_signature(self, processor: Callable[..., Any]) -> None:
        """Validate that a history processor has been correct signature.

        Valid signatures:
        - sync: (messages) -> msgs
        - sync with ctx: (ctx, messages) -> msgs
        - async: async (messages) -> msgs
        - async with ctx: async (ctx, messages) -> msgs

        Args:
            processor: The processor to validate

        Raises:
            ValueError: If signature is not valid
        """
        # Define constant for parameter validation
        two_params = 2

        sig = inspect.signature(processor)
        params = list(sig.parameters.values())

        # Check parameter count
        if len(params) not in (1, two_params):
            msg = f"History processor must take 1 or {two_params} arguments, got {len(params)}"
            raise ValueError(msg)

        # Second parameter (if present) must be named 'messages' or similar
        if len(params) == two_params:
            last_param_name = params[1].name.lower()
            if last_param_name not in ("messages", "msgs", "history"):
                msg = (
                    f"Second parameter of history processor must be "
                    f"messages/msgs/history, got {params[1].name}"
                )
                raise ValueError(msg)

    def _resolve_history_processors(self, *, _warn: bool = True) -> list[Callable[..., Any]]:
        """Resolve history processors from config with caching.

        .. deprecated::
            This method is deprecated and will be removed in v0.5.0.
            Use ``ProcessHistoryAdapter`` instead.

        Returns:
            List of resolved processor callables
        """
        if _warn:
            warnings.warn(
                "_resolve_history_processors() is deprecated and will be removed in v0.5.0. "
                "Use ProcessHistoryAdapter instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        # Return cached result if available
        if self._resolved_history_processors is not None:
            return self._resolved_history_processors

        resolved: list[Callable[..., Any]] = []

        # Import paths from MemoryConfig (session)
        if memory_cfg := self.conversation._config:
            processor_paths = getattr(memory_cfg, "history_processors", None) or []
            if processor_paths:
                from agentpool.utils.importing import import_callable

                for path in processor_paths:
                    try:
                        processor = import_callable(path)
                        self._validate_processor_signature(processor)
                        resolved.append(processor)
                    except Exception as e:
                        msg = f"Failed to resolve history processor '{path}': {e}"
                        raise ValueError(msg) from e

        # Deprecated direct callables (append after config-based processors)
        if self._direct_history_processors:
            for processor in self._direct_history_processors:
                self._validate_processor_signature(processor)
                resolved.append(processor)

        self._resolved_history_processors = resolved
        return resolved

    @classmethod
    def from_config(  # noqa: PLR0915
        cls,
        config: NativeAgentConfig,
        *,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        input_provider: InputProvider | None = None,
        agent_pool: AgentPool[Any] | None = None,
        deps_type: type[TDeps] | None = None,
    ) -> Self:
        """Create a native Agent from a config object.

        This is the preferred way to instantiate an Agent from configuration.
        Handles system prompt resolution, model resolution, toolsets setup, etc.

        Args:
            config: Native agent configuration
            name: Optional name override (used for manifest lookups, defaults to config.name)
            event_handlers: Optional event handlers (merged with config handlers)
            input_provider: Optional input provider for user interactions
            agent_pool: Optional agent pool for coordination
            deps_type: Optional dependency type

        Returns:
            Configured Agent instance
        """
        from agentpool.models.manifest import AgentsManifest
        from agentpool.utils.result_utils import to_type
        from agentpool_config.system_prompts import (
            FilePromptConfig,
            FunctionPromptConfig,
            LibraryPromptConfig,
            PackagePromptConfig,
            StaticPromptConfig,
        )
        from agentpool_toolsets.builtin.workers import WorkersTools

        name = config.name or "native_agent"
        # Get manifest from pool or create empty one
        manifest = agent_pool.manifest if agent_pool is not None else AgentsManifest()
        # Normalize system_prompt to a list for iteration
        sys_prompts: list[str] = []
        if (sys_prompt := config.system_prompt) is not None:
            prompts_to_process = [sys_prompt] if isinstance(sys_prompt, str) else sys_prompt
            for prompt in prompts_to_process:
                match prompt:
                    case (str() as sys_prompt) | StaticPromptConfig(content=sys_prompt):
                        sys_prompts.append(sys_prompt)
                    case FilePromptConfig(path=path, variables=variables):
                        # ConfigPath has already resolved the path relative to config directory
                        # Just use it directly
                        template_path = Path(str(path))
                        template_content = template_path.read_text("utf-8")
                        if variables:
                            from jinja2 import Template

                            template = Template(template_content)
                            content = template.render(**variables)
                        else:
                            content = template_content
                        sys_prompts.append(content)
                    case LibraryPromptConfig(reference=reference):
                        if agent_pool is None:
                            msg = f"Cannot resolve library prompt {reference!r}: no agent pool"
                            raise ValueError(msg)
                        try:
                            content = agent_pool.prompt_manager.get.sync(reference)
                            sys_prompts.append(content)
                        except Exception as e:
                            msg = f"Failed to load library prompt {reference!r} for {name!r}"
                            logger.exception(msg)
                            raise ValueError(msg) from e
                    case FunctionPromptConfig(function=function, arguments=arguments):
                        content = function(**arguments)
                        sys_prompts.append(content)
                    case PackagePromptConfig(
                        package=pkg,
                        resource=resource,
                        variables=variables,
                    ):
                        from importlib.resources import files as pkg_files

                        template_content = (pkg_files(pkg) / resource).read_text(encoding="utf-8")
                        if variables:
                            from jinja2 import Template

                            content = Template(template_content).render(**variables)
                        else:
                            content = template_content
                        sys_prompts.append(content)

        # Prepare toolsets list
        toolsets_list = config.get_toolsets()
        if config_tool_provider := config.get_tool_provider():
            toolsets_list.append(config_tool_provider)
        # Convert workers config to a toolset (backwards compatibility)
        if config.workers:
            workers_provider = WorkersTools(workers=list(config.workers), name="workers")
            toolsets_list.append(workers_provider)
        # Resolve output type from config
        resolved_output_type = to_type(t, manifest.responses) if (t := config.output_type) else str
        # Merge event handlers
        config_handlers = config.get_event_handlers()
        merged_handlers: list[AnyEventHandlerType] = [*config_handlers, *(event_handlers or [])]

        # Handle model configuration - resolve model_variants reference if needed
        from agentpool.models.model_configs import StringModelConfig

        model_config = config.model
        if (
            isinstance(model_config, StringModelConfig)
            and model_config.identifier in manifest.model_variants
        ):
            # The identifier is a model_variants key, use the variant config
            model_config = manifest.model_variants[model_config.identifier]

        resolved_model = manifest.resolve_model(model_config)
        return cls(
            model=resolved_model.get_model(),
            model_settings=resolved_model.get_model_settings(),
            system_prompt=sys_prompts,
            name=name,
            display_name=config.display_name,
            deps_type=deps_type,
            env=config.get_execution_environment(),
            description=config.description,
            retries=config.retries,
            session=config.get_session_config(),
            output_retries=config.output_retries,
            end_strategy=config.end_strategy,
            agent_config=config,
            input_provider=input_provider,
            output_type=resolved_output_type,  # type: ignore[arg-type]
            event_handlers=merged_handlers or None,
            agent_pool=agent_pool,
            tool_mode=config.tool_mode,
            knowledge=config.knowledge,
            toolsets=toolsets_list,
            hooks=config.hooks.get_agent_hooks() if config.hooks else None,
            tool_confirmation_mode=config.requires_tool_confirmation,
            builtin_tools=config.get_builtin_tools() or None,
            usage_limits=config.usage_limits,
            providers=config.model_providers,
            metadata=getattr(config, "metadata", None),
            capabilities=None,  # Built lazily in get_agentlet() from self.config.capabilities
        )

    async def __aenter__(self) -> Self:
        """Enter async context and set up MCP servers."""
        # Collect all coroutines that need to be run
        coros: list[Coroutine[Any, Any, Any]] = [
            super().__aenter__(),
            *self.conversation.get_initialization_tasks(),
        ]
        try:
            if self.parallel_init and coros:
                await asyncio.gather(*coros)
            else:
                for coro in coros:
                    await coro
            # Format system prompt once at startup (enables caching)
            self._formatted_system_prompt = await self.sys_prompts.format_system_prompt(self)
        except Exception as e:
            raise RuntimeError("Failed to initialize agent") from e
        else:
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit async context."""
        await super().__aexit__(exc_type, exc_val, exc_tb)

    @overload
    @classmethod
    def from_callback(
        cls,
        callback: Callable[..., Awaitable[TResult]],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, TResult]: ...

    @overload
    @classmethod
    def from_callback(
        cls,
        callback: Callable[..., TResult],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, TResult]: ...

    @classmethod
    def from_callback(
        cls,
        callback: ProcessorCallback[Any],
        *,
        name: str | None = None,
        **kwargs: Any,
    ) -> Agent[None, Any]:
        """Create an agent from a processing callback.

        Args:
            callback: Function to process messages. Can be:
                - sync or async
                - with or without context
                - must return str for pipeline compatibility
            name: Optional name for the agent
            kwargs: Additional arguments for agent
        """
        from agentpool.utils.inspection import get_fn_name
        from agentpool.utils.model_helpers import function_to_model
        from agentpool.utils.signatures import get_return_type

        name = name or get_fn_name(callback) or "processor"
        model = function_to_model(callback)
        output_type = get_return_type(callback)
        return Agent(model=model, name=name, output_type=output_type or str, **kwargs)

    @property
    def name(self) -> str:
        """Get agent name."""
        return self._name or "agentpool"

    @name.setter
    def name(self, value: str) -> None:
        """Set agent name."""
        self._name = value

    def _resolve_model_string(self, model: str) -> tuple[Model, ModelSettings | None]:
        """Resolve a model string, checking variants first.

        Args:
            model: Model identifier or variant name

        Returns:
            Tuple of (Model instance, ModelSettings or None)
            Settings are only returned for variants.
        """
        from agentpool.utils.model_helpers import infer_model

        # Check if it's a variant
        ctx = self.host_context
        if ctx and model in ctx.manifest.model_variants:
            config = ctx.manifest.model_variants[model]
            return config.get_model(), config.get_model_settings()
        # Regular model string - no settings
        return infer_model(model), None

    def to_structured[NewOutputDataT](
        self,
        output_type: type[NewOutputDataT],
    ) -> Agent[TDeps, NewOutputDataT]:
        """Convert this agent to a structured agent.

        Warning: This method mutates the agent in place and breaks caching.
        Changing output type modifies tool definitions sent to the API.

        Args:
            output_type: Type for structured responses.

        Returns:
            Self (same instance, not a copy)
        """
        self.log.debug("Setting result type", output_type=output_type)
        self._output_type = to_type(output_type)  # type: ignore[assignment]
        return self  # type: ignore

    @property
    def model_name(self) -> str | None:
        """Get the model name in a consistent format (provider:model_name)."""
        # Construct full model ID with provider prefix (e.g., "anthropic:claude-haiku-4-5")
        return f"{self._model.system}:{self._model.model_name}" if self._model else None

    def to_tool(
        self,
        *,
        name: str | None = None,
        description: str | None = None,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
        parent: Agent[Any, Any] | None = None,
        **_kwargs: Any,
    ) -> FunctionTool[OutputDataT]:
        """Create a tool from this agent.

        Args:
            name: Optional tool name override
            description: Optional tool description override
            reset_history_on_run: Clear agent's history before each run
            pass_message_history: Pass parent's message history to agent
            parent: Optional parent agent for history/context sharing
        """

        async def wrapped_tool(prompt: str) -> Any:
            if pass_message_history and not parent:
                raise ToolError("Parent agent required for message history sharing")

            if reset_history_on_run:
                await self.conversation.clear()

            history = None
            if pass_message_history and parent:
                history = parent.conversation.get_history()
                old = self.conversation.get_history()
                self.conversation.set_history(history)
            result = await self.run(prompt)
            if history:
                self.conversation.set_history(old)
            return result.data

        # Set the correct return annotation dynamically
        wrapped_tool.__annotations__ = {"prompt": str, "return": self._output_type or Any}
        normalized_name = self.name.replace("_", " ").title()
        docstring = f"Get expert answer from specialized agent: {normalized_name}"
        if desc := (description or self.description):
            docstring = f"{docstring}\n\n{desc}"
        tool_name = name or f"ask_{self.name}"
        wrapped_tool.__doc__ = docstring
        wrapped_tool.__name__ = tool_name
        return Tool.from_callable(wrapped_tool, source="agent")

    async def get_agentlet[AgentOutputType](  # noqa: PLR0915
        self,
        model: ModelType | None,
        output_type: type[AgentOutputType] | None,
        input_provider: InputProvider | None = None,
        run_ctx: AgentRunContext | None = None,
    ) -> PydanticAgent[AgentContext[TDeps], AgentOutputType]:
        """Create pydantic-ai agent from current state."""
        final_type = to_type(output_type) if output_type not in [None, str] else self._output_type
        actual_model = model or self._model
        if isinstance(actual_model, str):
            model_, _settings = self._resolve_model_string(actual_model)
        else:
            model_ = actual_model

        # Resolve history processors with caching
        history_processors = self._resolve_history_processors(_warn=False)

        # Yield to ensure interrupt() can run before iteration_task is created.
        # Without this, get_agentlet() may complete synchronously, causing
        # iteration_task to be created and cancelled before it starts — which
        # skips its finally block and leaves the event queue stalled.
        await asyncio.sleep(0)

        # Collect capabilities from all sources
        tool_capabilities: list[Any] = []
        direct_tools: list[Any] = []
        # 1. Tool providers — collect capabilities or fall back to direct tools
        for provider in self._all_capabilities:
            # M3: providers are now AbstractCapability instances directly.
            # They don't have get_capabilities() — they ARE capabilities.
            from pydantic_ai.capabilities import AbstractCapability as _AbstractCapability

            if isinstance(provider, _AbstractCapability):
                tool_capabilities.append(provider)
            else:
                # Provider not yet migrated to capability system — register
                # tools directly via the legacy `tools` parameter
                try:
                    provider_tools = await provider.get_tools()
                    for tool in provider_tools:
                        from agentpool.agents.native_agent.tool_wrapping import wrap_tool

                        context_for_tools = self.get_context(
                            input_provider=input_provider, run_ctx=run_ctx
                        )
                        wrapped = wrap_tool(tool, context_for_tools)
                        direct_tools.append(tool.to_pydantic_ai(function_override=wrapped))
                except Exception:
                    logger.exception(
                        "Failed to register tools from provider",
                        provider=type(provider).__name__,
                    )
        # 2. Hooks capability — always registered (unified tool interception)
        from agentpool.agents.native_agent.tool_intercept import ToolInterceptCapability

        hooks_capability = ToolInterceptCapability(hook_manager=self._hook_manager)
        tool_capabilities.append(hooks_capability)
        # 3. Deferred tool bridge: intercepts deferred tool calls before
        #    approval_bridge can resolve them. Block-strategy calls are
        #    excluded from returned results so they remain unresolved for
        #    CheckpointManager (Task 13).
        from agentpool.agents.native_agent.deferred_bridge import (
            create_deferred_bridge_capability,
        )

        # Collect tools with deferred=True for the deferred bridge
        deferred_tools: dict[str, str] = {}
        try:
            # Timeout to prevent hang when ACP MCP providers are still connecting
            all_tools = await asyncio.wait_for(self._get_all_tools(), timeout=5.0)
            for tool in all_tools:
                if tool.deferred:
                    deferred_tools[tool.name] = tool.deferred_strategy
        except TimeoutError:
            logger.warning("get_tools() timed out in get_agentlet(), using empty deferred_tools")
        except Exception:
            logger.exception("Failed to collect deferred tools — using empty dict")

        tool_capabilities.append(create_deferred_bridge_capability(deferred_tools))
        # 3b. Elicitation bridge: intercepts deferred elicitation calls (from
        #     MCP servers) before approval_bridge. Checkpoints the session,
        #     emits ElicitationDeferredEvent, and registers a future for
        #     later resolution when the user responds.
        from agentpool.agents.native_agent.checkpoint import CheckpointManager
        from agentpool.agents.native_agent.elicitation_bridge import (
            ElicitationFutureRegistry,
            create_elicitation_bridge_capability,
        )

        elicitation_registry = ElicitationFutureRegistry()
        if run_ctx is not None:
            run_ctx.elicitation_registry = elicitation_registry
            # Set configurable elicitation timeout from agent config.
            if self.config is not None:
                td = self.config.elicitation_timeout
                run_ctx.elicitation_timeout = td.total_seconds() if td is not None else None
        checkpoint_mgr: CheckpointManager | None = None
        if self.host_context is not None:
            checkpoint_mgr = CheckpointManager(
                storage_manager=self.host_context.storage,
            )
        if run_ctx is not None:
            run_ctx.checkpoint_manager = checkpoint_mgr
        tool_capabilities.append(
            create_elicitation_bridge_capability(
                registry=elicitation_registry,
                checkpoint_manager=checkpoint_mgr,
            )
        )
        # 4. Approval bridge: routes pydantic-ai deferred approvals to InputProvider
        from agentpool.agents.native_agent.approval_bridge import (
            create_approval_bridge_capability,
        )

        tool_capabilities.append(create_approval_bridge_capability(self, input_provider))
        # 4. MCP servers
        mcp_capabilities = await self.mcp.get_capabilities(
            session_id=run_ctx.session_id if run_ctx else None
        )
        tool_capabilities.extend(mcp_capabilities)
        # 5. Skill capabilities — from pool-scoped instances created during __aenter__.
        #    Each SkillManagerCap provides tools and MCP servers.
        pool = self._agent_pool
        if pool is not None:
            pool_capabilities = pool.skill_capabilities
            if pool_capabilities:
                from agentpool.capabilities.skill_manager_cap import SkillManagerCap

                tool_capabilities.extend(
                    cap for cap in pool_capabilities if isinstance(cap, SkillManagerCap)
                )

        # Collect pydantic-ai compatible instructions from SystemPrompts and providers
        all_instructions: list[Any] = []

        # Start with system prompts in pydantic-ai format
        system_instructions = await self.sys_prompts.to_pydantic_ai_instructions(self)
        all_instructions.extend(system_instructions)

        # Collect instructions from all capabilities
        for cap in self._all_capabilities:
            try:
                cap_instructions = cap.get_instructions()
                # Handle both sync (returns str|None|list) and async (returns coroutine)
                if hasattr(cap_instructions, "__await__"):
                    cap_instructions = await cap_instructions
                if cap_instructions is not None:
                    if isinstance(cap_instructions, list):
                        all_instructions.extend(cap_instructions)
                    else:
                        all_instructions.append(cap_instructions)
            except Exception as e:
                # Capability failure - log and continue
                logger.exception(
                    "Failed to get instructions from capability",
                    capability=type(cap).__name__,
                    error=str(e),
                )
                continue

        # 4. History processors
        if history_processors:
            tool_capabilities.extend(ProcessHistory(p) for p in history_processors)
        # 5. Builtin tools
        if self._builtin_tools:
            tool_capabilities.extend(NativeTool(t) for t in self._builtin_tools)

        # Merge extra capabilities from Agent.__init__() API
        if self._extra_capabilities:
            tool_capabilities.extend(self._extra_capabilities)

        # Merge user-provided capabilities from config
        if self.config and self.config.capabilities:
            from pydantic import BaseModel as _BaseModel

            from agentpool_config.capabilities import (
                GenericCapabilityConfig,
                build_capability,
            )

            for cap in self.config.capabilities:
                if cap is None:
                    continue
                if isinstance(cap, GenericCapabilityConfig):
                    tool_capabilities.append(cap.build())
                elif isinstance(cap, _BaseModel):
                    from typing import cast as _cast

                    # Typed built-in config (LoopDetectionCapabilityConfig, etc.)
                    tool_capabilities.append(build_capability(_cast(Any, cap)))
                else:
                    # Pre-instantiated AbstractCapability
                    tool_capabilities.append(cap)

        # Handle retries parameter: newer pydantic-ai uses dict form for output_retries
        if AgentRetries is not None and self._output_retries is not None:
            retries_param: int | dict[str, int] = {
                "tools": self._retries,
                "output": self._output_retries,
            }
        else:
            retries_param = self._retries

        # When HandleDeferredToolCalls capabilities are present, add
        # DeferredToolRequests to output_type so pydantic-ai can return
        # deferred tool requests as agent output (required for checkpoint/resume).
        from pydantic_ai.tools import DeferredToolRequests

        if tool_capabilities:
            final_output_type: Any = [final_type, DeferredToolRequests]
        else:
            final_output_type = final_type

        agent_kwargs: dict[str, Any] = {
            "name": self.name,
            "model": model_,
            "model_settings": self.model_settings,
            "instructions": all_instructions,
            "retries": retries_param,
            "end_strategy": self._end_strategy,
            "deps_type": AgentContext[TDeps],
            "output_type": final_output_type,
            "tools": list(direct_tools),
            "capabilities": tool_capabilities if tool_capabilities else None,
        }
        if AgentRetries is None and self._output_retries is not None:
            agent_kwargs["output_retries"] = self._output_retries

        return PydanticAgent(**agent_kwargs)

    async def _execute_node(self, *prompts: Any, **kwargs: Any) -> ChatMessage[Any]:
        """Execute agent as a pydantic-graph step node.

        Detects graph context via *_state* in kwargs (injected by
        :class:`~agentpool.messaging.graph_adapter.MessageNodeStep`) and
        delegates execution to :class:`~agentpool.agents.native_agent.turn.NativeTurn`,
        forwarding all events to the EventBus for the parent graph
        to drain.

        Args:
            *prompts: Input prompts passed from the graph.
            **kwargs: Must contain ``_state`` (an
                :class:`~agentpool.messaging.graph_adapter.AgentPoolState`).

        Returns:
            The final response ChatMessage.

        Raises:
            RuntimeError: If ``_state`` or required sub-keys are missing.
        """
        from agentpool.messaging.graph_adapter import AgentPoolState

        state = kwargs.get("_state")
        if not isinstance(state, AgentPoolState):
            raise TypeError(
                f"{self.__class__.__name__}._execute_node() requires _state in kwargs. "
                "Use MessageNodeStep to wrap this agent for graph execution."
            )

        kw = state.kwargs
        run_ctx = kw.get("run_ctx")
        if run_ctx is None:
            raise RuntimeError("run_ctx required in state.kwargs for graph execution")

        # Get or create EventBus for graph path — events flow through EventBus
        # instead of state.event_queue.
        from agentpool.orchestrator.core import EventBus

        event_bus = run_ctx.event_bus
        if event_bus is None:
            event_bus = EventBus()
            run_ctx.event_bus = event_bus

        turn = NativeTurn(
            agent=self,
            prompts=list(prompts),
            run_ctx=run_ctx,
            message_history=kw["message_history"],
            parent_id=kw.get("effective_parent_id"),
        )
        session_id = kw["session_id"]
        turn_failed = False
        error_msg = ""
        async for event in turn.execute():
            await event_bus.publish(session_id, event)
            if isinstance(event, RunErrorEvent):
                turn_failed = True
                error_msg = event.message
        if turn_failed:
            raise RuntimeError(f"NativeTurn execution failed: {error_msg}")
        result = turn.final_message

        state.result = result
        return result

    async def _stream_events(
        self,
        run_ctx: AgentRunContext,
        prompts: list[UserContent],
        *,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        effective_parent_id: str | None,
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
        **pydantic_ai_kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[OutputDataT]]:
        """Stream agent events in real-time using NativeTurn.

        Delegates to :class:`~agentpool.agents.native_agent.turn.NativeTurn`
        which drives the PydanticAI agent run loop with ``agent_run.next(node)``,
        yielding fine-grained streaming events including ``RunStartedEvent``,
        ``PartStartEvent``, ``ToolCallStartEvent``, and ``StreamCompleteEvent``.

        Events are published to the EventBus via ``NativeTurn.execute()``;
        this method yields nothing — the caller picks up the result via
        ``run_ctx.terminal_tool_result`` side channel.
        """
        assert session_id is not None  # Initialized by BaseAgent.run_stream()

        # Get or create EventBus — events go directly to EventBus.
        event_bus = run_ctx.event_bus
        if event_bus is None:
            from agentpool.orchestrator.core import EventBus

            event_bus = EventBus()
            run_ctx.event_bus = event_bus

        # Convert MessageHistory to list[ModelMessage] for pydantic-ai
        model_messages: list[ModelMessage] = []
        for chat_msg in message_history.get_history():
            model_messages.extend(chat_msg.messages)
        # Inject RetryPromptPart for any trailing unprocessed tool calls
        # (e.g. from a cancelled turn).
        from agentpool.orchestrator.run import inject_cancelled_tool_results

        model_messages = inject_cancelled_tool_results(model_messages)

        turn = NativeTurn(
            agent=self,
            prompts=list(prompts),  # type: ignore[arg-type]
            run_ctx=run_ctx,
            message_history=model_messages,
            parent_id=user_msg.message_id,
            **pydantic_ai_kwargs,
        )
        turn_failed = False
        error_msg = ""
        async for event in turn.execute():
            yield event
            if isinstance(event, RunErrorEvent):
                turn_failed = True
                error_msg = event.message
        if turn_failed:
            raise RuntimeError(f"NativeTurn execution failed: {error_msg}")
        result = turn.final_message

        # Store result for _run_stream_once() to pick up — avoids race
        # condition with EventBus consumer cancelling TaskGroup.
        run_ctx.terminal_tool_result = result

    def register_worker(
        self,
        worker: MessageNode[Any, Any],
        *,
        name: str | None = None,
        reset_history_on_run: bool = True,
        pass_message_history: bool = False,
    ) -> Tool:
        """Register another agent as a worker tool."""
        return self._worker_provider.register_worker(
            worker,
            name=name,
            reset_history_on_run=reset_history_on_run,
            pass_message_history=pass_message_history,
            parent=self if pass_message_history else None,
        )

    async def set_model(self, model: Model | str) -> None:
        """Set the model for this agent."""
        if isinstance(model, str):
            await self._set_mode(model, "model")
        else:
            # Direct Model instance assignment (no signal emission)
            self._model = model

    def create_turn(
        self,
        prompts: list[UserContent],
        run_ctx: AgentRunContext,
        message_history: list[ModelMessage],
        **pydantic_ai_kwargs: Any,
    ) -> Turn:
        """Create a NativeTurn for single-cycle execution.

        Args:
            prompts: Pre-converted prompt strings for this turn.
            run_ctx: Per-run isolated context.
            message_history: Incoming message history.
            **pydantic_ai_kwargs: Extra kwargs forwarded to
                ``NativeTurn.__init__()`` → ``agentlet.iter()`` (e.g.
                ``deferred_tool_results`` for crash recovery resume).

        Returns:
            A NativeTurn instance for single-cycle execution.
        """
        # Skip inject_cancelled_tool_results when resuming with
        # deferred_tool_results — pydantic-ai's _handle_deferred_tool_results
        # handles unprocessed tool calls directly. Adding RetryPromptPart
        # via inject_cancelled_tool_results would conflict with the
        # deferred_tool_results (same tool_call_id appears in both,
        # causing "already executed" or mismatch errors).
        if "deferred_tool_results" not in pydantic_ai_kwargs:
            from agentpool.orchestrator.run import inject_cancelled_tool_results

            message_history = inject_cancelled_tool_results(message_history)
        return NativeTurn(
            agent=self,
            prompts=prompts,  # type: ignore[arg-type]
            run_ctx=run_ctx,
            message_history=message_history,
            hooks=self.hooks,
            **pydantic_ai_kwargs,
        )

    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        """Cancel the iteration task running the LLM API call.

        Args:
            run_ctx: Optional per-run context (unused in native agent,
                kept for signature compatibility with the ACP subclass).
        """
        del run_ctx  # Unused in native agent; kept for ACP subclass signature
        iteration_task = self._iteration_task
        if iteration_task is not None and not iteration_task.done():
            iteration_task.cancel()

    @asynccontextmanager
    async def temporary_state[T](
        self,
        *,
        output_type: type[T] | None = None,
        tools: list[ToolType] | None = None,
        replace_tools: bool = False,
        history: list[AnyPromptType] | SessionQuery | None = None,
        replace_history: bool = False,
        pause_routing: bool = False,
        model: ModelType | None = None,
    ) -> AsyncIterator[Self | Agent[T]]:
        """Temporarily modify agent state.

        Args:
            output_type: Temporary output type to use
            tools: Temporary tools to make available
            replace_tools: Whether to replace existing tools
            history: Conversation history (prompts or query)
            replace_history: Whether to replace existing history
            pause_routing: Whether to pause message routing
            model: Temporary model override
        """
        old_model = self._model
        old_settings = self.model_settings
        if output_type:
            old_type = self._output_type
            self.to_structured(output_type)
        async with AsyncExitStack() as stack:
            if tools is not None:  # Tools
                await stack.enter_async_context(
                    self._temporary_tools(tools, exclusive=replace_tools)
                )

            if history is not None:  # History
                await stack.enter_async_context(
                    self.conversation.temporary_state(history, replace_history=replace_history)
                )

            if pause_routing:  # Routing
                await stack.enter_async_context(self.connections.paused_routing())

            if model is not None:  # Model
                if isinstance(model, str):
                    self._model, settings = self._resolve_model_string(model)
                    if settings:
                        self.model_settings = settings
                else:
                    self._model = model

            try:
                yield self
            finally:  # Restore model and settings
                if model is not None:
                    if old_model:
                        self._model = old_model
                    self.model_settings = old_settings
                if output_type:
                    self.to_structured(old_type)

    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models for this agent.

        Uses tokonomics model discovery to fetch models from configured providers.
        Defaults to models.dev if no providers specified.

        Returns:
            List of tokonomics ModelInfo, or None if discovery fails
        """
        from tokonomics.model_discovery import get_all_models

        delta = timedelta(days=200)
        return await get_all_models(providers=self._providers or ["models.dev"], max_age=delta)

    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for this agent."""
        from agentpool.agents.modes import ModeCategory as ModeCategoryRuntime, ModeInfo
        from agentpool.agents.native_agent.helpers import (
            get_model_category,
            get_permission_category,
        )

        categories: list[ModeCategory] = []
        # Use native ToolConfirmationMode value directly
        mode_category = get_permission_category(self.tool_confirmation_mode)
        categories.append(mode_category)
        # Check configured model_variants first (RFC-0034: configured-first)
        ctx = self.host_context
        if ctx and ctx.manifest.model_variants:
            # current_mode_id should be the actual model identifier to match option values
            current_model_id = self.model_name or ""
            model_modes = []
            for variant_name, config in ctx.manifest.model_variants.items():
                model = config.get_model()
                mode_id = f"{model.system}:{model.model_name}"
                model_modes.append(
                    ModeInfo(
                        id=mode_id,
                        name=variant_name,
                        category_id="model",
                    )
                )
            model_category = ModeCategoryRuntime(
                id="model",
                name="Model",
                available_modes=model_modes,
                current_mode_id=current_model_id,
                category="model",
            )
            categories.append(model_category)
        elif models := await self.get_available_models():
            current_model = self.model_name or (models[0].id if models else "")
            model_category = get_model_category(current_model, models)
            categories.append(model_category)
        return categories

    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        """Handle permissions and model mode switching."""
        if category_id == "mode":
            # Use native ToolConfirmationMode values directly
            if mode_id not in VALID_MODES:
                raise UnknownModeError(mode_id, VALID_MODES)
            self.tool_confirmation_mode = mode_id  # type: ignore[assignment]
            await self.update_state(config_id="mode", value_id=mode_id)

        elif category_id == "model":
            self.log.info("_set_mode called for model: %s", mode_id)
            # Resolve variant name from actual model identifier if needed
            variant_name = mode_id
            ctx = self.host_context
            if ctx and mode_id not in ctx.manifest.model_variants:
                # mode_id is an actual model identifier, find matching variant
                for vn, config in ctx.manifest.model_variants.items():
                    model = config.get_model()
                    resolved = f"{model.system}:{model.model_name}"
                    if resolved == mode_id:
                        variant_name = vn
                        self.log.info(
                            "Resolved model identifier %s to variant %s",
                            mode_id,
                            variant_name,
                        )
                        break
            # Validate model exists (check both tokonomics models and model_variants)
            is_valid = False
            if models := await self.get_available_models():
                valid_ids = [m.pydantic_ai_id for m in models]
                if mode_id in valid_ids:
                    is_valid = True
                    self.log.info("Model %s validated against tokonomics", mode_id)
            # Also check model_variants from manifest (by variant name or identifier)
            if not is_valid and ctx and variant_name in ctx.manifest.model_variants:
                is_valid = True
                self.log.info(
                    "Model %s validated against model_variants (variant: %s)",
                    mode_id,
                    variant_name,
                )
            if not is_valid:
                available = list(ctx.manifest.model_variants.keys()) if ctx else "N/A"
                self.log.warning(
                    "Model %s validation failed. Available variants: %s",
                    mode_id,
                    available,
                )
                raise UnknownModeError(mode_id, valid_ids if models else [])
            # Set the model using variant name (preserves model_settings)
            old_model = self._model
            self._model, settings = self._resolve_model_string(variant_name)
            if settings:
                self.model_settings = settings
            self.log.info("Model changed from %s to %s", old_model, self._model)
            await self.update_state(config_id="model", value_id=mode_id)
        else:
            raise UnknownCategoryError(category_id, ["mode", "model"])

    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List sessions from storage.

        For native agents, queries the pool's session store for all sessions
        associated with this agent. Fetches conversation titles from storage.

        Args:
            cwd: Filter sessions by working directory (optional).
                 Uses path normalization (resolve) for comparison, so
                 trailing slashes, symlinks, and relative paths are handled.
            limit: Maximum number of sessions to return (optional)

        Returns:
            List of SessionData objects
        """
        if not self.host_context:
            return []
        # Get sessions from session store
        try:
            # Get session IDs from store — do NOT filter by agent_name so that
            # sessions from previous default_agents remain visible in the TUI.
            # Filter by cwd at the SQL level when provided.
            session_ids = await self.host_context.storage.list_session_ids(cwd=cwd)
            # Batch load all sessions in one query instead of N+1
            sessions = await self.host_context.storage.load_sessions_batch(session_ids)
            # Python-level cwd filter as secondary safeguard for path normalization
            # (resolve handles trailing slashes, symlinks, relative paths)
            resolved_filter = Path(cwd).resolve() if cwd is not None else None
            if resolved_filter is not None:
                sessions = [
                    s for s in sessions if s.cwd and Path(s.cwd).resolve() == resolved_filter
                ]
            # Apply limit
            if limit is not None:
                sessions = sessions[:limit]
        except Exception:
            self.log.exception("Failed to list sessions")
            return []
        else:
            return sessions

    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session from storage.

        Loads session data and restores conversation history for this agent.
        Message history loads independently from session metadata (separate tables).

        Args:
            session_id: Unique identifier for the session to load

        Returns:
            SessionData if session was found and loaded, None otherwise.
        """
        if not self.host_context:
            return None

        session_data: SessionData | None = None
        try:
            session_data = await self.host_context.storage.load_session(session_id)
        except Exception:
            self.log.exception("Failed to load session data", session_id=session_id)

        try:
            messages = await self.host_context.storage.get_session_messages(session_id)
            self.conversation.chat_messages.clear()
            self.conversation.chat_messages.extend(messages)
            self.log.info(
                "Session loaded with conversation history",
                session_id=session_id,
                message_count=len(messages),
            )
        except RuntimeError as e:
            self.log.info(
                "Session loaded (no history support)", session_id=session_id, error=str(e)
            )
        except Exception:
            self.log.exception("Failed to load session messages", session_id=session_id)

        return session_data


if __name__ == "__main__":
    import logging

    logfire.configure()
    logfire.instrument_pydantic_ai()
    logging.basicConfig(handlers=[logfire.LogfireLoggingHandler()])
    sys_prompt = "Open browser with google,"
    _model = "openai:gpt-5-nano"

    async def handle_events(ctx: AgentContext[Any], event: RichAgentStreamEvent[Any]) -> None:
        print(f"[EVENT] {type(event).__name__}: {event}")

    agent = Agent(model=_model, tools=["webbrowser.open"], event_handlers=[handle_events])
    result = agent.run.sync(sys_prompt)
