"""Run lifecycle mixin for SessionController.

Extracted from session_controller.py as part of the session-debt-cleanup file split.
Contains run handle creation, message routing, and run lifecycle methods.
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
)
from agentpool.lifecycle import RunState
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

    async def _consume_run(self, run_handle: RunHandle, initial_prompt: str) -> None:
        """Drive a RunHandle.start() async generator to completion.

        Events are published to the EventBus inside ``start()``, so this
        coroutine only needs to keep the generator alive. The generator
        stays alive across turns — ``start()`` implements an idle/wake/turn
        loop that blocks on the CommChannel feedback queue between turns.
        Closing the generator prematurely (e.g. after the first
        ``StreamCompleteEvent``) would kill the RunHandle and prevent
        subsequent turns from executing.

        The generator exits naturally when ``RunHandle.close()`` is called
        during session cleanup (sets ``_closing`` → loop exits → ``finally``
        block sets ``complete_event``). This coroutine does NOT call
        ``gen.aclose()`` — that is handled by ``RunHandle.close()``.

        If ``start()`` raises an exception before exiting, a
        ``RunErrorEvent`` and ``RunFailedEvent`` are published to the
        EventBus so that subscribers (e.g. background_output in
        BackgroundTaskCapability) are unblocked instead of waiting forever.

        Uses ``safe_span(...)`` instead of ``@logfire.instrument`` or raw
        ``with logfire.span(...)`` because this method is invoked via
        ``asyncio.create_task()`` from ``_start_run_handle()``. Logfire's
        ``@handle_internal_errors`` on ``LogfireSpan.__exit__`` can swallow
        ``ValueError`` from ``_detach()`` and skip ``_end()``, leaving the
        span unended and unexported. ``safe_span`` calls ``_detach()`` and
        ``_end()`` separately to prevent this.

        Args:
            run_handle: The run handle whose ``start()`` to consume.
            initial_prompt: The first user prompt.
        """
        with safe_span(
            "session.consume_run",
            session_id=run_handle.session_id,
            run_id=run_handle.run_id,
        ):
            gen = run_handle.start(initial_prompt)
            try:
                # Drain the generator to completion. Do NOT break on
                # StreamCompleteEvent or RunErrorEvent — the generator
                # stays alive across turns (idle/wake/turn loop). The
                # generator exits naturally when RunHandle.close() sets
                # _closing and the loop exits.
                async for _event in gen:
                    pass
            except Exception as exc:
                logger.exception(
                    "RunHandle.start() raised for run_id=%s session_id=%s",
                    run_handle.run_id,
                    run_handle.session_id,
                )
                error_event = RunErrorEvent(
                    message=f"{type(exc).__name__}: {exc}",
                    run_id=run_handle.run_id,
                    agent_name=(
                        run_handle.agent.name
                        if run_handle.agent is not None
                        else run_handle.agent_type
                    ),
                )
                if self._event_bus is not None:
                    await self._event_bus.publish(run_handle.session_id, error_event)
                    await self._event_bus.publish(
                        run_handle.session_id,
                        RunFailedEvent(
                            run_id=run_handle.run_id,
                            session_id=run_handle.session_id,
                            exception=exc,
                        ),
                    )

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
        """Create, register, and launch a RunHandle via the new path.

        Creates the RunHandle with ProtocolTrigger and ProtocolChannel
        lifecycle dimensions when an EventBus is available. The
        ProtocolChannel publishes events to the EventBus (replacing
        the direct ``event_bus.publish()`` calls in ``start()``),
        and the ProtocolTrigger allows steer/followup delivery via
        ``trigger.deliver()``.

        The initial prompt is routed through ``run_handle.followup()``
        before ``start()`` is called (D17). This ensures the initial
        prompt gets a ``message_id`` and can be revoked before delivery.
        ``start()`` is called with an empty string — the first
        ``_idle_loop()`` iteration drains the ``followup()`` feedback
        and uses it as the first turn's prompt.

        Args:
            session: The session state.
            agent: The agent instance (native or ACP).
            session_id: The session identifier.
            content: The initial prompt (text or structured content blocks).
            deps: Optional dependencies to pass to the agent run context
                (e.g. delegation_depth from BackgroundTaskCapability).
            message_id: Optional message ID for the initial prompt.
                Auto-generated as UUID4 if not provided.

        Returns:
            The ``message_id`` string on success, ``None`` if the handle
            is closing.
        """
        from agentpool.lifecycle import (
            MemoryJournal,
            ProtocolChannel,
            ProtocolTrigger,
        )

        event_bus = self._event_bus
        run_ctx = AgentRunContext(session_id=session_id, event_bus=event_bus, deps=deps)
        # Bridge agent.conversation (ChatMessage list) → list[ModelMessage]
        # so the new RunHandle has the full conversation history from prior
        # turns. Without this, each new RunHandle starts with empty
        # _message_history and the model loses all context.
        # All agent types (BaseAgent, NativeAgent, ACPAgent) have a
        # ``conversation`` attribute (MessageHistory).
        model_messages: list[ModelMessage] = []
        conversation = agent.conversation
        if conversation is not None:
            for chat_msg in conversation.get_history():
                model_messages.extend(chat_msg.messages)
        # Inject RetryPromptPart for any trailing unprocessed tool calls
        # (e.g. from a cancelled turn). Without this, PydanticAI rejects
        # the next user prompt with "unprocessed tool calls" error.
        model_messages = inject_cancelled_tool_results(model_messages)

        # Create lifecycle dimensions for protocol server integration.
        trigger = ProtocolTrigger()
        comm_channel: ProtocolChannel | None = None
        journal: MemoryJournal | None = None
        if event_bus is not None:
            journal = MemoryJournal()
            comm_channel = ProtocolChannel(
                journal=journal,
                event_bus=event_bus,
                session_id=session_id,
            )

        host_ctx = self.pool.get_context()
        # Build an AgentRegistry with agent names from the manifest.
        # Agents are created lazily, so the registry stores names only.
        # The DelegationService uses session_pool to spawn actual instances.
        from agentpool.host.registry import AgentRegistry

        agent_registry = AgentRegistry(
            dict.fromkeys(self.pool.manifest.agents),  # type: ignore[arg-type]
        )

        # Reset the agent's _cancelled flag from any prior run.
        # BaseAgent.interrupt() sets _cancelled = True, but it's only
        # reset in run_in_background() and _prepare_standalone_context().
        # Without this, a stale _cancelled = True from a previous
        # aborted run would persist and cause is_cancelled() to return
        # True for ACP sessions.
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
            _trigger_source=trigger,
            _comm_channel=comm_channel,
            _journal=journal,
            _host_context=host_ctx,
            _agent_registry=agent_registry,
        )
        self._runs[run_handle.run_id] = run_handle
        session.current_run_id = run_handle.run_id

        # D17: Route initial prompt through followup() before start().
        # This ensures the initial prompt gets a message_id and can be
        # revoked before delivery. start("") is called with empty prompt —
        # the first _idle_loop() iteration drains the followup() feedback.
        mid = run_handle.followup(content, message_id=message_id)

        task = asyncio.create_task(self._consume_run(run_handle, ""))
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
    ) -> str | None:
        """Route a message to the appropriate handler based on session state.

        Dispatch logic extracted from :meth:`receive_request` so that
        callers (e.g. :meth:`SessionPool.send_message`) can route messages
        without the deprecated ``priority`` string parameter.

        Idle sessions create a RunHandle via :meth:`_start_run_handle`.
        Busy sessions call ``RunHandle.steer()`` (``"asap"``) or
        ``RunHandle.followup()`` (``"when_idle"``).

        Args:
            session: The live session state (must already exist).
            agent: The resolved agent instance for this session.
            session_id: Target session identifier.
            content: Message / prompt content (text or structured content
                blocks). Passed through without stringification.
            priority: ``"when_idle"`` to queue, ``"asap"`` to inject.
                Aliases: ``"steer"`` → ``"asap"``, ``"followup"`` →
                ``"when_idle"``.
            deps: Optional dependencies for the agent run context.
            message_id: Optional message ID. Auto-generated as UUID4 if
                not provided.

        Returns:
            The ``message_id`` string on success, ``None`` for rejection.
        """
        resolved = {"steer": "asap", "followup": "when_idle"}.get(priority, priority)
        # D9: Do NOT stringify list content. Pass str | list[Any] directly
        # to steer()/followup() which accept both via Feedback.content_blocks.
        async with session._request_lock:
            if session.closing or session.is_closing:
                return None
            # Stale-run detection: if current_run_id points to a missing
            # or terminal run, clear it and start a new run.
            if session.current_run_id is not None:
                existing_run = self._runs.get(session.current_run_id)
                if existing_run is None or existing_run._run_state == RunState.DONE:
                    session.current_run_id = None
            if session.current_run_id is None:
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
                    return run.steer(content, message_id=message_id)
                return run.followup(content, message_id=message_id)
        return None

    def cancel_run_for_session(self, session_id: str) -> bool:
        """Cancel the active run for a session.

        Only cancels the run if the RunHandle is in the RUNNING state.
        If the handle is IDLE (between turns) or DONE, the cancel is
        skipped to avoid killing the idle/wake/turn generator
        prematurely — an IDLE RunHandle can still process subsequent
        turns.

        Args:
            session_id: The session whose run should be cancelled.

        Returns:
            ``True`` if cancellation was initiated (RunHandle was
            RUNNING), ``False`` if the session/run was not found or
            the RunHandle was not in the RUNNING state.
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
        if run_handle._run_state != RunState.RUNNING:
            logger.warning(
                "cancel_run_for_session: RunHandle %s is not RUNNING (state=%s), skipping cancel",
                run_id,
                run_handle._run_state,
            )
            return False
        run_handle.cancel()
        return True

    def revoke_inject(self, session_id: str, message_id: str) -> bool:
        """Revoke a pending steer or followup message by ID.

        Delegates to ``RunHandle.revoke()`` on the session's active run.
        A message can be revoked if still pending in the CommChannel queue
        or PydanticAI ``pending_messages`` list. Once delivered to the
        model, revocation returns ``False``.

        Args:
            session_id: The session containing the message.
            message_id: The ID of the message to revoke.

        Returns:
            ``True`` if revoked or already gone (idempotent), ``False``
            if the session/run is not found or the message was already
            delivered.
        """
        session = self.get_session(session_id)
        if session is None or session.current_run_id is None:
            return False
        run_handle = self._runs.get(session.current_run_id)
        if run_handle is None:
            return False
        return run_handle.revoke(message_id)

    async def wait_for_completion(self, session_id: str, timeout: float | None = 300) -> str:
        """Wait for the active run on a session to complete a single turn.

        Looks up the active run via ``session.current_run_id`` and awaits
        either ``run_handle._turn_complete_event`` (single-turn completion)
        or ``run_handle.complete_event`` (full run completion), whichever
        fires first. This decouples callers from the ``RunHandle`` type
        entirely while supporting multi-turn RunHandles that stay alive
        between turns.

        Args:
            session_id: The session to wait for.
            timeout: Maximum seconds to wait. Defaults to 300 seconds.
                ``None`` is treated as 300 seconds. Callers should cancel
                the run on ``TimeoutError`` to break through ``__aexit__``
                hangs.

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
        # Wait for either the per-turn completion event or the full run
        # completion event, whichever fires first. _turn_complete_event
        # is cleared at turn start (run.py _execute_turn) and set when a
        # single turn finishes (normally or via cancel). complete_event
        # is set in start()'s finally block when the RunHandle exits.
        turn_done = asyncio.ensure_future(run_handle._turn_complete_event.wait())
        run_done = asyncio.ensure_future(run_handle.complete_event.wait())
        try:
            done, _pending = await asyncio.wait(
                {turn_done, run_done},
                timeout=timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise TimeoutError
        finally:
            turn_done.cancel()
            run_done.cancel()
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
            # This is a safety net — normally start() clears it, but if
            # the run died unexpectedly, current_run_id would be stale.
            session = self.get_session(run_handle.session_id)
            if session is not None and session.current_run_id == run_id:
                session.current_run_id = None
