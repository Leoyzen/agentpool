"""OpenCode protocol handler for SessionPool integration.

Bridges SessionPool's EventBus with OpenCode's SSE event system.
When ``opencode.use_session_pool=True``, this handler manages per-session
EventBus subscriptions, event forwarding, message delegation, and session
lifecycle. When disabled, the handler raises errors so callers fall back to
the legacy ServerState session management code.

Per-agent canary:
    Individual agents can opt into SessionPool via
    ``agent.metadata.use_session_pool: true``.  When set, it overrides the
    global ``opencode.use_session_pool`` manifest flag for that agent.  This allows
    gradual rollout agent-by-agent without affecting the entire pool.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from agentpool.agents.events import (
    RunErrorEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool_server.mixins import ProtocolEventConsumerMixin
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


class OpenCodeProtocolHandler(ProtocolEventConsumerMixin):
    """Protocol handler that routes OpenCode sessions through SessionPool.

    Attributes:
        _agent_pool: The AgentPool used to resolve the SessionPool.
        _state: Optional ServerState for broadcasting OpenCode SSE events.
        _consumer_tasks: Mapping of session_id -> asyncio consumer Task.
        _consumer_queues: Mapping of session_id -> EventBus queue.
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
        self._consumer_tasks: dict[str, asyncio.Task[None]] = {}
        self._consumer_queues: dict[str, asyncio.Queue[Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def agent_pool(self) -> AgentPool:
        """Return the AgentPool (exposed for the mixin)."""
        return self._agent_pool

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
            if not self._agent_uses_session_pool(agent_name):
                logger.debug(
                    "SessionPool disabled for agent, skipping event consumer",
                    session_id=session_id,
                    agent_name=agent_name,
                )
                return

            await self.start_event_consumer(session_id)
            logger.info("Started event consumer for session", session_id=session_id)

    async def _handle_event(
        self,
        session_id: str,
        event: RichAgentStreamEvent[Any],
    ) -> None:
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

    async def _handle_spawn_session_start(
        self,
        session_id: str,
        event: SpawnSessionStart,
    ) -> None:
        """Handle SpawnSessionStart by logging the new child session.

        The mixin automatically starts the child consumer; this hook is a
        no-op beyond debug logging.

        Args:
            session_id: The session the event was received on.
            event: The spawn event describing the new child session.
        """
        logger.debug(
            "SpawnSessionStart received",
            session_id=session_id,
            child_session_id=event.child_session_id,
        )

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
        await session_pool.receive_request(session_id, message)

    async def close_session(self, session_id: str) -> None:
        """Close a session and clean up its EventBus subscription.

        Cancels the consumer task, unsubscribes from the EventBus, and
        closes the session in the SessionPool.

        Args:
            session_id: The session to close.
        """
        async with self._lock:
            await self.stop_event_consumer(session_id)

        session_pool = self._session_pool
        if session_pool is not None:
            await session_pool.close_session(session_id)

        logger.info("Closed session via handler", session_id=session_id)
