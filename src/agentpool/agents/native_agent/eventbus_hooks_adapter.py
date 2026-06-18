"""EventBus adapter for pydantic-ai Hooks capability.

Bridges pydantic-ai lifecycle hooks to AgentPool's EventBus pub/sub system.
Uses composition to avoid inheriting from Hooks directly (Hooks.__init__
has 20+ parameters).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import uuid

from pydantic_ai import AgentRunResult
from pydantic_ai.capabilities import Hooks
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.tools import RunContext, ToolDefinition

from agentpool.agents.context import AgentContext
from agentpool.agents.events import RunStartedEvent
from agentpool.orchestrator.core import EventBus


if TYPE_CHECKING:
    from pydantic_ai.capabilities.abstract import ValidatedToolArgs


class EventBusHooksAdapter:
    """Wraps a Hooks capability, publishing lifecycle events to EventBus.

    Uses composition instead of inheriting Hooks directly to avoid
    __init__ signature conflicts (Hooks has 20+ hook parameters).

    Bridged events:
    - ``before_run`` -> :class:`RunStartedEvent`
    - ``before_tool_execute`` -> :class:`ToolCallStartEvent`
    - ``after_tool_execute`` -> :class:`ToolCallCompleteEvent`

    ``after_run`` delegates to the original hook but does **not** publish a
    separate completion event because :class:`StreamCompleteEvent` is already
    emitted by the agent streaming pipeline.
    """

    def __init__(self, hooks: Hooks[Any], event_bus: EventBus) -> None:
        """Initialize the adapter.

        Args:
            hooks: The pydantic-ai Hooks capability to wrap.
            event_bus: The AgentPool EventBus to publish events to.
        """
        self._hooks = hooks
        self._event_bus = event_bus

    def _get_session_id(self, ctx: RunContext[AgentContext[Any]]) -> str | None:
        """Extract session ID from RunContext.

        Args:
            ctx: The pydantic-ai run context.

        Returns:
            The session ID if available, otherwise None.
        """
        agent_ctx = ctx.deps
        if agent_ctx is not None and agent_ctx.run_ctx is not None:
            return agent_ctx.run_ctx.session_id
        return None

    def as_capability(self) -> Hooks:
        """Return a Hooks capability that delegates to wrapped hooks + EventBus.

        Returns:
            A new Hooks instance with wrapped lifecycle callbacks.
        """
        # Build a Hooks instance with our wrapped hooks
        new_hooks = Hooks(
            before_run=self._wrap_before_run(),
            after_run=self._wrap_after_run(),
            before_tool_execute=self._wrap_before_tool_execute(),
            after_tool_execute=self._wrap_after_tool_execute(),
            ordering=self._hooks.get_ordering(),
        )

        # Copy all other hook entries from the original Hooks so they
        # continue to fire transparently. This avoids listing all 30+
        # constructor parameters and is future-proof against new hooks.
        for key, entries in self._hooks._registry.items():
            if key not in {"before_run", "after_run", "before_tool_execute", "after_tool_execute"}:
                new_hooks._registry.setdefault(key, []).extend(entries)

        return new_hooks

    def _wrap_before_run(self):
        """Wrap before_run hook to publish RunStartedEvent."""
        original = self._hooks.before_run

        async def wrapped(ctx: RunContext[AgentContext[Any]]) -> None:
            session_id = self._get_session_id(ctx)
            if session_id:
                await self._event_bus.publish(
                    session_id,
                    RunStartedEvent(
                        session_id=session_id,
                        run_id=str(uuid.uuid4()),
                        agent_name=ctx.deps.node_name if ctx.deps else None,
                    ),
                )
            if original is not None:
                await original(ctx)

        return wrapped

    def _wrap_after_run(self):
        """Wrap after_run hook to delegate to original.

        Does not publish a separate completion event because
        StreamCompleteEvent is already emitted by the streaming pipeline.
        """
        original = self._hooks.after_run

        async def wrapped(
            ctx: RunContext[AgentContext[Any]], *, result: AgentRunResult[Any]
        ) -> AgentRunResult[Any]:
            if original is not None:
                return await original(ctx, result=result)
            return result

        return wrapped

    def _wrap_before_tool_execute(self):
        """Wrap before_tool_execute hook as transparent passthrough.

        ToolCallStartEvent is now produced by RunExecutor, making
        EventBus publication here redundant.
        """
        original = self._hooks.before_tool_execute

        async def wrapped(
            ctx: RunContext[AgentContext[Any]],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
        ) -> ValidatedToolArgs:
            if original is not None:
                return await original(ctx, call=call, tool_def=tool_def, args=args)
            return args

        return wrapped

    def _wrap_after_tool_execute(self):
        """Wrap after_tool_execute hook as transparent passthrough.

        ToolCallCompleteEvent is now produced by the stream path via
        process_tool_event() and enqueued by the caller, making
        EventBus publication here redundant.
        """
        original = self._hooks.after_tool_execute

        async def wrapped(
            ctx: RunContext[AgentContext[Any]],
            *,
            call: ToolCallPart,
            tool_def: ToolDefinition,
            args: ValidatedToolArgs,
            result: Any,
        ) -> Any:
            if original is not None:
                return await original(ctx, call=call, tool_def=tool_def, args=args, result=result)
            return result

        return wrapped
