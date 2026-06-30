"""Base class for message processing nodes."""

from __future__ import annotations

from abc import ABC, abstractmethod
import asyncio
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal, Self, overload

from anyenv.signals import Signal

from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.talk import AggregatedTalkStats
from agentpool.utils.tasks import TaskManager


if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import timedelta
    from types import TracebackType

    from evented.event_data import EventData
    from evented_config import EventConfig

    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.common_types import (
        AnyTransformFn,
        AsyncFilterFn,
        ProcessorCallback,
        QueueStrategy,
    )
    from agentpool.delegation import AgentPool
    from agentpool.messaging.context import NodeContext
    from agentpool.storage import StorageManager
    from agentpool.talk import Talk, TeamTalk
    from agentpool.talk.stats import AggregatedMessageStats, MessageStats
    from agentpool.tools.base import FunctionTool
    from agentpool.ui.base import InputProvider
    from agentpool_config.forward_targets import ConnectionType
    from agentpool_config.mcp_server import MCPServerConfig


logger = get_logger(__name__)

SourceType = Literal["agent", "team_parallel", "team_sequential"]
"""Type alias for source_type values used in event streaming.

Distinguishes the origin of a subagent event:
- ``"agent"``: a single agent (native, ACP, Claude Code, etc.)
- ``"team_parallel"``: a parallel team (:class:`Team`)
- ``"team_sequential"``: a sequential team (:class:`TeamRun`)
"""


def get_source_type(node: MessageNode[Any, Any]) -> SourceType:
    """Return the source-type tag for *node* used in event streaming.

    Uses ``isinstance`` checks with **local** (deferred) imports so that
    calling this function never creates a circular import between
    ``messagenode`` ↔ ``team`` / ``teamrun``.

    Args:
        node: Any :class:`MessageNode` instance.

    Returns:
        The corresponding :data:`SourceType` value.
    """
    from agentpool.delegation.base_team import BaseTeam
    from agentpool.delegation.team import Team

    if isinstance(node, Team):
        return "team_parallel"
    if isinstance(node, BaseTeam):
        # BaseTeam but not Team → TeamRun (or any future sequential subclass)
        return "team_sequential"
    # Check it's at least a MessageNode — unknown subclasses get a warning
    if not isinstance(node, MessageNode):
        logger.warning("Unexpected node type %s, defaulting to 'agent'", type(node).__name__)
    return "agent"


