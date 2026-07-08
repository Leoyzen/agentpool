"""ACP Turn — wraps ACP session/prompt stream into a single reactive Turn.

This module provides :class:`ACPTurn`, a :class:`~agentpool.orchestrator.turn.Turn`
subclass that drives an ACP client through a single prompt → stream → complete
cycle, yielding :class:`~agentpool.agents.events.RichAgentStreamEvent` items
and populating ``message_history`` / ``final_message`` after execution.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from agentpool.agents.events import (
    RunErrorEvent,
    StreamCompleteEvent,
)
from agentpool.orchestrator.turn import HookAwareTurn, Turn


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Sequence
    from typing import Any

    from pydantic_ai import ModelMessage

    from acp.schema import ContentBlock, SessionUpdate
    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.hooks import AgentHooks
    from agentpool.messaging import ChatMessage


class ACPClientProtocol(Protocol):
    """Protocol defining the ACP client interface expected by ACPTurn.

    The ACP client must provide four capabilities:

    - :meth:`prompt` — send a prompt to the remote agent (non-blocking, returns None)
    - :meth:`stream_events` — return an async iterator of session updates (no args)
    - :attr:`stop_reason` — return the stop reason after streaming completes
    - :meth:`get_messages` — return the full list of session updates for history
    """

    async def prompt(self, session_id: str, content: list[ContentBlock]) -> None: ...

    def stream_events(self) -> AsyncIterator[SessionUpdate]: ...

    @property
    def stop_reason(self) -> str | None: ...

    async def get_messages(self, session_id: str) -> list[SessionUpdate]: ...


def _convert_updates_to_model_messages(
    updates: Sequence[SessionUpdate],
    *,
    session_id: str,
    agent_name: str | None = None,
    model_name: str | None = None,
) -> tuple[list[ModelMessage], ChatMessage[str] | None]:
    """Convert ACP session updates to model messages and final chat message.

    Uses :class:`~agentpool.agents.acp_agent.acp_converters.ACPMessageAccumulator`
    to build :class:`~agentpool.messaging.ChatMessage` objects from the raw
    session updates, then flattens the model messages.

    Returns:
        A tuple of (model_messages, final_chat_message). The final chat message
        is the last assistant message, or None if no messages were produced.
    """
    from agentpool.agents.acp_agent.acp_converters import ACPMessageAccumulator

    accumulator = ACPMessageAccumulator(
        session_id=session_id,
        agent_name=agent_name,
        model_name=model_name,
    )
    for update in updates:
        accumulator.process(update)
    chat_messages = accumulator.finalize()

    model_messages: list[ModelMessage] = []
    for msg in chat_messages:
        model_messages.extend(msg.messages)

    final_msg: ChatMessage[str] | None = None
    for msg in reversed(chat_messages):
        if msg.role == "assistant":
            final_msg = msg
            break

    return model_messages, final_msg


class ACPTurn(HookAwareTurn, Turn):
    """Single reactive turn wrapping an ACP session/prompt stream.

    Encapsulates one complete ACP interaction cycle: sending a prompt to the
    remote agent, streaming session updates as native events, and collecting
    the final message history.
    """

    def __init__(
        self,
        acp_client: ACPClientProtocol,
        prompts: list[str],
        run_ctx: AgentRunContext,
        message_history: list[ModelMessage],
        session_id: str,
        agent_name: str | None = None,
        hooks: AgentHooks | None = None,
        env: Any | None = None,
    ) -> None:
        super().__init__()
        self._acp_client = acp_client
        self._prompts = prompts
        self._run_ctx = run_ctx
        self._session_id = session_id
        self._agent_name = agent_name
        self._hooks = hooks
        self._agent_env = env

    @property
    def _hook_env(self) -> Any | None:
        """Execution environment for command hooks."""
        return self._agent_env

    @property
    def _hook_agent_name(self) -> str:
        """Agent name passed to hook invocations."""
        return self._agent_name or ""

    @property
    def _hook_prompt(self) -> str:
        """The user prompt for this turn."""
        return str(self._prompts)

    async def execute(self) -> AsyncGenerator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
        """Execute one ACP prompt → stream → complete cycle.

        Yields:
            Native streaming events mapped from ACP session updates.

        Raises:
            asyncio.CancelledError: Re-raised if the turn is cancelled.
        """
        from agentpool.agents.acp_agent.acp_converters import (
            acp_to_native_event,
            convert_to_acp_content,
        )
        from agentpool.agents.events import ToolCallCompleteEvent, ToolCallStartEvent

        run_id = self._run_ctx.run_id

        turn_start = time.perf_counter()
        try:
            # --- Phase 0: Fire pre_turn hooks ---
            pre_turn_result = await self._fire_pre_turn_hooks()
            if pre_turn_result is not None and pre_turn_result.get("decision") == "deny":
                self._run_ctx.cancelled = True
                from agentpool.messaging import ChatMessage

                self._final_message = ChatMessage[str](
                    content="",
                    role="assistant",
                    message_id=str(uuid4()),
                    session_id=self._session_id,
                )
                yield StreamCompleteEvent(cancelled=True, message=self._final_message)
                return
            # Convert all user prompts to ACP ContentBlock list.
            # Join all prompts instead of taking only the last one.
            full_prompt = "\n\n".join(self._prompts) if self._prompts else ""
            content = convert_to_acp_content([full_prompt])

            # --- Phase 1: Send prompt (non-blocking) ---
            try:
                await self._acp_client.prompt(self._session_id, content)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield RunErrorEvent(
                    message=str(exc),
                    run_id=run_id,
                    agent_name=self._agent_name,
                )
                return

            # --- Phase 2: Stream events ---
            try:
                async for update in self._acp_client.stream_events():
                    native_event = acp_to_native_event(update)
                    if native_event is not None:
                        # Fire advisory tool hooks for tool-related events.
                        # These are advisory — they log and augment but cannot
                        # prevent the external agent from calling tools.
                        match native_event:
                            case ToolCallStartEvent(
                                tool_name=tn,
                                raw_input=ti,
                                tool_call_id=tcid,
                            ):
                                await self._fire_pre_tool_hooks(tn, ti, tcid)
                            case ToolCallCompleteEvent(
                                tool_name=tn,
                                tool_input=ti,
                                tool_result=tr,
                                tool_call_id=tcid,
                            ):
                                await self._fire_post_tool_hooks(
                                    tn,
                                    ti,
                                    tr,
                                    0.0,
                                    tcid,
                                )
                            case _:
                                pass
                        yield native_event
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield RunErrorEvent(
                    message=str(exc),
                    run_id=run_id,
                    agent_name=self._agent_name,
                )
                return

            # --- Phase 3: Collect message history ---
            try:
                raw_updates = await self._acp_client.get_messages(self._session_id)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                yield RunErrorEvent(
                    message=str(exc),
                    run_id=run_id,
                    agent_name=self._agent_name,
                )
                return

            model_messages, final_msg = _convert_updates_to_model_messages(
                raw_updates,
                session_id=self._session_id,
            )
            self._message_history = model_messages

            if final_msg is not None:
                self._final_message = final_msg
            else:
                from agentpool.messaging import ChatMessage

                self._final_message = ChatMessage[str](
                    content="",
                    role="assistant",
                    message_id=str(uuid4()),
                    session_id=self._session_id,
                )

            yield StreamCompleteEvent(message=self._final_message)
        finally:
            duration_ms = (time.perf_counter() - turn_start) * 1000
            await self._fire_post_turn_hooks(self._final_message, duration_ms=duration_ms)
