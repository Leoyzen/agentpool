"""NativeTurn wraps pydantic-ai iter/next cycle into a single reactive Turn.

Provides a :class:`Turn` subclass that drives ``agentlet.iter()`` +
``agent_run.next()`` and yields :class:`RichAgentStreamEvent` via
:class:`EventMapper`.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from pydantic_ai import CallToolsNode, ModelRequestNode
from pydantic_ai.exceptions import UndrainedPendingMessagesError
from pydantic_graph import End

from agentpool.agents.events.events import (
    RunErrorEvent,
    RunStartedEvent,
    StreamCompleteEvent,
    ToolCallCompleteEvent,
)
from agentpool.agents.native_agent.helpers import extract_text_from_messages
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.orchestrator.event_mapper import EventMapper
from agentpool.orchestrator.turn import Turn
from agentpool.tasks.exceptions import RunAbortedError
from agentpool.tools.base import is_terminal_tool


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.agents.native_agent.agent import Agent


logger = get_logger(__name__)


class NativeTurn(Turn):
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
    ) -> None:
        """Initialize the turn.

        Args:
            agent: The native Agent whose agentlet will be executed.
            prompts: Pre-converted prompt strings for this turn.
            run_ctx: Per-run isolated context (cancellation, deps, etc.).
            message_history: Incoming message history as pydantic-ai
                ModelMessage list.
        """
        super().__init__()
        self._agent = agent
        self._prompts = prompts
        self._run_ctx = run_ctx
        self._message_history_input = message_history
        self._message_id = uuid4().hex

    async def execute(self) -> AsyncGenerator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
        """Execute one reactive cycle of the pydantic-ai agent loop.

        Yields:
            Stream events during execution (text deltas, tool calls,
            lifecycle notifications).

        Raises:
            asyncio.CancelledError: If the turn is cancelled mid-execution.
        """
        agentlet = await self._agent.get_agentlet(
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
            all_tools = await self._agent.tools.get_tools()
            for tool in all_tools:
                if tool.category:
                    mapper.tool_kind_map[tool.name] = tool.category
                if is_terminal_tool(tool):
                    terminal_tool_names.add(tool.name)
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
        effective_prompts = (
            [*staged_text.split("\n\n"), *self._prompts]
            if staged_text is not None
            else self._prompts
        )
        # If staged_content was consumed, combine it into a single prompt
        # with the user request (matching the old run_stream pattern).
        if staged_text is not None:
            user_request = "\n\n".join(self._prompts)
            effective_prompts = [f"{staged_text}\n\n{user_request}"] if user_request else [staged_text]

        agent_run: Any = None
        try:
            yield RunStartedEvent(
                agent_name=self._agent.name,
                run_id=self._run_ctx.run_id or self._message_id,
                session_id=self._run_ctx.session_id,
            )
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

                        if terminal_tool_completed:
                            break

                    node = await agent_run.next(node)

                self._message_history = agent_run.all_messages()

        except RunAbortedError:
            logger.debug("Run aborted — treating as graceful stop")
            if agent_run is not None:
                try:
                    self._message_history = agent_run.all_messages()
                except Exception:  # noqa: BLE001
                    logger.debug(
                        "Could not retrieve agent_run messages after RunAbortedError",
                    )

        except UndrainedPendingMessagesError as exc:
            logger.warning(
                "UndrainedPendingMessagesError — pending messages may have been dropped",
                error=str(exc),
            )
            if agent_run is not None:
                with contextlib.suppress(Exception):
                    self._message_history = agent_run.all_messages()

        except asyncio.CancelledError:
            logger.debug("NativeTurn cancelled")
            raise

        except Exception as exc:
            logger.exception("NativeTurn execution failed")
            yield RunErrorEvent(
                message=str(exc),
                agent_name=self._agent.name,
            )
            return

        finally:
            if self._run_ctx._run_handle is not None:
                self._run_ctx._run_handle.active_agent_run = None

        # Always yield StreamCompleteEvent so RunHandle.start() can break
        # out of its turn loop. Even when message_history is None (e.g.
        # RunAbortedError before agent_run was created), we yield a
        # terminal event with an empty message.
        if self._message_history is not None:
            content = extract_text_from_messages(self._message_history)
        else:
            content = ""
        self._final_message = ChatMessage(
            content=content,
            role="assistant",
            name=self._agent.name,
            message_id=self._message_id,
            session_id=self._run_ctx.session_id,
        )
        yield StreamCompleteEvent(message=self._final_message)
