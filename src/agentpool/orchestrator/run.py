"""Ephemeral run handle for agent execution lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Self

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    StreamCompleteEvent,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


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


def inject_cancelled_tool_results(messages: list[ModelMessage]) -> list[ModelMessage]:
    """Inject RetryPromptPart for unprocessed tool calls in message history.

    When a turn is cancelled mid-tool-call, the message history ends with a
    ``ModelResponse`` containing ``ToolCallPart``\\s but no corresponding
    ``ModelRequest`` with tool results. PydanticAI rejects new user prompts
    in this state with:
    "Cannot provide a new user prompt when the message history contains
    unprocessed tool calls."

    This function scans the message history for trailing unprocessed tool
    calls and appends a ``ModelRequest`` with a ``RetryPromptPart`` for each,
    telling the model the tool was cancelled. This preserves the model's
    decision context (it knows it called the tool) while satisfying
    PydanticAI's message history validation.

    Args:
        messages: The message history to sanitize.

    Returns:
        A new list with cancelled tool results injected if needed.
    """
    from pydantic_ai.messages import ModelRequest, ModelResponse, RetryPromptPart, ToolCallPart

    if not messages:
        return list(messages)

    # Find the last ModelResponse with unprocessed tool calls.
    # A tool call is "unprocessed" if there is no subsequent ModelRequest
    # containing a ToolReturnPart or RetryPromptPart with the same tool_call_id.
    result = list(messages)

    # Check if the last message is a ModelResponse with tool calls.
    last_msg = result[-1]
    if not isinstance(last_msg, ModelResponse):
        return result

    # Collect tool calls that need results.
    pending_tool_calls: list[ToolCallPart] = []
    for part in last_msg.parts:
        match part:
            case ToolCallPart(tool_name=tool_name, tool_call_id=call_id) if tool_name and call_id:
                pending_tool_calls.append(part)

    if not pending_tool_calls:
        return result

    # Build a ModelRequest with RetryPromptPart for each pending tool call.
    retry_parts: list[ModelRequest] = []
    for tc in pending_tool_calls:
        retry_parts.append(
            ModelRequest(parts=[
                RetryPromptPart(
                    content=f"Tool '{tc.tool_name}' was cancelled. The user interrupted the run before the tool could complete.",
                    tool_name=tc.tool_name,
                    tool_call_id=tc.tool_call_id,
                ),
            ]),
        )

    result.extend(retry_parts)
    return result


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
        _turn_complete_event: Per-turn completion event, set when a single
            turn finishes (normally or via cancel). Replaces session-level
            ``complete_event`` for ACP client blocking on a single turn.
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
    _turn_complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _turn_was_cancelled: bool = False
    _interrupt_task: asyncio.Task[None] | None = None

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
        # Set current_task so cancel() can interrupt the running turn.
        self.run_ctx.current_task = asyncio.current_task()
        # Wire _cancel_fn so cancel() triggers agent._interrupt() (ACP
        # CancelNotification, native _iteration_task cancel).
        self._cancel_fn = self._create_cancel_fn()

        try:
            async with session.turn_lock:
                current_prompts: list[str] = [initial_prompt]
                while not self._closing:
                    if not current_prompts:
                        self._status = RunStatus.idle
                        self._idle_event.clear()
                        # Check if messages were queued during cancel/cleanup
                        # before blocking. Without this, messages routed through
                        # _message_queue by the cancel path would deadlock: cancel()
                        # sets _idle_event, but clear() above removes it, and wait()
                        # blocks forever with no one to re-set it.
                        if not self._message_queue:
                            await self._idle_event.wait()
                            if self._closing:
                                break
                        current_prompts = list(self._message_queue)
                        self._message_queue.clear()
                        if not current_prompts:
                            continue

                    self._status = RunStatus.running
                    # Reset per-turn state: clear the completion event
                    # and clear any stale cancelled flag from a prior turn.
                    self._turn_complete_event.clear()
                    self._turn_was_cancelled = False
                    if self.run_ctx.cancelled:
                        self.run_ctx.cancelled = False
                    turn = agent.create_turn(
                        prompts=current_prompts,
                        run_ctx=self.run_ctx,
                        message_history=self._message_history,
                    )
                    # Publish RunStartedEvent before turn.execute() so
                    # consumers know a new turn is starting. This was
                    # previously yielded by NativeTurn.execute() itself,
                    # causing duplicate events when RunHandle.start()
                    # also published turn events. We publish to the event
                    # bus without yielding to avoid inflating the event
                    # count seen by generator consumers.
                    run_started = RunStartedEvent(
                        run_id=self.run_id,
                        session_id=self.session_id,
                        agent_name=self.agent_type,
                        parent_session_id=session.parent_session_id if session is not None else None,
                    )
                    await event_bus.publish(self.session_id, run_started)

                    # Set _current_input_provider ContextVar so MCP
                    # elicitation can access it during turn execution.
                    # Only set() without reset(): start() runs inside an
                    # asyncio.Task which copies the parent Context, so
                    # set() only affects this task's private context copy.
                    # When the task ends the context is discarded. Calling
                    # reset() is unnecessary and can raise ValueError when
                    # the async generator is GC-collected in a different
                    # Context (race between task cancellation and generator
                    # suspension at a yield point).
                    if session.input_provider is not None:
                        from agentpool.mcp_server.manager import _current_input_provider

                        _current_input_provider.set(session.input_provider)

                    # Save user prompt to agent conversation before execution.
                    # This ensures user messages are preserved even if the turn
                    # fails or is cancelled (mirroring _run_stream_once() behavior).
                    agent.conversation.add_chat_messages([
                        ChatMessage(
                            content="\n".join(current_prompts),
                            role="user",
                            name=agent.name,
                            session_id=self.session_id,
                        ),
                    ])

                    turn_failed = False
                    try:
                        async for event in turn.execute():
                            logger.info(
                                "RunHandle.start() got event=%s session=%s run_id=%s",
                                type(event).__name__,
                                self.session_id,
                                self.run_id,
                            )
                            await event_bus.publish(self.session_id, event)
                            # Save assistant final message to conversation BEFORE
                            # yielding. The _consume_run caller closes the generator
                            # immediately after receiving StreamCompleteEvent, which
                            # prevents any code after `yield event` from executing.
                            if isinstance(event, StreamCompleteEvent) and event.message is not None:
                                agent.conversation.add_chat_messages(
                                    [event.message],
                                    extend_last=True,
                                )  # type: ignore[arg-type]
                            yield event
                            if isinstance(event, RunErrorEvent):
                                turn_failed = True
                                break
                            if isinstance(event, StreamCompleteEvent):
                                break
                    except Exception as e:  # noqa: BLE001
                        turn_failed = True
                        error_event = RunErrorEvent(
                            message=str(e),
                            run_id=self.run_id,
                            agent_name=self.agent_type,
                        )
                        await event_bus.publish(self.session_id, error_event)
                        yield error_event

                    if self.run_ctx.cancelled:
                        # Turn was cancelled — publish RunFailedEvent, set turn
                        # complete, clear prompts, and continue to idle for next turn.
                        # RunFailedEvent must be published BEFORE _turn_complete_event
                        # so the event converter can emit
                        # TurnCompleteUpdate(stop_reason="cancelled").
                        await event_bus.publish(
                            self.session_id,
                            RunFailedEvent(
                                run_id=self.run_id,
                                session_id=self.session_id,
                                exception=RuntimeError("Run cancelled"),
                            ),
                        )
                        # Capture cancelled state BEFORE setting _turn_complete_event.
                        # handle_prompt() checks run_handle.cancelled after waking from
                        # _turn_complete_event.wait(). But the loop may reset cancelled=False
                        # before handle_prompt() gets scheduled (e.g., when steer messages
                        # are queued). _turn_was_cancelled preserves the state for observation.
                        self._turn_was_cancelled = True
                        self._turn_complete_event.set()
                        # Route queued steer messages through _message_queue
                        # instead of directly into current_prompts. This forces
                        # the loop through idle, preserving cancelled=True for
                        # handle_prompt() to observe before the next turn resets it.
                        if self.run_ctx.queued_steer_messages:
                            self._message_queue.extend(self.run_ctx.queued_steer_messages)
                            self.run_ctx.queued_steer_messages.clear()
                        current_prompts = []  # Prevent re-execution of cancelled prompt
                        # Preserve the cancelled turn's message history so the
                        # next turn sees the partial conversation context.
                        # Without this, `continue` skips line 300 and the
                        # next turn starts with stale _message_history.
                        if not turn_failed:
                            with contextlib.suppress(RuntimeError):
                                self._message_history = turn.message_history
                        # Do NOT reset cancelled here — handle_prompt() needs to
                        # observe it. It will be reset at the start of the next turn.
                        continue

                    if turn_failed:
                        break

                    if not turn_failed:
                        try:
                            self._message_history = turn.message_history
                        except RuntimeError:
                            pass

                    # Between turns: wait for background child tasks to complete,
                    # then collect their steer messages as prompts for next turn.
                    child_events_timed_out = False
                    if self.run_ctx.child_done_events:
                        try:
                            async with asyncio.timeout(30):
                                await asyncio.gather(*[
                                    e.wait() for e in list(self.run_ctx.child_done_events.values())
                                ])
                        except TimeoutError:
                            child_events_timed_out = True
                            logger.warning(
                                "Timeout waiting for child_done_events",
                                run_id=self.run_id,
                                pending=len(self.run_ctx.child_done_events),
                            )

                    # Collect queued steer messages from completed children
                    # as prompts for the next turn.
                    if self.run_ctx.queued_steer_messages:
                        self._message_queue.extend(self.run_ctx.queued_steer_messages)
                        self.run_ctx.queued_steer_messages.clear()

                    if child_events_timed_out:
                        # On timeout, clear ALL child_done_events since we are
                        # proceeding regardless. New child tasks may have been
                        # registered during the wait, but we cannot wait further.
                        self.run_ctx.child_done_events.clear()
                    else:
                        # Only remove completed events; new child tasks may have
                        # been registered between gather() and here.
                        completed_keys = [
                            k for k, e in list(self.run_ctx.child_done_events.items()) if e.is_set()
                        ]
                        for k in completed_keys:
                            del self.run_ctx.child_done_events[k]

                    current_prompts = list(self._message_queue)
                    self._message_queue.clear()

                    # Signal that this turn has completed normally.
                    self._turn_was_cancelled = False
                    self._turn_complete_event.set()

                self._status = RunStatus.done
        finally:
            self._status = RunStatus.done
            self._turn_complete_event.set()
            self.complete_event.set()

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
        """Whether the last completed turn was cancelled.

        Returns ``_turn_was_cancelled`` (captured at the moment
        ``_turn_complete_event`` was set) rather than the live
        ``run_ctx.cancelled`` flag, which may have been reset by
        the time the caller observes it.
        """
        return self._turn_was_cancelled

    def cancel(self) -> None:
        """Cancel the run without triggering synchronous cleanup.

        Sets the cancelled flag on the run context and wakes the idle
        event to unblock the turn loop. Calls the registered cancel
        function (wired in ``start()``) which schedules
        ``agent._interrupt()`` for subclass-specific cleanup.

        The ``start()`` loop task is NOT cancelled here — it must
        continue running to process the ``cancelled`` flag, emit
        stream-complete events, and transition to idle/done gracefully.
        """
        self.run_ctx.cancelled = True
        self._idle_event.set()

        if self._cancel_fn is not None:
            self._cancel_fn()

    def _create_cancel_fn(self) -> Callable[[], None]:
        """Create a cancel function that schedules ``agent._interrupt()``.

        Returns a callable that, when invoked, schedules the agent's
        ``_interrupt`` coroutine as a background task. The task reference
        is stored in ``self._interrupt_task`` to prevent GC.
        """
        agent = self.agent
        run_ctx = self.run_ctx

        def _cancel() -> None:
            if agent is None:
                return
            coro = agent._interrupt(run_ctx)
            if asyncio.iscoroutine(coro):
                self._interrupt_task = asyncio.create_task(coro)

        return _cancel

    def _cleanup_run(self) -> None:
        """Invoke cleanup callback and signal completion.

        The complete_event is set *after* all cleanup so that waiters
        observe the handle only when it is fully settled.
        """
        if self._cleanup_callback is not None:
            self._cleanup_callback(self.run_id)
        self.complete_event.set()
