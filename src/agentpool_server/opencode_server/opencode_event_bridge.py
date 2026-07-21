"""Event conversion and EventBus subscription mixin.

Extracted from session_pool_integration.py as part of the session-debt-cleanup
file split. Contains the event bridge mixin that implements the
ProtocolEventConsumerMixin hooks for OpenCodeSessionPoolIntegration,
handling event conversion, EventBus subscription, and the event consumer
lifecycle.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, cast

from agentpool.agents.events.events import (
    CustomEvent,
    PartStartEvent,
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    SpawnSessionStart,
    StreamCompleteEvent,
    UserMessageInsertedEvent,
)
from agentpool.log import get_logger
from agentpool.utils import identifiers as identifier
from agentpool.utils.time_utils import now_ms
from agentpool_server.opencode_server.event_adapter import OpenCodeEventAdapter
from agentpool_server.opencode_server.event_processor_context import (
    EventProcessorContext,
)
from agentpool_server.opencode_server.models import (
    AssistantMessage,
    MessagePath,
    MessageTime,
    MessageUpdatedEvent,
    MessageWithParts,
    PartUpdatedEvent,
    SessionErrorEvent,
    SessionStatus,
    StepStartPart,
    TimeCreated,
    UserMessage,
)
from agentpool_server.opencode_server.opencode_message_bridge import (
    append_message_to_session,
    get_messages_for_session,
)
from agentpool_server.opencode_server.opencode_session_routes import (
    ensure_session,
    set_session_status,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from agentpool.orchestrator.core import EventBus, EventEnvelope
    from agentpool_server.opencode_server.state import ServerState


logger = get_logger(__name__)


class OpenCodeEventBridgeMixin:
    """Mixin providing event conversion and EventBus consumer lifecycle.

    Implements the ProtocolEventConsumerMixin hooks for
    OpenCodeSessionPoolIntegration, handling event subscription, event
    conversion to OpenCode SSE events, and parent/child session tracking.

    Attributes:
        session_pool: The SessionPool instance (provided by main class).
        server_state: The OpenCode server state (provided by main class).
        _contexts: Per-session EventProcessorContext instances
            (provided by main class).
        _adapters: Per-session OpenCodeEventAdapter instances
            (provided by main class).
        _message_registered: Per-session message registration flags
            (provided by main class).
        _child_to_parent: Mapping of child session IDs to parent session IDs
            (provided by main class).
        _child_spawns: Mapping of child session IDs to SpawnSessionStart events
            (provided by main class).
        _children_of: Mapping of parent session IDs to child session ID sets
            (provided by main class).
        _resume_contexts: Per-session serialized context data for resume
            (provided by main class).
        _pending_message_ids: Pending canonical message IDs from REST handlers
            (provided by main class).
    """

    session_pool: Any  # SessionPool
    server_state: ServerState
    _contexts: dict[str, EventProcessorContext]
    _adapters: dict[str, OpenCodeEventAdapter]
    _message_registered: dict[str, bool]
    _child_to_parent: dict[str, str]
    _child_spawns: dict[str, SpawnSessionStart]
    _children_of: dict[str, set[str]]
    _resume_contexts: dict[str, dict[str, Any]]
    _pending_message_ids: dict[str, str]
    _pending_message_metadata: dict[str, dict[str, str | None]]

    if TYPE_CHECKING:

        def get_session_context_data(self, session_id: str) -> dict[str, Any] | None: ...
        async def _create_subagent_tool_part(self, session_id: str, event: Any) -> Any: ...
        async def start_event_consumer(self, session_id: str) -> None: ...
        async def stop_event_consumer(self, session_id: str) -> None: ...
        async def _update_parent_toolpart(
            self,
            parent_session_id: str,
            child_session_id: str,
            spawn_event: SpawnSessionStart,
            event: Any,
        ) -> None: ...
        async def _update_parent_toolpart_error(
            self,
            parent_session_id: str,
            child_session_id: str,
            spawn_event: SpawnSessionStart,
            event: Any,
        ) -> None: ...

    @property
    def event_bus(self) -> EventBus:
        """Return the EventBus instance to subscribe to."""
        return cast("EventBus", self.session_pool.event_bus)

    def _get_subscription_scope(self) -> str:
        """Return the EventBus subscription scope.

        Overridden to "session" so that only the exact session's events are
        consumed. Child session events are handled by separate consumers
        created in response to SpawnSessionStart (see _on_spawn_session_start).

        Returns:
            The subscription scope string.
        """
        return "session"

    def _create_assistant_message(self, session_id: str) -> tuple[str, MessageWithParts]:
        """Create a fresh assistant message for a new turn.

        Resolves the canonical message_id from pending IDs (set by the REST
        handler), agent/model info from session state and pending metadata,
        and constructs a ``MessageWithParts.assistant`` instance.

        Args:
            session_id: The session to create the message for.

        Returns:
            A tuple of (assistant_msg_id, assistant_msg).
        """
        assistant_msg_id = self._pending_message_ids.pop(session_id, None)
        if assistant_msg_id is None:
            assistant_msg_id = identifier.ascending("message")

        agent_name = "agentpool"
        model_id, provider_id = self.server_state.resolve_default_model_info()
        session_state = self.session_pool.sessions.get_session(session_id)
        if session_state is not None:
            agent_name = session_state.agent_name
        pending_meta = self._pending_message_metadata.pop(session_id, None)
        if pending_meta is not None:
            pending_model_id = pending_meta.get("model_id")
            if pending_model_id is not None:
                model_id = pending_model_id
            pending_provider_id = pending_meta.get("provider_id")
            if pending_provider_id is not None:
                provider_id = pending_provider_id

        assistant_msg = MessageWithParts.assistant(
            message_id=assistant_msg_id,
            session_id=session_id,
            time=MessageTime(created=now_ms()),
            agent_name=agent_name,
            model_id=model_id,
            parent_id=session_id,
            provider_id=provider_id,
            path=MessagePath(
                cwd=self.server_state.working_dir,
                root=self.server_state.working_dir,
            ),
            mode=agent_name,
        )
        return assistant_msg_id, assistant_msg

    async def _finalize_assistant_time(
        self,
        session_id: str,
        *,
        warn: bool = False,
    ) -> None:
        """Finalize time.completed on the assistant message if not already set.

        Used by StreamCompleteEvent, RunFailedEvent, and D1 reset to ensure
        the assistant message's time.completed is set before the turn ends
        or a new turn begins.

        Args:
            session_id: The session whose assistant message to finalize.
            warn: If True, log a warning when finalizing an incomplete turn
                (indicates StreamCompleteEvent was missed — D2 red flag).
        """
        ctx = self._contexts.get(session_id)
        if ctx is None:
            return
        info = ctx.assistant_msg.info
        if isinstance(info, AssistantMessage) and info.time.completed is None:
            if warn:
                logger.warning(
                    "Finalizing incomplete turn — StreamCompleteEvent missed",
                    session_id=session_id,
                    previous_message_id=ctx.assistant_msg_id,
                )
            info.time.completed = now_ms()
            await self.server_state.broadcast_event(MessageUpdatedEvent.create(info))
            await append_message_to_session(self.server_state, session_id, ctx.assistant_msg)

    async def _before_consumer_loop(self, session_id: str) -> None:
        """Set up per-session context before the consumer loop starts.

        On a fresh session, creates a new :class:`EventProcessorContext` and
        :class:`OpenCodeEventAdapter`.  On a **resumed** session (where
        :meth:`set_session_context_data` was called before ``start_event_consumer``),
        restores the context from the serialized data so that accumulated
        text, tool parts, and tracking state are preserved.

        !!! note
            Restored contexts are NOT re-broadcast to the frontend.
            The parts already exist on the client from the original session.

        Args:
            session_id: The session whose consumer is starting.
        """
        # --- Check for persisted resume context -------------------------------
        resume_data = self.get_session_context_data(session_id)
        if resume_data:
            ctx = EventProcessorContext.deserialize(
                resume_data,
                state=self.server_state,
                working_dir=self.server_state.working_dir,
            )
            event_adapter = OpenCodeEventAdapter(ctx)
            self._contexts[session_id] = ctx
            self._adapters[session_id] = event_adapter
            # On resume, the assistant message was already registered in the
            # original session. Mark it as such so _handle_event does not
            # re-broadcast MessageUpdatedEvent.
            self._message_registered[session_id] = True
            logger.info(
                "Restored EventProcessorContext from persisted data",
                session_id=session_id,
                response_text_len=len(ctx.response_text),
            )
            return

        # --- Fresh context (original behaviour) -------------------------------
        # D14: Use the canonical message_id from the REST handler if available
        # instead of generating an independent one. This resolves the dual
        # assistant_msg_id split-message issue.
        assistant_msg_id, assistant_msg = self._create_assistant_message(session_id)

        ctx = EventProcessorContext(
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            assistant_msg=assistant_msg,
            state=self.server_state,
            working_dir=self.server_state.working_dir,
        )
        event_adapter = OpenCodeEventAdapter(ctx)
        self._contexts[session_id] = ctx
        self._adapters[session_id] = event_adapter
        self._message_registered[session_id] = False

    async def _on_spawn_session_start(self, session_id: str, envelope: EventEnvelope) -> None:
        """Handle SpawnSessionStart by creating ToolPart and child consumer.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope containing the spawn session start event.
        """
        try:
            event = envelope.event
            if not isinstance(event, SpawnSessionStart):
                return
            child_id = event.child_session_id
            if not child_id or child_id == session_id:
                return

            ctx = self._contexts.get(session_id)
            if ctx is None:
                return

            # Best-effort: make child session visible in protocol state.
            # Failure here (e.g. incomplete mock, storage error) must not
            # block ToolPart creation or assistant message registration.
            try:
                await self._ensure_child_session_visible(session_id, event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to ensure child session visible",
                    session_id=session_id,
                    child_session_id=event.child_session_id,
                    exc_info=True,
                )

            # Ensure assistant message is registered before ToolPart creation
            if not self._message_registered.get(session_id, False):
                await append_message_to_session(self.server_state, session_id, ctx.assistant_msg)
                await self.server_state.broadcast_event(
                    MessageUpdatedEvent.create(ctx.assistant_msg.info)
                )
                self._message_registered[session_id] = True

            # Distinguish parent vs child events.  Child events arrive
            # raw via scope="descendants".
            # Use envelope.source_session_id because many streaming events
            # (e.g.PartDeltaEvent from pydantic-ai) do not carry a
            # session_id attribute on the payload itself.

            tool_part = await self._create_subagent_tool_part(session_id, event)
            if tool_part is not None:
                subagent_key = f"{event.depth}:{event.source_name}:{child_id}"
                ctx.add_subagent_tool_part(subagent_key, tool_part)

            # Track parent-child relationship for later ToolPart updates
            self._child_to_parent[child_id] = session_id
            self._child_spawns[child_id] = event
            self._children_of.setdefault(session_id, set()).add(child_id)

            # Start dedicated consumer for the child session
            await self.start_event_consumer(child_id)
        except Exception:
            logger.exception(
                "SpawnSessionStart handler failed",
                session_id=session_id,
                child_session_id=getattr(envelope.event, "child_session_id", None),
            )

    async def _ensure_child_session_visible(
        self,
        parent_session_id: str,
        spawn_event: SpawnSessionStart,
    ) -> None:
        """Create OpenCode-visible child session scaffolding for task navigation.

        SessionPool owns the execution session. OpenCode also needs a session
        model and at least the delegated prompt in message storage so the TUI
        can open the child task immediately, even before the child stream emits
        its first token.
        """
        child_session_id = spawn_event.child_session_id
        await ensure_session(
            self.server_state,
            child_session_id,
            parent_id=parent_session_id,
        )

        existing_messages = await get_messages_for_session(self.server_state, child_session_id)
        if existing_messages:
            return

        prompt = spawn_event.metadata.get("prompt") or spawn_event.description
        if not prompt:
            prompt = f"Run {spawn_event.source_name or 'subagent'} task"

        user_msg = UserMessage(
            id=identifier.ascending("message"),
            session_id=child_session_id,
            time=TimeCreated.now(),
            agent=spawn_event.source_name or "subagent",
            model=None,
        )
        user_msg_with_parts = MessageWithParts(info=user_msg)
        text_part = user_msg_with_parts.add_text_part(prompt)

        await append_message_to_session(self.server_state, child_session_id, user_msg_with_parts)
        await self.server_state.broadcast_event(MessageUpdatedEvent.create(user_msg))
        await self.server_state.broadcast_event(PartUpdatedEvent.create(text_part))

    async def _handle_event(  # noqa: PLR0915
        self, session_id: str, envelope: EventEnvelope
    ) -> None:
        """Handle a single event from the EventBus.

        Distinguishes parent vs child events (via the child-to-parent mapping),
        updates parent ToolParts on child completion/error, and converts all
        events to OpenCode SSE events via the adapter.

        Args:
            session_id: The session whose consumer received the event.
            envelope: The event envelope to handle.
        """
        try:
            event = envelope.event

            # SpawnSessionStart is handled in _on_spawn_session_start; skip here
            if isinstance(event, SpawnSessionStart):
                return

            # Check if this event originated from a child session.
            # Child events are handled by the child consumer (started via
            # _on_spawn_session_start). We only process parent events here.
            is_child_event = envelope.source_session_id != session_id

            if is_child_event:
                # Child completion/error: update parent ToolPart, then skip.
                # Other child events (PartDeltaEvent etc.) are handled by the
                # dedicated child consumer started in _on_spawn_session_start.
                parent_id = self._child_to_parent.get(envelope.source_session_id)
                if parent_id is not None:
                    spawn = self._child_spawns.get(envelope.source_session_id)
                    if isinstance(event, StreamCompleteEvent) and spawn is not None:
                        await self._update_parent_toolpart(
                            parent_session_id=parent_id,
                            child_session_id=envelope.source_session_id,
                            spawn_event=spawn,
                            event=event,
                        )
                    elif isinstance(event, RunErrorEvent) and spawn is not None:
                        await self._update_parent_toolpart_error(
                            parent_session_id=parent_id,
                            child_session_id=envelope.source_session_id,
                            spawn_event=spawn,
                            event=event,
                        )
                return

            # When scope="session", child events are received by the child
            # consumer itself (not the parent consumer). In that case,
            # session_id == envelope.source_session_id, so is_child_event is
            # False.  We still need to update the parent ToolPart when this
            # session is a child session.
            parent_id = self._child_to_parent.get(session_id)
            if parent_id is not None:
                spawn = self._child_spawns.get(session_id)
                if isinstance(event, StreamCompleteEvent) and spawn is not None:
                    await self._update_parent_toolpart(
                        parent_session_id=parent_id,
                        child_session_id=session_id,
                        spawn_event=spawn,
                        event=event,
                    )
                elif isinstance(event, RunErrorEvent) and spawn is not None:
                    await self._update_parent_toolpart_error(
                        parent_session_id=parent_id,
                        child_session_id=session_id,
                        spawn_event=spawn,
                        event=event,
                    )

            # Handle run lifecycle events for session status
            match event:
                case RunStartedEvent():
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="busy")
                    )
                case StreamCompleteEvent():
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="idle")
                    )
                    # D3: Finalize assistant message time.completed if not
                    # already set. The prompt_async path returns 204
                    # immediately and never sets time.completed on the
                    # assistant message, so the event bridge must do it here
                    # when StreamCompleteEvent arrives.
                    await self._finalize_assistant_time(session_id)
                case RunFailedEvent(exception=exc):
                    await set_session_status(
                        self.server_state, session_id, SessionStatus(type="idle")
                    )
                    # C3 fallback: If the agent crashed before any event
                    # triggered C3 registration, the assistant message was
                    # never appended to session state or broadcast via SSE.
                    # Register it now so _finalize_assistant_time can
                    # finalize and broadcast it.
                    if not self._message_registered.get(session_id, False):
                        ctx = self._contexts.get(session_id)
                        if ctx is not None:
                            await append_message_to_session(
                                self.server_state, session_id, ctx.assistant_msg
                            )
                            await self.server_state.broadcast_event(
                                MessageUpdatedEvent.create(ctx.assistant_msg.info)
                            )
                            self._message_registered[session_id] = True
                    # D3: Finalize time.completed for failed runs too,
                    # so the next turn's D1 reset doesn't log a
                    # false-positive warning about a missed
                    # StreamCompleteEvent.
                    await self._finalize_assistant_time(session_id)
                    if isinstance(exc, Exception) and not isinstance(exc, asyncio.CancelledError):
                        await self.server_state.broadcast_event(
                            SessionErrorEvent.from_exception(exc, session_id=session_id)
                        )
                case _:
                    pass

            # C4: CustomEvent wraps SSE broadcast events (e.g.
            # SessionCreatedEvent) republished from the OpenCodeEventBridge.
            # These are not real agent events and must NOT trigger assistant
            # message registration. Only skip bridge-wrapped CustomEvents
            # (source="opencode_event_bridge"); tool-emitted CustomEvents
            # (source=None or tool name) may carry meaningful payload and
            # should fall through to adapter processing.
            if isinstance(event, CustomEvent) and event.source == "opencode_event_bridge":
                return

            ctx = self._contexts.get(session_id)
            if ctx is None:
                return

            # D1: On RunStartedEvent for a subsequent turn (consumer already
            # running from turn 1), reset per-turn state so turn 2 gets a
            # fresh assistant message ID instead of reusing turn 1's.
            # _before_consumer_loop() only runs once (consumer start is
            # idempotent), so turns 2+ need this explicit reset.
            if isinstance(event, RunStartedEvent) and self._message_registered.get(
                session_id, False
            ):
                # D2/D3: Finalize previous turn's assistant message if
                # StreamCompleteEvent was missed or not yet processed. This
                # prevents the previous turn's time.completed from being lost
                # when the D1 reset creates a new message. The warning makes
                # the D2 red flag (running turn killed by new turn) visible.
                await self._finalize_assistant_time(session_id, warn=True)

                assistant_msg_id, assistant_msg = self._create_assistant_message(session_id)
                ctx.assistant_msg_id = assistant_msg_id
                ctx.assistant_msg = assistant_msg
                # Reset per-turn mutable tracking state
                ctx.response_text = ""
                ctx.text_part = None
                ctx.reasoning_part = None
                ctx.tool_parts.clear()
                ctx.tool_outputs.clear()
                ctx.tool_inputs.clear()
                ctx.subagent_tool_parts.clear()
                ctx.is_errored = False
                ctx.input_tokens = 0
                ctx.output_tokens = 0
                ctx.total_cost = 0.0
                ctx.stream_start_ms = now_ms()

                self._message_registered[session_id] = False

            # Update assistant message with real agent info from RunStartedEvent.
            # RunStartedEvent is the first event in a run and carries the real
            # agent_name from the RunLoop. This is more reliable than the session
            # state lookup in _before_consumer_loop (which may not have the
            # agent name for sessions created outside the REST handler).
            if isinstance(event, RunStartedEvent) and event.agent_name:
                info = ctx.assistant_msg.info
                if isinstance(info, AssistantMessage):
                    info.agent = event.agent_name
                    info.mode = event.agent_name

            # NOTE: Do NOT overwrite ctx.assistant_msg_id from event.message_id.
            # NativeTurn generates its own UUID for _message_id (uuid4().hex)
            # which is different from the canonical assistant_msg_id generated
            # by the REST handler (identifier.ascending("message", ...)).
            # Overwriting causes a mismatch: parts get the NativeTurn UUID as
            # their message_id while the assistant message keeps the REST
            # handler's ID, so the UI cannot associate parts with the message.
            # The canonical assistant_msg_id from the REST handler is correct.

            # Steer split: When a steer UserMessageInsertedEvent was received
            # during an active turn, the next PartStartEvent (from the new
            # ModelRequestNode after steer drain) triggers a logical turn split.
            # This finalizes the current assistant message (A1) and creates a
            # new one (A2) with a fresh message ID, so the steer user message
            # sorts between A1 and A2 in the TUI's lexicographic message ID
            # ordering. Without this split, the steer user message would sort
            # after the entire assistant message (which reuses one ID for both
            # pre-steer and post-steer content).
            if (
                isinstance(event, PartStartEvent)
                and ctx._steer_received
                and self._message_registered.get(session_id, False)
            ):
                await self._finalize_assistant_time(session_id)

                assistant_msg_id, assistant_msg = self._create_assistant_message(session_id)
                ctx.assistant_msg_id = assistant_msg_id
                ctx.assistant_msg = assistant_msg
                # Reset per-turn mutable tracking state (same as D1 reset)
                ctx.response_text = ""
                ctx.text_part = None
                ctx.reasoning_part = None
                ctx.tool_parts.clear()
                ctx.tool_outputs.clear()
                ctx.tool_inputs.clear()
                ctx.subagent_tool_parts.clear()
                ctx.is_errored = False
                ctx.input_tokens = 0
                ctx.output_tokens = 0
                ctx.total_cost = 0.0
                ctx.stream_start_ms = now_ms()

                self._message_registered[session_id] = False
                ctx._steer_received = False

            # Set _steer_received flag when a steer user message arrives during
            # an active turn. The next PartStartEvent will trigger the split.
            if isinstance(event, UserMessageInsertedEvent) and event.delivery == "steer":
                ctx._steer_received = True

            # Register assistant message on first non-spawn, non-custom,
            # non-user-message-inserted event.
            # C3: The event bridge is the sole broadcast point for the assistant
            # message. This ensures the message is visible only when the agent
            # actually starts producing events, not before.
            # UserMessageInsertedEvent creates a user message, not an assistant
            # message, so it must not trigger assistant registration.
            is_user_message_inserted = isinstance(event, UserMessageInsertedEvent)
            if not is_user_message_inserted and not self._message_registered.get(session_id, False):
                await append_message_to_session(self.server_state, session_id, ctx.assistant_msg)
                await self.server_state.broadcast_event(
                    MessageUpdatedEvent.create(ctx.assistant_msg.info)
                )
                # C3: Also broadcast a StepStartPart so the frontend sees the
                # step-start indicator when the agent actually begins work.
                step_start_part = StepStartPart(
                    id=identifier.ascending("part"),
                    message_id=ctx.assistant_msg_id,
                    session_id=session_id,
                )
                ctx.assistant_msg.parts.append(step_start_part)
                await self.server_state.broadcast_event(PartUpdatedEvent.create(step_start_part))
                self._message_registered[session_id] = True

            adapter = self._adapters.get(session_id)
            if adapter is None:
                return

            async for oc_event in adapter.convert_event(event):
                await self.server_state.broadcast_event(oc_event)
        except Exception:
            logger.exception(
                "Event handler failed",
                session_id=session_id,
                event_type=type(envelope.event).__name__,
            )

    async def _after_consumer_loop(self, session_id: str) -> None:
        """Clean up per-session context after the consumer loop exits.

        Args:
            session_id: The session whose consumer has stopped.
        """
        # Stop any child consumers that were started from this session
        for child_id in list(self._children_of.get(session_id, [])):
            try:
                await self.stop_event_consumer(child_id)
            except Exception:
                logger.exception(
                    "Failed to stop child event consumer",
                    child_id=child_id,
                )
        self._children_of.pop(session_id, None)

        # Clean up per-session state
        self._contexts.pop(session_id, None)
        self._adapters.pop(session_id, None)
        self._message_registered.pop(session_id, None)
        self._child_to_parent.pop(session_id, None)
        self._child_spawns.pop(session_id, None)

    # ------------------------------------------------------------------
    # Backward-compatible wrappers (used by tests)
    # ------------------------------------------------------------------

    async def _start_event_consumer(self, session_id: str) -> None:
        """Backward-compatible wrapper for the mixin's start_event_consumer."""
        await self.start_event_consumer(session_id)
        logger.info("Started session-scoped event consumer", session_id=session_id)

    async def _stop_event_consumer(self, session_id: str) -> None:
        """Backward-compatible wrapper for the mixin's stop_event_consumer."""
        await self.stop_event_consumer(session_id)
        logger.info("Stopped session-scoped event consumer", session_id=session_id)

    async def subscribe_to_events(self, session_id: str) -> AsyncIterator[Any]:
        """Subscribe to session events and yield converted OpenCode events.

        Creates a minimal EventProcessorContext so that AgentPool events
        can be converted to OpenCode SSE events via OpenCodeEventAdapter.

        Args:
            session_id: The session to subscribe to.

        Yields:
            OpenCode Event objects.
        """
        assistant_msg_id = identifier.ascending("message")
        assistant_msg = MessageWithParts(
            info=UserMessage(
                id=assistant_msg_id,
                session_id=session_id,
                time=TimeCreated.now(),
            )
        )
        ctx = EventProcessorContext(
            session_id=session_id,
            assistant_msg_id=assistant_msg_id,
            assistant_msg=assistant_msg,
            state=self.server_state,
            working_dir=self.server_state.working_dir,
        )
        event_adapter = OpenCodeEventAdapter(ctx)
        event_stream = await self.session_pool.event_bus.subscribe(session_id)

        try:
            from agentpool.orchestrator.core import drain_and_merge

            async for event in drain_and_merge(event_stream):
                async for oc_event in event_adapter.convert_event(event.event):
                    yield oc_event
        finally:
            await self.session_pool.event_bus.unsubscribe(session_id, event_stream)
