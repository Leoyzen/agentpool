"""OpenCode protocol handler for SessionPool integration.

Bridges SessionPool's EventBus with OpenCode's SSE event system.
When ``opencode.use_session_pool=True``, this handler manages per-session
EventBus subscriptions, event forwarding, message delegation, and session
lifecycle. When disabled, the handler raises errors so callers fall back to
the legacy ServerState session management code.

Per-agent canary:
    Individual agents can opt into SessionPool via
    ``agent.metadata.use_session_pool: true``.  When set, it overrides the
    global ``opencode.use_session_pool`` flag for that agent.  This allows
    gradual rollout agent-by-agent without affecting the entire pool.
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import RunErrorEvent, StreamCompleteEvent
from agentpool.log import get_logger
from agentpool_server.opencode_server.models.events import (
    Event,
    SessionErrorEvent,
    SessionIdleEvent,
)

if TYPE_CHECKING:
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.delegation import AgentPool
    from agentpool.orchestrator import SessionPool
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


class OpenCodeProtocolHandler:
    """Protocol handler that routes OpenCode sessions through SessionPool.

    Attributes:
        _agent_pool: The AgentPool used to resolve the SessionPool.
        _state: Optional ServerState for broadcasting OpenCode SSE events.
        _event_bus_subscriptions: Mapping of session_id -> EventBus queue.
        _consumer_tasks: Mapping of session_id -> asyncio consumer Task.
        _lock: Serializes subscription/unsubscription operations.
    """

    def __init__(self, agent_pool: AgentPool, *, state: ServerState | None = None) -> None:
        """Initialize the handler.

        Args:
            agent_pool: The agent pool that owns the SessionPool.
            state: Optional server state for SSE broadcasting.
        """
        self._agent_pool = agent_pool
        self._state = state
        self._event_bus_subscriptions: dict[
            str, asyncio.Queue[RichAgentStreamEvent[Any] | None]
        ] = {}
        self._consumer_tasks: dict[str, asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    def _agent_uses_session_pool(self, agent_name: str | None = None) -> bool:
        """Return whether SessionPool should be used for *agent_name*.

        Resolution order:

        1. **Per-agent override** — if *agent_name* is given and the
           corresponding agent config has ``metadata.use_session_pool`` set
           (bool), that value wins.
        2. **Global fallback** — otherwise the global
           ``opencode.use_session_pool`` manifest flag is returned.

        Args:
            agent_name: Name of the agent to check.  ``None`` falls back to
                the global flag immediately.

        Returns:
            ``True`` if SessionPool is enabled for the agent.
        """
        global_flag = self._agent_pool.manifest.opencode.use_session_pool
        if agent_name is None:
            return global_flag

        cfg = self._agent_pool.manifest.agents.get(agent_name)
        if cfg is None:
            return global_flag

        metadata = getattr(cfg, "metadata", None)
        if not isinstance(metadata, dict):
            return global_flag

        per_agent = metadata.get("use_session_pool")
        if isinstance(per_agent, bool):
            return per_agent

        return global_flag

    @property
    def _session_pool(self) -> SessionPool | None:
        """Get the active SessionPool from the agent pool."""
        return self._agent_pool.session_pool

    async def _ensure_event_consumer(
        self,
        session_id: str,
        agent_name: str | None = None,
    ) -> None:
        """Subscribe to the EventBus once per session and start the consumer loop.

        Idempotent: subsequent calls for the same session_id are no-ops.

        If the per-agent canary flag (or global flag) disables SessionPool,
        the consumer is *not* started so that the legacy ServerState path can
        take over.

        Args:
            session_id: The session to subscribe to.
            agent_name: Optional agent name for per-agent canary checks.
        """
        async with self._lock:
            if session_id in self._consumer_tasks:
                return

            if not self._agent_uses_session_pool(agent_name):
                logger.debug(
                    "SessionPool disabled for agent, skipping event consumer",
                    session_id=session_id,
                    agent_name=agent_name,
                )
                return

            session_pool = self._session_pool
            if session_pool is None:
                logger.warning(
                    "SessionPool not available, cannot start event consumer",
                    session_id=session_id,
                )
                return

            queue = await session_pool.event_bus.subscribe(session_id)
            self._event_bus_subscriptions[session_id] = queue
            task = asyncio.create_task(
                self._event_consumer_loop(session_id, queue),
                name=f"opencode_event_consumer_{session_id}",
            )
            self._consumer_tasks[session_id] = task
            logger.info("Started event consumer for session", session_id=session_id)

    async def _event_consumer_loop(
        self,
        session_id: str,
        queue: asyncio.Queue[RichAgentStreamEvent[Any] | None],
    ) -> None:
        """Read events from the EventBus queue and forward them as SSE.

        Runs until a sentinel ``None`` is received or the task is cancelled.

        Args:
            session_id: The session whose events are being consumed.
            queue: The EventBus queue to read from.
        """
        try:
            while True:
                event = await queue.get()
                if event is None:
                    logger.debug(
                        "Event consumer received sentinel, exiting",
                        session_id=session_id,
                    )
                    break
                await self._forward_event(session_id, event)
        except asyncio.CancelledError:
            logger.debug("Event consumer cancelled", session_id=session_id)
            raise
        except Exception:
            logger.exception("Event consumer loop failed", session_id=session_id)
        finally:
            async with self._lock:
                self._event_bus_subscriptions.pop(session_id, None)
                self._consumer_tasks.pop(session_id, None)
            logger.info("Event consumer stopped", session_id=session_id)

    async def _forward_event(self, session_id: str, event: RichAgentStreamEvent[Any]) -> None:
        """Convert a single agent event to an OpenCode event and broadcast it.

        Args:
            session_id: The session the event belongs to.
            event: The RichAgentStreamEvent from the EventBus.
        """
        if self._state is None:
            return

        oc_event = self._convert_event(session_id, event)
        if oc_event is not None:
            await self._state.broadcast_event(oc_event)

    def _convert_event(
        self, session_id: str, event: RichAgentStreamEvent[Any]
    ) -> Event | None:
        """Convert a RichAgentStreamEvent to an OpenCode SSE Event.

        This is a skeleton conversion. Full event mapping (text deltas,
        tool calls, reasoning parts, etc.) will be implemented in later
        migration groups.

        Args:
            session_id: The session the event belongs to.
            event: The agent stream event to convert.

        Returns:
            An OpenCode Event, or None if no conversion is available yet.
        """
        match event:
            case StreamCompleteEvent():
                return SessionIdleEvent.create(session_id=session_id)
            case RunErrorEvent(message=msg):
                return SessionErrorEvent.from_exception(
                    exception=Exception(str(msg)),
                    session_id=session_id,
                )
            case _:
                # TODO(Group 5.x): Implement full event conversion.
                # Events such as PartDeltaEvent, ToolCallStartEvent,
                # ToolCallCompleteEvent, etc. need to be mapped to
                # OpenCode PartUpdatedEvent, PartDeltaEvent, etc.
                return None

    async def handle_message(
        self,
        session_id: str,
        message: str,
        agent_name: str | None = None,
    ) -> None:
        """Process a user message through the SessionPool.

        Ensures the session exists, starts the event consumer, and delegates
        to ``session_pool.process_prompt()``.

        Args:
            session_id: The target session ID.
            message: The user prompt/message to process.
            agent_name: Optional agent name for per-agent canary checks.

        Raises:
            RuntimeError: If SessionPool is disabled or not initialized.
        """
        if not self._agent_uses_session_pool(agent_name):
            msg = "OpenCode use_session_pool is disabled"
            raise RuntimeError(msg)

        session_pool = self._session_pool
        if session_pool is None:
            msg = "SessionPool is not initialized"
            raise RuntimeError(msg)

        await self._ensure_event_consumer(session_id, agent_name)
        await session_pool.create_session(session_id)
        await session_pool.process_prompt(session_id, message)

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up its EventBus subscription.

        Cancels the consumer task, unsubscribes from the EventBus, and
        closes the session in the SessionPool.

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            task = self._consumer_tasks.pop(session_id, None)
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

            queue = self._event_bus_subscriptions.pop(session_id, None)
            session_pool = self._session_pool
            if queue is not None and session_pool is not None:
                await session_pool.event_bus.unsubscribe(session_id, queue)

        session_pool = self._session_pool
        if session_pool is not None:
            await session_pool.close_session(session_id)

        logger.info("Closed session via handler", session_id=session_id)
