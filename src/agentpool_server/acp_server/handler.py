"""ACP Protocol Handler using SessionPool for session and turn management.

This module provides ``ACPProtocolHandler``, a protocol handler that delegates
ACP session lifecycle and prompt processing to the ``SessionPool`` orchestration
layer.

The handler bridges AgentPool's EventBus with the ACP protocol by running a
per-session event consumer loop that converts agent stream events to ACP
session updates.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import anyio

from acp.agent.acp_requests import ACPRequests
from acp.schema.capabilities import ClientCapabilities
from agentpool.agents.events.events import SpawnSessionStart
from agentpool.log import get_logger
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider
from agentpool_server.mixins import ConsumerShutdown, ProtocolEventConsumerMixin


if TYPE_CHECKING:
    from collections.abc import Sequence

    from acp import Client
    from acp.schema import ContentBlock, PromptResponse, StopReason
    from agentpool import AgentPool
    from agentpool.orchestrator.core import EventBus, EventEnvelope
    from agentpool_server.acp_server.session_manager import ACPSessionManager

logger = get_logger(__name__)


class ACPProtocolHandler(ProtocolEventConsumerMixin):
    """ACP protocol handler backed by SessionPool.

    Manages per-session event consumers that subscribe to the SessionPool's
    EventBus and forward converted events to the ACP client. Prompt handling
    is delegated to ``SessionPool.receive_request()``.

    Args:
        agent_pool: The agent pool containing the SessionPool.
        event_converter: Template converter used to derive per-session
            converters. The display mode is extracted from this instance.
        client: ACP client for sending session update notifications.
        client_capabilities: Client capabilities for elicitation support
            gating. If None, falls back to legacy request_permission.
    """

    def __init__(
        self,
        agent_pool: AgentPool[Any],
        session_manager: ACPSessionManager,
        event_converter: ACPEventConverter,
        client: Client,
        client_capabilities: ClientCapabilities | None = None,
    ) -> None:
        """Initialize the protocol handler."""
        super().__init__()
        self.agent_pool = agent_pool
        self.session_manager = session_manager
        self._event_converter_template = event_converter
        self.client = client
        self.client_capabilities = client_capabilities
        self._converters: dict[str, ACPEventConverter] = {}

    @property
    def event_bus(self) -> EventBus:
        """Return the EventBus instance to subscribe to."""
        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            raise RuntimeError("SessionPool not available")
        return session_pool.event_bus

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Overridden to "session" so that only the exact session's events are
        consumed.  Child session events are handled by separate consumers
        created in response to SpawnSessionStart (see _on_spawn_session_start).
        This prevents event interleaving when a parent and its background-task
        child run concurrently.

        Returns:
            The subscription scope string.
        """
        return "session"

    async def _on_spawn_session_start(self, session_id: str, envelope: EventEnvelope) -> None:
        """Start a dedicated consumer for the newly spawned child session.

        Skips background tasks (spawn_mechanism="task") since their events
        should remain server-side and not be streamed to the ACP client.
        Only sync subagents get a child consumer so their progress is visible
        in real-time.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope containing the spawn session start event.
        """
        event = envelope.event
        if isinstance(event, SpawnSessionStart):
            # Skip background tasks — their events stay server-side
            if getattr(event, "spawn_mechanism", None) == "task":
                return

            child_sid = event.child_session_id
            if child_sid and child_sid != session_id:
                await self.start_event_consumer(child_sid)

    async def _before_consumer_loop(self, session_id: str) -> None:
        """Create per-session ACPEventConverter before loop starts.

        Args:
            session_id: The session whose consumer is starting.
        """
        client_supports_turn_complete = (
            self.client_capabilities is not None
            and self.client_capabilities.turn_complete is True
        )
        converter = ACPEventConverter(
            subagent_display_mode=self._event_converter_template.subagent_display_mode,
            client_supports_turn_complete=client_supports_turn_complete,
        )
        self._converters[session_id] = converter

    async def _handle_event(self, session_id: str, envelope: EventEnvelope) -> None:
        """Handle a single event from the EventBus.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope to handle.

        Raises:
            ConsumerShutdown: When the ACP client connection is closed.
        """
        # Use envelope's source_session_id for routing (for child session routing)
        event_sid = envelope.source_session_id
        effective_sid = event_sid if event_sid else session_id

        # Look up converter: try event's session first, fall back to consumer's session
        converter = self._converters.get(effective_sid) or self._converters.get(session_id)
        if converter is None:
            return

        try:
            async for update in converter.convert(envelope.event):
                from acp.schema import SessionNotification

                notification = SessionNotification(
                    session_id=effective_sid,
                    update=update,
                )
                await self.client.session_update(notification)
        except (ConnectionResetError, BrokenPipeError) as e:
            logger.debug(
                "Client connection closed gracefully",
                session_id=session_id,
                error=str(e),
            )
            raise ConsumerShutdown from e
        except anyio.ClosedResourceError as e:
            logger.debug(
                "Stream closed gracefully",
                session_id=session_id,
                error=str(e),
            )
            raise ConsumerShutdown from e
        except anyio.EndOfStream as e:
            logger.debug(
                "Stream closed gracefully",
                session_id=session_id,
                error=str(e),
            )
            raise ConsumerShutdown from e
        except Exception:
            logger.exception(
                "Failed to convert or send event",
                session_id=session_id,
                event_type=type(envelope.event).__name__,
            )

    async def _after_consumer_loop(self, session_id: str) -> None:
        """Clean up per-session converter.

        Args:
            session_id: The session whose consumer has stopped.
        """
        self._converters.pop(session_id, None)

    async def _event_consumer_loop(self, session_id: str) -> None:
        """Backward-compatible wrapper for mixin's consumer loop.

        Supports direct invocation (e.g., from tests) by lazily subscribing
        when no queue has been set up via ``start_event_consumer()``.

        Args:
            session_id: The session whose events to consume.
        """
        if self._consumer_queues.get(session_id) is None:
            queue = await self.event_bus.subscribe(
                session_id, scope=self._get_subscription_scope()
            )
            self._consumer_queues[session_id] = queue
        await super()._event_consumer_loop(session_id)

    async def _ensure_event_consumer(self, session_id: str) -> None:
        """Subscribe to EventBus once per session and start consumer loop.

        If a consumer task already exists and has not finished, this is a
        no-op.

        Args:
            session_id: The session to ensure a consumer for.
        """
        await self.start_event_consumer(session_id)
        logger.debug("Started event consumer", session_id=session_id)

    async def handle_prompt(
        self,
        session_id: str,
        prompt: Sequence[ContentBlock],
    ) -> PromptResponse:
        """Process a prompt through the SessionPool.

        Ensures the session exists (via ``SessionPool.create_session``) and
        that an event consumer is running before delegating the prompt to
        ``SessionPool.receive_request()``.

        Args:
            session_id: The ACP session identifier.
            prompt: ACP content blocks from the prompt request.

        Returns:
            A ``PromptResponse`` with the stop reason.
        """
        from agentpool_server.acp_server.converters import from_acp_content

        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            logger.error("SessionPool not available", session_id=session_id)
            return self._prompt_response("end_turn")

        # Recover cwd from session_store for clients reconnecting after pool swaps
        cwd = "."
        if self.session_manager.session_store is not None:
            try:
                stored = await self.session_manager.session_store.load(session_id)
                if stored and stored.cwd:
                    cwd = stored.cwd
            except Exception:  # noqa: BLE001
                pass  # Use default cwd

        # Ensure the session exists in the SessionPool (pass recovered cwd as metadata)
        await session_pool.create_session(session_id, cwd=cwd)

        # Add session MCP providers to SessionPool's per-session agent.
        # Use deduplication because get_or_create_session_agent returns a cached
        # per-session agent; adding the same provider repeatedly causes tool name
        # conflicts in pydantic-ai's CombinedToolset.
        acp_session = self.session_manager.get_session(session_id)
        if acp_session is not None and acp_session.session_mcp_providers:
            try:
                session_agent = await session_pool.sessions.get_or_create_session_agent(
                    session_id
                )
                for provider in acp_session.session_mcp_providers:
                    if provider not in session_agent.tools.external_providers:
                        session_agent.tools.add_provider(provider)
                logger.info(
                    "Added session MCP providers to SessionPool agent",
                    session_id=session_id,
                    num_providers=len(acp_session.session_mcp_providers),
                )
            except Exception:
                logger.exception(
                    "Failed to add session MCP providers to SessionPool agent",
                    session_id=session_id,
                )

        # Start event consumer before processing so no events are dropped
        await self._ensure_event_consumer(session_id)

        # Convert ACP content blocks to agent prompts
        contents = [from_acp_content(block, fs=None) for block in prompt]

        # Create ACP input provider for elicitation and tool confirmations
        # through the ACP protocol (not falling back to StdlibInputProvider)
        acp_requests = ACPRequests(client=self.client, session_id=session_id)
        session_proxy = _ACPSessionProxy(
            requests=acp_requests,
            client_capabilities=self.client_capabilities,
        )
        input_provider = ACPInputProvider(session=session_proxy)  # type: ignore[arg-type]

        stop_reason: StopReason = "end_turn"
        try:
            run_handle = await session_pool.receive_request(
                session_id, contents, input_provider=input_provider
            )
            # Legacy clients (no turn_complete support) block until the run finishes
            # so they don't need session/update turn_complete notifications.
            if run_handle is not None and not (
                self.client_capabilities is not None
                and self.client_capabilities.turn_complete
            ):
                await run_handle.complete_event.wait()
        except asyncio.CancelledError:
            logger.info("Prompt processing cancelled", session_id=session_id)
            stop_reason = "cancelled"
        except Exception:
            logger.exception("Prompt processing failed", session_id=session_id)
            stop_reason = "end_turn"

        return self._prompt_response(stop_reason)

    async def close_session(self, session_id: str) -> None:
        """Close a session and tear down its event consumer.

        Sends the EventBus sentinel to gracefully stop the consumer loop,
        waits for it to finish, then delegates to
        ``SessionPool.close_session()``.

        Args:
            session_id: The session to close.
        """
        session_pool = self.agent_pool.session_pool

        # Stop the event consumer (mixin's stop handles cancellation + unsubscribe)
        await self.stop_event_consumer(session_id)

        # Signal EventBus to close session
        if session_pool is not None:
            await session_pool.event_bus.close_session(session_id)

        # Delegate to SessionPool for final cleanup
        if session_pool is not None:
            try:
                await session_pool.close_session(session_id)
            except Exception:
                logger.exception("SessionPool close_session failed", session_id=session_id)

    def _prompt_response(self, stop_reason: StopReason) -> PromptResponse:
        """Build a minimal PromptResponse.

        Args:
            stop_reason: The ACP stop reason.

        Returns:
            A ``PromptResponse`` with the given stop reason.
        """
        from acp.schema import PromptResponse

        return PromptResponse(stop_reason=stop_reason)


class _ACPSessionProxy:
    """Lightweight proxy providing the subset of ACPSession that ACPInputProvider needs.

    ACPProtocolHandler does not have a full ACPSession instance, but
    ACPInputProvider only needs ``requests`` and ``client_capabilities``.
    This proxy bridges the gap so elicitation/tool-confirmation flows
    through the ACP protocol instead of falling back to StdlibInputProvider.
    """

    def __init__(
        self,
        requests: ACPRequests,
        client_capabilities: ClientCapabilities | None = None,
    ) -> None:
        self.requests = requests
        self.client_capabilities = client_capabilities or ClientCapabilities()
