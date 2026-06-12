"""Base class for all agent types."""

from __future__ import annotations

from abc import abstractmethod
import asyncio
from collections.abc import Callable
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
import inspect
import os

from pathlib import Path
import re
import warnings
from typing import TYPE_CHECKING, Any, ClassVar, Literal, assert_never, overload

from anyenv import MultiEventHandler, method_spawner
from anyenv.signals import Signal
import anyio
from upathtools.filesystems import IsolatedMemoryFileSystem

from agentpool.agents.context import AgentContext, AgentRunContext
from agentpool.agents.events import StreamCompleteEvent, resolve_event_handlers
from agentpool.agents.modes import ModeInfo
from agentpool.common_types import IndividualEventHandler
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, MessageHistory, MessageNode
from agentpool.prompts.convert import convert_prompts
from agentpool.tools.manager import ToolManager
from agentpool.utils.inspection import call_with_context
from agentpool.utils.time_utils import get_now


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from contextvars import Token
    from datetime import datetime

    from evented_config import EventConfig
    from exxec import ExecutionEnvironment
    from fsspec import AbstractFileSystem
    from pydantic_ai import UserContent
    from slashed import BaseCommand, CommandStore
    from tokonomics.model_discovery.model_info import ModelInfo
    from upathtools.filesystems import OverlayFileSystem

    from acp.schema import AvailableCommandsUpdate
    from agentpool.agents.events import ToastInfo
    from agentpool.agents.events import (
        CommandCompleteEvent,
        CommandOutputEvent,
        RichAgentStreamEvent,
        StreamWithCommandsEvent,
    )
    from agentpool.agents.modes import ConfigOptionChanged, ModeCategory, ModeCategoryId
    from agentpool.agents.native_agent import Agent
    from agentpool.common_types import (
        AgentName,
        AnyEventHandlerType,
        MCPServerStatus,
        ProcessorCallback,
        PromptCompatible,
        StrPath,
    )
    from agentpool.delegation import AgentPool, Team, TeamRun
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import ChatMessage
    from agentpool.sessions import SessionData
    from agentpool.talk.stats import MessageStats
    from agentpool.ui.base import InputProvider
    from agentpool_config.mcp_server import MCPServerConfig

    # Union type for state updates emitted via state_updated signal
    type StateUpdate = (
        ModeInfo | ModelInfo | AvailableCommandsUpdate | ConfigOptionChanged | ToastInfo
    )


# ContextVar for per-execution isolation of _current_run_ctx (RFC-0021 compliance)
_current_run_ctx_var: ContextVar[AgentRunContext | None] = ContextVar(
    "_current_run_ctx_var",
    default=None,
)

# ContextVar for SessionPool bypass flag (set by TurnRunner before agent calls)
_bypass_session_pool: ContextVar[bool] = ContextVar(
    "_bypass_session_pool",
    default=False,
)


logger = get_logger(__name__)

# Literal type for all agent types
type AgentTypeLiteral = Literal["native", "acp"]


_SLASH_PATTERN: re.Pattern[str] = re.compile(r"^/([\w-]+)(?:\s+(.*))?$")


def _parse_slash_command(command_text: str) -> tuple[str, str] | None:
    """Parse slash command into name and args.

    Args:
        command_text: Full command text

    Returns:
        Tuple of (cmd_name, args) or None if invalid
    """
    if match := _SLASH_PATTERN.match(command_text.strip()):
        cmd_name = match.group(1)
        args = match.group(2) or ""
        return cmd_name, args.strip()
    return None


def _is_slash_command(text: str) -> bool:
    """Check if text starts with a slash command."""
    return bool(_SLASH_PATTERN.match(text.strip()))


def _should_bypass_session_pool() -> bool:
    """Detect if the caller should bypass SessionPool delegation.

    SessionPool internal turns: When run()/run_stream() is called from within
    a TurnRunner turn (e.g., via message forwarding), delegating back to
    SessionPool would cause a deadlock on the per-session turn_lock. Detected
    via _bypass_session_pool ContextVar set by TurnRunner.

    Returns:
        True if SessionPool delegation should be bypassed, False otherwise.
    """
    # Case 1: ContextVar set by TurnRunner before agent calls
    if _bypass_session_pool.get():
        return True

    return False


