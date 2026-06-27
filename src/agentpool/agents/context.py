"""Runtime context models for Agents."""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import anyio

from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from mcp.types import ElicitRequestParams, ElicitResult, ErrorData
    from upathtools.filesystems import IsolatedMemoryFileSystem, OverlayFileSystem

    from agentpool import Agent
    from agentpool.agents.events import StreamEventEmitter
    from agentpool.orchestrator.core import EventBus
    from agentpool.orchestrator.run import RunHandle
    from agentpool.tools.base import Tool


ConfirmationResult = Literal["allow", "skip", "abort_run", "abort_chain"]

logger = get_logger(__name__)


class _DeprecatedField:
    """Data descriptor that warns when a deprecated dataclass field is accessed."""

    def __init__(self, *, default_factory: Any, msg: str) -> None:
        self.default_factory = default_factory
        self.msg = msg

    def __get__(self, obj: Any, objtype: type[Any] | None = None) -> Any:
        if obj is None:
            return self
        value = obj.__dict__.get("session_id")
        if value is None:
            value = self.default_factory()
            obj.__dict__["session_id"] = value
        logger.warning(self.msg)
        return value

    def __set__(self, obj: Any, value: Any) -> None:
        logger.warning(self.msg)
        obj.__dict__["session_id"] = value


MAX_SUBAGENT_DEPTH: int = 5
"""Maximum nesting depth for subagent delegations."""


class SubagentDepthError(Exception):
    """Raised when subagent nesting exceeds MAX_SUBAGENT_DEPTH."""


@dataclass(kw_only=True)
class AgentRunContext:
    """Per-execution isolated state container for agent runs.

    This dataclass holds all state that is specific to a single run execution,
    ensuring isolation between concurrent runs. It is separate from AgentContext
    which is the PydanticAI context passed to tools.

    Attributes:
        cancelled: Whether the run has been cancelled.
        current_task: The asyncio.Task for the current run, if any.
        depth: Current delegation depth (0 = top-level run).
        event_bus: Optional event bus for cross-session event routing.
        injection_manager: Manages prompt injection and queuing for this run.
        session_id: Session ID for this run.
        deps: Optional dependencies passed to the run.
        start_time: Timestamp when the run started (for metrics).
    """

    cancelled: bool = False
    """Whether the run has been cancelled."""

    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """Unique identifier for this run."""

    current_task: asyncio.Task[Any] | None = None
    """The asyncio.Task for the current run, if any."""

    depth: int = 0
    """Current delegation depth (0 = top-level run)."""

    event_bus: EventBus | None = None
    """Optional event bus for cross-session event routing."""

    injection_manager: PromptInjectionManager = field(default_factory=PromptInjectionManager)
    """Manages prompt injection and queuing for this run."""

    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """Session ID for this run."""

    deps: Any = None
    """Optional dependencies passed to the run."""

    start_time: float = field(default_factory=time.perf_counter)
    """Timestamp when the run started (for metrics)."""

    completed: bool = False
    """Whether the run has completed (stream finished)."""

    terminal_tool_result: Any = None
    """Result returned by a terminal tool such as `attempt_completion`."""

    terminal_tool_name: str | None = None
    """Name of the terminal tool that completed the run."""

    checkpointed: bool = False
    """Whether the run has been checkpointed (deferred tools pending)."""

    _run_handle: RunHandle | None = None
    """Run handle for this execution, set by TurnRunner._run_turn_unlocked()."""

    child_done_events: dict[str, anyio.Event] = field(default_factory=dict)
    """Per-child-session done events for tracking subagent completion."""

    queued_steer_messages: list[str] = field(default_factory=list)
    """Steer messages queued during post-iteration wait window."""

    steer_callback: Callable[[str, str], Awaitable[bool]] | None = None
    """Set by TurnRunner, allows tools to call steer() via run_ctx."""

    async def complete_background_task(self, child_session_id: str, message: str) -> None:
        """Signal that a background child task has completed.

        Calls steer_callback first (if set), then pops and sets the done_event.
        Ordering is critical: steer BEFORE signal to prevent RunExecutor
        from waking before the steer message is queued.
        """
        if self.steer_callback is not None:
            try:
                await self.steer_callback(self.session_id, message)
            except Exception:
                logger.exception(
                    "steer_callback raised in complete_background_task",
                    child_session_id=child_session_id,
                )
        else:
            logger.warning(
                "complete_background_task called without steer_callback",
                child_session_id=child_session_id,
            )
        event = self.child_done_events.pop(child_session_id, None)
        if event is not None:
            event.set()


