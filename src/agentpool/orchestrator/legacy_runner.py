"""Legacy turn runner for non-native agents.

Preserves the manual queue-based turn execution system used by
non-native agents (ACP, ClaudeCode, AGUI).
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING, Any
import uuid

from agentpool.log import get_logger
from agentpool.orchestrator.core import (
    DEFAULT_MAX_AUTO_RESUME,
    EventBus,
    SessionController,
    SessionState,
)
from agentpool.orchestrator.run import RunHandle, RunStatus


if TYPE_CHECKING:
    from agentpool.agents.context import AgentRunContext


logger = get_logger(__name__)


class LegacyTurnRunner:
    """Manages turn lifecycle and auto-resume for non-native agents.

    Extracted from ``TurnRunner`` to preserve manual queue-based execution
    for non-native agents (ACP, ClaudeCode, AGUI).  Creates ``RunHandle``
    instances and registers them in ``SessionController._runs``.

    Safety features:
    - Per-session injection queue locks
    - Max auto-resume iterations (configurable)
    - Turn serialization via ``SessionState.turn_lock``
    - Atomic drain operations
    - RunHandle tracking in ``SessionController._runs``
    """

    def __init__(
        self,
        session_controller: SessionController,
        enable_auto_resume: bool = True,
        max_auto_resume: int = DEFAULT_MAX_AUTO_RESUME,
    ) -> None:
        """Initialize the legacy turn runner.

        Args:
            session_controller: The session controller for agent lifecycle.
            enable_auto_resume: Whether to enable auto-resume loop.
            max_auto_resume: Maximum auto-resume iterations.
        """
        self.sessions = session_controller
        self.event_bus = EventBus(session_controller=session_controller)
        self._post_turn_injections: dict[str, list[str]] = {}
        self._post_turn_prompts: dict[str, list[tuple[Any, ...]]] = {}
        self._injection_locks: dict[str, asyncio.Lock] = {}
        self._injection_locks_lock = asyncio.Lock()
        self._enable_auto_resume = enable_auto_resume
        self._max_auto_resume = max_auto_resume
        self._turn_timings: list[tuple[float, float]] = []
        self._max_turn_timing_history: int = 100
        self._background_tasks: set[asyncio.Task[Any]] = set()
        self._runs: dict[str, AgentRunContext] = {}

    async def _get_injection_lock(self, session_id: str) -> asyncio.Lock:
        """Get or create per-session injection lock.

        Args:
            session_id: The session to get the lock for.

        Returns:
            The per-session injection lock.
        """
        async with self._injection_locks_lock:
            lock = self._injection_locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._injection_locks[session_id] = lock
            return lock

    async def _run_turn_unlocked(  # noqa: PLR0915
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a single turn - caller MUST hold ``session.turn_lock``.

        Creates a ``RunHandle`` (when none exists for the run) and
        registers it in ``SessionController._runs``.  The run context
        is taken from the handle so that ``RunHandle.run_ctx`` is the
        authoritative context for the turn.

        Args:
            session_id: The session to run the turn for.
            *prompts: Prompts to pass to the agent.
            **kwargs: Additional arguments passed to the agent.
        """
        agent = await self.sessions.get_or_create_session_agent(session_id)
        _session = self.sessions.get_session(session_id)

        from agentpool.agents.base_agent import _current_run_ctx_var

        run_id_override = self.sessions._pending_run_ids.pop(session_id, None)
        run_id = run_id_override or uuid.uuid4().hex

        # Get or create RunHandle
        run_handle = self.sessions._runs.get(run_id)
        created_run_handle = False
        if run_handle is None:
            agent_type = (
                _session.metadata.get("agent_type", "unknown")
                if _session is not None
                else "unknown"
            )
            run_handle = RunHandle(
                run_id=run_id,
                session_id=session_id,
                agent_type=agent_type,
            )
            self.sessions._runs[run_id] = run_handle
            created_run_handle = True
        run_handle.start(asyncio.current_task())

        # Use RunHandle's run_ctx as the authoritative context
        run_ctx = run_handle.run_ctx
        run_ctx.deps = kwargs.get("deps")
        run_ctx.run_id = run_id
        run_ctx.cancelled = False
        run_ctx.current_task = asyncio.current_task()
        run_ctx.event_bus = self.event_bus
        run_ctx.session_id = session_id
        _current_run_ctx_var.set(run_ctx)

        if _session is not None and _session.current_run_id is None:
            _session.current_run_id = run_id
        self._runs[run_id] = run_ctx

        async def _consume_event_queue() -> None:
            """Consume events from run_ctx.event_queue and publish to EventBus."""
            try:
                while True:
                    event = await run_ctx.event_queue.get()
                    if event is None:
                        break
                    await self.event_bus.publish(session_id, event)
            except asyncio.CancelledError:
                pass

        event_consumer = asyncio.create_task(
            _consume_event_queue(),
            name=f"event_consumer_{session_id}",
        )

        turn_start = time.monotonic()
        try:
            try:
                async for event in agent._run_stream_once(
                    run_ctx, *prompts, session_id=session_id, **kwargs
                ):
                    await self.event_bus.publish(session_id, event)

                run_ctx.injection_manager.flush_pending_to_queue()
                while run_ctx.injection_manager.has_queued() and not run_ctx.cancelled:
                    current_prompts = run_ctx.injection_manager.pop_queued()
                    if current_prompts is None:
                        break
                    async for event in agent._run_stream_once(
                        run_ctx, *current_prompts, session_id=session_id, **kwargs
                    ):
                        await self.event_bus.publish(session_id, event)
                    run_ctx.injection_manager.flush_pending_to_queue()
            except Exception as exc:
                if run_handle is not None and run_handle.status not in (
                    RunStatus.completed,
                    RunStatus.failed,
                ):
                    run_handle.fail(exception=exc, event_bus=self.event_bus)
                raise
        finally:
            run_ctx.completed = True
            if _session is not None:
                _session.current_run_id = None
            self._runs.pop(run_id, None)
            _current_run_ctx_var.set(None)

            event_consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_consumer

            turn_end = time.monotonic()
            self._turn_timings.append((turn_start, turn_end))
            if len(self._turn_timings) > self._max_turn_timing_history:
                self._turn_timings.pop(0)

            # Clean up RunHandle if we created it
            if created_run_handle and run_handle is not None:
                if run_handle.status not in (RunStatus.completed, RunStatus.failed):
                    run_handle.complete()
                run_handle.complete_event.set()
                self.sessions._runs.pop(run_id, None)

    async def run_turn(
        self,
        session_id: str,
        *prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a single turn for a session.

        Acquires ``session.turn_lock`` to enforce "1 turn per session".
        Events are delivered exclusively via EventBus.

        Args:
            session_id: The session to run the turn for.
            *prompts: Prompts to pass to the agent.
            **kwargs: Additional arguments passed to the agent.
        """
        session = await self.sessions.get_or_create_session(session_id)

        async with session.turn_lock:
            if session.is_closing:
                logger.debug("Session is closing, skipping turn", session_id=session_id)
                return
            await self._run_turn_unlocked(session_id, *prompts, **kwargs)

    async def run_loop(
        self,
        session_id: str,
        *initial_prompts: Any,
        **kwargs: Any,
    ) -> None:
        """Run a turn loop until no more post-turn work.

        Only one ``run_loop`` per session at a time (enforced by
        ``SessionState.turn_lock``).  Events are delivered exclusively
        via EventBus.

        Args:
            session_id: The session to run the loop for.
            *initial_prompts: Initial prompts to start the loop.
            **kwargs: Additional arguments passed to the agent.
        """
        session = await self.sessions.get_or_create_session(session_id)

        async with session.turn_lock:
            if session.is_closing:
                logger.debug("Session is closing, skipping turn", session_id=session_id)
                return

            try:
                await self._run_turn_unlocked(session_id, *initial_prompts, **kwargs)
                await self._process_queued_work(session_id, session, **kwargs)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Turn loop failed", session_id=session_id)
                await self._drain_post_turn_injections(session_id)
                await self._drain_post_turn_prompts(session_id)

    async def inject_prompt(self, session_id: str, message: str) -> bool:
        """Inject a message into a session.

        If the session has an active turn, injects immediately.
        Otherwise, queues for the next turn and triggers auto-resume.

        Does NOT acquire ``session.turn_lock``.

        Args:
            session_id: The session to inject into.
            message: The message to inject.

        Returns:
            True if injected into active turn, False if queued.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            logger.debug(
                "Cannot inject: session=%s agent=%s is_closing=%s",
                session is not None,
                session.agent is not None if session else False,
                session.is_closing if session else False,
            )
            return False

        agent = session.agent
        run_ctx = agent.get_active_run_context()
        if run_ctx is not None and not run_ctx.completed:
            run_ctx.injection_manager.inject(message)
            return True

        lock = await self._get_injection_lock(session_id)
        async with lock:
            run_ctx = agent.get_active_run_context()
            if run_ctx is not None and not run_ctx.completed:
                run_ctx.injection_manager.inject(message)
                return True
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                logger.debug("Session closed while waiting for lock")
                return False
            self._post_turn_injections.setdefault(session_id, []).append(message)

        logger.debug("Queued injection for next turn, triggering auto-resume")
        task = asyncio.create_task(self._trigger_auto_resume(session_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def queue_prompt(self, session_id: str, *prompts: Any) -> bool:
        """Queue prompts for a session.

        Similar to ``inject_prompt`` but for full prompts.
        Does NOT acquire ``session.turn_lock``.

        Args:
            session_id: The session to queue prompts for.
            *prompts: Prompts to queue.

        Returns:
            True if queued into active turn, False if stored for later.
        """
        session = self.sessions.get_session(session_id)
        if session is None or session.agent is None or session.is_closing:
            return False

        agent = session.agent
        run_ctx = agent.get_active_run_context()
        if run_ctx is not None:
            run_ctx.injection_manager.queue(*prompts)
            return True

        lock = await self._get_injection_lock(session_id)
        async with lock:
            run_ctx = agent.get_active_run_context()
            if run_ctx is not None:
                run_ctx.injection_manager.queue(*prompts)
                return True
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                return False
            self._post_turn_prompts.setdefault(session_id, []).append(prompts)

        task = asyncio.create_task(self._trigger_auto_resume(session_id))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return False

    async def _process_queued_work(
        self,
        session_id: str,
        session: SessionState,
        **kwargs: Any,
    ) -> None:
        """Process queued post-turn work under ``turn_lock``.

        Shared logic used by both ``run_loop()`` and
        ``_trigger_auto_resume()``.  Caller MUST hold
        ``session.turn_lock``.

        Args:
            session_id: The session to process queued work for.
            session: The session state.
            **kwargs: Additional arguments passed to the agent.
        """
        if session.is_closing:
            logger.debug("Session is closing, skipping queued work")
            return

        injections = await self._drain_post_turn_injections(session_id)
        prompts = await self._drain_post_turn_prompts(session_id)

        logger.debug(
            "Drained injections=%s prompts=%s",
            len(injections),
            len(prompts),
        )

        if injections:
            logger.debug("Running turn with injections")
            await self._run_turn_unlocked(session_id, *injections, **kwargs)
            logger.debug("Turn with injections completed")

        for prompt_group in prompts:
            await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)

        for iteration in range(self._max_auto_resume):
            if session.is_closing:
                logger.debug("Session closing during auto-resume")
                break

            injections = await self._drain_post_turn_injections(session_id)
            prompts = await self._drain_post_turn_prompts(session_id)

            if not injections and not prompts:
                logger.debug("No more queued work, stopping auto-resume")
                break

            logger.info(
                "Auto-resuming turn",
                session_id=session_id,
                iteration=iteration + 1,
                injections=len(injections),
                prompts=len(prompts),
            )

            if injections:
                await self._run_turn_unlocked(session_id, *injections, **kwargs)

            for prompt_group in prompts:
                await self._run_turn_unlocked(session_id, *prompt_group, **kwargs)
        else:
            logger.warning(
                "Auto-resume loop exceeded max iterations",
                session_id=session_id,
                max_iterations=self._max_auto_resume,
            )

    async def _trigger_auto_resume(self, session_id: str) -> None:
        """Trigger auto-resume for a session if no turn is active.

        Fire-and-forget task that ensures post-turn work queued after
        ``run_loop()`` exits gets processed promptly.

        Args:
            session_id: The session to trigger auto-resume for.
        """
        logger.debug("_trigger_auto_resume called for %s", session_id)
        try:
            session = self.sessions.get_session(session_id)
            if session is None or session.is_closing:
                logger.debug("Session not found or closing")
                return

            async with session.turn_lock:
                if session.is_closing:
                    logger.debug("Session closing after acquiring lock")
                    return

                current_session = self.sessions.get_session(session_id)
                if current_session is not session:
                    logger.debug("Session changed")
                    return

                if self._enable_auto_resume:
                    logger.debug("Processing queued work")
                    await self._process_queued_work(session_id, session)
                    logger.debug("Finished processing queued work")
                else:
                    injections = await self._drain_post_turn_injections(session_id)
                    prompts = await self._drain_post_turn_prompts(session_id)

                    if injections:
                        await self._run_turn_unlocked(session_id, *injections)
                    for prompt_group in prompts:
                        await self._run_turn_unlocked(session_id, *prompt_group)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("Auto-resume trigger failed", session_id=session_id)

    async def _drain_post_turn_injections(self, session_id: str) -> list[str]:
        """Drain and return post-turn injections for a session (atomic).

        Args:
            session_id: The session to drain injections from.

        Returns:
            The drained injection messages.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            return self._post_turn_injections.pop(session_id, [])

    async def _drain_post_turn_prompts(self, session_id: str) -> list[tuple[Any, ...]]:
        """Drain and return post-turn prompts for a session (atomic).

        Args:
            session_id: The session to drain prompts from.

        Returns:
            The drained prompt groups.
        """
        lock = await self._get_injection_lock(session_id)
        async with lock:
            return self._post_turn_prompts.pop(session_id, [])