class MessageNode[TDeps, TResult](ABC):
    """Base class for all message processing nodes."""

    message_received = Signal[ChatMessage[Any]]()
    """Signal emitted when node receives a message."""

    message_sent = Signal[ChatMessage[Any]]()
    """Signal emitted when node creates a message."""

    def __init__(
        self,
        name: str | None = None,
        description: str | None = None,
        display_name: str | None = None,
        mcp_servers: Sequence[str | MCPServerConfig] | None = None,
        agent_pool: AgentPool[Any] | None = None,
        enable_logging: bool = True,
        event_configs: Sequence[EventConfig] | None = None,
    ) -> None:
        """Initialize message node."""
        super().__init__()

        from agentpool.mcp_server.manager import MCPManager
        from agentpool.messaging import EventManager
        from agentpool.messaging.connection_manager import ConnectionManager

        async def _event_handler(event: EventData) -> None:
            if prompt := event.to_prompt():
                await self.run(prompt)

        self.task_manager = TaskManager()
        self._name = name or self.__class__.__name__
        self._display_name = display_name
        self.log = logger.bind(agent_name=self._name)
        self.agent_pool = agent_pool
        self.description = description
        self.connections = ConnectionManager(self)
        cfgs = list(event_configs) if event_configs else None
        self._events = EventManager(
            configs=cfgs,
            event_callbacks=[_event_handler],
            source_name=self._name,
        )
        name_ = f"node_{self._name}"
        # Share the pool's MCPManager when available to avoid duplicate
        # MCP subprocess spawning. The pool owns the lifecycle; agents
        # with a shared manager skip __aexit__ cleanup on it.
        # However, when the agent has its own MCP servers (agent-level),
        # create a dedicated MCPManager for them. Pool-level servers are
        # still accessible via agent_pool.mcp and are added separately by
        # the orchestrator via agent.tools.add_provider().
        if agent_pool is not None and not mcp_servers:
            self._mcp_shared = True
            self.mcp = agent_pool.mcp
        else:
            self._mcp_shared = False
            self.mcp = MCPManager(name_, servers=mcp_servers, owner=self.name, _warn=False)
        self.enable_db_logging = enable_logging

    async def log_session(
        self,
        session_id: str | None = None,
        initial_prompt: str | None = None,
        model: str | None = None,
        parent_session_id: str | None = None,
    ) -> None:
        """Log conversation to storage if enabled.

        Should be called at the start of run_stream() after session_id is set.
        For native agents, generate session_id first with uuid4().
        For wrapped agents (Claude Code), set session_id from SDK session first.

        Args:
            session_id: Optional session ID for the conversation.
            initial_prompt: Optional initial prompt to trigger title generation.
            model: Requested model identifier for this session.
            parent_session_id: Optional parent session ID.
        """
        if self.enable_db_logging and self.storage and session_id:
            await self.storage.log_session(
                session_id=session_id,
                node_name=self.name,
                model=model,
                initial_prompt=initial_prompt,
                parent_session_id=parent_session_id,
            )

    async def emit_agent_event(
        self, event: RichAgentStreamEvent[Any], source_session_id: str | None = None
    ) -> None:
        """Emit an agent stream event via the event manager.

        Args:
            event: The agent stream event to emit
            source_session_id: Optional ID of the session that produced the event
        """
        await self._events.emit_agent_event(event, source_session_id=source_session_id)

    async def __aenter__(self) -> Self:
        """Initialize base message node."""
        try:
            await self._events.__aenter__()
            if not self._mcp_shared:
                await self.mcp.__aenter__()
        except Exception as e:
            await self.__aexit__(type(e), e, e.__traceback__)

            raise RuntimeError(f"Failed to initialize {self.name}") from e
        else:
            return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Clean up base resources."""
        await self._events.__aexit__(exc_type, exc_val, exc_tb)
        if not self._mcp_shared:
            await self.mcp.__aexit__(exc_type, exc_val, exc_tb)
        await self.task_manager.cleanup_tasks()

    @property
    def connection_stats(self) -> AggregatedTalkStats:
        """Get stats for all active connections of this node."""
        stats = [talk.stats for talk in self.connections.get_connections()]
        return AggregatedTalkStats(stats=stats)

    def get_context(
        self,
        data: Any = None,
        input_provider: InputProvider | None = None,
    ) -> NodeContext:
        """Create a new context for this node.

        Args:
            data: Optional custom data to attach to the context
            input_provider: Optional input provider override

        Returns:
            A new NodeContext instance
        """
        raise NotImplementedError

    @property
    def storage(self) -> StorageManager | None:
        """Get storage manager from pool."""
        return self.agent_pool.storage if self.agent_pool else None

    @property
    def name(self) -> str:
        """Get agent name."""
        return self._name

    @property
    def display_name(self) -> str:
        """Get human-readable display name, falls back to name."""
        return self._display_name or self._name

    @property
    def agent_type(self) -> str:
        """Return the agent-type string used for persistence.

        This is the *persistence-domain* identifier (stored in
        ``SessionData.agent_type``).  It differs from :data:`SourceType`
        which is the *event-domain* identifier used during streaming.

        Subclasses may override this to provide a more specific type
        string (e.g. ``"native"``, ``"acp"``, ``"claude_code"``).

        Returns:
            A string identifying the agent type for storage purposes.
        """
        return get_source_type(self)

    def to_tool(
        self, *, name: str | None = None, description: str | None = None, **kwargs: Any
    ) -> FunctionTool[TResult]:
        """Convert node to a callable tool.

        Args:
            name: Optional tool name override
            description: Optional tool description override
            **kwargs: Additional arguments for subclass customization

        Returns:
            Tool instance that can be registered
        """
        from agentpool.tools.base import FunctionTool

        async def wrapped(prompt: str) -> TResult:
            result = await self.run(prompt)
            return result.content

        tool_name = name or f"ask_{self.name}"
        docstring = description or f"Get expert answer from {self.name}"
        if self.description:
            docstring = f"{docstring}\n\n{self.description}"
        wrapped.__doc__ = docstring
        wrapped.__name__ = tool_name
        return FunctionTool.from_callable(wrapped)

    @overload
    def __rshift__(
        self, other: MessageNode[Any, Any] | ProcessorCallback[Any]
    ) -> Talk[TResult]: ...

    @overload
    def __rshift__(
        self, other: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]]
    ) -> TeamTalk[TResult]: ...

    def __rshift__(
        self,
        other: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
    ) -> Talk[Any] | TeamTalk[Any]:
        """Connect agent to another agent or group.

        Example:
            agent >> other_agent  # Connect to single agent
            agent >> (agent2 & agent3)  # Connect to group
            agent >> "other_agent"  # Connect by name (needs pool)
        """
        return self.connect_to(other)

    @overload
    def connect_to(
        self,
        target: MessageNode[Any, Any] | ProcessorCallback[Any],
        *,
        queued: Literal[True],
        queue_strategy: Literal["concat"],
    ) -> Talk[str]: ...

    @overload
    def connect_to(
        self,
        target: MessageNode[Any, Any] | ProcessorCallback[Any],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
    ) -> Talk[TResult]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        queued: Literal[True],
        queue_strategy: Literal["concat"],
    ) -> TeamTalk[str]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, TResult] | ProcessorCallback[TResult]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
    ) -> TeamTalk[TResult]: ...

    @overload
    def connect_to(
        self,
        target: Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
    ) -> TeamTalk: ...

    def connect_to(
        self,
        target: MessageNode[Any, Any]
        | ProcessorCallback[Any]
        | Sequence[MessageNode[Any, Any] | ProcessorCallback[Any]],
        *,
        connection_type: ConnectionType = "run",
        name: str | None = None,
        priority: int = 0,
        delay: timedelta | None = None,
        queued: bool = False,
        queue_strategy: QueueStrategy = "latest",
        transform: AnyTransformFn[Any] | None = None,
        filter_condition: AsyncFilterFn | None = None,
        stop_condition: AsyncFilterFn | None = None,
        exit_condition: AsyncFilterFn | None = None,
    ) -> Talk[Any] | TeamTalk:
        """Create connection(s) to target(s)."""
        # Handle callable case
        from agentpool.agents import Agent
        from agentpool.delegation.base_team import BaseTeam

        if callable(target):
            target = Agent.from_callback(target)
            if pool := self.agent_pool:
                target.agent_pool = pool
        # we are explicit here just to make disctinction clear, we only want sequences
        # of message units
        if isinstance(target, Sequence) and not isinstance(target, BaseTeam):
            targets: list[MessageNode[Any, Any]] = []
            for t in target:
                match t:
                    case _ if callable(t):
                        other = Agent.from_callback(t)
                        if pool := self.agent_pool:
                            other.agent_pool = pool
                        targets.append(other)
                    case MessageNode():
                        targets.append(t)
                    case _:
                        raise TypeError(f"Invalid node type: {type(t)}")
        else:
            targets = target  # type: ignore[assignment]
        return self.connections.create_connection(
            self,
            targets,
            connection_type=connection_type,
            priority=priority,
            name=name,
            delay=delay,
            queued=queued,
            queue_strategy=queue_strategy,
            transform=transform,
            filter_condition=filter_condition,
            stop_condition=stop_condition,
            exit_condition=exit_condition,
        )

    async def disconnect_all(self) -> None:
        """Disconnect from all nodes."""
        for target in list(self.connections.get_targets()):
            self.stop_passing_results_to(target)

    def stop_passing_results_to(self, other: MessageNode[Any, Any]) -> None:
        """Stop forwarding results to another node."""
        self.connections.disconnect(other)

    def _get_deps(self) -> TDeps | None:
        """Return dependencies for graph execution.

        Returns:
            The node's dependencies, or None if not configured.
        """
        return None

    def _build_single_node_graph(
        self,
    ) -> Any:
        """Build a single-node pydantic-graph wrapping this node.

        Returns:
            An immutable Graph ready for execution.
        """
        from agentpool.messaging.graph_adapter import MessageNodeStep

        return MessageNodeStep(self).build_single_node_graph()

    async def _execute_node(self, *prompts: Any, **kwargs: Any) -> ChatMessage[TResult]:
        """Core execution logic without graph wrapping.

        Subclasses that do not override :meth:`run` must implement this
        method. Subclasses that override :meth:`run` (the default for
        all existing agent types) do not need to implement this method.

        Args:
            *prompts: Input prompts.
            **kwargs: Additional execution arguments.

        Returns:
            The resulting ChatMessage.

        Raises:
            NotImplementedError: If neither ``run()`` nor ``_execute_node()``
                is overridden.
        """
        raise NotImplementedError(
            f"{self.__class__.__name__} must implement _execute_node() or override run() directly."
        )

    async def run(self, *prompts: Any, **kwargs: Any) -> ChatMessage[TResult]:
        """Execute node with prompts via pydantic-graph single-node graph.

        Builds a single-node graph and runs it to completion, wrapping the
        graph run with :class:`SignalEmittingGraphRun` so that
        ``message_received`` and ``message_sent`` signals are emitted at
        step boundaries. Subclasses may override this method to provide
        custom execution logic; in that case the graph-based path is
        bypassed.

        Args:
            *prompts: Input prompts.
            **kwargs: Additional execution arguments.

        Returns:
            The resulting ChatMessage.
        """
        from pydantic_graph.id_types import NodeID

        from agentpool.messaging.graph_adapter import AgentPoolState
        from agentpool.messaging.signal_adapter import SignalEmittingGraphRun

        graph = self._build_single_node_graph()
        state = AgentPoolState(node=self, prompts=prompts, kwargs=kwargs)
        node_mapping: dict[NodeID, MessageNode[Any, Any]] = {NodeID(self.name): self}
        async with graph.iter(state=state, deps=self._get_deps(), inputs=None) as graph_run:
            signal_run = SignalEmittingGraphRun(graph_run, node_mapping=node_mapping)
            async for _ in signal_run:
                pass
        return state.result  # type: ignore[return-value]

    async def run_stream(
        self,
        *prompts: Any,
        **kwargs: Any,
    ) -> AsyncIterator[RichAgentStreamEvent[TResult]]:
        """Run with streaming output via pydantic-graph Graph.iter().

        Uses :meth:`Graph.iter` to drive execution step-by-step, wrapping
        the graph run with :class:`SignalEmittingGraphRun` so that
        ``message_received`` and ``message_sent`` signals are emitted at
        step boundaries. For nodes that do not override :meth:`run_stream`
        (e.g. most agent subclasses), this yields the final result wrapped
        in a :class:`StreamCompleteEvent`. Agent subclasses typically
        override this with rich event streaming.

        Args:
            *prompts: Input prompts.
            **kwargs: Additional execution arguments.

        Yields:
            RichAgentStreamEvent tokens during execution.
        """
        from pydantic_graph.id_types import NodeID

        from agentpool.agents.events import RunErrorEvent, StreamCompleteEvent
        from agentpool.messaging.graph_adapter import AgentPoolState
        from agentpool.messaging.signal_adapter import SignalEmittingGraphRun

        graph = self._build_single_node_graph()
        state = AgentPoolState(node=self, prompts=prompts, kwargs=kwargs)
        node_mapping: dict[NodeID, MessageNode[Any, Any]] = {NodeID(self.name): self}

        async with graph.iter(state=state, deps=self._get_deps(), inputs=None) as graph_run:
            signal_run = SignalEmittingGraphRun(graph_run, node_mapping=node_mapping)
            async for _ in signal_run:
                # Generic nodes do not produce intermediate stream events;
                # drain the event queue in case a subclass pushed events.
                while not state.event_queue.empty():
                    try:
                        event = state.event_queue.get_nowait()
                        yield event
                        if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                            return
                    except asyncio.QueueEmpty:
                        break

        # Yield any remaining events after graph completion
        while not state.event_queue.empty():
            try:
                event = state.event_queue.get_nowait()
                yield event
                if isinstance(event, StreamCompleteEvent | RunErrorEvent):
                    return
            except asyncio.QueueEmpty:
                break

        # Yield the final result wrapped in StreamCompleteEvent
        if state.result is not None:
            yield StreamCompleteEvent(message=state.result)

    async def run_message(
        self,
        message: ChatMessage[Any],
        **kwargs: Any,
    ) -> ChatMessage[TResult]:
        """Run with an incoming ChatMessage (e.g., from Talk routing).

        Extracts content from the message, preserves session_id,
        and sets parent_id to track the message chain.

        Args:
            message: The incoming ChatMessage to process
            **kwargs: Additional arguments passed to run()

        Returns:
            Response ChatMessage with message chain tracked via parent_id
        """
        return await self.run(
            message.content,
            session_id=message.session_id,
            parent_id=message.message_id,
            **kwargs,
        )

    async def get_message_history(
        self, session_id: str | None = None, limit: int | None = None
    ) -> list[ChatMessage[Any]]:
        """Get message history from storage.

        Args:
            session_id: Optional session ID to query history for.
            limit: Maximum number of messages to return.

        Returns:
            List of chat messages from the session.
        """
        from agentpool_config.session import SessionQuery

        if not self.enable_db_logging or not self.storage or not session_id:
            return []
        query = SessionQuery(name=session_id, limit=limit)
        return await self.storage.filter_messages(query)

    async def log_message(self, message: ChatMessage[Any]) -> None:
        """Handle message from chat signal."""
        if self.enable_db_logging and self.storage:
            await self.storage.log_message(message)

    @abstractmethod
    async def get_stats(self) -> MessageStats | AggregatedMessageStats:
        """Get message statistics for this node."""

    @abstractmethod
    def run_iter(self, *prompts: Any, **kwargs: Any) -> AsyncIterator[ChatMessage[Any]]:
        """Yield messages during execution."""