@dataclass(kw_only=True)
class AgentContext[TDeps = Any](NodeContext[TDeps]):
    """Runtime context for agent execution.

    Generically typed with AgentContext[Type of Dependencies]
    """

    tool_name: str | None = None
    """Name of the currently executing tool."""

    tool_call_id: str | None = None
    """ID of the current tool call."""

    tool_input: dict[str, Any] = field(default_factory=dict)
    """Input arguments for the current tool call."""

    model_name: str | None = None
    """Model name in provider:model format (e.g., 'anthropic:claude-haiku-4-5')."""

    run_ctx: AgentRunContext | None = None
    """Reference to the per-run context for accessing run-isolated state."""

    @property
    def native_agent(self) -> Agent[TDeps, Any]:
        """Current agent, type-narrowed to native pydantic-ai Agent."""
        from agentpool import Agent

        assert isinstance(self.node, Agent)
        return self.node  # ty: ignore[invalid-return-type]

    async def handle_elicitation(self, params: ElicitRequestParams) -> ElicitResult | ErrorData:
        """Handle elicitation request for additional information."""
        provider = self.get_input_provider()
        return await provider.get_elicitation(params)

    def get_session_state(self) -> Any | None:
        """Get the SessionState for the current run if available.

        Returns:
            The SessionState from SessionPool, or None if not in a pooled session.
        """
        if self.run_ctx is None:
            return None
        session_id = self.run_ctx.session_id
        if not session_id:
            return None
        pool = self.node.agent_pool
        if pool is None or pool.session_pool is None:
            return None
        return pool.session_pool.sessions.get_session(session_id)

    async def report_progress(self, progress: float, total: float | None, message: str) -> None:
        """Report progress by emitting event into the agent's stream."""
        from agentpool.agents.events import ToolCallProgressEvent

        logger.info("Reporting tool call progress", progress=progress, total=total, message=message)
        progress_event = ToolCallProgressEvent(
            progress=int(progress),
            total=int(total) if total is not None else 100,
            message=message,
            tool_name=self.tool_name or "",
            tool_call_id=self.tool_call_id or "",
            tool_input=self.tool_input,
        )
        if self.run_ctx is not None and self.run_ctx.event_bus is not None:
            await self.run_ctx.event_bus.publish(self.run_ctx.session_id, progress_event)
        else:
            logger.debug("report_progress called with no active run context or event_bus — event dropped")

    @property
    def events(self) -> StreamEventEmitter:
        """Get event emitter with context automatically injected."""
        from agentpool.agents.events import StreamEventEmitter

        event_bus = self.run_ctx.event_bus if self.run_ctx else None
        return StreamEventEmitter(self, event_bus=event_bus)

    async def handle_confirmation(self, tool: Tool, args: dict[str, Any]) -> ConfirmationResult:
        """Handle tool execution confirmation.

        Returns "allow" if:
        - No confirmation handler is set
        - Handler confirms the execution

        Args:
            tool: The tool being executed
            args: Arguments passed to the tool

        Returns:
            Confirmation result indicating how to proceed
        """
        provider = self.get_input_provider()
        # Get tool_confirmation_mode if available (NativeAgent only)
        # Other agents handle permission checks in their own way
        mode = getattr(self.agent, "tool_confirmation_mode", "per_tool")
        if (mode == "per_tool" and not tool.requires_confirmation) or mode == "never":
            return "allow"
        return await provider.get_tool_confirmation(self, tool.description or "")

    @property
    def internal_fs(self) -> IsolatedMemoryFileSystem:
        """Access agent's internal filesystem for tool state.

        Tools can use this to store logs, history, temporary files, etc.
        The filesystem is scoped to the agent instance.

        Returns:
            In-memory filesystem for this agent
        """
        return self.agent.internal_fs

    async def create_child_session(
        self,
        agent_name: str,
        agent_type: str,
        parent_session_id: str | None = None,
        *,
        spawn_mechanism: str = "foreground",
        description: str = "",
        tool_call_id: str | None = None,
        **metadata: Any,
    ) -> str:
        """Create a child session for a subagent delegation.

        When the agent pool and its session pool are available, the child
        session is created via ``SessionPool.create_session()`` so that
        parent-child relationships, project context, and working directory
        are inherited automatically.  When no pool is present (e.g. during
        standalone or test runs) a new session ID is generated without
        persistence.

        When ``run_ctx`` is set (i.e. the agent is running inside a pooled
        session), a ``SpawnSessionStart`` event is auto-emitted and a
        ``done_event`` is registered on ``run_ctx.child_done_events`` so
        that callers can await subagent completion.

        Args:
            agent_name: Name of the child agent.
            agent_type: Type of the child agent (``"native"``, ``"claude"``, etc.).
            parent_session_id: Explicit parent session ID.  When *None* the
                current node's ``session_id`` is used as the parent.
            spawn_mechanism: How the subagent is created — ``"foreground"``
                for synchronous delegation, ``"task"`` for background.
            description: Human-readable description of the spawn operation.
            tool_call_id: ID of the tool call that triggered the spawn.
            **metadata: Additional metadata to attach to the child session.

        Returns:
            The child session ID string.
        """
        child_sid: str
        pool = self.node.agent_pool
        if pool is not None and pool.session_pool is not None:
            effective_parent = parent_session_id or self.node._events.session_id
            # Guard against MagicMock auto-generated attributes in tests:
            # _events.session_id may return a Mock when not explicitly set.
            if isinstance(effective_parent, str):
                from agentpool.utils.identifiers import generate_session_id

                child_session = await pool.session_pool.create_session(
                    session_id=generate_session_id(),
                    agent_name=agent_name,
                    parent_session_id=effective_parent,
                    agent_type=agent_type,
                    **metadata,
                )
                child_sid = child_session.session_id
            else:
                from agentpool.utils.identifiers import generate_session_id

                child_sid = generate_session_id()
        else:
            # Fallback: no pool, no session_pool — generate ephemeral ID.
            from agentpool.utils.identifiers import generate_session_id

            child_sid = generate_session_id()

        # Auto-emit SpawnSessionStart and register done_event when running
        # inside a pooled session (run_ctx is set).  In standalone/test mode
        # (run_ctx is None) this is skipped.
        if self.run_ctx is not None:
            child_depth = self.run_ctx.depth + 1
            if child_depth > MAX_SUBAGENT_DEPTH:
                raise SubagentDepthError(
                    f"Subagent depth {child_depth} exceeds limit {MAX_SUBAGENT_DEPTH}",
                )
            from agentpool.agents.events.events import SpawnSessionStart

            event_spawn_mechanism: Literal["task", "spawn"] = (
                "task" if spawn_mechanism == "task" else "spawn"
            )
            spawn_event = SpawnSessionStart(
                child_session_id=child_sid,
                parent_session_id=self.run_ctx.session_id,
                tool_call_id=tool_call_id or self.tool_call_id,
                spawn_mechanism=event_spawn_mechanism,
                source_name=agent_name,
                source_type="agent",
                depth=child_depth,
                description=description,
            )
            await self.events.emit_event(spawn_event)
            done_event = anyio.Event()
            self.run_ctx.child_done_events[child_sid] = done_event

        return child_sid

    @property
    def overlay_fs(self) -> OverlayFileSystem:
        """Access unified filesystem combining agent storage and VFS resources.

        Provides a layered view where writes go to agent's internal filesystem
        and reads fall through to VFS resources.

        Returns:
            OverlayFileSystem for this agent
        """
        return self.agent.overlay_fs
