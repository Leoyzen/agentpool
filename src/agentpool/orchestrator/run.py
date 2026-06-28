"""Ephemeral run handle for agent execution lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Self

import anyio

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import RunErrorEvent, RunStartedEvent, StreamCompleteEvent
from agentpool.log import get_logger


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable

    from pydantic_ai import AgentRun
    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.orchestrator.core import EventBus, SessionState


logger = get_logger(__name__)


def _create_set_event() -> asyncio.Event:
    """Create an asyncio.Event initialized to the set (signaled) state."""
    event = asyncio.Event()
    event.set()
    return event


class RunStatus(Enum):
    """Lifecycle states for an agent run.

    Values:
        pending: RunHandle created but not yet started.
        running: Actively executing.
        completed: Finished normally.
        failed: Finished with an error.
        checkpointed: Run state persisted for later resumption.
        idle: RunHandle created but no active turn.
        done: RunHandle closed or cancelled.
    """

    pending = auto()
    running = auto()
    completed = auto()
    failed = auto()
    checkpointed = auto()
    idle = auto()
    done = auto()


@dataclass
class RunHandle:
    """Ephemeral runtime handle for a single agent run.

    RunHandle is not serializable and exists only for the duration of a run.
    It bridges the SessionPool's run tracking with the actual asyncio.Task
    and AgentRunContext.

    In the new session-level lifecycle, RunHandle owns an idle/wake/turn
    loop via :meth:`start` (async generator). The loop alternates between
    idle (waiting for messages) and running (executing a single
    :class:`~agentpool.orchestrator.turn.Turn`). Messages can be injected
    mid-turn via :meth:`steer` or queued for the next turn via
    :meth:`followup`.

    Attributes:
        run_id: Unique identifier for this run.
        session_id: Session this run belongs to.
        agent_type: Type of agent running (e.g. ``"native"``, ``"claude"``).
        status: Legacy lifecycle state (used by old code paths).
        agent: The agent instance driving turns.
        event_bus: Event bus for publishing stream events.
        session: Per-session state containing the turn lock.
        run_ctx: Per-run isolated state container.
        complete_event: Set after cleanup finishes.
        _cleanup_callback: Optional callback invoked with run_id during cleanup.
        active_agent_run: Reference to PydanticAI AgentRun, set by
            NativeTurn during execution and cleared in ``finally``.
        _status: New primary lifecycle state (idle/running/done).
        _closing: Flag indicating :meth:`close` has been called.
        _idle_event: asyncio.Event that is set when idle (for wake-up).
        _message_queue: Queued prompts for the next turn.
        _message_history: Accumulated message history across turns.
    """

    run_id: str
    session_id: str
    agent_type: str
    status: RunStatus = RunStatus.pending
    agent: BaseAgent[Any, Any] | None = None
    event_bus: EventBus | None = None
    session: SessionState | None = None
    run_ctx: AgentRunContext = field(default_factory=AgentRunContext)
    complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cleanup_callback: Callable[[str], None] | None = None
    active_agent_run: AgentRun[Any, Any] | None = None
    _cancel_fn: Callable[[], None] | None = None
    _status: RunStatus = RunStatus.idle
    _closing: bool = False
    _idle_event: asyncio.Event = field(default_factory=_create_set_event)
    _message_queue: list[str] = field(default_factory=list)
    _message_history: list[ModelMessage] = field(default_factory=list)

    # ------------------------------------------------------------------
    # New session-level lifecycle
    # ------------------------------------------------------------------

    async def start(self, initial_prompt: str) -> AsyncGenerator[RichAgentStreamEvent]:  # noqa: PLR0915
        """Start the idle/wake/turn loop as an async generator.

        Yields :class:`RichAgentStreamEvent` tokens from each turn's
        :meth:`~agentpool.orchestrator.turn.Turn.execute`. Between turns,
        the handle goes idle and waits for :meth:`steer` or
        :meth:`followup` to wake it.

        Args:
            initial_prompt: The first user prompt to process.
        """
        agent = self.agent
        event_bus = self.event_bus
        session = self.session
        if agent is None:
            raise RuntimeError("agent must be set before calling start()")
        if event_bus is None:
            raise RuntimeError("event_bus must be set before calling start()")
        if session is None:
            raise RuntimeError("session must be set before calling start()")

        # Wire steer_callback so complete_background_task() can inject
        # messages into the active turn via RunHandle.steer().
        self.run_ctx.steer_callback = self._steer_callback_wrapper
        # Set _run_handle on run_ctx so NativeTurn can access active_agent_run
        self.run_ctx._run_handle = self

        async with session.turn_lock:
            current_prompts: list[str] = [initial_prompt]
            while not self._closing:
                if not current_prompts:
                    self._status = RunStatus.idle
                    self._idle_event.clear()
                    await self._idle_event.wait()
                    if self._closing:
                        break
                    current_prompts = list(self._message_queue)
                    self._message_queue.clear()
                    if not current_prompts:
                        continue

                self._status = RunStatus.running
                turn = agent.create_turn(
                    prompts=current_prompts,
                    run_ctx=self.run_ctx,
                    message_history=self._message_history,
                )
                await event_bus.publish(
                    self.session_id,
                    RunStartedEvent(
                        run_id=self.run_id,
                        session_id=self.session_id,
                        agent_name=self.agent_type,
                    ),
                )

                turn_failed = False
                try:
                    async for event in turn.execute():
                        await event_bus.publish(self.session_id, event)
                        yield event
                        if isinstance(event, StreamCompleteEvent):
                            break
                except Exception as e:  # noqa: BLE001
                    turn_failed = True
                    await event_bus.publish(
                        self.session_id,
                        RunErrorEvent(
                            message=str(e),
                            run_id=self.run_id,
                            agent_name=self.agent_type,
                        ),
                    )

                if not turn_failed:
                    try:
                        self._message_history = turn.message_history
                    except RuntimeError:
                        pass

                # Between turns: wait for background child tasks to complete,
                # then collect their steer messages as prompts for next turn.
                if self.run_ctx.child_done_events:
                    try:
                        async with asyncio.timeout(30):
                            await asyncio.gather(
                                *[
                                    e.wait()
                                    for e in self.run_ctx.child_done_events.values()
                                ]
                            )
                    except TimeoutError:
                        logger.warning(
                            "Timeout waiting for child_done_events",
                            run_id=self.run_id,
                            pending=len(self.run_ctx.child_done_events),
                        )

                    # Collect queued steer messages from completed children
                    # as prompts for the next turn.
                    if self.run_ctx.queued_steer_messages:
                        self._message_queue.extend(
                            self.run_ctx.queued_steer_messages
                        )
                        self.run_ctx.queued_steer_messages.clear()

                    self.run_ctx.child_done_events.clear()

                current_prompts = list(self._message_queue)
                self._message_queue.clear()

            self._status = RunStatus.done

    def steer(self, message: str) -> bool:
        """Inject a steer message into the active turn or wake idle handle.

        Returns:
            True if the message was delivered, False if the handle is
            closing or in a non-steerable state.
        """
        if self._closing:
            return False

        if self._status == RunStatus.idle:
            self._message_queue.append(message)
            self._idle_event.set()
            return True

        if self._status == RunStatus.running:
            agent_run = self.active_agent_run
            if agent_run is not None:
                agent_run.enqueue(message, priority="asap")
                return True
            self.run_ctx.queued_steer_messages.append(message)
            return True

        return False

    def followup(self, message: str) -> bool:
        """Queue a follow-up prompt for the next turn.

        Returns:
            True if the message was queued, False if the handle is closing.
        """
        if self._closing:
            return False
        self._message_queue.append(message)
        if self._status == RunStatus.idle:
            self._idle_event.set()
        return True

    async def _steer_callback_wrapper(self, session_id: str, message: str) -> bool:
        """Adapter wrapping :meth:`steer` for use as :attr:`AgentRunContext.steer_callback`.

        The :attr:`~agentpool.agents.context.AgentRunContext.steer_callback` field
        expects ``Callable[[str, str], Awaitable[bool]]``, called as
        ``await callback(session_id, message)`` from
        :meth:`~agentpool.agents.context.AgentRunContext.complete_background_task`.
        This adapter discards the ``session_id`` argument (``RunHandle`` is already
        bound to a single session) and delegates to :meth:`steer`.

        Args:
            session_id: Ignored; required by the callback signature convention.
            message: The steer message to inject into the active turn.

        Returns:
            True if the message was delivered, False otherwise.
        """
        return self.steer(message)

    def close(self) -> None:
        """Signal the run loop to stop after the current turn."""
        self._closing = True
        self._idle_event.set()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Legacy lifecycle (old code paths)
    # ------------------------------------------------------------------

    def _start_task(self, task: asyncio.Task[Any] | None = None) -> None:
        """Transition the run to running and store the task.

        Args:
            task: The asyncio.Task driving this run, if any.
        """
        self.status = RunStatus.running
        self.run_ctx.current_task = task

    def complete(self) -> None:
        """Transition the run to completed and trigger cleanup."""
        self.status = RunStatus.completed
        self._cleanup_run()

    def checkpoint(self) -> None:
        """Transition the run to checkpointed and trigger cleanup."""
        self.status = RunStatus.checkpointed
        self._cleanup_run()

    def fail(
        self,
        exception: BaseException | None = None,
        *,
        event_bus: Any | None = None,
    ) -> None:
        """Transition the run to failed and trigger cleanup.

        Args:
            exception: Optional exception that caused the failure.
            event_bus: Optional event bus to publish RunFailedEvent on.
        """
        self.status = RunStatus.failed
        if exception is not None:
            self.run_ctx.cancelled = True
        if event_bus is not None:
            from agentpool.agents.events import RunFailedEvent

            self._event_task = asyncio.create_task(
                event_bus.publish(
                    self.session_id,
                    RunFailedEvent(
                        run_id=self.run_id,
                        session_id=self.session_id,
                        exception=exception or RuntimeError("Run failed without exception"),
                    ),
                )
            )
        self._cleanup_run()

    @property
    def cancelled(self) -> bool:
        """Whether the run has been cancelled."""
        return self.run_ctx.cancelled

    def cancel(self) -> None:
        """Cancel the run without triggering synchronous cleanup.

        Sets the cancelled flag on the run context and wakes the idle
        event to unblock the turn loop. Delegates to the registered
        cancel function if available, otherwise cancels
        ``run_ctx.current_task``.
        """
        self.run_ctx.cancelled = True
        self._idle_event.set()

        if self._cancel_fn is not None:
            self._cancel_fn()
            return

        task = self.run_ctx.current_task
        if task is not None and not task.done():
            task.cancel()

    def _cleanup_run(self) -> None:
        """Invoke cleanup callback and signal completion.

        The complete_event is set *after* all cleanup so that waiters
        observe the handle only when it is fully settled.
        """
        if self._cleanup_callback is not None:
            self._cleanup_callback(self.run_id)
        with anyio.CancelScope(shield=True):
            self.complete_event.set()
