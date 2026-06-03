"""RunExecutor drives PydanticAI's ``agent.iter()`` + ``agent_run.next()`` loop.

Replaces bare ``async for node in agent_run:`` with explicit
``await agent_run.next(node)`` to ensure ``after_node_run`` capability
hooks fire. This is required for :class:`PendingMessageDrainCapability`
to drain ``asap`` and ``when_idle`` queued messages at the correct time.

The RunExecutor uses an isolated ``agent_iteration_task`` (background task)
to drive the PydanticAI run loop. Events are streamed through an async queue
that the consumer drains. This pattern preserves CancelScope safety: when
the consumer is cancelled, the background task gets a shielded cleanup window.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic_ai import CallToolsNode, FunctionToolCallEvent, ModelRequestNode
from pydantic_ai.exceptions import UndrainedPendingMessagesError
from pydantic_ai.messages import BaseToolCallPart, ToolCallPart
from pydantic_graph import End

from agentpool.agents.events import (
    RichAgentStreamEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallStartEvent,
)
from agentpool.agents.native_agent.helpers import (
    extract_text_from_messages,
    process_tool_event,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage, MessageHistory
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.native_agent.agent import Agent


logger = get_logger(__name__)


type RunExecutorEvent = RichAgentStreamEvent[Any]


class RunExecutor:
    """Drives a PydanticAI agent run using ``agent_run.next(node)``.

    Args:
        agent: The native Agent instance whose agentlet will be executed.
    """

    def __init__(self, agent: Agent[Any, Any]) -> None:
        self._agent = agent
        self._iteration_task: asyncio.Task[Any] | None = None

    async def execute(  # noqa: PLR0915
        self,
        *,
        prompts: list[Any],
        run_ctx: AgentRunContext,
        user_msg: ChatMessage[Any],
        message_history: MessageHistory,
        message_id: str,
        session_id: str,
        _parent_id: str | None = None,
        input_provider: Any | None = None,
        deps: Any | None = None,
    ) -> AsyncIterator[RunExecutorEvent]:
        """Execute the agent run and yield streaming events.

        Yields events in the following order:
        1. ``RunStartedEvent``
        2. ``PartStartEvent`` / ``PartDeltaEvent`` from ModelRequestNode
        3. ``ToolCallStartEvent`` / ``ToolCallCompleteEvent`` from CallToolsNode
        4. ``StreamCompleteEvent`` with the final message

        The iteration runs in a background task so that cancellation of the
        consumer does not immediately tear down the PydanticAI run context,
        giving ``PendingMessageDrainCapability`` a chance to clean up.

        Args:
            prompts: Pre-converted PydanticAI UserContent prompts.
            run_ctx: Per-run isolated context (cancellation, event queue, etc.).
            user_msg: The original user message for this turn.
            message_history: Conversation history (used to build message_history
                passed to the agentlet).
            message_id: Message ID for the assistant response.
            session_id: Session ID for event routing.
            parent_id: Optional parent message ID for threading.
            input_provider: Optional input provider for confirmations.
            deps: Optional user dependencies.

        Yields:
            ``RichAgentStreamEvent`` tokens in execution order.

        Raises:
            RuntimeError: If the stream completes without producing a result.
        """
        import time

        run_id = str(uuid4())
        start_time = time.perf_counter()

        yield RunStartedEvent(
            session_id=session_id,
            run_id=run_id,
            agent_name=self._agent.name,
        )

        # Build agentlet from current agent state
        agentlet = await self._agent.get_agentlet(
            None,
            self._agent._output_type,
            input_provider,
            run_ctx,
        )
        agent_deps = self._agent.get_context(
            input_provider=input_provider,
            run_ctx=run_ctx,
        )
        if deps is not None:
            agent_deps.data = deps

        # Strip the user message if it is already the last entry in history
        # (it will be re-added by PydanticAI from the prompts)
        history_list = message_history.get_history()
        if history_list and history_list[-1] is user_msg:
            history_list = history_list[:-1]
        history = [m for run in history_list for m in run.to_pydantic_ai()]

        event_queue: asyncio.Queue[RunExecutorEvent | None] = asyncio.Queue()
        iteration_error: BaseException | None = None
        response_msg: ChatMessage[Any] | None = None

        async def agent_iteration_task() -> None:
            """Background task that drives ``agentlet.iter()`` with ``next()``.

            Pushes all node-level events onto *event_queue*. A sentinel
            ``None`` is pushed when the run finishes or errors.
            """
            nonlocal iteration_error, response_msg
            pending_tcs: dict[str, BaseToolCallPart] = {}

            try:
                async with agentlet.iter(
                    prompts,
                    deps=agent_deps,
                    message_history=history,
                    usage_limits=self._agent._default_usage_limits,
                ) as agent_run:
                    node = agent_run.next_node

                    while True:
                        if run_ctx.cancelled:
                            logger.debug("Run cancelled, breaking iteration loop")
                            break

                        if isinstance(node, End):
                            break

                        if isinstance(node, ModelRequestNode | CallToolsNode):
                            async with node.stream(agent_run.ctx) as stream:
                                async for event in stream:
                                    if run_ctx.cancelled:
                                        break

                                    # Map FunctionToolCallEvent -> ToolCallStartEvent
                                    if isinstance(event, FunctionToolCallEvent):
                                        tool_part = event.part
                                        if isinstance(tool_part, ToolCallPart):
                                            await event_queue.put(
                                                ToolCallStartEvent(
                                                    tool_call_id=tool_part.tool_call_id,
                                                    tool_name=tool_part.tool_name,
                                                    title=f"Executing: {tool_part.tool_name}",
                                                    raw_input=safe_args_as_dict(
                                                        tool_part,
                                                        default={},
                                                    ),
                                                )
                                            )

                                    # Raw PydanticAI event (backward compat)
                                    await event_queue.put(event)

                                    # process_tool_event handles ToolCallCompleteEvent
                                    combined = await process_tool_event(
                                        self._agent.name,
                                        event,
                                        pending_tcs,
                                        message_id,
                                        run_ctx,
                                    )
                                    if combined is not None:
                                        await event_queue.put(combined)

                        node = await agent_run.next(node)

                        if isinstance(node, End):
                            break

                # Build final response message
                if run_ctx.cancelled:
                    partial_content = extract_text_from_messages(
                        agent_run.all_messages(),
                        include_interruption_note=True,
                    )
                    response_msg = ChatMessage(
                        content=partial_content,
                        role="assistant",
                        name=self._agent.name,
                        message_id=message_id,
                        session_id=session_id,
                        parent_id=user_msg.message_id,
                        response_time=time.perf_counter() - start_time,
                        finish_reason="stop",
                    )
                elif agent_run.result:
                    response_msg = await ChatMessage.from_run_result(
                        agent_run.result,
                        agent_name=self._agent.name,
                        message_id=message_id,
                        session_id=session_id,
                        parent_id=user_msg.message_id,
                        response_time=time.perf_counter() - start_time,
                        metadata=None,
                    )
                else:
                    msg = "Stream completed without producing a result"
                    raise RuntimeError(msg)  # noqa: TRY301

            except asyncio.CancelledError:
                logger.debug("Agent iteration task cancelled")
                raise
            except UndrainedPendingMessagesError as exc:
                logger.warning(
                    "UndrainedPendingMessagesError caught — "
                    "pending messages may have been dropped",
                    error=str(exc),
                )
                iteration_error = exc
            except BaseException as exc:
                logger.exception("Agent iteration failed")
                iteration_error = exc
            finally:
                await event_queue.put(None)

        self._iteration_task = asyncio.create_task(agent_iteration_task())

        try:
            while True:
                try:
                    event = await asyncio.wait_for(
                        event_queue.get(),
                        timeout=0.1,
                    )
                except TimeoutError:
                    current = asyncio.current_task()
                    if current is not None and current.cancelling() > 0:
                        raise asyncio.CancelledError from None
                    if run_ctx.cancelled:
                        break
                    continue

                if event is None:
                    break
                yield event

        finally:
            if self._iteration_task is not None and not self._iteration_task.done():
                self._iteration_task.cancel()
                with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                    await asyncio.wait_for(
                        asyncio.shield(self._iteration_task),
                        timeout=2.0,
                    )
            self._iteration_task = None

        if iteration_error is not None:
            raise iteration_error

        if response_msg is not None:
            yield StreamCompleteEvent(message=response_msg)
