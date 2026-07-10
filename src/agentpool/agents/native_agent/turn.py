"""NativeTurn wraps pydantic-ai iter/next cycle into a single reactive Turn.

Provides a :class:`Turn` subclass that drives ``agentlet.iter()`` +
``agent_run.next()`` and yields :class:`RichAgentStreamEvent` via
:class:`EventMapper`.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic_ai import CallToolsNode, ModelRequestNode
from pydantic_ai.exceptions import UndrainedPendingMessagesError
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage
from pydantic_graph import End

from agentpool.agents.events.events import (
    RunErrorEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
)
from agentpool.agents.native_agent.helpers import extract_text_from_messages
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.messaging.messages import TokenCost
from agentpool.orchestrator.event_mapper import EventMapper
from agentpool.orchestrator.turn import HookAwareTurn, Turn
from agentpool.tasks.exceptions import RunAbortedError
from agentpool.tools.base import is_terminal_tool


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pydantic_ai import PydanticAgent
    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.agents.native_agent.agent import Agent
    from agentpool.hooks import AgentHooks


logger = get_logger(__name__)


class NativeTurn(HookAwareTurn, Turn):
    """Wraps pydantic-ai iter/next cycle into a single reactive Turn.

    Drives the pydantic-ai ``agent.iter()`` + ``agent_run.next()`` loop,
    mapping stream events to :class:`RichAgentStreamEvent` via
    :class:`EventMapper`.  After execution, :attr:`message_history`
    and :attr:`final_message` become available.

    Attributes:
        _agent: The native Agent instance whose agentlet will be executed.
        _prompts: Pre-converted prompt strings for this turn.
        _run_ctx: Per-run isolated context (cancellation, deps, etc.).
        _message_history_input: Incoming message history as pydantic-ai
            ModelMessage list.
        _message_id: Unique ID for the assistant response message.
    """

    def __init__(
        self,
        agent: Agent[Any, Any],
        prompts: list[str],
        run_ctx: AgentRunContext,
        message_history: list[ModelMessage],
        parent_id: str | None = None,
        hooks: AgentHooks | None = None,
    ) -> None:
        """Initialize the turn.

        Args:
            agent: The native Agent whose agentlet will be executed.
            prompts: Pre-converted prompt strings for this turn.
            run_ctx: Per-run isolated context (cancellation, deps, etc.).
            message_history: Incoming message history as pydantic-ai
                ModelMessage list.
            parent_id: Optional parent message ID for threading.
            hooks: Optional AgentHooks for pre_turn/post_turn hook firing.
        """
        super().__init__()
        self._agent = agent
        self._prompts = prompts
        self._run_ctx = run_ctx
        self._message_history_input = message_history
        self._input_history_len = len(message_history)
        self._message_id = uuid4().hex
        self._parent_id = parent_id
        self._hooks = hooks

    @property
    def _hook_env(self) -> Any | None:
        """Execution environment for command hooks."""
        return self._agent.env

    @property
    def _hook_agent_name(self) -> str:
        """Agent name passed to hook invocations."""
        return self._agent.name

    @property
    def _hook_prompt(self) -> str:
        """The user prompt for this turn."""
        return str(self._prompts)

    async def execute(self) -> AsyncGenerator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
        """Execute one reactive cycle of the pydantic-ai agent loop.

        Yields:
            Stream events during execution (text deltas, tool calls,
            lifecycle notifications).

        Raises:
            asyncio.CancelledError: If the turn is cancelled mid-execution.
        """
        turn_start = time.perf_counter()
        try:
            # Fire pre_turn hooks. If denied, cancel the turn immediately.
            pre_turn_result = await self._fire_pre_turn_hooks()
            if pre_turn_result is not None and pre_turn_result.get("decision") == "deny":
                self._run_ctx.cancelled = True
                self._final_message = ChatMessage(content="", role="assistant")
                yield StreamCompleteEvent(message=self._final_message, cancelled=True)
                return

            agentlet: PydanticAgent[Any, Any] = await self._agent.get_agentlet(
                model=None,
                output_type=None,
                run_ctx=self._run_ctx,
            )

            mapper = EventMapper(
                agent_name=self._agent.name,
                message_id=self._message_id,
            )

            terminal_tool_names: set[str] = set()
            try:
                # Use timeout to prevent hang when MCP providers are still
                # connecting (e.g. ACP session/load hasn't arrived yet).
                # MCP tools are handled via snapshot/get_capabilities path,
                # so get_tools() here is only for building tool kind map.
                all_tools = await asyncio.wait_for(
                    self._agent._get_all_tools(),
                    timeout=5.0,
                )
                for tool in all_tools:
                    if tool.category:
                        mapper.tool_kind_map[tool.name] = tool.category
                    if is_terminal_tool(tool):
                        terminal_tool_names.add(tool.name)
            except TimeoutError:
                logger.warning(
                    "get_tools() timed out after 5s, skipping tool kind map",
                    agent=self._agent.name,
                )
            except Exception:  # noqa: BLE001
                logger.debug("Failed to build tool kind map", exc_info=True)

            agent_deps = self._agent.get_context(
                input_provider=None,
                run_ctx=self._run_ctx,
            )
            if self._run_ctx.deps is not None:
                agent_deps.data = self._run_ctx.deps

            # Consume staged_content (e.g. skill instructions injected by
            # skill_bridge) and prepend to prompts. This mirrors the old
            # run_stream() path which did the same before calling agentlet.iter().
            # Without this, skill instructions are silently discarded.
            staged_text = await self._agent.staged_content.consume_as_text()
            if staged_text is not None:
                user_request = "\n\n".join(self._prompts)
                effective_prompts = (
                    [f"{staged_text}\n\n{user_request}"] if user_request else [staged_text]
                )
            else:
                effective_prompts = self._prompts

            agent_run: Any = None
            try:
                async with agentlet.iter(
                    effective_prompts,
                    deps=agent_deps,
                    message_history=self._message_history_input,
                    usage_limits=self._agent._default_usage_limits,
                ) as agent_run:
                    if self._run_ctx._run_handle is not None:
                        self._run_ctx._run_handle.active_agent_run = agent_run

                    node = agent_run.next_node

                    while not isinstance(node, End):
                        if self._run_ctx.cancelled:
                            break

                        if isinstance(node, ModelRequestNode | CallToolsNode):
                            terminal_tool_completed = False
                            # Cooperative cancellation is handled via run_ctx.cancelled
                            # checked on every streaming chunk below.
                            try:
                                async with node.stream(agent_run.ctx) as stream:
                                    async for event in stream:
                                        if self._run_ctx.cancelled:
                                            break

                                        mapped = mapper.map_event(event)
                                        if mapped is not None:
                                            yield mapped

                                        if (
                                            isinstance(mapped, ToolCallCompleteEvent)
                                            and mapped.tool_name in terminal_tool_names
                                        ):
                                            self._run_ctx.terminal_tool_name = mapped.tool_name
                                            self._run_ctx.terminal_tool_result = mapped.tool_result
                                            terminal_tool_completed = True
                                            break
                            finally:
                                self._agent._iteration_task = None

                            logger.info("Node stream ended", node_type=type(node).__name__)

                            if terminal_tool_completed:
                                break

                        if self._run_ctx.cancelled:
                            break

                        node_type = type(node).__name__
                        logger.info("Advancing agent_run.next()", node_type=node_type)
                        try:
                            iteration_task = asyncio.create_task(agent_run.next(node))
                            self._agent._iteration_task = iteration_task
                            node = await iteration_task
                            logger.info(
                                "agent_run.next() completed",
                                next_node_type=type(node).__name__,
                            )
                        finally:
                            self._agent._iteration_task = None

                    self._message_history = agent_run.all_messages()
                    logger.info("After while loop — building final message")

            except RunAbortedError:
                logger.info("RunAbortedError caught")
                if agent_run is not None:
                    try:
                        self._message_history = agent_run.all_messages()
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "Could not retrieve agent_run messages after RunAbortedError",
                        )

            except UndrainedPendingMessagesError as exc:
                logger.info("UndrainedPendingMessagesError caught", error=str(exc))
                if agent_run is not None:
                    with contextlib.suppress(Exception):
                        self._message_history = agent_run.all_messages()

            except asyncio.CancelledError:
                if self._run_ctx.cancelled:
                    # Cancellation came from cancel() — exit gracefully
                    # without yielding StreamCompleteEvent. Set _final_message
                    # so turn.final_message doesn't raise for callers.
                    # Capture _message_history from agent_run so the cancelled
                    # turn's partial messages are preserved for the next turn.
                    if agent_run is not None:
                        with contextlib.suppress(Exception):
                            self._message_history = agent_run.all_messages()
                    self._final_message = ChatMessage(
                        content="",
                        role="assistant",
                        name=self._agent.name,
                        message_id=self._message_id,
                        session_id=self._run_ctx.session_id,
                        parent_id=self._parent_id,
                    )
                    return
                raise

            except Exception as exc:
                logger.exception("NativeTurn execution failed")
                yield RunErrorEvent(
                    message=str(exc),
                    agent_name=self._agent.name,
                    run_id=self._run_ctx.run_id,
                )
                return

            finally:
                if self._run_ctx._run_handle is not None:
                    self._run_ctx._run_handle.active_agent_run = None

            # Build final message always (even when cancelled) so that
            # turn.final_message is accessible to callers after execute()
            # returns. When cancelled via cancel(), we skip yielding
            # StreamCompleteEvent to avoid double turn_complete (end_turn
            # + cancelled).
            if self._message_history is not None:
                # Only extract text from messages generated in THIS turn,
                # not from the input history (which may contain previous
                # assistant responses that would pollute the content).
                # Use agent_run.new_messages() which returns only messages
                # generated during this run, avoiding issues with shared
                # state in concurrent runs.
                if agent_run is not None:
                    new_messages = agent_run.new_messages()
                else:
                    new_messages = self._message_history[self._input_history_len :]
                content: Any = extract_text_from_messages(new_messages)
                if agent_run is not None:
                    try:
                        run_result = agent_run.result
                        if run_result is not None:
                            structured = getattr(run_result, "output", None)
                            if structured is not None and not isinstance(structured, str):
                                content = structured
                    except Exception:  # noqa: BLE001
                        logger.debug(
                            "Failed to extract structured result from agent run",
                            exc_info=True,
                        )
            else:
                content = ""

            # Extract cost_info and usage from agent_run so downstream consumers
            # (Talk stats, storage, ACP event converter) can track token usage.
            cost_info: TokenCost | None = None
            request_usage: RequestUsage | None = None
            if agent_run is not None:
                try:
                    run_usage = agent_run.usage
                    cost_info = await TokenCost.from_usage(
                        usage=run_usage,
                        model=self._agent.model_name or "",
                    )
                    # Extract RequestUsage from the last ModelResponse in new_messages.
                    # agent_run.usage is RunUsage (cumulative), but ChatMessage.usage
                    # expects RequestUsage (per-request) for ACP/OpenCode converters.
                    for msg in reversed(new_messages):
                        if isinstance(msg, ModelResponse):
                            request_usage = msg.usage
                            break
                except Exception:  # noqa: BLE001
                    logger.debug("Failed to extract usage from agent run", exc_info=True)

            self._final_message = ChatMessage(
                content=content,
                role="assistant",
                name=self._agent.name,
                message_id=self._message_id,
                session_id=self._run_ctx.session_id,
                parent_id=self._parent_id,
                cost_info=cost_info,
                usage=request_usage or RequestUsage(),
                response_time=time.perf_counter() - self._run_ctx.start_time,
                messages=new_messages if agent_run is not None else [],
            )

            # Belt-and-suspenders: if cancelled during execution (e.g.
            # CancelledError swallowed by pydantic-ai inside agent_run.next()),
            # exit without yielding StreamCompleteEvent.
            if self._run_ctx.cancelled:
                logger.info("Skipping StreamCompleteEvent — run_ctx.cancelled is True")
                return

            logger.info("Yielding StreamCompleteEvent")
            yield StreamCompleteEvent(message=self._final_message)
        finally:
            # Fire post_turn hooks even on error/cancellation, with
            # per-turn elapsed time.
            # _final_message may be None if the turn errored before
            # producing one — pass it as-is.
            duration_ms = (time.perf_counter() - turn_start) * 1000
            await self._fire_post_turn_hooks(self._final_message, duration_ms=duration_ms)
