"""ACP v2 protocol handler.

In v2, ``session/prompt`` returns immediately with an empty result.
The agent processes asynchronously and communicates state via
``state_update`` notifications through the EventBus.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio

from acp_v2.schema.client_responses import PromptResponse
from acp_v2.schema.notifications import SessionNotification
from acp_v2.schema.session_updates import StateUpdate, UserMessage
from agentpool.agents.events.events import SpawnSessionStart
from agentpool.log import get_logger
from agentpool_server.acp_server.v2.event_converter import ACPEventConverterV2
from agentpool_server.acp_server.v2.prompt_lifecycle import PromptLifecycleManager
from agentpool_server.mixins import ConsumerShutdown, ProtocolEventConsumerMixin


if TYPE_CHECKING:
    from collections.abc import Sequence

    from acp import Client
    from acp.schema import ContentBlock
    from agentpool import AgentPool
    from agentpool.orchestrator.core import EventBus, EventEnvelope
    from agentpool_server.acp_server.session_manager import ACPSessionManager

logger = get_logger(__name__)


class ACPProtocolHandlerV2(ProtocolEventConsumerMixin):
    """v2 protocol handler with async prompt lifecycle.

    Delegates session lifecycle to SessionPool. Prompt returns immediately.
    Events are consumed from EventBus and converted via ACPEventConverterV2.
    """

    def __init__(
        self,
        agent_pool: AgentPool[Any],
        session_manager: ACPSessionManager,
        client: Client,
        client_capabilities: Any = None,
    ) -> None:
        super().__init__()
        self.agent_pool = agent_pool
        self.session_manager = session_manager
        self.client = client
        self.client_capabilities = client_capabilities
        self._converters: dict[str, ACPEventConverterV2] = {}
        self._lifecycle: dict[str, PromptLifecycleManager] = {}

    @property
    def event_bus(self) -> EventBus:
        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            raise RuntimeError("SessionPool not available")
        return session_pool.event_bus

    def _get_subscription_scope(self) -> str:
        return "session"

    async def _on_spawn_session_start(self, session_id: str, envelope: EventEnvelope) -> None:
        event = envelope.event
        if isinstance(event, SpawnSessionStart):
            child_sid = event.child_session_id
            if child_sid and child_sid != session_id:
                await self.start_event_consumer(child_sid)

    async def _before_consumer_loop(self, session_id: str) -> None:
        self._converters[session_id] = ACPEventConverterV2()
        self._lifecycle[session_id] = PromptLifecycleManager()

    async def _handle_event(self, session_id: str, envelope: EventEnvelope) -> None:
        event_sid = envelope.source_session_id or session_id
        converter = self._converters.get(event_sid) or self._converters.get(session_id)
        if converter is None:
            return
        try:
            async for update in converter.convert(envelope.event):
                notification = SessionNotification(
                    session_id=event_sid, update=update
                )
                await self.client.session_update(notification)  # pyright: ignore[reportArgumentType]
        except (ConnectionResetError, BrokenPipeError) as e:
            raise ConsumerShutdown from e
        except anyio.ClosedResourceError as e:
            raise ConsumerShutdown from e
        except anyio.EndOfStream as e:
            raise ConsumerShutdown from e
        except Exception:
            logger.exception(
                "Failed to convert or send v2 event",
                session_id=session_id,
                event_type=type(envelope.event).__name__,
            )

    async def _after_consumer_loop(self, session_id: str) -> None:
        self._converters.pop(session_id, None)
        self._lifecycle.pop(session_id, None)

    async def _event_consumer_loop(self, session_id: str) -> None:
        if self._consumer_streams.get(session_id) is None:
            stream = await self.event_bus.subscribe(
                session_id, scope=self._get_subscription_scope()
            )
            self._consumer_streams[session_id] = stream
        await super()._event_consumer_loop(session_id)

    async def _ensure_event_consumer(self, session_id: str) -> None:
        await self.start_event_consumer(session_id)
        logger.debug("Started v2 event consumer", session_id=session_id)

    async def handle_prompt(
        self,
        session_id: str,
        prompt: Sequence[ContentBlock],
    ) -> PromptResponse:
        """Process a v2 prompt — returns immediately with empty result.

        Sends user_message notification and starts async agent execution.
        Turn completion is communicated via state_update: idle.
        """

        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            logger.error("SessionPool not available", session_id=session_id)
            return PromptResponse()

        await self._ensure_event_consumer(session_id)

        lifecycle = self._lifecycle.get(session_id)
        if lifecycle is None:
            lifecycle = PromptLifecycleManager()
            self._lifecycle[session_id] = lifecycle

        message_id = str(asyncio.uuid4())
        await self._send_user_message(session_id, prompt, message_id)
        lifecycle.transition_to_running()
        await self._send_state_update(session_id, "running")

        self.agent_pool.tasks.create_task(
            self._run_agent_turn(session_id, prompt, message_id)
        )

        return PromptResponse()

    async def _send_user_message(
        self,
        session_id: str,
        prompt: Sequence[ContentBlock],
        message_id: str,
    ) -> None:
        from agentpool_server.acp_server.converters import from_acp_content

        content_blocks = from_acp_content(list(prompt))
        notification = SessionNotification(
            session_id=session_id,
            update=UserMessage(
                message_id=message_id,
                content=content_blocks,
            ),
        )
        try:
            await self.client.session_update(notification)  # pyright: ignore[reportArgumentType]
        except Exception:
            logger.exception("Failed to send user_message", session_id=session_id)

    async def _send_state_update(
        self, session_id: str, state: str, stop_reason: str | None = None
    ) -> None:
        update: StateUpdate
        if stop_reason:
            update = StateUpdate(state=state, stop_reason=stop_reason)  # type: ignore[arg-type]
        else:
            update = StateUpdate(state=state)  # type: ignore[arg-type]
        notification = SessionNotification(session_id=session_id, update=update)
        try:
            await self.client.session_update(notification)  # pyright: ignore[reportArgumentType]
        except Exception:
            logger.exception("Failed to send state_update", session_id=session_id)

    async def _run_agent_turn(
        self,
        session_id: str,
        prompt: Sequence[ContentBlock],
        message_id: str,
    ) -> None:
        """Run agent turn asynchronously, send state_update on completion."""

        try:
            await session_pool_receive_request(
                self.agent_pool, session_id, prompt, message_id
            )
        except Exception:
            logger.exception("Agent turn failed", session_id=session_id)
            await self._send_state_update(session_id, "idle", "refusal")
            return

        lifecycle = self._lifecycle.get(session_id)
        if lifecycle:
            lifecycle.transition_to_idle("end_turn")
        await self._send_state_update(session_id, "idle", "end_turn")

    async def close_session(self, session_id: str) -> None:
        await self.stop_event_consumer(session_id)
        await self.session_manager.close_session(session_id)

    async def cancel_session(self, session_id: str) -> None:
        session_pool = self.agent_pool.session_pool
        if session_pool is not None:
            controller = session_pool.controller
            if controller is not None:
                await controller.cancel_run(session_id)
        await self._send_state_update(session_id, "idle", "cancelled")


async def session_pool_receive_request(
    agent_pool: AgentPool[Any],
    session_id: str,
    prompt: Sequence[ContentBlock],
    message_id: str,
) -> None:
    """Delegate to SessionPool.receive_request."""

    session_pool = agent_pool.session_pool
    if session_pool is None:
        return

    await session_pool.controller.receive_request(
        session_id=session_id,
        content=list(prompt),
        priority="when_idle",
    )
