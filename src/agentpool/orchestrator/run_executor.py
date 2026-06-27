"""RunExecutor drives PydanticAI's ``agent.iter()`` + ``agent_run.next()`` loop.

Replaces bare ``async for node in agent_run:`` with explicit
``await agent_run.next(node)`` to ensure ``after_node_run`` capability
hooks fire. This is required for :class:`PendingMessageDrainCapability`
to drain ``asap`` and ``when_idle`` queued messages at the correct time.

Events are published directly to an :class:`EventBus` via ``publish()``.
The caller subscribes to the EventBus to receive events. This fire-and-forget
pattern relies on ``anyio.create_task_group()`` cancellation for teardown.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import anyio
from pydantic_ai import CallToolsNode, FunctionToolCallEvent, ModelRequestNode
from pydantic_ai.exceptions import UndrainedPendingMessagesError
from pydantic_ai.messages import BaseToolCallPart, PartStartEvent, ToolCallPart
from pydantic_graph import End

from agentpool.agents.events import (
    RunErrorEvent,
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
from agentpool.tasks.exceptions import RunAbortedError
from agentpool.tools.base import is_terminal_tool
from agentpool.utils.pydantic_ai_helpers import safe_args_as_dict


if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.native_agent.agent import Agent
    from agentpool.orchestrator.core import EventBus


logger = get_logger(__name__)


class RunExecutor:
    """Drives a PydanticAI agent run using ``agent_run.next(node)``.

    Args:
        agent: The native Agent instance whose agentlet will be executed.
    """

    def __init__(self, agent: Agent[Any, Any]) -> None:
        self._agent = agent
        self._iteration_task: asyncio.Task[Any] | None = None

    async def execute(  # noqa: PLR0915, C901
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
        event_bus: EventBus,
    ) -> ChatMessage[Any]:
        """Execute the agent run and publish events to the EventBus.

        Events are published in the following order:
        1. ``RunStartedEvent``
        2. ``PartStartEvent`` / ``PartDeltaEvent`` from ModelRequestNode
        3. ``ToolCallStartEvent`` / ``ToolCallCompleteEvent`` from CallToolsNode
        4. ``StreamCompleteEvent`` with the final message

        The iteration runs inside ``anyio.create_task_group()``. If the
        task group is cancelled, all child tasks (including PydanticAI
        iteration) are cancelled, which is the desired behavior.

        Args:
            prompts: Pre-converted PydanticAI UserContent prompts.
            run_ctx: Per-run isolated context (cancellation, etc.).
            user_msg: The original user message for this turn.
            message_history: Conversation history passed to the agentlet.
            message_id: Message ID for the assistant response.
            session_id: Session ID for event routing.
            parent_id: Optional parent message ID for threading.
            input_provider: Optional input provider for confirmations.
            deps: Optional user dependencies.
            event_bus: EventBus to publish events to.

        Returns:
            The final ``ChatMessage`` response from the agent run.

        Raises:
            RuntimeError: If the stream completes without producing a result.
            BaseException: Re-raises any error from the agent iteration.
        """
        import time

        if self._iteration_task is not None and not self._iteration_task.done():
            logger.warning(
                "Concurrent RunExecutor.execute() call detected — "
                "a previous execution is still in progress"
            )

        run_id = str(uuid4())
        start_time = time.perf_counter()

        def _make_interrupted_msg() -> ChatMessage[Any]:
            return ChatMessage(
                content="[Interrupted]",
                role="assistant",
                name=self._agent.name,
                message_id=message_id,
                session_id=session_id,
                parent_id=user_msg.message_id,
                response_time=time.perf_counter() - start_time,
                finish_reason="stop",
            )

        iteration_error: BaseException | None = None
        response_msg: ChatMessage[Any] | None = None
        iteration_messages: list[Any] | None = None

        try:
            await event_bus.publish(
                session_id,
                RunStartedEvent(
                    run_id=run_id,
                    agent_name=self._agent.name,
                    session_id=session_id,
                    parent_session_id=_parent_id,
                ),
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

            async def agent_iteration_task(steer_prompts: list[str] | None = None) -> None:
                """Background task that drives ``agentlet.iter()`` with ``next()``.

                Publishes all node-level events directly to *event_bus*.
                """
                nonlocal iteration_error, response_msg, iteration_messages
                # Capture the current task for _interrupt() compatibility.
                # execute() no longer yields events, so the caller can't set
                # _iteration_task from outside. The task must self-register.
                _task = asyncio.current_task()
                if _task is not None:
                    self._iteration_task = _task
                    self._agent._iteration_task = _task
                pending_tcs: dict[str, BaseToolCallPart] = {}
                emitted_tool_starts: set[str] = set()
                terminal_tool_completed = False

                # Pre-compute tool kind lookup for ToolCallStartEvent
                _tool_kind_map: dict[str, str] = {}
                _terminal_tool_names: set[str] = set()
                try:
                    all_agent_tools = await self._agent.tools.get_tools()
                    for t in all_agent_tools:
                        if t.category:
                            _tool_kind_map[t.name] = t.category
                        if is_terminal_tool(t):
                            _terminal_tool_names.add(t.name)
                except Exception:
                    logger.debug("Failed to build tool kind map", exc_info=True)

                try:
                    async with agentlet.iter(
                        steer_prompts if steer_prompts is not None else prompts,
                        deps=agent_deps,
                        message_history=history,
                        usage_limits=self._agent._default_usage_limits,
                    ) as agent_run:
                        if run_ctx._run_handle is not None:
                            run_ctx._run_handle.active_agent_run = agent_run
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
                                                if tool_part.tool_call_id not in emitted_tool_starts:
                                                    emitted_tool_starts.add(tool_part.tool_call_id)
                                                    tool_kind = _tool_kind_map.get(
                                                        tool_part.tool_name, "other"
                                                    )
                                                    await event_bus.publish(
                                                        session_id,
                                                        ToolCallStartEvent(
                                                            tool_call_id=tool_part.tool_call_id,
                                                            tool_name=tool_part.tool_name,
                                                            title=f"Executing: {tool_part.tool_name}",
                                                            kind=tool_kind,  # type: ignore[arg-type]
                                                            raw_input=safe_args_as_dict(
                                                                tool_part,
                                                                default={},
                                                            ),
                                                        ),
                                                    )
                                        elif isinstance(event, PartStartEvent) and isinstance(
                                            event.part, BaseToolCallPart
                                        ):
                                            tool_part = event.part
                                            if tool_part.tool_call_id not in emitted_tool_starts:
                                                emitted_tool_starts.add(tool_part.tool_call_id)
                                                tool_kind = _tool_kind_map.get(
                                                    tool_part.tool_name, "other"
                                                )
                                                await event_bus.publish(
                                                    session_id,
                                                    ToolCallStartEvent(
                                                        tool_call_id=tool_part.tool_call_id,
                                                        tool_name=tool_part.tool_name,
                                                        title=f"Executing: {tool_part.tool_name}",
                                                        kind=tool_kind,  # type: ignore[arg-type]
                                                        raw_input=safe_args_as_dict(
                                                            tool_part,
                                                            default={},
                                                        ),
                                                    ),
                                                )

                                        # Raw PydanticAI event (backward compat)
                                        await event_bus.publish(session_id, event)

                                        # process_tool_event handles ToolCallCompleteEvent
                                        combined = await process_tool_event(
                                            self._agent.name,
                                            event,
                                            pending_tcs,
                                            message_id,
                                            run_ctx,
                                        )
                                        if combined is not None:
                                            await event_bus.publish(session_id, combined)
                                            if combined.tool_name in _terminal_tool_names:
                                                run_ctx.terminal_tool_name = combined.tool_name
                                                run_ctx.terminal_tool_result = combined.tool_result
                                                terminal_tool_completed = True
                                                break

                                    if terminal_tool_completed:
                                        break

                            if terminal_tool_completed:
                                break

                            node = await agent_run.next(node)

                            if isinstance(node, End):
                                break

                        iteration_messages = agent_run.all_messages()

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
                    elif run_ctx.terminal_tool_name:
                        response_msg = ChatMessage(
                            content=(
                                str(run_ctx.terminal_tool_result)
                                if run_ctx.terminal_tool_result is not None
                                else ""
                            ),
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

                except RunAbortedError:
                    logger.debug("Run aborted by user — treating as graceful cancellation")
                    run_ctx.cancelled = True
                    # Do NOT set iteration_error — route to graceful completion path
                except asyncio.CancelledError:
                    logger.debug("Agent iteration task cancelled")
                    raise
                except UndrainedPendingMessagesError as exc:
                    logger.warning(
                        "UndrainedPendingMessagesError caught — pending messages may have been dropped",
                        error=str(exc),
                    )
                    iteration_error = exc
                except BaseException as exc:
                    logger.exception("Agent iteration failed")
                    iteration_error = exc
                finally:
                    self._iteration_task = None
                    self._agent._iteration_task = None
                    if run_ctx._run_handle is not None:
                        run_ctx._run_handle.active_agent_run = None

            async with anyio.create_task_group() as tg:
                tg.start_soon(agent_iteration_task)

            # Re-raise iteration errors so the outer except can publish RunErrorEvent
            if iteration_error is not None:
                raise iteration_error

            # Fallback: cancelled before any response was produced (e.g. RunAbortedError)
            if response_msg is None:
                response_msg = _make_interrupted_msg()
                await event_bus.publish(
                    session_id,
                    StreamCompleteEvent(message=response_msg, cancelled=True),
                )
                return response_msg

            # === RE-ITERATION LOOP ===
            while True:
                if run_ctx.cancelled:
                    break
                if bool(run_ctx.child_done_events):
                    events = list(run_ctx.child_done_events.values())  # snapshot before await
                    for ev in events:
                        await ev.wait()
                    if run_ctx.cancelled:
                        break
                if not run_ctx.queued_steer_messages:
                    break
                # Re-iterate with queued steer messages
                steer_msgs = run_ctx.queued_steer_messages.copy()
                run_ctx.queued_steer_messages.clear()
                run_ctx.child_done_events.clear()
                if iteration_messages:
                    history = iteration_messages
                iteration_messages = None
                async with anyio.create_task_group() as tg:
                    tg.start_soon(agent_iteration_task, steer_msgs)
                if iteration_error is not None:
                    raise iteration_error  # noqa: TRY301
                if response_msg is None:
                    response_msg = _make_interrupted_msg()
                    break

            await event_bus.publish(
                session_id,
                StreamCompleteEvent(message=response_msg, cancelled=run_ctx.cancelled),
            )
            return response_msg

        except RunAbortedError:
            logger.debug("Run aborted by user — treating as graceful cancellation")
            run_ctx.cancelled = True
            if response_msg is None:
                response_msg = _make_interrupted_msg()
            await event_bus.publish(
                session_id,
                StreamCompleteEvent(message=response_msg, cancelled=True),
            )
            return response_msg
        except asyncio.CancelledError:
            if response_msg is None:
                response_msg = _make_interrupted_msg()
            await event_bus.publish(
                session_id,
                StreamCompleteEvent(message=response_msg, cancelled=True),
            )
            raise
        except BaseException as exc:
            await event_bus.publish(
                session_id,
                RunErrorEvent(
                    message=str(exc),
                    agent_name=self._agent.name,
                ),
            )
            raise
