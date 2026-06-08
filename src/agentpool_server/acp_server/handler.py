"""ACP Protocol Handler using SessionPool for session and turn management.

This module provides ``ACPProtocolHandler``, a protocol handler that delegates
ACP session lifecycle and prompt processing to the ``SessionPool`` orchestration
layer when the ``acp.use_session_pool`` feature flag is enabled.

The handler bridges AgentPool's EventBus with the ACP protocol by running a
per-session event consumer loop that converts agent stream events to ACP
session updates.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from acp.agent.acp_requests import ACPRequests
from acp.schema.capabilities import ClientCapabilities
from agentpool.log import get_logger
from agentpool_server.acp_server.event_converter import ACPEventConverter
from agentpool_server.acp_server.input_provider import ACPInputProvider


if TYPE_CHECKING:
    from collections.abc import Sequence

    from acp import Client
    from acp.schema import ContentBlock, PromptResponse, StopReason
    from agentpool import AgentPool
    from agentpool.agents.events import RichAgentStreamEvent

logger = get_logger(__name__)


class ACPProtocolHandler:
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
        event_converter: ACPEventConverter,
        client: Client,
        client_capabilities: ClientCapabilities | None = None,
    ) -> None:
        """Initialize the protocol handler."""
        self.agent_pool = agent_pool
        self._event_converter_template = event_converter
        self.client = client
        self.client_capabilities = client_capabilities
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._consumer_queues: dict[str, asyncio.Queue[RichAgentStreamEvent[Any] | None]] = {}

    def _should_use_session_pool(self) -> bool:
        """Check whether the current main agent has the per-agent canary flag.

        Returns:
            True if ``agent.metadata.use_session_pool`` is set and truthy,
            False otherwise (falls back to the legacy session path).
        """
        try:
            agent = self.agent_pool.main_agent
        except RuntimeError:
            return False
        return bool(agent.metadata.get("use_session_pool", False))

    def _ensure_event_consumer(self, session_id: str) -> None:
        """Subscribe to EventBus once per session and start consumer loop.

        If a consumer task already exists and has not finished, this is a
        no-op.  Skips creation when the per-agent canary flag is disabled.

        Args:
            session_id: The session to ensure a consumer for.
        """
        if not self._should_use_session_pool():
            return

        task = self._consumer_tasks.get(session_id)
        if task is not None and not task.done():
            return

        task = asyncio.create_task(
            self._event_consumer_loop(session_id),
            name=f"acp_event_consumer_{session_id}",
        )
        self._consumer_tasks[session_id] = task
        logger.debug("Started event consumer", session_id=session_id)

    async def _event_consumer_loop(self, session_id: str) -> None:
        """Forward events from EventBus to ACP protocol.

        Subscribes to the SessionPool EventBus for the given session,
        converts each event through ``ACPEventConverter``, and emits ACP
        ``session/update`` notifications.

        The loop exits when a ``None`` sentinel is received (sent by
        ``EventBus.close_session``) or when the task is cancelled.

        Args:
            session_id: The session whose events to consume.
        """
        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            logger.warning(
                "SessionPool not available, cannot start event consumer",
                session_id=session_id,
            )
            return

        queue = await session_pool.event_bus.subscribe(session_id, scope="descendants")
        self._consumer_queues[session_id] = queue

        # Derive turn_complete support from stored client capabilities
        client_supports_turn_complete = (
            self.client_capabilities is not None and self.client_capabilities.turn_complete is True
        )

        # Create a per-session converter so tool-call state is isolated
        converter = ACPEventConverter(
            client_supports_turn_complete=client_supports_turn_complete,
        )

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break

                try:
                    async for update in converter.convert(event):
                        from acp.schema import SessionNotification

                        notification = SessionNotification(
                            session_id=session_id,
                            update=update,
                        )
                        await self.client.session_update(notification)
                except (ConnectionResetError, BrokenPipeError) as e:
                    logger.debug(
                        "Client connection closed gracefully",
                        session_id=session_id,
                        error=str(e),
                    )
                    break
                except Exception as e:
                    import anyio

                    if isinstance(e, (anyio.ClosedResourceError, anyio.EndOfStream)):
                        logger.debug(
                            "Stream closed gracefully",
                            session_id=session_id,
                            error=str(e),
                        )
                        break
                    logger.exception(
                        "Failed to convert or send event",
                        session_id=session_id,
                        event_type=type(event).__name__,
                    )
        except asyncio.CancelledError:
            logger.debug("Event consumer cancelled", session_id=session_id)
            raise
        finally:
            await session_pool.event_bus.unsubscribe(session_id, queue)
            self._consumer_queues.pop(session_id, None)
            logger.debug("Event consumer stopped", session_id=session_id)

    async def handle_prompt(
        self,
        session_id: str,
        prompt: Sequence[ContentBlock],
    ) -> PromptResponse | None:
        """Process a prompt through the SessionPool.

        Ensures the session exists (via ``SessionPool.create_session``) and
        that an event consumer is running before delegating the prompt to
        ``SessionPool.receive_request()``.

        When the per-agent canary flag is disabled, returns ``None`` so the
        caller can fall back to the legacy session path.

        Args:
            session_id: The ACP session identifier.
            prompt: ACP content blocks from the prompt request.

        Returns:
            A ``PromptResponse`` with the stop reason, or ``None`` when the
            per-agent flag is disabled.
        """
        from agentpool_server.acp_server.converters import from_acp_content

        if not self._should_use_session_pool():
            logger.debug(
                "Per-agent canary flag off, skipping SessionPool",
                session_id=session_id,
            )
            return None

        session_pool = self.agent_pool.session_pool
        if session_pool is None:
            logger.error("SessionPool not available", session_id=session_id)
            return self._prompt_response("end_turn")

        # Ensure the session exists in the SessionPool
        await session_pool.create_session(session_id)

        # Start event consumer before processing so no events are dropped
        self._ensure_event_consumer(session_id)

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
                session_id, *contents, input_provider=input_provider
            )
            # Legacy clients (no turn_complete support) block until the run finishes
            # so they don't need session/update turn_complete notifications.
            if run_handle is not None and not (
                self.client_capabilities is not None and self.client_capabilities.turn_complete
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

        Skips SessionPool cleanup when the per-agent canary flag is disabled.

        Args:
            session_id: The session to close.
        """
        if not self._should_use_session_pool():
            logger.debug(
                "Per-agent canary flag off, skipping SessionPool close",
                session_id=session_id,
            )
            return

        session_pool = self.agent_pool.session_pool

        # Signal the consumer loop to exit via EventBus sentinel
        if session_pool is not None:
            await session_pool.event_bus.close_session(session_id)

        # Wait for the consumer task to finish (or cancel it)
        task = self._consumer_tasks.pop(session_id, None)
        if task is not None and not task.done():
            try:
                await asyncio.wait_for(task, timeout=5.0)
            except TimeoutError:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    logger.exception(
                        "Unexpected exception during consumer task cancellation",
                        session_id=session_id,
                    )
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception(
                    "Unexpected exception in consumer task during graceful shutdown",
                    session_id=session_id,
                )

        self._consumer_queues.pop(session_id, None)

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
