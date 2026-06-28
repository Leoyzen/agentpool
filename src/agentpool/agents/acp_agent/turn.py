"""ACP Turn — wraps ACP session/prompt stream into a single reactive Turn.

This module provides :class:`ACPTurn`, a :class:`~agentpool.orchestrator.turn.Turn`
subclass that drives an ACP client through a single prompt → stream → complete
cycle, yielding :class:`~agentpool.agents.events.RichAgentStreamEvent` items
and populating ``message_history`` / ``final_message`` after execution.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from agentpool.agents.events import (
    RunErrorEvent,
    StreamCompleteEvent,
)
from agentpool.orchestrator.turn import Turn


if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from pydantic_ai import ModelMessage

    from acp.schema import ContentBlock, PromptResponse, SessionUpdate
    from agentpool.agents.context import AgentRunContext
    from agentpool.agents.events import RichAgentStreamEvent
    from agentpool.messaging import ChatMessage


class ACPClientProtocol(Protocol):
    """Protocol defining the ACP client interface expected by ACPTurn.

    The ACP client must provide three methods:

    - :meth:`prompt` — send a prompt to the remote agent, return a response handle
    - :meth:`stream_events` — return an async iterator of session updates
    - :meth:`get_messages` — return the full list of session updates for history
    """

    async def prompt(self, session_id: str, content: list[ContentBlock]) -> PromptResponse: ...

    def stream_events(self, response: PromptResponse) -> AsyncIterator[SessionUpdate]: ...

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


class ACPTurn(Turn):
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
    ) -> None:
        super().__init__()
        self._acp_client = acp_client
        self._prompts = prompts
        self._run_ctx = run_ctx
        self._initial_message_history = message_history
        self._session_id = session_id

    async def execute(self) -> AsyncIterator[RichAgentStreamEvent]:
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

        run_id = self._run_ctx.run_id

        # Convert all user prompts to ACP ContentBlock list.
        # Join all prompts instead of taking only the last one.
        full_prompt = "\n\n".join(self._prompts) if self._prompts else ""
        content = convert_to_acp_content([full_prompt])

        # --- Phase 1: Send prompt ---
        try:
            response = await self._acp_client.prompt(self._session_id, content)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield RunErrorEvent(
                message=str(exc),
                run_id=run_id,
            )
            return

        # --- Phase 2: Stream events ---
        try:
            async for update in self._acp_client.stream_events(response):
                if native_event := acp_to_native_event(update):
                    yield native_event
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            yield RunErrorEvent(
                message=str(exc),
                run_id=run_id,
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
