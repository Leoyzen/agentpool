"""Runtime context models for Agents."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, Literal
import uuid
import warnings

from agentpool.agents.prompt_injection import PromptInjectionManager
from agentpool.log import get_logger
from agentpool.messaging.context import NodeContext


if TYPE_CHECKING:
    from mcp.types import ElicitRequestParams, ElicitResult, ErrorData
    from upathtools.filesystems import IsolatedMemoryFileSystem, OverlayFileSystem

    from agentpool import Agent
    from agentpool.agents.events import StreamEventEmitter
    from agentpool.tools.base import Tool


ConfirmationResult = Literal["allow", "skip", "abort_run", "abort_chain"]

logger = get_logger(__name__)


class _DeprecatedField:
    """Descriptor that emits a DeprecationWarning when the field is accessed.

    The value is stored in the instance ``__dict__`` under a private key
    (``_deprecated_<name>``) so that ``dataclasses.asdict()`` — which calls
    ``getattr()`` — continues to work correctly.

    Because this is a *data descriptor* (it defines both ``__get__`` and
    ``__set__``), it takes precedence over the instance ``__dict__`` entry
    that the dataclass ``__init__`` would normally create.

    Args:
        default_factory: Callable that produces the default value when the
            field has not been set yet.
        msg: Custom deprecation message.  When *None* a generic message is
            constructed from the owning class and field names.
    """

    def __init__(
        self,
        default_factory: Any,
        *,
        msg: str | None = None,
    ) -> None:
        self._default_factory = default_factory
        self._msg = msg
        self._name: str = ""
        self._private_key: str = ""

    def __set_name__(self, owner: type, name: str) -> None:
        self._name = name
        self._private_key = f"_deprecated_{name}"

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            # Class-level access (e.g. introspection) — return the descriptor.
            return self
        value = instance.__dict__.get(self._private_key)
        if value is None and self._private_key not in instance.__dict__:
            value = self._default_factory()
            instance.__dict__[self._private_key] = value
        msg = self._msg or f"{type(instance).__name__}.{self._name} is deprecated"
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        return value

    def __set__(self, instance: Any, value: Any) -> None:
        msg = self._msg or f"{type(instance).__name__}.{self._name} is deprecated"
        warnings.warn(msg, DeprecationWarning, stacklevel=2)
        instance.__dict__[self._private_key] = value


@dataclass(kw_only=True)
class AgentRunContext:
    """Per-execution isolated state container for agent runs.

    This dataclass holds all state that is specific to a single run execution,
    ensuring isolation between concurrent runs. It is separate from AgentContext
    which is the PydanticAI context passed to tools.

    !!! warning "Deprecated"
        ``session_id`` is deprecated.  Session IDs are now managed by
        ``SessionManager`` / ``ensure_session`` on the agent itself.  Accessing
        or setting ``session_id`` on ``AgentRunContext`` emits a
        ``DeprecationWarning``.

    Attributes:
        cancelled: Whether the run has been cancelled.
        current_task: The asyncio.Task for the current run, if any.
        depth: Current delegation depth (0 = top-level run).
        event_queue: Queue for streaming events from this run.
        injection_manager: Manages prompt injection and queuing for this run.
        session_id: **Deprecated** — use agent-level ``session_id`` instead.
        deps: Optional dependencies passed to the run.
        start_time: Timestamp when the run started (for metrics).
    """

    cancelled: bool = False
    """Whether the run has been cancelled."""

    current_task: asyncio.Task[Any] | None = None
    """The asyncio.Task for the current run, if any."""

    depth: int = 0
    """Current delegation depth (0 = top-level run)."""

    event_queue: asyncio.Queue[Any] = field(default_factory=asyncio.Queue)
    """Queue for streaming events from this run."""

    injection_manager: PromptInjectionManager = field(default_factory=PromptInjectionManager)
    """Manages prompt injection and queuing for this run."""

    # DEPRECATED: session_id on AgentRunContext is a dead field.  Session IDs
    # are now managed by SessionManager / ensure_session on the agent.  The
    # _DeprecatedField descriptor (assigned after the class body) emits
    # DeprecationWarning on every access but preserves full backward
    # compatibility (including dataclasses.asdict()).
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    """**Deprecated** — session IDs are managed by SessionManager."""

    deps: Any = None
    """Optional dependencies passed to the run."""

    start_time: float = field(default_factory=time.perf_counter)
    """Timestamp when the run started (for metrics)."""


# Replace the plain dataclass attribute with a data descriptor that intercepts
# all reads and writes.  The dataclass machinery has already registered
# ``session_id`` in ``AgentRunContext.__dataclass_fields__`` so asdict() and
# other introspection continue to work.  Because _DeprecatedField defines both
# __get__ and __set__ it is a *data descriptor* and takes precedence over the
# instance __dict__ entry — guaranteeing that every access emits the warning.
AgentRunContext.session_id = _DeprecatedField(  # type: ignore[assignment]
    default_factory=lambda: uuid.uuid4().hex,
    msg="AgentRunContext.session_id is deprecated — use agent-level session_id instead",
)


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
    """Reference to the per-run context for accessing run-isolated state like event_queue."""

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
        # Use run_ctx.event_queue for per-run isolation, fallback to agent queue
        if self.run_ctx is not None:
            await self.run_ctx.event_queue.put(progress_event)
        else:
            await self.agent._event_queue.put(progress_event)

    @property
    def events(self) -> StreamEventEmitter:
        """Get event emitter with context automatically injected."""
        from agentpool.agents.events import StreamEventEmitter

        return StreamEventEmitter(self)

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
    ) -> str:
        """Create a child session for a subagent delegation.

        When the agent pool and its session pool are available, the child
        session is created via ``SessionPool.create_session()`` so that
        parent-child relationships, project context, and working directory
        are inherited automatically.  When no pool is present (e.g. during
        standalone or test runs) a new session ID is generated without
        persistence.

        Args:
            agent_name: Name of the child agent.
            agent_type: Type of the child agent (``"native"``, ``"claude"``, etc.).
            parent_session_id: Explicit parent session ID.  When *None* the
                current node's ``session_id`` is used as the parent.

        Returns:
            The child session ID string.
        """
        pool = self.node.agent_pool
        if pool is not None:
            effective_parent = parent_session_id or self.node.session_id
            if effective_parent is None:
                from agentpool.utils.identifiers import generate_session_id

                return generate_session_id()

            if pool.session_pool is not None:
                from agentpool.utils.identifiers import generate_session_id

                child_session = await pool.session_pool.create_session(
                    session_id=generate_session_id(),
                    agent_name=agent_name,
                    parent_session_id=effective_parent,
                    agent_type=agent_type,
                )
                return child_session.session_id

        # No pool available — generate an ephemeral ID without persistence.
        from agentpool.utils.identifiers import generate_session_id

        return generate_session_id()

    @property
    def overlay_fs(self) -> OverlayFileSystem:
        """Access unified filesystem combining agent storage and VFS resources.

        Provides a layered view where writes go to agent's internal filesystem
        and reads fall through to VFS resources.

        Returns:
            OverlayFileSystem for this agent
        """
        return self.agent.overlay_fs
