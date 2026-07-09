"""Ephemeral run handle for agent execution lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Self
import uuid

from agentpool.agents.context import AgentRunContext
from agentpool.agents.events import (
    RunErrorEvent,
    RunFailedEvent,
    RunStartedEvent,
    StateUpdate,
    StreamCompleteEvent,
)
from agentpool.lifecycle import (
    DirectChannel,
    EventTransport,
    Feedback,
    ImmediateTrigger,
    InProcessTransport,
    Journal,
    MemoryJournal,
    MemorySnapshotStore,
    RunState,
    SnapshotStore,
    TriggerSource,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from typing import Any

    from pydantic_ai import AgentRun
    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.lifecycle.protocols import CommChannel
    from agentpool.orchestrator.core import EventBus, SessionState


logger = get_logger(__name__)


def _create_set_event() -> asyncio.Event:
    """Create an asyncio.Event initialized to the set (signaled) state."""
    event = asyncio.Event()
    event.set()
    return event


def inject_cancelled_tool_results(messages: list[ModelMessage]) -> list[ModelMessage]:
    r"""Inject RetryPromptPart for unprocessed tool calls in message history.

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
    retry_parts: list[ModelRequest] = [
        ModelRequest(
            parts=[
                RetryPromptPart(
                    content=(
                        f"Tool '{tc.tool_name}' was cancelled. "
                        "The user interrupted the run before the tool could complete."
                    ),
                    tool_name=tc.tool_name,
                    tool_call_id=tc.tool_call_id,
                ),
            ],
        )
        for tc in pending_tool_calls
    ]

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
    _closed: bool = False
    _idle_event: asyncio.Event = field(default_factory=_create_set_event)
    _message_queue: list[str] = field(default_factory=list)
    _message_history: list[ModelMessage] = field(default_factory=list)
    _turn_complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _turn_was_cancelled: bool = False
    _interrupt_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Lifecycle dimensions (M2)
    # ------------------------------------------------------------------
    _trigger_source: TriggerSource | None = None
    _journal: Journal | None = None
    _snapshot_store: SnapshotStore | None = None
    _comm_channel: CommChannel | None = None
    _event_transport: EventTransport | None = None
    _lifecycle_session_id: str = "default"
    _run_state: RunState = RunState.IDLE
    _state_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _recover_strategy: str = "mark_interrupted"
    """Crash recovery strategy: ``"mark_interrupted"`` or ``"retry"``.

    Only active when both ``lifecycle.journal`` and ``lifecycle.snapshot``
    are durable.
    """
    _recovered_inflight_turn_id: str | None = None
    """Turn ID of the in-flight Turn detected during crash recovery.

    Set by ``start()`` when ``resume_result.is_inflight`` is ``True``.
    Used by the ``"retry"`` strategy to check
    ``journal.get_tool_executions(turn_id)`` before re-executing.
    """

    def __post_init__(self) -> None:
        """Initialize default lifecycle dimensions.

        Any dimension left as ``None`` is populated with the default
        in-process implementation. The journal is injected into the
        CommChannel to ensure the channel can persist events.
        """
        if self._journal is None:
            self._journal = MemoryJournal()
        if self._snapshot_store is None:
            self._snapshot_store = MemorySnapshotStore()
        if self._comm_channel is None:
            self._comm_channel = DirectChannel(self._journal)
        else:
            # Inject journal into existing CommChannel if not already set.
            try:
                existing_journal: Journal | None = self._comm_channel._journal  # type: ignore[attr-defined]
            except AttributeError:
                existing_journal = None
            if existing_journal is None:
                self._comm_channel._journal = self._journal  # type: ignore[attr-defined]
        if self._event_transport is None:
            self._event_transport = InProcessTransport()
        if self._trigger_source is None:
            self._trigger_source = ImmediateTrigger("")

    @property
    def is_running(self) -> bool:
        """Whether the RunLoop is in the RUNNING state.

        Returns:
            ``True`` if the lifecycle state is ``RunState.RUNNING``.
        """
        return self._run_state == RunState.RUNNING

    @property
    def _channel_publishes_to_event_bus(self) -> bool:
        """Whether the CommChannel publishes events to the EventBus itself.

        ``ProtocolChannel`` calls ``event_bus.publish(session_id, event)``
        inside its own ``publish()`` method. When this is the case,
        ``start()`` must NOT also call ``event_bus.publish()`` directly
        to avoid double-publishing.

        ``DirectChannel`` does not publish to the EventBus, so the
        direct call in ``start()`` is required.

        Returns:
            ``True`` if the CommChannel publishes to the EventBus
            internally, ``False`` otherwise.
        """
        from agentpool.lifecycle.comm_channel import ProtocolChannel

        return isinstance(self._comm_channel, ProtocolChannel)

    @property
    def recovered_tool_executions(self) -> list[Any]:
        """Tool executions from the interrupted Turn, for idempotent retry.

        When ``recover_strategy == "retry"`` and an in-flight Turn was
        detected, this property returns the list of completed tool
        execution records from the journal. The Turn execution path
        can check this to skip already-completed tools during
        re-execution.

        Returns:
            List of ``ToolExecutionRecord`` objects, or empty list if
            no in-flight Turn was recovered or no journal is configured.
        """
        if self._recovered_inflight_turn_id is None:
            return []
        if self._journal is None:
            return []
        return self._journal.get_tool_executions(self._recovered_inflight_turn_id)

    async def _transition(
        self,
        new_state: RunState,
        stop_reason: str | None = None,
    ) -> None:
        """Transition to a new RunState, notifying CommChannel and publishing StateUpdate.

        Acquires the internal state lock to serialize transitions. After
        setting the state, calls ``comm_channel.on_state_change()`` and
        publishes a ``StateUpdate`` event via ``comm_channel.publish()``.

        Args:
            new_state: The target RunState.
            stop_reason: Optional reason for the transition.
        """
        async with self._state_lock:
            if self._run_state == new_state and stop_reason is None:
                return
            self._run_state = new_state
        if self._comm_channel is not None:
            self._comm_channel.on_state_change(new_state)
            state_event = StateUpdate(
                session_id=self._lifecycle_session_id,
                state=new_state,
                stop_reason=stop_reason,
            )
            await self._comm_channel.publish(state_event)

    # ------------------------------------------------------------------
    # New session-level lifecycle
    # ------------------------------------------------------------------

    async def start(self, initial_prompt: str) -> AsyncGenerator[RichAgentStreamEvent[Any]]:  # noqa: PLR0915
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

        # --- Lifecycle dimensions (M2) ---
        # Crash recovery: check journal for prior state.
        assert self._journal is not None  # set by __post_init__
        assert self._snapshot_store is not None  # set by __post_init__
        assert self._comm_channel is not None  # set by __post_init__
        assert self._trigger_source is not None  # set by __post_init__
        recovered_prompt: str | None = None
        resume_result = self._journal.resume(self._snapshot_store)
        if resume_result is not None:
            if resume_result.is_inflight:
                # In-flight crash recovery: replay journaled events.
                self._comm_channel._replaying = True
                try:
                    for event in resume_result.events:
                        await self._comm_channel.publish(event)
                finally:
                    self._comm_channel._replaying = False
                self._recovered_inflight_turn_id = resume_result.inflight_turn_id
                # Apply recovery strategy.
                if self._recover_strategy == "retry":
                    # Re-queue the interrupted Turn's prompt for re-execution.
                    # The prompt is extracted from the snapshot state dict
                    # saved before the Turn started.
                    state_dict = resume_result.state
                    if isinstance(state_dict, dict):
                        prompt_val: Any = state_dict.get("prompt")
                        if isinstance(prompt_val, str) and prompt_val:
                            recovered_prompt = prompt_val
                elif self._recover_strategy == "mark_interrupted":
                    # Mark the interrupted Turn's result as interrupted in
                    # the snapshot store so it's not re-detected on next
                    # recovery.
                    if resume_result.inflight_turn_id is not None:
                        self._snapshot_store.save_turn_result(
                            resume_result.inflight_turn_id,
                            {"status": "interrupted"},
                        )
                await self._transition(RunState.IDLE, stop_reason="crash_recovery")
            else:
                await self._transition(RunState.IDLE)
        else:
            # Fresh start: save initial snapshot.
            self._snapshot_store.save(
                {"state": RunState.IDLE.value, "run_id": self.run_id},
            )
            await self._transition(RunState.IDLE)

        # Subscribe dimensions.
        self._trigger_source.subscribe(self)
        self._comm_channel.attach(self)

        try:
            async with session.turn_lock:
                # For "retry" recovery, prepend the recovered prompt.
                if recovered_prompt is not None:
                    current_prompts: list[str] = [recovered_prompt]
                else:
                    current_prompts = [initial_prompt]
                while not self._closing:
                    if not current_prompts:
                        self._status = RunStatus.idle
                        self._idle_event.clear()
                        # Drain CommChannel feedback queue (ProtocolChannel)
                        # BEFORE deciding to block. Feedback may have been
                        # enqueued by steer/followup via deliver_feedback()
                        # while the loop was running (e.g., during cancel).
                        # Without this, the loop would block on
                        # _idle_event.wait() even though feedback is already
                        # available in the CommChannel.
                        if self._comm_channel is not None:
                            while True:
                                fb = self._comm_channel.recv()
                                if fb is None:
                                    break
                                self._message_queue.append(fb.content)
                        # Check if messages were queued during cancel/cleanup
                        # before blocking. Without this, messages routed through
                        # _message_queue by the cancel path would deadlock: cancel()
                        # sets _idle_event, but clear() above removes it, and wait()
                        # blocks forever with no one to re-set it.
                        if not self._message_queue:
                            await self._idle_event.wait()
                            if self._closing:
                                # Drain any feedback from CommChannel
                                # before checking for pending messages.
                                if self._comm_channel is not None:
                                    while True:
                                        fb = self._comm_channel.recv()
                                        if fb is None:
                                            break
                                        self._message_queue.append(fb.content)
                                # Process pending messages before exiting
                                # so close() with queued followups are
                                # handled as final Turns.
                                if not self._message_queue:
                                    break
                        # Drain CommChannel feedback queue again after
                        # waking from idle (feedback may have arrived
                        # during the wait).
                        if self._comm_channel is not None:
                            while True:
                                fb = self._comm_channel.recv()
                                if fb is None:
                                    break
                                self._message_queue.append(fb.content)
                        current_prompts = list(self._message_queue)
                        self._message_queue.clear()
                        if not current_prompts:
                            continue

                    self._status = RunStatus.running
                    # Lifecycle state transition: IDLE → RUNNING.
                    await self._transition(RunState.RUNNING)
                    # Generate a unique turn_id for this Turn.
                    turn_id = str(uuid.uuid4())
                    self.run_ctx.turn_id = turn_id
                    # Reset per-turn state: clear the completion event
                    # and clear any stale cancelled flag from a prior turn.
                    self._turn_complete_event.clear()
                    self._turn_was_cancelled = False
                    if self.run_ctx.cancelled:
                        self.run_ctx.cancelled = False
                    # Clear hooks_fired so the new turn's hooks can fire
                    # even if the previous turn already fired them.
                    self.run_ctx.hooks_fired.clear()
                    turn = agent.create_turn(
                        prompts=current_prompts,  # type: ignore[arg-type]
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
                        parent_session_id=session.parent_session_id
                        if session is not None
                        else None,
                    )
                    if not self._channel_publishes_to_event_bus:
                        await event_bus.publish(self.session_id, run_started)
                    await self._comm_channel.publish(run_started)

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

                    # Pre-turn snapshot: save prompt and turn_id for
                    # crash recovery. If the process crashes during
                    # turn.execute(), this snapshot allows the "retry"
                    # strategy to recover the prompt and turn_id.
                    self._snapshot_store.save({
                        "state": RunState.RUNNING.value,
                        "run_id": self.run_id,
                        "turn_id": turn_id,
                        "prompt": "\n".join(current_prompts),
                    })

                    turn_failed = False
                    try:
                        async for event in turn.execute():
                            if not self._channel_publishes_to_event_bus:
                                await event_bus.publish(self.session_id, event)
                            await self._comm_channel.publish(event)
                            # Save assistant final message to conversation BEFORE
                            # yielding. The _consume_run caller closes the generator
                            # immediately after receiving StreamCompleteEvent, which
                            # prevents any code after `yield event` from executing.
                            if isinstance(event, StreamCompleteEvent) and event.message is not None:
                                agent.conversation.add_chat_messages(
                                    [event.message],
                                    extend_last=True,
                                )
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
                        if not self._channel_publishes_to_event_bus:
                            await event_bus.publish(self.session_id, error_event)
                        await self._comm_channel.publish(error_event)
                        yield error_event

                    if self.run_ctx.cancelled:
                        # Turn was cancelled — publish RunFailedEvent, set turn
                        # complete, clear prompts, and continue to idle for next turn.
                        # RunFailedEvent must be published BEFORE _turn_complete_event
                        # so the event converter can emit
                        # TurnCompleteUpdate(stop_reason="cancelled").
                        cancelled_event = RunFailedEvent(
                            run_id=self.run_id,
                            session_id=self.session_id,
                            exception=RuntimeError("Run cancelled"),
                        )
                        if not self._channel_publishes_to_event_bus:
                            await event_bus.publish(self.session_id, cancelled_event)
                        await self._comm_channel.publish(cancelled_event)
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
                        with contextlib.suppress(RuntimeError):
                            self._message_history = turn.message_history

                    # Lifecycle state transition: RUNNING → IDLE.
                    await self._transition(RunState.IDLE)

                    # Snapshot at turn boundary (after state transition).
                    self._snapshot_store.save(
                        {
                            "state": self._run_state.value,
                            "run_id": self.run_id,
                            "turn_id": turn_id,
                        },
                    )
                    # Save turn result for idempotency.
                    with contextlib.suppress(RuntimeError):
                        final_msg = turn.final_message
                        self._snapshot_store.save_turn_result(turn_id, final_msg)

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

                    # Drain CommChannel feedback queue (ProtocolChannel).
                    # Feedback may have been enqueued by steer/followup
                    # via deliver_feedback() during the Turn. Steer
                    # feedback is prioritized as next-turn prompts.
                    feedback_steer: list[str] = []
                    if self._comm_channel is not None:
                        while True:
                            fb = self._comm_channel.recv()
                            if fb is None:
                                break
                            if fb.is_steer:
                                feedback_steer.append(fb.content)
                            else:
                                self._message_queue.append(fb.content)

                    current_prompts = feedback_steer + list(self._message_queue)
                    self._message_queue.clear()

                    # Signal that this turn has completed normally.
                    self._turn_was_cancelled = False
                    self._turn_complete_event.set()

                self._status = RunStatus.done
        finally:
            self._status = RunStatus.done
            self._closed = True
            # Lifecycle state transition: → DONE.
            with contextlib.suppress(Exception):
                await self._transition(RunState.DONE)
            self._turn_complete_event.set()
            self.complete_event.set()
            # Close lifecycle dimensions.
            with contextlib.suppress(Exception):
                if self._trigger_source is not None:
                    self._trigger_source.close()
            with contextlib.suppress(Exception):
                if self._comm_channel is not None:
                    self._comm_channel.close()
            with contextlib.suppress(Exception):
                if self._event_transport is not None:
                    self._event_transport.close()

    def steer(self, message: str) -> bool:
        """Inject a steer message into the active turn or wake idle handle.

        For ProtocolChannel (bidirectional CommChannel with
        ``deliver_feedback()``), routes through the CommChannel feedback
        loop. For DirectChannel (unidirectional), falls back to the
        existing ``_message_queue`` / ``active_agent_run.enqueue()``
        logic.

        Returns:
            True if the message was delivered, False if the handle is
            closing or in a non-steerable state.

        Raises:
            RuntimeError: If :meth:`close` has already been called.
        """
        if self._closed:
            msg = "Cannot steer after close()"
            raise RuntimeError(msg)

        if self._closing:
            return False

        # Try ProtocolChannel feedback path (deliver_feedback exists).
        if self._comm_channel is not None:
            try:
                deliver: Any | None = self._comm_channel.deliver_feedback  # type: ignore[attr-defined]
            except AttributeError:
                deliver = None
            if deliver is not None:
                feedback = Feedback(content=message, is_steer=True)
                self._comm_channel.deliver_feedback(feedback)  # type: ignore[attr-defined]
                # Always set _idle_event when delivering via ProtocolChannel.
                # If the loop is running, the event is cleared when entering
                # idle, and the loop then drains CommChannel feedback. If the
                # loop is transitioning to idle (e.g., after cancel), the
                # event prevents blocking on _idle_event.wait() when the
                # feedback is already in the CommChannel queue.
                self._idle_event.set()
                return True

        # Fallback: DirectChannel path (existing logic).
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

        For ProtocolChannel, routes through the CommChannel feedback
        loop with ``is_steer=False``. For DirectChannel, falls back
        to the existing ``_message_queue`` logic.

        Returns:
            True if the message was queued, False if the handle is closing.
        """
        if self._closing:
            return False

        # Try ProtocolChannel feedback path (deliver_feedback exists).
        if self._comm_channel is not None:
            try:
                deliver: Any | None = self._comm_channel.deliver_feedback  # type: ignore[attr-defined]
            except AttributeError:
                deliver = None
            if deliver is not None:
                feedback = Feedback(content=message, is_steer=False)
                self._comm_channel.deliver_feedback(feedback)  # type: ignore[attr-defined]
                # Always set _idle_event (see steer() for rationale).
                self._idle_event.set()
                return True

        # Fallback: DirectChannel path (existing logic).
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
        """Signal the run loop to stop after the current turn.

        Sets ``_closing`` flag to signal the loop to exit. Wakes any
        idle wait via ``_idle_event.set()``. If the loop is idle (not
        actively running a Turn), schedules an immediate transition
        to ``RunState.DONE``.

        The ``start()`` finally block sets ``_closed=True`` and closes
        all lifecycle dimensions (comm_channel, trigger_source,
        event_transport). This method only sets ``_closing`` — it does
        NOT close dimensions or set ``_closed``.

        Calling ``close()`` twice is a no-op: the second call returns
        immediately because ``_closing`` is already ``True``.

        After the ``start()`` finally block has run (``_closed=True``),
        calling :meth:`steer` raises ``RuntimeError``.
        """
        if self._closing:
            return
        self._closing = True
        self._idle_event.set()
        # If idle and not in the start() loop, schedule immediate
        # transition to DONE. The start() loop's finally block also
        # transitions to DONE, so this handles the case where the
        # loop isn't running or has already exited.
        if self._run_state != RunState.DONE:
            async def _safe_done() -> None:
                with contextlib.suppress(Exception):
                    await self._transition(RunState.DONE)
            with contextlib.suppress(RuntimeError):
                self._close_task: asyncio.Task[None] | None = asyncio.create_task(_safe_done())

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
