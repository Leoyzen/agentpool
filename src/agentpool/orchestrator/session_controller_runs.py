"""Run lifecycle mixin for SessionController.

Extracted from session_controller.py as part of the session-debt-cleanup file split.
Contains run handle creation, message routing, and run lifecycle methods.

In the per-prompt RunHandle model, each RunHandle executes exactly one
turn. ``_consume_run()`` chains RunHandles by checking ``prompt_queue``
after each turn terminates.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
import uuid

import logfire

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    RunFailedEvent,
    UserMessageInsertedEvent,
)
from agentpool.log import get_logger
from agentpool.observability.spans import safe_span
from agentpool.orchestrator.run import RunHandle, inject_cancelled_tool_results


if TYPE_CHECKING:
    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.delegation import AgentPool
    from agentpool.orchestrator.event_bus import EventBus
    from agentpool.orchestrator.session_controller import SessionState


logger = get_logger(__name__)


class SessionControllerRunsMixin:
    """Mixin providing run lifecycle and message routing methods for SessionController.

    Attributes:
        pool: The agent pool (provided by SessionController).
        _sessions: Active sessions dict (provided by SessionController).
        _runs: Active run handles (provided by SessionController).
        _lock: Global lock (provided by SessionController).
        _event_bus: Event bus for cross-turn events (provided by SessionController).
        _background_tasks: Background task references (provided by SessionController).
    """

    pool: AgentPool[Any]
    _sessions: dict[str, SessionState]
    _runs: dict[str, RunHandle]
    _lock: asyncio.Lock
    _event_bus: EventBus | None
    _background_tasks: set[asyncio.Task[Any]]

    def get_session(self, session_id: str) -> SessionState | None: ...

    async def _emit_user_message_inserted(
        self,
        session_id: str,
        content: str | list[Any],
        delivery: str,
        source: str,
        message_id: str | None = None,
    ) -> None:
        """Publish ``UserMessageInsertedEvent`` to the EventBus.

        Wraps emission in a ``logfire.span`` to prevent orphan traces.
        Catches all exceptions and logs a warning — emission failures
        must never break the routing path.

        Args:
            session_id: The session the message was inserted into.
            content: Message content (text or multi-modal part list).
            delivery: ``"initial"``, ``"steer"``, or ``"followup"``.
            source: ``"protocol"``, ``"background_task"``, or ``"internal"``.
            message_id: Optional message ID; auto-generated if ``None``.
        """
        with logfire.span(
            "event.user_message_inserted.emit",
            session_id=session_id,
            delivery=delivery,
            source=source,
        ):
            try:
                event = UserMessageInsertedEvent(
                    session_id=session_id,
                    message_id=message_id or str(uuid.uuid4()),
                    content=content,
                    delivery=delivery,  # type: ignore[arg-type]
                    source=source,  # type: ignore[arg-type]
                )
                if self._event_bus is not None:
                    await self._event_bus.publish(session_id, event)
            except Exception:  # noqa: BLE001
                logger.warning(
                    "Failed to emit UserMessageInsertedEvent",
                    exc_info=True,
                )

    async def _consume_run(self, run_handle: RunHandle, initial_prompt: str | list[Any]) -> None:
        """Drive RunHandle execution to completion, chaining prompts.

        In the per-prompt model, each RunHandle executes exactly one turn
        and terminates. This method drains the generator, then checks
        ``prompt_queue`` for queued followup prompts. If non-empty, it
        creates a new RunHandle and chains to the next turn.

        The ``_request_lock`` is held during the chaining check to prevent
        ``_route_message()`` from creating a concurrent RunHandle.

        Args:
            run_handle: The initial run handle whose ``start()`` to consume.
            initial_prompt: The first user prompt (text or structured content).
        """
        with safe_span(
            "session.consume_run",
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
        ):
            session = self.get_session(run_handle.session_id)
            current_prompt: str | list[Any] = initial_prompt
            current_handle = run_handle
            while True:
                gen = current_handle.start(current_prompt)
                turn_failed = False
                try:
                    async for _event in gen:
                        pass
                except Exception as exc:
                    logger.exception(
                        "RunHandle.start() raised for run_id=%s session_id=%s",
                        current_handle.run_id,
                        current_handle.session_id,
                    )
                    error_event = RunErrorEvent(
                        message=f"{type(exc).__name__}: {exc}",
                        run_id=current_handle.run_id,
                        agent_name=(
                            current_handle.agent.name
                            if current_handle.agent is not None
                            else current_handle.agent_type
                        ),
                    )
                    if self._event_bus is not None:
                        await self._event_bus.publish(current_handle.session_id, error_event)
                        await self._event_bus.publish(
                            current_handle.session_id,
                            RunFailedEvent(
                                run_id=current_handle.run_id,
                                session_id=current_handle.session_id,
                                exception=exc,
                            ),
                        )
                    turn_failed = True

                # Generator terminated naturally — clean up this RunHandle.
                self._runs.pop(current_handle.run_id, None)

                if turn_failed:
                    # On error, do NOT chain — mark idle and break.
                    if session is not None:
                        async with session._request_lock:
                            if session.current_run_id == current_handle.run_id:
                                session.set_current_run_id(None)
                    break

                # Check prompt_queue for chained prompts (holding _request_lock
                # to prevent _route_message() from racing).
                if session is None:
                    break
                async with session._request_lock:
                    if session.current_run_id == current_handle.run_id:
                        session.set_current_run_id(None)
                    if session.prompt_queue.empty():
                        break  # No more prompts, session goes idle.
                    try:
                        next_prompt = session.prompt_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    # Publish UserMessageInsertedEvent for followup-from-queue.
                    # This covers the gap where _create_per_prompt_handle()
                    # creates a RunHandle WITHOUT calling _route_message().
                    await self._emit_user_message_inserted(
                        current_handle.session_id,
                        next_prompt,
                        delivery="followup",
                        source="internal",
                    )
                    # Create a new RunHandle for the next prompt.
                    agent = current_handle.agent
                    if agent is None:
                        break
                    current_handle = self._create_per_prompt_handle(session, agent, next_prompt)
                    current_prompt = next_prompt
                    # Loop continues — execute the next turn.

    def _create_per_prompt_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        prompt: str | list[Any],
    ) -> RunHandle:
        """Create a new RunHandle for a single prompt (chaining).

        Bridges ``_message_history`` from ``agent.conversation`` and
        passes SessionState lifecycle dimensions. This is the per-prompt
        creation path used by ``_consume_run()`` chaining.

        Args:
            session: The session state.
            agent: The agent instance.
            prompt: The prompt for this turn.

        Returns:
            A newly created and registered RunHandle.
        """
        # Bridge agent.conversation → list[ModelMessage]
        model_messages: list[ModelMessage] = []
        conversation = agent.conversation
        if conversation is not None:
            for chat_msg in conversation.get_history():
                model_messages.extend(chat_msg.messages)
        model_messages = inject_cancelled_tool_results(model_messages)

        run_ctx = AgentRunContext(
            session_id=session.session_id,
            event_bus=self._event_bus,
        )

        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session.session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=self._event_bus,
            session=session,
            run_ctx=run_ctx,
            _message_history=model_messages,
            _host_context=session._host_context,
            _agent_registry=session._agent_registry,
        )
        self._runs[run_handle.run_id] = run_handle
        session.set_current_run_id(run_handle.run_id)
        return run_handle

    @logfire.instrument("session.start_run_handle")
    def _start_run_handle(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
        content: str | list[Any],
        *,
        deps: Any = None,
        message_id: str | None = None,
    ) -> str | None:
        """Create, register, and launch a RunHandle for a single prompt.

        In the per-prompt model, the RunHandle executes one turn and
        terminates. ``_consume_run()`` handles chaining via
        ``prompt_queue``.

        Lifecycle dimensions are sourced from ``SessionState`` (initialized
        in ``get_or_create_session_agent()``). The initial prompt is passed
        directly to ``start()``.

        Args:
            session: The session state.
            agent: The agent instance (native or ACP).
            session_id: The session identifier.
            content: The initial prompt (text or structured content blocks).
            deps: Optional dependencies to pass to the agent run context.
            message_id: Optional message ID for the initial prompt.

        Returns:
            The ``message_id`` string on success, ``None`` if the handle
            is closing.
        """
        # Bridge agent.conversation → list[ModelMessage]
        model_messages: list[ModelMessage] = []
        conversation = agent.conversation
        if conversation is not None:
            for chat_msg in conversation.get_history():
                model_messages.extend(chat_msg.messages)
        model_messages = inject_cancelled_tool_results(model_messages)

        event_bus = self._event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus, deps=deps)

        # Reset the agent's _cancelled flag from any prior run.
        agent._cancelled = False

        run_handle = RunHandle(
            run_id=uuid.uuid4().hex,
            session_id=session_id,
            agent_type=agent.AGENT_TYPE,
            agent=agent,
            event_bus=event_bus,
            session=session,
            run_ctx=run_ctx,
            _message_history=model_messages,
            _host_context=session._host_context,
            _agent_registry=session._agent_registry,
        )
        self._runs[run_handle.run_id] = run_handle
        session.set_current_run_id(run_handle.run_id)

        # Generate message_id for the initial prompt.
        from agentpool.lifecycle.types import Feedback

        fb_kwargs: dict[str, Any] = {}
        if message_id is not None:
            fb_kwargs["message_id"] = message_id
        if isinstance(content, list):
            fb = Feedback(content="", is_steer=False, content_blocks=content, **fb_kwargs)
        else:
            fb = Feedback(content=content, is_steer=False, **fb_kwargs)
        mid = fb.message_id

        task = asyncio.create_task(self._consume_run(run_handle, content))
        # Keep a strong reference to prevent GC from destroying the task.
        self._background_tasks.add(task)

        def _on_run_done(t: asyncio.Task[Any], rid: str = run_handle.run_id) -> None:
            self._background_tasks.discard(t)
            if not t.cancelled() and t.exception() is not None:
                logger.error(
                    "Background run task failed for run_id=%s: %s",
                    rid,
                    t.exception(),
                )
            self._cleanup_run(rid)

        task.add_done_callback(_on_run_done)
        return mid

    @logfire.instrument("session.route_message")
    async def _route_message(
        self,
        session: SessionState,
        agent: BaseAgent[Any, Any],
        session_id: str,
        content: str | list[Any],
        *,
        priority: str = "when_idle",
        deps: Any = None,
        message_id: str | None = None,
        delivery: str | None = None,
    ) -> str | None:
        """Route a message to the appropriate handler based on session state.

        Idle sessions create a RunHandle via :meth:`_start_run_handle`.
        Busy sessions call ``RunHandle.steer()`` (``"asap"``) or enqueue
        to ``SessionState.prompt_queue`` (``"when_idle"``).

        Before the routing action, publishes ``UserMessageInsertedEvent``
        to the EventBus (if available) so protocol frontends can display
        the user message.

        Args:
            session: The live session state (must already exist).
            agent: The resolved agent instance for this session.
            session_id: Target session identifier.
            content: Message / prompt content (text or structured content
                blocks).
            priority: ``"when_idle"`` to queue, ``"asap"`` to inject.
            deps: Optional dependencies for the agent run context.
            message_id: Optional message ID.
            delivery: Optional delivery label (``"initial"``, ``"steer"``,
                ``"followup"``). If ``None``, inferred from session state
                and priority.

        Returns:
            The ``message_id`` string on success, ``None`` for rejection.
        """
        resolved = {"steer": "asap", "followup": "when_idle"}.get(priority, priority)
        async with session._request_lock:
            if session.closing or session.is_closing:
                return None
            # Stale-run detection: if current_run_id points to a missing
            # or completed run, clear it and start a new run.
            if session.current_run_id is not None:
                existing_run = self._runs.get(session.current_run_id)
                if existing_run is None or existing_run.complete_event.is_set():
                    session.set_current_run_id(None)
            if session.current_run_id is None:
                # Idle session — initial delivery.
                inferred_delivery = delivery or "initial"
                await self._emit_user_message_inserted(
                    session_id,
                    content,
                    delivery=inferred_delivery,
                    source="protocol",
                    message_id=message_id,
                )
                return self._start_run_handle(
                    session,
                    agent,
                    session_id,
                    content,
                    deps=deps,
                    message_id=message_id,
                )
            run = self._runs.get(session.current_run_id) if session.current_run_id else None
            if run is not None:
                if resolved == "asap":
                    # Steer — inject into active turn.
                    inferred_delivery = delivery or "steer"
                    await self._emit_user_message_inserted(
                        session_id,
                        content,
                        delivery=inferred_delivery,
                        source="protocol",
                        message_id=message_id,
                    )
                    return run.steer(content, message_id=message_id)
                # Followup: enqueue to SessionState.prompt_queue.
                inferred_delivery = delivery or "followup"
                await self._emit_user_message_inserted(
                    session_id,
                    content,
                    delivery=inferred_delivery,
                    source="protocol",
                    message_id=message_id,
                )
                from agentpool.lifecycle.types import Feedback

                fb_kwargs: dict[str, Any] = {}
                if message_id is not None:
                    fb_kwargs["message_id"] = message_id
                if isinstance(content, list):
                    fb = Feedback(content="", is_steer=False, content_blocks=content, **fb_kwargs)
                else:
                    fb = Feedback(content=content, is_steer=False, **fb_kwargs)
                session.prompt_queue.put_nowait(fb.content_blocks or fb.content)
                return None  # Queued — _wait_and_finalize will early-return
        return None

    def cancel_run_for_session(self, session_id: str) -> bool:
        """Cancel the active run for a session.

        Only cancels the run if the RunHandle is still active
        (``complete_event`` is not set). If the handle has already
        completed, the cancel is skipped.

        Args:
            session_id: The session whose run should be cancelled.

        Returns:
            ``True`` if cancellation was initiated, ``False`` if the
            session/run was not found or already completed.
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        run_id = session.current_run_id
        if run_id is None:
            return False
        run_handle = self._runs.get(run_id)
        if run_handle is None:
            return False
        if run_handle.complete_event.is_set():
            logger.warning(
                "cancel_run_for_session: RunHandle %s already completed, skipping cancel",
                run_id,
            )
            return False
        run_handle.cancel()
        return True

    def revoke_inject(self, session_id: str, message_id: str) -> bool:
        """Revoke a pending steer or followup message by ID.

        In the per-prompt model, revocation is handled by
        ``SessionState.revoke()`` which cancels queued steer messages
        in ``feedback_queue``.

        Args:
            session_id: The session containing the message.
            message_id: The ID of the message to revoke.

        Returns:
            ``True`` if revoked or already gone (idempotent), ``False``
            if the session is not found.
        """
        session = self.get_session(session_id)
        if session is None:
            return False
        return session.revoke(message_id)

    async def wait_for_completion(self, session_id: str, timeout: float | None = 300) -> str:
        """Wait for the active run on a session to complete.

        In the per-prompt model, ``complete_event`` fires when the
        RunHandle's single-turn generator terminates. If
        ``_consume_run()`` chains to a new RunHandle, the caller must
        re-check ``session.current_run_id`` to detect the new turn.

        Args:
            session_id: The session to wait for.
            timeout: Maximum seconds to wait. Defaults to 300 seconds.

        Returns:
            The ``session_id`` on completion.

        Raises:
            SessionNotFoundError: If the session does not exist.
            asyncio.TimeoutError: If the run does not complete within
                ``timeout`` seconds.
        """
        from agentpool.orchestrator.session_controller import SessionNotFoundError

        if timeout is None:
            timeout = 300
        session = self.get_session(session_id)
        if session is None:
            raise SessionNotFoundError(session_id)
        run_id = session.current_run_id
        if run_id is None:
            return session_id
        run_handle = self._runs.get(run_id)
        if run_handle is None:
            return session_id
        # In per-prompt model, complete_event fires when the single-turn
        # generator terminates — same semantic as the old _turn_complete_event.
        async with asyncio.timeout(timeout):
            await run_handle.complete_event.wait()
        return session_id

    def _cleanup_run(self, run_id: str) -> None:
        """Clean up a run after it completes.

        Removes the handle from _runs and signals completion.

        Args:
            run_id: The run ID to clean up.
        """
        run_handle = self._runs.pop(run_id, None)
        if run_handle is not None:
            run_handle.complete_event.set()
            # Clear current_run_id if it still points to this run.
            session = self.get_session(run_handle.session_id)
            if session is not None and session.current_run_id == run_id:
                session.set_current_run_id(None)