class BaseAgent[TDeps = None, TResult = str](MessageNode[TDeps, TResult]):
    """Base class for Agent and ACPAgent.

    Provides shared infrastructure:
    - tools: ToolManager for tool registration and execution
    - conversation: MessageHistory for conversation state
    - event_handler: MultiEventHandler for event distribution
    - _event_queue: Queue for streaming events
    - _input_provider: Provider for user input/confirmations (deprecated: use SessionState.input_provider)
    - env: ExecutionEnvironment for running code/commands
    - context property: Returns NodeContext for the agent

    Signals:
        - run_failed: Emitted when agent execution fails with error details
    """

    # Abstract class variable - subclasses must define this
    AGENT_TYPE: ClassVar[AgentTypeLiteral]

    @dataclass(frozen=True)
    class RunFailedEvent:
        """Event emitted when agent execution fails."""

        agent_name: str
        """Name of the agent that failed."""
        message: str
        """Error description."""
        exception: Exception
        """The exception that caused the failure."""
        timestamp: Any = field(default_factory=get_now)  # datetime
        """When the failure occurred."""

    @dataclass(frozen=True)
    class AgentReset:
        """Emitted when agent is reset."""

        agent_name: AgentName
        timestamp: datetime = field(default_factory=get_now)

    @dataclass(frozen=True)
    class InterruptEvent:
        """Emitted when agent is interrupted."""

        agent_name: AgentName
        timestamp: datetime = field(default_factory=get_now)

    agent_reset = Signal[AgentReset]()
    # Signal emitted when agent execution fails
    run_failed: Signal[RunFailedEvent] = Signal()
    state_updated: Signal[StateUpdate] = Signal()
    # Signal emitted when agent is interrupted
    interrupted: Signal[InterruptEvent] = Signal()

    def __init__(
        self,
        *,
        name: str = "agent",
        deps_type: type[TDeps] | None = None,
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
        # New shared parameters
        env: ExecutionEnvironment | StrPath | None = None,
        input_provider: InputProvider | None = None,
        output_type: type[TResult] = str,  # type: ignore[assignment]
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        commands: Sequence[BaseCommand] | None = None,
        hooks: AgentHooks | None = None,
    ) -> None:
        """Initialize base agent with shared infrastructure.

        Args:
            name: Agent name
            deps_type: Type of dependencies to use
            description: Agent description
            display_name: Human-readable display name
            mcp_servers: MCP server configurations
            agent_pool: Agent pool for coordination
            enable_logging: Whether to enable database logging
            event_configs: Event trigger configurations
            env: Execution environment, or a path (str/PathLike) to use as cwd
                for a LocalExecutionEnvironment
            input_provider: Provider for user input and confirmations
            output_type: Output type for this agent
            event_handlers: Event handlers for this agent
            commands: Slash commands to register with this agent
            hooks: Agent hooks for intercepting agent behavior at run and tool events
        """
        from exxec import ExecutionEnvironment, LocalExecutionEnvironment
        from slashed import CommandStore

        from agentpool.agents.staged_content import StagedContent
        from agentpool_commands import get_commands

        super().__init__(
            name=name,
            description=description,
            display_name=display_name,
            mcp_servers=mcp_servers,
            agent_pool=agent_pool,
            enable_logging=enable_logging,
            event_configs=event_configs,
        )
        self._infinite = False
        self.deps_type = deps_type  # or type(None)
        self._background_task: asyncio.Task[ChatMessage[Any]] | None = None
        storage = agent_pool.storage if agent_pool else None
        self.conversation = MessageHistory(storage=storage)
        match env:
            case ExecutionEnvironment():
                self.env = env
            case str() | os.PathLike() | None:
                self.env = LocalExecutionEnvironment(cwd=str(env) if env is not None else None)
            case _ as unreachable:
                assert_never(unreachable)
        self._input_provider = input_provider
        if input_provider is not None:
            warnings.warn(
                "BaseAgent._input_provider is deprecated. Use SessionState.input_provider instead.",
                DeprecationWarning,
                stacklevel=2,
            )
        self._output_type: type[TResult] = output_type
        self.tools = ToolManager(_warn=False)
        handlers = resolve_event_handlers(event_handlers)
        self.event_handler: MultiEventHandler[IndividualEventHandler] = MultiEventHandler(handlers)
        self.hooks = hooks
        self._cancelled = False
        # _background_run_ctx is used only for the background task's internal state.
        # It is intentionally NOT used as a fallback in get_active_run_context().
        self._background_run_ctx: AgentRunContext | None = None
        # Deferred initialization support - subclasses set True in __aenter__,
        # override ensure_initialized() to do actual connection
        self._connect_pending: bool = False
        self._command_store = CommandStore(commands=[*get_commands(), *(commands or [])])
        # Initialize store (registers builtin help/exit commands)
        self._command_store._initialize_sync()
        # Internal filesystem for tool/session state (can get written to via AgentContext)
        self._internal_fs = IsolatedMemoryFileSystem()
        self.staged_content = StagedContent()
        self.metadata: dict[str, Any] = {}

    @property
    def _current_run_ctx(self) -> AgentRunContext | None:
        """Get current run context (using ContextVar for concurrency safety)."""
        return _current_run_ctx_var.get()

    def __repr__(self) -> str:
        typ = self.__class__.__name__
        desc = f", {self.description!r}" if self.description else ""
        return f"{typ}({self.name!r}, model={self.model_name!r}{desc})"

    def set_session_context(self, session_id: str, parent_session_id: str | None = None) -> None:
        """Set session context for the agent and its event manager.

        Args:
            session_id: The session ID to set
            parent_session_id: Optional parent session ID
        """
        self._events.session_id = session_id
        self._events.parent_session_id = parent_session_id

    async def __prompt__(self) -> str:
        typ = self.__class__.__name__
        model = self.model_name or "default"
        parts = [f"Agent: {self.name}", f"Type: {typ}", f"Model: {model}"]
        if self.description:
            parts.append(f"Description: {self.description}")
        parts.extend([await self.tools.__prompt__(), self.conversation.__prompt__()])
        return "\n".join(parts)

    @overload
    def __and__(  # if other doesnt define deps, we take the agents one
        self, other: ProcessorCallback[Any] | Team[TDeps] | Agent[TDeps, Any]
    ) -> Team[TDeps]: ...

    @overload
    def __and__(  # otherwise, we dont know and deps is Any
        self, other: ProcessorCallback[Any] | Team[Any] | Agent[Any, Any]
    ) -> Team[Any]: ...

    def __and__(self, other: MessageNode[Any, Any] | ProcessorCallback[Any]) -> Team[Any]:
        """Create sequential team using & operator.

        Example:
            group = analyzer & planner & executor  # Create group of 3
            group = analyzer & existing_group  # Add to existing group
        """
        from agentpool.agents.native_agent import Agent
        from agentpool.delegation.team import Team

        match other:
            case Team():
                return Team([self, *other.nodes])
            case Callable():
                agent_2 = Agent.from_callback(other, agent_pool=self.agent_pool)  # ty: ignore[no-matching-overload]
                return Team([self, agent_2])
            case MessageNode():
                return Team([self, other])
            case _:
                raise ValueError(f"Invalid agent type: {type(other)}")

    @overload
    def __or__(self, other: MessageNode[TDeps, Any]) -> TeamRun[TDeps, Any]: ...

    @overload
    def __or__[TOtherDeps](self, other: MessageNode[TOtherDeps, Any]) -> TeamRun[Any, Any]: ...

    @overload
    def __or__(self, other: ProcessorCallback[Any]) -> TeamRun[Any, Any]: ...

    def __or__(self, other: MessageNode[Any, Any] | ProcessorCallback[Any]) -> TeamRun[Any, Any]:
        # Create new execution with sequential mode (for piping)
        from agentpool import TeamRun
        from agentpool.agents.native_agent import Agent

        if callable(other):
            other = Agent.from_callback(other, agent_pool=self.agent_pool)

        return TeamRun([self, other])

    async def update_state(self, config_id: str, value_id: str) -> None:
        from agentpool.agents.modes import ConfigOptionChanged

        self.log.info("Config option changed", config_id=config_id, mode=value_id)
        change = ConfigOptionChanged(config_id=config_id, value_id=value_id)
        await self.state_updated.emit(change)

    @property
    def command_store(self) -> CommandStore:
        """Get the command store for slash commands."""
        return self._command_store

    @property
    def internal_fs(self) -> IsolatedMemoryFileSystem:
        """Get the internal filesystem for tool/session state.

        Tools can use this to store logs, history, temporary files, etc.
        Access via AgentContext.fs in tool implementations.

        Returns:
            In-memory filesystem scoped to this agent
        """
        return self._internal_fs

    @property
    def overlay_fs(self) -> OverlayFileSystem:
        """Get unified filesystem view combining agent storage and VFS resources.

        Provides a layered filesystem where:
        - Writes go to the agent's internal filesystem (upper layer)
        - Reads fall through to VFS resources if not found locally

        Returns:
            OverlayFileSystem combining internal_fs and pool's VFS registry
        """
        from upathtools.filesystems import OverlayFileSystem

        # Build layers: internal_fs on top, VFS resources below
        layers: list[AbstractFileSystem] = [self._internal_fs]
        if self.agent_pool is not None and not self.agent_pool.vfs_registry.is_empty:
            layers.append(self.agent_pool.vfs_registry.get_fs())
        return OverlayFileSystem(filesystems=layers)

    async def reset(self) -> None:
        """Reset agent state (conversation history and tool states)."""
        await self.conversation.clear()
        await self.tools.reset_states()
        event = self.AgentReset(agent_name=self.name)
        await self.agent_reset.emit(event)

    def get_context(
        self,
        data: Any = None,
        input_provider: InputProvider | None = None,
        tool_call_id: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_name: str | None = None,
        run_ctx: AgentRunContext | None = None,
    ) -> AgentContext[Any]:
        """Create a new context for this agent.

        Args:
            data: Optional custom data to attach to the context
            input_provider: Optional input provider override
            tool_call_id: Optional tool call ID
            tool_input: Optional tool input
            tool_name: Optional tool name
            run_ctx: Optional per-run context for accessing run-isolated state

        Returns:
            A new AgentContext instance
        """
        return AgentContext(
            node=self,
            pool=self.agent_pool,
            input_provider=input_provider or self._input_provider,
            data=data,
            model_name=self.model_name,
            tool_call_id=tool_call_id,
            tool_input=tool_input or {},
            tool_name=tool_name,
            run_ctx=run_ctx,
        )

    @property
    @abstractmethod
    def model_name(self) -> str | None:
        """Get the model name used by this agent."""
        ...

    @abstractmethod
    async def set_model(self, model: str) -> None:
        """Set the model for this agent.

        Args:
            model: New model identifier to use
        """
        ...

    async def run_iter(
        self,
        *prompt_groups: Sequence[PromptCompatible],
        store_history: bool = True,
        wait_for_connections: bool | None = None,
    ) -> AsyncIterator[ChatMessage[TResult]]:
        """Run agent sequentially on multiple prompt groups.

        Args:
            prompt_groups: Groups of prompts to process sequentially
            store_history: Whether to store in conversation history
            wait_for_connections: Whether to wait for connected agents

        Yields:
            Response messages in sequence

        Example:
            questions = [
                ["What is your name?"],
                ["How old are you?", image1],
                ["Describe this image", image2],
            ]
            async for response in agent.run_iter(*questions):
                print(response.content)
        """
        for prompts in prompt_groups:
            response = await self.run(
                *prompts,
                store_history=store_history,
                wait_for_connections=wait_for_connections,
            )
            yield response  # pyright: ignore

    async def run_in_background(
        self,
        *prompt: PromptCompatible,
        max_count: int | None = None,
        interval: float = 1.0,
        **kwargs: Any,
    ) -> asyncio.Task[ChatMessage[TResult] | None]:
        """Run agent continuously in background with prompt or dynamic prompt function.

        Args:
            prompt: Static prompt or function that generates prompts
            max_count: Maximum number of runs (None = infinite)
            interval: Seconds between runs
            **kwargs: Arguments passed to run()
        """
        self._infinite = max_count is None

        # Create run context for background task
        self._background_run_ctx = AgentRunContext()

        async def _continuous() -> ChatMessage[Any]:
            count = 0
            self.log.debug("Starting continuous run", max_count=max_count, interval=interval)
            latest = None
            # _background_run_ctx is always set before starting the task
            run_ctx = self._background_run_ctx
            assert run_ctx is not None
            while (max_count is None or count < max_count) and not run_ctx.cancelled:
                try:
                    agent_ctx = self.get_context(run_ctx=run_ctx)
                    current_prompts = [
                        call_with_context(p, agent_ctx, **kwargs) if callable(p) else p
                        for p in prompt
                    ]
                    self.log.debug("Generated prompt", iteration=count)
                    latest = await self.run(current_prompts, **kwargs)
                    self.log.debug("Run continuous result", iteration=count)

                    count += 1
                    await anyio.sleep(interval)
                except asyncio.CancelledError:
                    self.log.debug("Continuous run cancelled")
                    break
                except Exception:
                    # Check if we were cancelled (may surface as other exceptions)
                    if run_ctx.cancelled:
                        self.log.debug("Continuous run cancelled via flag")
                        break
                    count += 1
                    self.log.exception("Background run failed")
                    await anyio.sleep(interval)
            self.log.debug("Continuous run completed", iterations=count)
            return latest  # type: ignore[return-value]

        await self.stop()  # Cancel any existing background task
        self._cancelled = False  # Reset cancellation flag for backward compat
        self._background_run_ctx.cancelled = False
        task = asyncio.create_task(_continuous(), name=f"background_{self.name}")
        self.log.debug("Started background task", task_name=task.get_name())
        self._background_task = task
        return task

    async def stop(self) -> None:
        """Stop continuous execution if running."""
        self._cancelled = True  # Signal cancellation via flag for backward compat
        if self._background_run_ctx:
            self._background_run_ctx.cancelled = True
        if self._background_task and not self._background_task.done():
            self._background_task.cancel()
            with suppress(asyncio.CancelledError):  # Expected when we cancel the task
                await self._background_task
            self._background_task = None

    def is_busy(self) -> bool:
        """Check if agent is currently processing tasks."""
        return bool(self.task_manager._pending_tasks or self._background_task)

    async def wait(self) -> ChatMessage[TResult]:
        """Wait for background execution to complete."""
        if not self._background_task:
            raise RuntimeError("No background task running")
        if self._infinite:
            raise RuntimeError("Cannot wait on infinite execution")
        try:
            return await self._background_task
        finally:
            self._background_task = None

    def _get_session_run_ctx(self, session_id: str | None = None) -> AgentRunContext | None:
        """Get active run context from SessionPool for cross-task access.

        Args:
            session_id: Optional session ID to look up. Falls back to
                self._events.session_id if not provided.

        Returns:
            The session's active run context, or None if not found.
        """
        if self.agent_pool is not None:
            session_pool = self.agent_pool.session_pool
            if session_pool is None:
                return None
            effective_session_id = session_id or getattr(self._events, "session_id", None)
            if effective_session_id is not None:
                session = session_pool.sessions.get_session(effective_session_id)
                if session is not None and session.current_run_id is not None:
                    run_handle = session_pool.get_run(session.current_run_id)
                    if run_handle is not None:
                        run_ctx = run_handle.run_ctx
                        if run_ctx is not None and not run_ctx.completed:
                            return run_ctx
        return None

    def get_active_run_context(self, session_id: str | None = None) -> AgentRunContext | None:
        """Get the currently active run context.

        Public API for external callers (e.g., SessionPool) to check if a
        turn is active and access the run context without relying on
        private attributes.

        Uses three-level fallback:
        1. ContextVar (_current_run_ctx_var) for the current task
        2. SessionPool lookup when pooled (via session_id or agent_pool)
        3. _background_run_ctx for background task state

        Args:
            session_id: Optional session ID for SessionPool lookup.
                When provided, used for the SessionPool fallback instead of
                instance state.

        Returns:
            The active run context, or None if no turn is running.
        """
        # Level 1: ContextVar for the current task (highest precedence)
        run_ctx = _current_run_ctx_var.get()
        if run_ctx is not None and not run_ctx.completed:
            return run_ctx

        # Level 2: SessionPool lookup when pooled and session has active run
        if self.agent_pool is not None:
            session_pool = self.agent_pool.session_pool
            if session_pool is not None:
                effective_session_id = session_id or getattr(self._events, "session_id", None)
                if effective_session_id is not None:
                    session = session_pool.sessions.get_session(effective_session_id)
                    if session is not None and session.current_run_id is not None:
                        run_handle = session_pool.get_run(session.current_run_id)
                        if run_handle is not None and not run_handle.run_ctx.completed:
                            return run_handle.run_ctx

        # Level 3: Background run context (lowest precedence)
        if self._background_run_ctx is not None and not self._background_run_ctx.completed:
            return self._background_run_ctx

        return None

    def is_turn_active(self) -> bool:
        """Check if a turn is currently running.

        Returns:
            True if there is an active run context, False otherwise.
        """
        return self.get_active_run_context() is not None

    def queue_prompt(self, *prompts: PromptCompatible, session_id: str | None = None) -> None:
        """Queue a prompt to be processed after the current run completes.

        When called during an active run_stream, the queued prompt will be
        processed in a continuation loop without exiting the stream. This
        allows tools or external code to schedule follow-up work.

        For native agents when pooled, delegates to SessionPool.receive_request()
        with priority="when_idle". The active-run path still queues directly into
        the injection_manager to avoid race conditions with tool augmentation.

        Args:
            *prompts: Prompts to queue (same format as run/run_stream)
            session_id: Optional session ID for SessionPool fallback lookup.

        Example:
            # In a tool implementation:
            async def my_tool(ctx: AgentContext) -> str:
                ctx.agent.queue_prompt("Now analyze the results")
                return "Initial work done"
        """
        run_ctx = self.get_active_run_context(session_id=session_id)

        # Native agents when pooled: route through SessionPool.receive_request.
        # For active runs, also queue directly for synchronous tool augmentation.
        if self.AGENT_TYPE == "native":
            if self.agent_pool is not None and self.agent_pool.session_pool is not None:
                effective_session_id = session_id or (
                    run_ctx.session_id if run_ctx else self._events.session_id
                )
                if effective_session_id is not None:
                    session_pool = self.agent_pool.session_pool
                    if run_ctx is not None and not run_ctx.completed:
                        # Active run: queue directly for immediate availability
                        if run_ctx.injection_manager is not None:
                            run_ctx.injection_manager.queue(*prompts)
                        # Also schedule through SessionPool for consistency
                        self.task_manager.fire_and_forget(
                            session_pool.receive_request(
                                effective_session_id, prompts, priority="when_idle"
                            )
                        )
                        return
                    # No active run: delegate to SessionPool for auto-resume
                    self.task_manager.fire_and_forget(
                        session_pool.receive_request(
                            effective_session_id, prompts, priority="when_idle"
                        )
                    )
                    return
            # Standalone native agents: fall through to legacy path

        # Legacy path for non-native agents and standalone native agents
        if run_ctx is not None and run_ctx.injection_manager is not None:
            run_ctx.injection_manager.queue(*prompts)

    def inject_prompt(self, message: str, session_id: str | None = None) -> None:
        """Inject a message into the conversation mid-run.

        The message will be injected after the next tool completes (if the
        agent supports tool hooks). If no tool executes before the run
        iteration completes, the message is automatically queued for the
        next iteration.

        For native agents when pooled, delegates to SessionPool.receive_request()
        with priority="asap". The active-run path still injects directly into
        the injection_manager to avoid race conditions with tool augmentation
        (after_tool_execute -> consume()).

        If no active run context exists (e.g., after end_turn), delegates to
        SessionPool.receive_request() to trigger auto-resume.

        Args:
            message: Message to inject
            session_id: Optional session ID. Falls back to the active run
                context's session_id if available.

        Example:
            # In a tool implementation:
            async def my_tool(ctx: AgentContext) -> str:
                ctx.agent.inject_prompt("Also check the test coverage")
                return "Changes made"
        """
        run_ctx = self.get_active_run_context(session_id=session_id)

        # Native agents when pooled: route through SessionPool.receive_request.
        # For active runs, also inject directly for synchronous tool augmentation.
        if self.AGENT_TYPE == "native":
            if self.agent_pool is not None and self.agent_pool.session_pool is not None:
                effective_session_id = session_id or (
                    run_ctx.session_id if run_ctx else self._events.session_id
                )
                if effective_session_id is not None:
                    session_pool = self.agent_pool.session_pool
                    if run_ctx is not None and not run_ctx.completed:
                        # Active run: inject directly for immediate availability
                        # (tool augmentation needs synchronous access)
                        if run_ctx.injection_manager is not None:
                            run_ctx.injection_manager.inject(message)
                        # Also schedule through SessionPool for consistency
                        self.task_manager.fire_and_forget(
                            session_pool.receive_request(
                                effective_session_id, message, priority="asap"
                            )
                        )
                        return
                    # No active run: delegate to SessionPool for auto-resume
                    self.task_manager.fire_and_forget(
                        session_pool.receive_request(effective_session_id, message, priority="asap")
                    )
                    return
                # FALLBACK: effective_session_id is None but session_pool exists.
                # This happens when BackgroundTaskProvider calls inject_prompt
                # after the lead agent's run has ended (no active run context
                # and agent's _events.session_id is None for shared agents).
                # Try to find the most recently active session for this agent.
                session_pool = self.agent_pool.session_pool
                sessions = session_pool.sessions.find_sessions_by_agent_name(self.name)
                if sessions:
                    most_recent = max(sessions, key=lambda s: s.last_active_at)
                    self.task_manager.fire_and_forget(
                        session_pool.receive_request(
                            most_recent.session_id, message, priority="asap"
                        )
                    )
                    return
            # Standalone native agents: fall through to legacy path

        # Legacy path for non-native agents and standalone native agents
        # CRITICAL: Check run_ctx.completed to avoid injecting into a turn that
        # has already finished (e.g., after end_turn).  If the turn is complete,
        # the message would be stuck in injection_manager.pending forever because
        # flush_pending_to_queue() has already been called and won't be called
        # again.  In that case, delegate to SessionPool for auto-resume.
        if run_ctx is not None and not run_ctx.completed and run_ctx.injection_manager is not None:
            run_ctx.injection_manager.inject(message)
            return

        # No active run context — delegate to SessionPool for auto-resume
        effective_session_id = session_id or (run_ctx.session_id if run_ctx else None)
        if self.agent_pool is not None and effective_session_id is not None:
            _session_pool = self.agent_pool.session_pool
            if _session_pool is None:
                return
            # Fire-and-forget: delegate to SessionPool for auto-resume.
            # Use task_manager to prevent GC of the task mid-execution.
            self.task_manager.fire_and_forget(
                _session_pool.inject_prompt(effective_session_id, message)
            )
            return

        # FALLBACK for shared agents: effective_session_id is None but session_pool exists.
        # This handles the case where BackgroundTaskProvider calls inject_prompt
        # after background task completion when the agent has no fixed session_id.
        if self.agent_pool is not None:
            _session_pool = self.agent_pool.session_pool
            if _session_pool is not None:
                sessions = _session_pool.sessions.find_sessions_by_agent_name(self.name)
                if sessions:
                    most_recent = max(sessions, key=lambda s: s.last_active_at)
                    self.task_manager.fire_and_forget(
                        _session_pool.receive_request(
                            most_recent.session_id, message, priority="asap"
                        )
                    )
                    return

        # No pool or session_id available — log warning
        self.log.warning(
            "inject_prompt called but no active run context or session pool available",
            agent_name=self.name,
        )

    def has_queued_prompts(self, session_id: str | None = None) -> bool:
        """Check if there are queued prompts waiting to be processed.

        Args:
            session_id: Optional session ID for SessionPool fallback lookup.
        """
        run_ctx = self.get_active_run_context(session_id=session_id)
        if run_ctx is not None and run_ctx.injection_manager is not None:
            return run_ctx.injection_manager.has_queued()
        return False

    def has_pending_injections(self, session_id: str | None = None) -> bool:
        """Check if there are pending injections.

        Args:
            session_id: Optional session ID for SessionPool fallback lookup.
        """
        run_ctx = self.get_active_run_context(session_id=session_id)
        if run_ctx is not None and run_ctx.injection_manager is not None:
            return run_ctx.injection_manager.has_pending()
        return False

    def clear_queued_prompts(self, session_id: str | None = None) -> None:
        """Clear all queued prompts and pending injections.

        Args:
            session_id: Optional session ID for SessionPool fallback lookup.
        """
        run_ctx = self.get_active_run_context(session_id=session_id)
        if run_ctx is not None and run_ctx.injection_manager is not None:
            run_ctx.injection_manager.clear()

    @method_spawner
    async def run_stream(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        depth: int = 0,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Run agent with streaming output.

        This method delegates to _run_stream_once() for actual processing.
        If prompts are queued via queue_prompt() during execution, they will be
        processed in sequence without exiting the stream.

        Args:
            *prompts: Input prompts (various formats supported)
            store_history: Whether to store in history
            message_id: Optional message ID
            session_id: Optional conversation ID
            parent_session_id: Optional parent conversation ID
            parent_id: Optional parent message ID
            message_history: Optional message history
            input_provider: Optional input provider
            wait_for_connections: Whether to wait for connected agents
            deps: Optional dependencies
            event_handlers: Optional event handlers
            depth: Current delegation depth (0 = top-level run)

        Yields:
            Stream events during execution
        """
        # When SessionPool is available and the caller is not bypassing it,
        # delegate to SessionPool for turn management and event routing.
        # AG-UI bypass is permanent — see docs/audit/agui-bypass-audit.md.
        if (
            not _should_bypass_session_pool()
            and self.agent_pool is not None
            and self.agent_pool.session_pool is not None
        ):
            from agentpool.utils.identifiers import generate_session_id

            effective_session_id = session_id or generate_session_id()
            session_pool = self.agent_pool.session_pool

            # If the session already exists but belongs to a different agent,
            # fall through to direct execution so THIS agent runs.
            existing_session = session_pool.sessions.get_session(effective_session_id)
            if existing_session is None or existing_session.agent_name == self.name:
                # Ensure session exists in SessionPool
                if existing_session is None:
                    await session_pool.create_session(
                        effective_session_id,
                        agent_name=self.name,
                        parent_session_id=parent_session_id,
                    )

                final_message: ChatMessage[TResult] | None = None
                async for event in session_pool.run_stream(
                    effective_session_id,
                    *prompts,  # type: ignore[arg-type]
                    input_provider=input_provider,
                ):
                    yield event
                    if isinstance(event, StreamCompleteEvent):
                        final_message = event.message
                if final_message is not None:
                    await self.message_sent.emit(final_message)
                    session = session_pool.sessions.get_session(effective_session_id)
                    if session is not None and getattr(session, "is_per_session_agent", False):
                        await self.connections.route_message(
                            final_message, wait=wait_for_connections
                        )
                return

        # Direct execution path for AG-UI bypass and standalone mode.
        # AG-UI requires direct agent access for protocol-specific event
        # transformation (AGUIEventStream). Standalone agents run without
        # an AgentPool / SessionPool.
        async for event in self._run_stream_direct(
            *prompts,
            store_history=store_history,
            message_id=message_id,
            session_id=session_id,
            parent_session_id=parent_session_id,
            parent_id=parent_id,
            message_history=message_history,
            input_provider=input_provider,
            wait_for_connections=wait_for_connections,
            deps=deps,
            event_handlers=event_handlers,
            depth=depth,
        ):
            yield event

    async def _run_stream_direct(
        self,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        depth: int = 0,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Direct streaming execution bypassing SessionPool delegation.

        This path is used for:
        1. AG-UI protocol streaming (permanent bypass — see docs/audit/agui-bypass-audit.md)
        2. Standalone agents without an AgentPool / SessionPool
        3. TurnRunner internal turns (deadlock prevention via ContextVar)

        Args:
            *prompts: Input prompts (various formats supported)
            store_history: Whether to store in history
            message_id: Optional message ID
            session_id: Optional conversation ID
            parent_session_id: Optional parent conversation ID
            parent_id: Optional parent message ID
            message_history: Optional message history
            input_provider: Optional input provider
            wait_for_connections: Whether to wait for connected agents
            deps: Optional dependencies
            event_handlers: Optional event handlers
            depth: Current delegation depth (0 = top-level run)

        Yields:
            Stream events during execution
        """
        from agentpool.utils.identifiers import generate_session_id

        # Initialize session_id once for the entire run (including queued prompts)
        effective_session_id = session_id or generate_session_id()

        user_prompts = [str(p) for p in prompts if isinstance(p, str)]
        initial_prompt = user_prompts[-1] if user_prompts else None

        await self.log_session(
            session_id=effective_session_id,
            initial_prompt=initial_prompt,
            model=self.model_name,
            parent_session_id=parent_session_id,
        )

        # Create per-run context for state isolation
        run_ctx = AgentRunContext(deps=deps, depth=depth)
        # Reset cancellation state and track current task
        run_ctx.cancelled = False
        self._cancelled = False
        run_ctx.current_task = asyncio.current_task()

        # RFC-0021: reset only via the token from set(); never set(None) (breaks nesting).
        # token is initialized so finally always has a bound name; reset only if set() succeeded.
        token: Token[AgentRunContext | None] | None = None
        try:
            token = _current_run_ctx_var.set(run_ctx)
            # Native agents use PydanticAI's PendingMessageDrainCapability for
            # enqueue/asap/when_idle handling.  Non-native agents still need
            # the manual follow-up loop.
            if self.AGENT_TYPE == "native":
                # Process initial prompts directly, skip manual follow-up loop
                async for event in self._run_stream_once(
                    run_ctx,
                    *prompts,
                    store_history=store_history,
                    message_id=message_id,
                    session_id=effective_session_id,
                    parent_session_id=parent_session_id,
                    parent_id=parent_id,
                    message_history=message_history,
                    input_provider=input_provider,
                    wait_for_connections=wait_for_connections,
                    deps=deps,
                    event_handlers=event_handlers,
                ):
                    yield event
            else:
                # Queue the initial prompts (skip if no injection_manager)
                if run_ctx.injection_manager is not None:
                    run_ctx.injection_manager.insert_queued(prompts)
                # Process queued prompts until queue is empty
                while (
                    run_ctx.injection_manager is not None
                    and run_ctx.injection_manager.has_queued()
                    and not run_ctx.cancelled
                ):
                    current_prompts = run_ctx.injection_manager.pop_queued()
                    if current_prompts is None:
                        break
                    async for event in self._run_stream_once(
                        run_ctx,
                        *current_prompts,
                        store_history=store_history,
                        message_id=message_id,
                        session_id=effective_session_id,
                        parent_session_id=parent_session_id,
                        parent_id=parent_id,
                        message_history=message_history,
                        input_provider=input_provider,
                        wait_for_connections=wait_for_connections,
                        deps=deps,
                        event_handlers=event_handlers,
                    ):
                        yield event

                    # After each iteration, flush unconsumed injections to queue
                    if run_ctx.injection_manager is not None:
                        run_ctx.injection_manager.flush_pending_to_queue()
        finally:
            if token is not None:
                # Suppress ValueError when token was created in a different async
                # context (happens during GeneratorExit / generator cleanup).
                # The ContextVar reverts to its default (None) in the original
                # context when the task exits, so ignoring the reset is safe.
                with suppress(ValueError):
                    _current_run_ctx_var.reset(token)
            if run_ctx.injection_manager is not None:
                run_ctx.injection_manager.clear()

    async def _run_stream_once(
        self,
        run_ctx: AgentRunContext,
        *prompts: PromptCompatible,
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        input_provider: InputProvider | None = None,
        wait_for_connections: bool | None = None,
        deps: TDeps | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Process a single prompt group with streaming output.

        This is the internal implementation called by run_stream().
        Session initialization is handled by the caller.

        Args:
            run_ctx: Per-run context for state isolation
            *prompts: Input prompts (various formats supported)
            store_history: Whether to store in history
            message_id: Optional message ID
            session_id: Optional conversation ID
            parent_session_id: Optional parent conversation ID
            parent_id: Optional parent message ID
            message_history: Optional message history
            input_provider: Optional input provider
            wait_for_connections: Whether to wait for connected agents
            deps: Optional dependencies
            event_handlers: Optional event handlers

        Yields:
            Stream events during execution
        """
        from anyenv import MultiEventHandler

        from agentpool.agents.events import resolve_event_handlers
        from agentpool.messaging import ChatMessage

        # Convert prompts to standard UserContent format
        converted_prompts = await convert_prompts(prompts)
        # Prepend any staged content
        if staged := await self.staged_content.consume_as_text():
            converted_prompts = [*converted_prompts, staged]
        # Get message history (either passed or agent's own)
        conversation = message_history if message_history is not None else self.conversation
        # Determine effective parent_id (from param or last message in history)
        pending_parts = conversation.get_pending_parts()
        effective_parent_id = parent_id if parent_id else conversation.get_last_message_id()

        user_msg = ChatMessage.user_prompt(
            message=converted_prompts,
            parent_id=effective_parent_id,
            session_id=session_id,
        )

        # Resolve event handlers
        if event_handlers is not None:
            resolved_handler = MultiEventHandler[IndividualEventHandler](
                resolve_event_handlers(event_handlers)
            )
        else:
            resolved_handler = self.event_handler

        # Stream events from implementation
        final_message = None
        conversation = message_history if message_history is not None else self.conversation

        # run_ctx is created by run_stream() (or future callers); do not replace it here or
        # per-run isolation (event queue, injections, cancellation) breaks.

        await self.message_received.emit(user_msg)

        # Save user message to conversation history immediately
        # This ensures user messages are preserved even if the run is cancelled
        # or no assistant response is generated (e.g., elicitation cancelled)
        if store_history:
            conversation.add_chat_messages([user_msg])

        try:
            # Execute pre-run hooks
            if self.hooks:
                pre_run_result = await self.hooks.run_pre_run_hooks(
                    agent_name=self.name,
                    prompt=user_msg.content
                    if isinstance(user_msg.content, str)
                    else str(user_msg.content),
                    session_id=session_id,
                )
                if pre_run_result.get("decision") == "deny":
                    reason = pre_run_result.get("reason", "Blocked by pre-run hook")
                    raise RuntimeError(f"Run blocked: {reason}")  # noqa: TRY301

            context = self.get_context(input_provider=input_provider, run_ctx=run_ctx)
            async for event in self._stream_events(
                run_ctx,
                [*pending_parts, *converted_prompts],
                user_msg=user_msg,
                effective_parent_id=effective_parent_id,
                store_history=store_history,
                message_id=message_id,
                session_id=session_id,
                parent_session_id=parent_session_id,
                parent_id=parent_id,
                message_history=conversation,
                input_provider=input_provider,
                wait_for_connections=wait_for_connections,
                deps=deps,
            ):
                await resolved_handler(context, event)
                yield event
                # Capture final message from StreamCompleteEvent
                if isinstance(event, StreamCompleteEvent):
                    final_message = event.message
        except Exception as e:
            self.log.exception("Agent stream failed")
            failed_event = BaseAgent.RunFailedEvent(
                agent_name=self.name,
                message="Agent stream failed",
                exception=e,
            )
            await self.run_failed.emit(failed_event)
            raise

        # Post-processing after stream completes
        if final_message is not None:
            # Execute post-run hooks
            if self.hooks:
                prompt_str = (
                    user_msg.content if isinstance(user_msg.content, str) else str(user_msg.content)
                )
                await self.hooks.run_post_run_hooks(
                    agent_name=self.name,
                    prompt=prompt_str,
                    result=final_message.content,
                    session_id=session_id,
                )

            # Emit signal (always - for event handlers)
            # Skip when running through session pool; run()/run_stream() will emit
            if not _should_bypass_session_pool():
                await self.message_sent.emit(final_message)
            # Route to connected agents (always - they decide what to do with it)
            await self.connections.route_message(final_message, wait=wait_for_connections)
            # Conditional persistence based on store_history
            # TODO: Verify store_history semantics across all use cases:
            #   - Should subagent tool calls set store_history=False?
            #   - Should forked/ephemeral runs always skip persistence?
            #   - Should signals still fire when store_history=False?
            #   Current behavior: store_history controls both DB logging AND conversation context
            if store_history:
                # Log to persistent storage and add to conversation context
                # Note: user_msg was already added at the start of the run
                # Use extend_last=True to include both user_msg and final_message in _last_messages
                await self.log_message(final_message)
                conversation.add_chat_messages([final_message], extend_last=True)

    async def _execute_slash_command_streaming(
        self, command_text: str
    ) -> AsyncIterator[CommandOutputEvent | CommandCompleteEvent]:
        """Execute a single slash command and yield events as they happen.

        Args:
            command_text: Full command text including slash

        Yields:
            Command output and completion events
        """
        from slashed.events import (
            CommandExecutedEvent,
            CommandOutputEvent as SlashedCommandOutputEvent,
        )

        from agentpool.agents.events import CommandCompleteEvent, CommandOutputEvent

        parsed = _parse_slash_command(command_text)
        if not parsed:
            self.log.warning("Invalid slash command", command=command_text)
            yield CommandCompleteEvent(command="unknown", success=False)
            return

        cmd_name, args = parsed
        # Set up event queue for this command execution
        event_queue: asyncio.Queue[Any] = asyncio.Queue()
        # Temporarily set event handler on command store
        old_handler = self._command_store.event_handler
        self._command_store.event_handler = event_queue.put
        # Use active run context (ContextVar + SessionPool fallback)
        run_ctx = self.get_active_run_context()
        cmd_ctx = self._command_store.create_context(data=self.get_context(run_ctx=run_ctx))
        command_str = f"{cmd_name} {args}".strip()
        try:
            execute_task = asyncio.create_task(
                self._command_store.execute_command(command_str, cmd_ctx)
            )
            success = True
            # Yield events from queue as command runs
            while not execute_task.done():
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=0.1)
                    match event:
                        case SlashedCommandOutputEvent(output=output):
                            yield CommandOutputEvent(command=cmd_name, output=output)
                        case CommandExecutedEvent(success=False, error=error) if error:
                            yield CommandOutputEvent(
                                command=cmd_name, output=f"Command error: {error}"
                            )
                            success = False
                except TimeoutError:
                    continue

            # Ensure command task completes and handle any remaining events
            try:
                await execute_task
            except Exception as e:
                self.log.exception("Command execution failed", command=cmd_name)
                success = False
                yield CommandOutputEvent(command=cmd_name, output=f"Command error: {e}")

            # Drain any remaining events from queue
            while not event_queue.empty():
                try:
                    event = event_queue.get_nowait()
                    if isinstance(event, SlashedCommandOutputEvent):
                        yield CommandOutputEvent(command=cmd_name, output=event.output)
                except asyncio.QueueEmpty:
                    break

            yield CommandCompleteEvent(command=cmd_name, success=success)

        finally:
            self._command_store.event_handler = old_handler

    async def run_stream_with_commands(
        self,
        *prompts: PromptCompatible,
        **kwargs: Any,
    ) -> AsyncIterator[StreamWithCommandsEvent[TResult]]:
        """Run agent with slash command support.

        Separates slash commands from regular prompts, executes commands first,
        then processes remaining content through the agent.

        Args:
            *prompts: Input prompts (may include slash commands)
            **kwargs: Additional arguments passed to run_stream

        Yields:
            Command events from slash command execution, then stream events from agent
        """
        # Separate slash commands from regular content
        commands: list[str] = []
        regular_prompts: list[Any] = []

        for prompt in prompts:
            if isinstance(prompt, str) and _is_slash_command(prompt):
                self.log.debug("Found slash command", command=prompt)
                commands.append(prompt.strip())
            else:
                regular_prompts.append(prompt)

        # Execute all commands first with streaming
        for command in commands:
            self.log.info("Processing slash command", command=command)
            async for cmd_event in self._execute_slash_command_streaming(command):
                yield cmd_event

        # If we have regular content, process it through the agent
        if regular_prompts:
            self.log.debug("Processing prompts through agent", num_prompts=len(regular_prompts))
            async for event in self.run_stream(*regular_prompts, **kwargs):
                yield event

    @abstractmethod
    def _stream_events(
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
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Agent-specific streaming implementation.

        Subclasses must implement this to provide their streaming logic.
        Prompts are pre-converted to UserContent format by run_stream().

        Args:
            run_ctx: Per-run context for state isolation
            prompts: Converted prompts in UserContent format
            user_msg: Pre-created user ChatMessage (from base class)
            effective_parent_id: Resolved parent message ID for threading
            message_id: Optional message ID
            session_id: Optional conversation ID
            parent_session_id: Optional parent conversation ID
            parent_id: Optional parent message ID
            input_provider: Optional input provider
            message_history: Optional message history
            deps: Optional dependencies
            wait_for_connections: Whether to wait for connected agents
            store_history: Whether to store in history

        Yields:
            Stream events during execution
        """
        ...

    async def set_tool_confirmation_mode(self, mode: str) -> None:
        """Set tool confirmation mode (agent-specific implementation).

        Each agent type handles permission modes differently:
        - NativeAgent: tool_confirmation_mode ("always", "never", "per_tool")
        - ACPAgent: auto_approve (bool)

        Subclasses should override this method if they support permission modes.
        The default implementation delegates to _set_mode(mode, "mode").

        Args:
            mode: Mode value in the agent's native format
        """
        await self._set_mode(mode, "mode")

    def is_initializing(self) -> bool:
        """Check if agent is still initializing.

        Returns:
            True if deferred initialization is pending
        """
        return self._connect_pending

    async def ensure_initialized(self) -> None:
        """Wait for deferred initialization to complete.

        Subclasses that use deferred init should:
        1. Set `self._connect_pending = True` in `__aenter__`
        2. Override this method to do actual connection work
        3. Set `self._connect_pending = False` when done

        The base implementation is a no-op for agents without deferred init.
        """

    def is_cancelled(self) -> bool:
        """Check if agent has been cancelled.

        Returns:
            True if cancellation was requested
        """
        # Check both instance flag (for backward compat) and background run context
        background_cancelled = (
            self._background_run_ctx.cancelled if self._background_run_ctx else False
        )
        return self._cancelled or background_cancelled

    async def interrupt(
        self, run_ctx: AgentRunContext | None = None, session_id: str | None = None
    ) -> None:
        """Interrupt the currently running stream.

        Sets the cancelled flag, calls subclass-specific _interrupt(),
        and emits the interrupted signal.

        When pooled, delegates to SessionPool to cancel the active run via
        RunHandle.  When run_ctx is not provided (e.g., from OpenCode
        abort_session), tries _current_run_ctx_var (ContextVar) first, then
        falls back to SessionPool's session.current_run_id + get_run() for
        cross-task access.

        Args:
            run_ctx: Optional per-run context for the stream to interrupt
            session_id: Optional session ID for SessionPool fallback lookup.
        """
        self._cancelled = True
        # When no run_ctx is provided, try ContextVar first (same task),
        # then fall back to SessionPool for cross-task access.
        effective_run_ctx = run_ctx
        if effective_run_ctx is None:
            effective_run_ctx = _current_run_ctx_var.get()
        if effective_run_ctx is None:
            effective_run_ctx = self._get_session_run_ctx(session_id=session_id)
        if effective_run_ctx:
            effective_run_ctx.cancelled = True
        if self._background_run_ctx:
            self._background_run_ctx.cancelled = True

        # When pooled, delegate to SessionPool for proper run cancellation.
        if self.agent_pool is not None and self.agent_pool.session_pool is not None:
            effective_session_id = session_id or (
                effective_run_ctx.session_id if effective_run_ctx else self._events.session_id
            )
            if effective_session_id is not None:
                session_pool = self.agent_pool.session_pool
                session_pool.sessions.cancel_run_for_session(effective_session_id)

        await self._interrupt(effective_run_ctx)
        await self.interrupted.emit(self.InterruptEvent(agent_name=self.name))
        logger.info("Agent interrupted", agent=self.name)

    @abstractmethod
    async def _interrupt(self, run_ctx: AgentRunContext | None = None) -> None:
        """Subclass-specific interrupt implementation.

        Args:
            run_ctx: Optional per-run context for the stream to interrupt
        """

    async def get_stats(self) -> MessageStats:
        """Get message statistics."""
        from agentpool.talk.stats import MessageStats

        return MessageStats(messages=list(self.conversation.chat_messages))

    async def get_mcp_server_info(self) -> dict[str, MCPServerStatus]:
        """Get information about configured MCP servers.

        Returns a dict mapping server names to their status info. Used by
        the OpenCode /mcp endpoint to display MCP servers in the UI.

        The default implementation checks external_providers on the tool manager.
        Subclasses may override to provide agent-specific MCP server info.

        Returns:
            Dict mapping server name to MCPServerStatus
        """
        return await self.tools.get_mcp_server_info()

    @method_spawner
    async def run(
        self,
        *prompts: PromptCompatible | ChatMessage[Any],
        store_history: bool = True,
        message_id: str | None = None,
        session_id: str | None = None,
        parent_session_id: str | None = None,
        parent_id: str | None = None,
        message_history: MessageHistory | None = None,
        deps: TDeps | None = None,
        input_provider: InputProvider | None = None,
        event_handlers: Sequence[AnyEventHandlerType] | None = None,
        wait_for_connections: bool | None = None,
        depth: int = 0,
    ) -> ChatMessage[TResult]:
        """Run agent with prompt and get response.

        This is the standard synchronous run method shared by all agent types.
        It collects all streaming events from run_stream() and returns the final message.

        Args:
            prompts: User query or instruction
            store_history: Whether the message exchange should be added to the
                            context window
            message_id: Optional message id for the returned message.
                        Automatically generated if not provided.
            session_id: Optional conversation id for the returned message.
            parent_session_id: Optional parent conversation id.
            parent_id: Parent message id
            message_history: Optional MessageHistory object to
                             use instead of agent's own conversation
            deps: Optional dependencies for the agent
            input_provider: Optional input provider for the agent
            event_handlers: Optional event handlers for this run (overrides agent's handlers)
            wait_for_connections: Whether to wait for connected agents to complete
            depth: Current delegation depth (0 = top-level run)

        Returns:
            ChatMessage containing response and run information

        Raises:
            RuntimeError: If no final message received from stream
            UnexpectedModelBehavior: If the model fails or behaves unexpectedly
        """
        # When SessionPool is available and the caller is not bypassing it,
        # delegate to SessionPool for turn management and event routing.
        # AG-UI bypass is permanent — see docs/audit/agui-bypass-audit.md.
        if (
            not _should_bypass_session_pool()
            and self.agent_pool is not None
            and self.agent_pool.session_pool is not None
        ):
            from agentpool.utils.identifiers import generate_session_id

            effective_session_id = session_id or generate_session_id()
            session_pool = self.agent_pool.session_pool

            # If the session already exists but belongs to a different agent,
            # fall through to direct execution so THIS agent runs.
            existing_session = session_pool.sessions.get_session(effective_session_id)
            if existing_session is None or existing_session.agent_name == self.name:
                # Ensure session exists in SessionPool
                if existing_session is None:
                    await session_pool.create_session(
                        effective_session_id,
                        agent_name=self.name,
                        parent_session_id=parent_session_id,
                    )

                # Subscribe to EventBus and process prompt
                queue = await session_pool.event_bus.subscribe(effective_session_id)
                process_kwargs = {
                    "store_history": store_history,
                    "message_id": message_id,
                    "parent_id": parent_id,
                    "message_history": message_history,
                    "input_provider": input_provider,
                    "wait_for_connections": wait_for_connections,
                    "deps": deps,
                    "event_handlers": event_handlers,
                }
                process_task = asyncio.create_task(
                    session_pool.process_prompt(effective_session_id, *prompts, **process_kwargs)
                )
                final_message: ChatMessage[TResult] | None = None
                try:
                    while not process_task.done():
                        try:
                            envelope = await asyncio.wait_for(queue.get(), timeout=1.0)
                            if envelope is not None and isinstance(
                                envelope.event, StreamCompleteEvent
                            ):
                                final_message = envelope.event.message
                        except TimeoutError:
                            continue

                    # Drain remaining events
                    while not queue.empty():
                        envelope = queue.get_nowait()
                        if envelope is not None and isinstance(
                            envelope.event, StreamCompleteEvent
                        ):
                            final_message = envelope.event.message

                    if (exc := process_task.exception()) is not None:
                        raise exc
                finally:
                    await session_pool.event_bus.unsubscribe(effective_session_id, queue)

                if final_message is None:
                    raise RuntimeError("No final message received from stream")
                await self.message_sent.emit(final_message)
                # Per-session agents don't inherit connections from the base agent.
                # Route from the base agent so Talk targets still receive the message.
                session = session_pool.sessions.get_session(effective_session_id)
                if session is not None and getattr(session, "is_per_session_agent", False):
                    await self.connections.route_message(final_message, wait=wait_for_connections)
                return final_message

        # Direct execution path for AG-UI bypass and standalone mode.
        final_message = None
        async for event in self.run_stream(
            *prompts,
            store_history=store_history,
            message_id=message_id,
            session_id=session_id,
            parent_session_id=parent_session_id,
            parent_id=parent_id,
            message_history=message_history,
            deps=deps,
            input_provider=input_provider,
            event_handlers=event_handlers,
            wait_for_connections=wait_for_connections,
            depth=depth,
        ):
            if isinstance(event, StreamCompleteEvent):
                final_message = event.message

        if final_message is None:
            raise RuntimeError("No final message received from stream")

        return final_message

    @abstractmethod
    async def get_available_models(self) -> list[ModelInfo] | None:
        """Get available models for this agent.

        Returns a list of models that can be used with this agent, or None
        if model discovery is not supported for this agent type.

        Uses tokonomics.ModelInfo which includes pricing, capabilities,
        and limits. Can be converted to protocol-specific formats (OpenCode, ACP).

        Returns:
            List of tokonomics ModelInfo, or None if not supported
        """
        ...

    @abstractmethod
    async def get_modes(self) -> list[ModeCategory]:
        """Get available mode categories for this agent.

        Returns a list of mode categories that can be switched. Each category
        represents a group of mutually exclusive modes (e.g., permissions,
        models, behavior presets).

        Different agent types expose different modes:
        - Native Agent: permissions + model selection
        - ACPAgent: Passthrough from remote server

        Returns:
            List of ModeCategory, empty list if no modes supported
        """
        ...

    @overload
    async def set_mode(self, mode: ModeInfo) -> None: ...

    @overload
    async def set_mode(self, mode: str, category_id: ModeCategoryId | str) -> None: ...

    async def set_mode(
        self, mode: ModeInfo | str, category_id: ModeCategoryId | str | None = None
    ) -> None:
        """Set a mode within a category.

        Args:
            mode: The mode to activate - either a ModeInfo object or mode ID string.
            category_id: Category ID. Required if mode is a string, optional if ModeInfo.
        """
        if isinstance(mode, ModeInfo):
            mode_id = mode.id
            resolved_category = category_id or mode.category_id
        else:
            mode_id = mode
            if not category_id:
                raise ValueError("category_id is required when mode is a string")
            resolved_category = category_id

        if not resolved_category:
            raise ValueError("category_id could not be determined from ModeInfo")

        await self._set_mode(mode_id, resolved_category)

    @abstractmethod
    async def _set_mode(self, mode_id: str, category_id: str) -> None:
        """Agent-specific mode switching implementation."""
        ...

    @abstractmethod
    async def list_sessions(
        self,
        *,
        cwd: str | None = None,
        limit: int | None = None,
    ) -> list[SessionData]:
        """List available sessions for this agent.

        Returns session information including session IDs, working directories,
        titles, and last update timestamps.

        Args:
            cwd: Filter sessions by working directory (optional)
            limit: Maximum number of sessions to return (optional)

        Returns:
            List of SessionData objects
        """
        ...

    @abstractmethod
    async def load_session(self, session_id: str) -> SessionData | None:
        """Load and restore a session by ID.

        Loads session data and restores the conversation history for the specified session.
        For agents that support session persistence, this switches the active session.

        Args:
            session_id: Unique identifier for the session to load

        Returns:
            SessionData if session was found and loaded, None otherwise
        """
        ...

    async def resume_session(self, session_id: str) -> SessionData | None:
        """Resume a session by ID without loading conversation history.

        Unlike load_session, this does NOT populate conversation.chat_messages.
        It restores the agent's internal state so the conversation can continue,
        but assumes the client already has the history (or doesn't need it).

        This is useful for:
        - Reconnecting after a disconnect
        - Automated workflows that don't need UI history
        - Faster session switching when history display isn't needed

        Default implementation calls load_session (subclasses may optimize).

        UNSTABLE: This feature is not part of the ACP spec yet.

        Args:
            session_id: Unique identifier for the session to resume

        Returns:
            SessionData if session was found and resumed, None otherwise
        """
        # Default: just delegate to load_session
        # Subclasses can override to skip history loading for efficiency
        return await self.load_session(session_id)

    async def load_rules(self, project_dir: str | None = None) -> None:
        """Load agent rules from global and project locations.

        Searches for AGENTS.md/CLAUDE.md files in:
        1. Global: ~/.config/agentpool/AGENTS.md (user-wide rules)
        2. Project: {project_dir}/AGENTS.md (project-specific rules)

        Both are merged and staged for injection into the first prompt.
        Uses the agent's execution environment for filesystem access,
        making this work across local and remote (ACP) environments.

        Args:
            project_dir: Project directory to search for rules. Falls back to
                env.cwd if not provided.
        """
        from agentpool_config.resolution import get_global_config_dir

        effective_project_dir = project_dir or self.env.cwd
        fs = self.env.get_fs()
        rules_file_names = ("AGENTS.md", "CLAUDE.md")
        rules_parts: list[str] = []
        # 1. Global rules from config directory
        global_dir = get_global_config_dir()
        for name in rules_file_names:
            path = global_dir / name
            if path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    rules_parts.append(f"## Global Rules\n\n{content}")
                    logger.debug("Loaded global rules", path=str(path))
                except OSError:
                    logger.exception("Failed to read global rules", path=str(path))
                break

        # 2. Project rules - use provided dir, env.cwd, or current directory
        if effective_project_dir is None:
            effective_project_dir = str(Path.cwd())

        if effective_project_dir:
            for name in rules_file_names:
                rules_path = f"{effective_project_dir}/{name}"
                try:
                    if fs:
                        # Use filesystem abstraction (works for local and ACP)
                        if await fs._exists(rules_path):
                            content_bytes = await fs._cat_file(rules_path)
                            content = content_bytes.decode("utf-8")
                            rules_parts.append(f"## Project Rules\n\n{content}")
                            logger.debug("Loaded project rules", path=rules_path)
                            break
                    else:
                        # Fallback to direct file access
                        local_path = Path(rules_path)
                        if local_path.is_file():
                            content = local_path.read_text(encoding="utf-8")
                            rules_parts.append(f"## Project Rules\n\n{content}")
                            logger.debug("Loaded project rules", path=rules_path)
                            break
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug("No project rules found", path=rules_path, error=str(exc))
                    break
                except Exception as exc:
                    # Handles MCP/RequestError from remote filesystems (ACP mode)
                    logger.debug("No project rules found", path=rules_path, error=str(exc))

        # Stage combined rules for first prompt
        if rules_parts:
            combined = "\n\n".join(rules_parts)
            self.staged_content.add_text(combined)
            logger.info(
                "Staged agent rules",
                global_rules=bool(any("Global" in p for p in rules_parts)),
                project_rules=bool(any("Project" in p for p in rules_parts)),
            )
