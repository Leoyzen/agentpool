"""Ephemeral run handle for agent execution lifecycle management."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Self
import uuid

import logfire

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
    RunOutcome,
    RunState,
    SnapshotStore,
    TriggerSource,
)
from agentpool.log import get_logger
from agentpool.messaging import ChatMessage
from agentpool.observability.spans import safe_span


if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from typing import Any

    from pydantic_ai import AgentRun
    from pydantic_ai.messages import ModelMessage

    from agentpool.agents.base_agent import BaseAgent
    from agentpool.agents.events.events import RichAgentStreamEvent
    from agentpool.host.context import HostContext
    from agentpool.host.registry import AgentRegistry
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
        outcome: Terminal outcome (``RunOutcome.COMPLETED``, ``FAILED``,
            ``CHECKPOINTED``) set when the run reaches ``RunState.DONE``.
            ``None`` while the run is active or was closed without outcome.
        agent: The agent instance driving turns.
        event_bus: Event bus for publishing stream events.
        session: Per-session state containing the turn lock.
        run_ctx: Per-run isolated state container.
        complete_event: Set after cleanup finishes.
        _cleanup_callback: Optional callback invoked with run_id during cleanup.
        active_agent_run: Reference to PydanticAI AgentRun, set by
            NativeTurn during execution and cleared in ``finally``.
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
    outcome: RunOutcome | None = None
    agent: BaseAgent[Any, Any] | None = None
    event_bus: EventBus | None = None
    session: SessionState | None = None
    run_ctx: AgentRunContext = field(default_factory=AgentRunContext)
    complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cleanup_callback: Callable[[str], None] | None = None
    active_agent_run: AgentRun[Any, Any] | None = None
    _cancel_fn: Callable[[], None] | None = None
    _closing: bool = False
    _closed: bool = False
    _force_cancelling: bool = False
    """Set by ``cancel()`` before calling ``task.cancel()`` to distinguish
    internal force-cancel (break through __aexit__ hang) from external
    ``task.cancel()`` (e.g. test cleanup). In ``start()``, only the
    force-cancel path catches ``CancelledError`` and continues to idle;
    external cancellation propagates and exits the loop."""
    _idle_event: asyncio.Event = field(default_factory=_create_set_event)
    _message_queue: list[str | list[Any]] = field(default_factory=list)
    _message_history: list[ModelMessage] = field(default_factory=list)
    _turn_complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _turn_was_cancelled: bool = False
    _interrupt_task: asyncio.Task[None] | None = None
    _current_turn: Any = None
    """The current Turn being executed. Set by ``_execute_turn()``, read by
    ``_handle_turn_result()`` and ``_drain_events()``."""
    _current_turn_id: str | None = None
    """The current turn ID. Set by ``_execute_turn()``, read by
    ``_drain_events()``."""
    _current_turn_failed: bool = False
    """Whether the current turn failed. Set by ``_execute_turn()``, read by
    ``_handle_turn_result()``."""

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

    # ------------------------------------------------------------------
    # AgentContext injection (M3 task group 15)
    # ------------------------------------------------------------------
    _host_context: HostContext | None = None
    """HostContext for constructing per-turn AgentContext.

    When set, ``start()`` constructs an ``AgentContext`` per turn and
    injects it into ``run_ctx.deps`` so capabilities like
    ``SubagentCapability`` can access the delegation service.
    """
    _agent_registry: AgentRegistry | None = None
    """Read-only registry of compiled agents for delegation."""
    _resume_deferred_tool_results: Any = None
    """Deferred tool results from checkpoint, forwarded to ``agent.create_turn()``
    via ``**pydantic_ai_kwargs`` during resume. Only set by
    ``_create_run_handle()`` when resuming from a checkpoint."""

    def __post_init__(self) -> None:
        """Initialize default lifecycle dimensions.

        Any dimension left as ``None`` is populated with the default
        in-process implementation. Both ``DirectChannel`` and
        ``ProtocolChannel`` receive the journal via their constructor,
        so no post-hoc journal injection is needed.

        If ``_comm_channel`` is provided but ``_journal`` is ``None``,
        the CommChannel's journal is reused. This ensures
        ``journal.resume()`` reads the same events that ``publish()``
        writes — without it, crash recovery silently fails because
        ``resume()`` reads from a fresh empty journal while events
        are written to the CommChannel's journal instance.
        """
        # If CommChannel is provided but _journal is not, reuse the
        # CommChannel's journal to ensure resume() reads the same events
        # that publish() writes. Without this, crash recovery silently
        # fails because resume() reads an empty journal.
        if self._journal is None and self._comm_channel is not None:
            self._journal = self._comm_channel.journal
        if self._journal is None:
            self._journal = MemoryJournal()
        if self._snapshot_store is None:
            self._snapshot_store = MemorySnapshotStore()
        if self._comm_channel is None:
            self._comm_channel = DirectChannel(self._journal)
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

    @property
    def _active_agent_run(self) -> AgentRun[Any, Any] | None:
        """Alias for ``active_agent_run``.

        Provides the underscore-prefixed access for internal callers
        that prefer the private naming convention.
        """
        return self.active_agent_run

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

    def _inject_agent_context(self) -> None:
        """Construct and inject AgentContext into run_ctx.deps.

        Builds a fresh ``AgentContext`` per turn using the host context,
        agent registry, and resource source. The AgentContext is set as
        ``run_ctx.deps`` so pydantic-ai's ``RunContext.deps`` carries it
        into tool calls. Capabilities like ``SubagentCapability`` access
        it via ``ctx.deps``.

        When ``_host_context`` is None (standalone execution without a
        pool), this is a no-op — ``run_ctx.deps`` stays at its prior value.
        """
        if self._host_context is None:
            return
        from agentpool.capabilities.agent_context import AgentContext
        from agentpool.capabilities.runloop_delegation import RunLoopDelegationService
        from agentpool.host.context import RunScope

        registry = self._agent_registry
        if registry is None:
            return

        scope = RunScope(
            config_id=self._host_context.config_id or "default",
            tenant_id=self._host_context.tenant_id or "default",
            session_id=self.session_id,
        )
        delegation = RunLoopDelegationService(
            registry=registry,
            host=self._host_context,
            session_id=self.session_id,
        )
        ctx = AgentContext(
            agent_registry=registry,
            delegation=delegation,
            session=self.session,  # type: ignore[arg-type]
            scope=scope,
            host=self._host_context,
            extension_registry=(
                self._host_context.extension_registry if self._host_context is not None else None
            ),
        )
        self.run_ctx.deps = ctx

    # ------------------------------------------------------------------
    # New session-level lifecycle
    # ------------------------------------------------------------------

    async def start(  # noqa: PLR0915
        self, initial_prompt: str | list[Any] = ""
    ) -> AsyncGenerator[RichAgentStreamEvent[Any]]:
        """Start the idle/wake/turn loop as an async generator.

        Yields :class:`RichAgentStreamEvent` tokens from each turn's
        :meth:`~agentpool.orchestrator.turn.Turn.execute`. Between turns,
        the handle goes idle and waits for :meth:`steer` or
        :meth:`followup` to wake it.

        When ``initial_prompt`` is empty (default), the loop starts in
        idle mode and picks up the first prompt from
        :meth:`followup` via the CommChannel feedback queue (D17). This
        ensures the initial prompt gets a ``message_id`` and can be
        revoked before delivery.

        Args:
            initial_prompt: The first user prompt to process. Empty
                string (default) triggers idle-first startup via
                followup/CommChannel. Can be a ``list`` for multimodal
                content (images, audio, etc.).
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

        recovered_prompt = await self._handle_recovery()

        with safe_span(
            "orchestration.run_handle.start",
            session_id=self.session_id,
            agent_type=self.agent_type,
        ):
            from agentpool.observability.trace import get_trace_id

            logfire.info(
                "RunLoop started",
                trace_id=get_trace_id(),
                session_id=self.session_id,
                agent_type=self.agent_type,
            )
            try:
                async with session.turn_lock:
                    # For "retry" recovery, prepend the recovered prompt.
                    if recovered_prompt is not None:
                        current_prompts: list[str | list[Any]] = [recovered_prompt]
                    else:
                        # CRITICAL: empty string must produce [] not [""].
                        # [""] is a non-empty list that bypasses _idle_loop()
                        # and executes a spurious empty-prompt turn.
                        current_prompts = [initial_prompt] if initial_prompt else []
                    while not self._closing:
                        try:
                            if not current_prompts:
                                current_prompts = await self._idle_loop()
                                if not current_prompts:
                                    continue

                            try:
                                async with contextlib.aclosing(
                                    self._execute_turn(
                                        agent,
                                        event_bus,
                                        session,
                                        current_prompts,
                                    ),
                                ) as turn_gen:
                                    async for event in turn_gen:
                                        yield event

                                action = await self._handle_turn_result(event_bus)
                            except asyncio.CancelledError:
                                # Force-cancel was triggered by cancel() to
                                # break through __aexit__ hangs. Only catch
                                # if _force_cancelling is set; external
                                # task.cancel() (e.g. test cleanup) must
                                # propagate so start() exits.
                                if not self._force_cancelling:
                                    raise
                                self._force_cancelling = False
                                with contextlib.suppress(Exception):
                                    await self._handle_turn_result(event_bus)
                                current_prompts = []
                                continue

                            if action == "continue":
                                current_prompts = []  # Prevent re-execution of cancelled prompt
                                continue
                            if action == "break":
                                break

                            current_prompts = await self._drain_events()
                        except asyncio.CancelledError:
                            # Catch CancelledError from _idle_loop() too
                            # (e.g. double cancel() throws at
                            # _idle_event.wait()). Without this, the
                            # generator dies and all subsequent messages
                            # get stuck. Re-check _force_cancelling: if
                            # set, swallow and continue to idle.
                            if not self._force_cancelling:
                                raise
                            self._force_cancelling = False
                            current_prompts = []
                            continue

            finally:
                self._closed = True
                # Set complete_event FIRST, before any await that might
                # raise CancelledError (BaseException, not caught by
                # suppress(Exception)). Without this, shutdown() hangs
                # waiting for complete_event when pydantic-ai's anyio
                # cancel scope triggers CancelledError during cleanup.
                self._turn_complete_event.set()
                self.complete_event.set()
                # Lifecycle state transition: → DONE.
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    await self._transition(RunState.DONE)
                # Close lifecycle dimensions.
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    if self._trigger_source is not None:
                        self._trigger_source.close()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    if self._comm_channel is not None:
                        self._comm_channel.close()
                with contextlib.suppress(Exception, asyncio.CancelledError):
                    if self._event_transport is not None:
                        self._event_transport.close()

    async def _handle_recovery(self) -> str | None:
        """Perform crash recovery and subscribe lifecycle dimensions.

        Checks the journal for prior state. If an in-flight Turn is
        detected, replays journaled events and applies the recovery
        strategy (``"retry"`` or ``"mark_interrupted"``). Saves the
        initial snapshot for fresh starts. Subscribes the trigger
        source and CommChannel to this handle.

        Returns:
            The recovered prompt for the ``"retry"`` strategy, or ``None``.
        """
        assert self._journal is not None
        assert self._snapshot_store is not None
        assert self._comm_channel is not None
        assert self._trigger_source is not None
        recovered_prompt: str | None = None
        resume_result = self._journal.resume(self._snapshot_store)
        if resume_result is not None:
            if resume_result.is_inflight:
                # In-flight crash recovery: replay journaled events.
                self._comm_channel.set_replaying(True)
                try:
                    for event in resume_result.events:
                        await self._comm_channel.publish(event)
                finally:
                    self._comm_channel.set_replaying(False)
                self._recovered_inflight_turn_id = resume_result.inflight_turn_id
                # Apply recovery strategy.
                if self._recover_strategy == "retry":
                    # Re-queue the interrupted Turn's prompt for re-execution.
                    # Prefer the full serialized prompts (with multimodal
                    # content) if available; fall back to text prompt.
                    state_dict = resume_result.state
                    if isinstance(state_dict, dict):
                        prompts_serialized: Any = state_dict.get("prompts_serialized")
                        if isinstance(prompts_serialized, str) and prompts_serialized:
                            from agentpool.storage.serialization import deserialize_prompts

                            deserialized = deserialize_prompts(prompts_serialized)
                            if deserialized:
                                # Store the full prompts for the idle loop
                                # to pick up. We extend the message queue
                                # so _idle_loop() collects each prompt as
                                # an individual item, preserving the
                                # original prompt structure.
                                self._message_queue.extend(deserialized)
                            else:
                                # Fall back to text prompt if deserialization fails
                                prompt_val: Any = state_dict.get("prompt")
                                if isinstance(prompt_val, str) and prompt_val:
                                    recovered_prompt = prompt_val
                        else:
                            prompt_val = state_dict.get("prompt")
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
        return recovered_prompt

    async def _idle_loop(self) -> list[str | list[Any]]:
        """Wait for idle, drain feedback, and collect prompts for next turn.

        Clears the idle event, drains CommChannel feedback and message
        queue, then blocks on the idle event if no prompts are available.
        After waking, drains feedback again and returns the collected
        prompts.

        Returns:
            List of prompts for the next turn. Empty list if closing
            with no pending messages.
        """
        self._idle_event.clear()
        # Drain CommChannel feedback queue (ProtocolChannel) BEFORE
        # deciding to block. Feedback may have been enqueued by
        # steer/followup via deliver_feedback() while the loop was
        # running (e.g., during cancel). Without this, the loop would
        # block on _idle_event.wait() even though feedback is already
        # available in the CommChannel.
        if self._comm_channel is not None:
            while True:
                fb = self._comm_channel.recv()
                if fb is None:
                    break
                if fb.content_blocks is not None:
                    self._message_queue.append(fb.content_blocks)
                else:
                    self._message_queue.append(fb.content)
        # Check if messages were queued during cancel/cleanup before
        # blocking. Without this, messages routed through _message_queue
        # by the cancel path would deadlock: cancel() sets _idle_event,
        # but clear() above removes it, and wait() blocks forever with
        # no one to re-set it.
        if not self._message_queue:
            await self._idle_event.wait()
            if self._closing:
                # Drain any feedback from CommChannel before checking
                # for pending messages.
                if self._comm_channel is not None:
                    while True:
                        fb = self._comm_channel.recv()
                        if fb is None:
                            break
                        if fb.content_blocks is not None:
                            self._message_queue.append(fb.content_blocks)
                        else:
                            self._message_queue.append(fb.content)
                # Process pending messages before exiting so close()
                # with queued followups are handled as final Turns.
                if not self._message_queue:
                    return []
        # Drain CommChannel feedback queue again after waking from
        # idle (feedback may have arrived during the wait).
        if self._comm_channel is not None:
            while True:
                fb = self._comm_channel.recv()
                if fb is None:
                    break
                if fb.content_blocks is not None:
                    self._message_queue.append(fb.content_blocks)
                else:
                    self._message_queue.append(fb.content)
        prompts = list(self._message_queue)
        self._message_queue.clear()
        return prompts

    async def _execute_turn(  # noqa: PLR0915
        self,
        agent: BaseAgent[Any, Any],
        event_bus: EventBus,
        session: SessionState,
        current_prompts: list[str | list[Any]],
    ) -> AsyncGenerator[RichAgentStreamEvent[Any]]:
        """Execute a single turn and yield stream events.

        Creates a Turn from the current prompts, publishes
        ``RunStartedEvent``, saves the user prompt to conversation
        history, takes a pre-turn snapshot, then executes the Turn
        and yields each event. On exception, publishes and yields a
        ``RunErrorEvent``.

        Stores the Turn, turn_id, and turn_failed flag on ``self`` for
        downstream sub-methods (``_handle_turn_result``,
        ``_drain_events``).

        Args:
            agent: The agent driving the turn.
            event_bus: The event bus for publishing events.
            session: The per-session state.
            current_prompts: Prompts for this turn.
        """
        assert self._comm_channel is not None
        assert self._snapshot_store is not None
        # Lifecycle state transition: IDLE -> RUNNING.
        await self._transition(RunState.RUNNING)
        # Generate a unique turn_id for this Turn.
        turn_id = str(uuid.uuid4())
        self.run_ctx.turn_id = turn_id
        # Reset per-turn state: clear the completion event and clear
        # any stale cancelled flag from a prior turn.
        self._turn_complete_event.clear()
        self._turn_was_cancelled = False
        if self.run_ctx.cancelled:
            self.run_ctx.cancelled = False
        # Construct per-turn AgentContext and inject as deps so
        # capabilities (SubagentCapability, etc.) can access the
        # delegation service, resource sources, and host.
        self._inject_agent_context()
        # Forward _resume_deferred_tool_results to agent.create_turn()
        # via **pydantic_ai_kwargs so it reaches NativeTurn → agentlet.iter().
        # Only set during resume from checkpoint; None for normal turns.
        create_turn_kwargs: dict[str, Any] = {}
        if self._resume_deferred_tool_results is not None:
            create_turn_kwargs["deferred_tool_results"] = self._resume_deferred_tool_results
        turn = agent.create_turn(
            prompts=current_prompts,  # type: ignore[arg-type]
            run_ctx=self.run_ctx,
            message_history=self._message_history,
            **create_turn_kwargs,
        )
        # Publish RunStartedEvent before turn.execute() so consumers
        # know a new turn is starting.
        run_started = RunStartedEvent(
            run_id=self.run_id,
            session_id=self.session_id,
            agent_name=self.agent.name if self.agent is not None else self.agent_type,
            parent_session_id=session.parent_session_id if session is not None else None,
        )
        if not self._comm_channel.publishes_to_event_bus:
            await event_bus.publish(self.session_id, run_started)
        await self._comm_channel.publish(run_started)
        # Set _current_input_provider ContextVar so MCP elicitation can
        # access it during turn execution. Only set() without reset():
        # start() runs inside an asyncio.Task which copies the parent
        # Context, so set() only affects this task's private context
        # copy. When the task ends the context is discarded.
        if session.input_provider is not None:
            from agentpool.mcp_server.manager import _current_input_provider

            _current_input_provider.set(session.input_provider)
        # Save user prompt to agent conversation before execution.
        # This ensures user messages are preserved even if the turn
        # fails or is cancelled. For list prompts (content_blocks),
        # extract text from each block for the ChatMessage.content
        # string representation.
        from agentpool.agents.native_agent.helpers import _summarize_content_block
        from agentpool.storage.serialization import serialize_prompts

        prompt_text = "\n".join(
            p if isinstance(p, str) else " ".join(_summarize_content_block(b) for b in p)
            for p in current_prompts
        )
        agent.conversation.add_chat_messages([
            ChatMessage(
                content=prompt_text,
                role="user",
                name=agent.name,
                session_id=self.session_id,
            ),
        ])
        # Pre-turn snapshot: save prompt and turn_id for crash
        # recovery. If the process crashes during turn.execute(),
        # this snapshot allows the "retry" strategy to recover.
        # Save BOTH text prompt (for logging) and full serialized
        # prompts (for crash recovery with multimodal content).
        self._snapshot_store.save({
            "state": RunState.RUNNING.value,
            "run_id": self.run_id,
            "turn_id": turn_id,
            "prompt": prompt_text,
            "prompts_serialized": serialize_prompts(current_prompts),
        })
        # Store turn state for downstream sub-methods.
        self._current_turn = turn
        self._current_turn_id = turn_id
        self._current_turn_failed = False
        turn_failed = False
        stream_complete_saved = False
        with safe_span(
            "orchestration.run_handle.execute_turn",
            turn_id=turn_id,
            session_id=self.session_id,
        ):
            try:
                async with contextlib.aclosing(turn.execute()) as event_gen:
                    async for event in event_gen:
                        if not self._comm_channel.publishes_to_event_bus:
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
                            stream_complete_saved = True
                        yield event
                        if isinstance(event, RunErrorEvent):
                            turn_failed = True
                            break
                        if isinstance(event, StreamCompleteEvent):
                            break
            except (GeneratorExit, asyncio.CancelledError):
                # GeneratorExit: from aclose() on start() — let safe_span
                #   __exit__ run in the finally block, then propagate.
                # CancelledError: may be raised by anyio cancel scope cleanup
                #   inside pydantic-ai's Agent.iter() during GeneratorExit
                #   processing, or from task.cancel(). Propagate as-is so
                #   callers can suppress appropriately.
                raise
            except Exception as e:  # noqa: BLE001
                turn_failed = True
                error_event = RunErrorEvent(
                    message=str(e),
                    run_id=self.run_id,
                    agent_name=self.agent.name if self.agent is not None else self.agent_type,
                )
                if not self._comm_channel.publishes_to_event_bus:
                    await event_bus.publish(self.session_id, error_event)
                await self._comm_channel.publish(error_event)
                yield error_event
            finally:
                self._current_turn_failed = turn_failed
                # Preserve partial history for ALL non-StreamCompleteEvent
                # exit paths. Without this:
                #
                # - RunErrorEvent (generic Exception): agent.conversation
                #   has only the user message — next turn loses context.
                # - CancelledError (cooperative cancel): _final_message IS
                #   set by NativeTurn but no StreamCompleteEvent is yielded,
                #   so the StreamCompleteEvent branch never fires.
                #
                # Use the private attribute to avoid raising when
                # _final_message was never set (e.g. generic Exception
                # before any output was produced).  Skip if the
                # StreamCompleteEvent branch already saved.
                if not stream_complete_saved and turn._final_message is not None:
                    agent.conversation.add_chat_messages(
                        [turn._final_message],
                        extend_last=True,
                    )

    async def _handle_turn_result(self, event_bus: EventBus) -> str:
        """Handle cancel and error outcomes after turn execution.

        If the turn was cancelled, publishes ``RunFailedEvent``, routes
        queued steer messages, transitions to IDLE, and returns
        ``"continue"``. If the turn failed, returns ``"break"``.
        Otherwise saves message history and returns ``"proceed"``.

        Args:
            event_bus: The event bus for publishing events.

        Returns:
            ``"continue"`` (cancel path), ``"break"`` (failure), or
            ``"proceed"`` (normal completion).
        """
        turn = self._current_turn
        turn_failed = self._current_turn_failed
        if turn is None:
            return "break"

        assert self._comm_channel is not None

        if self.run_ctx.cancelled:
            # Turn was cancelled -- publish RunFailedEvent, set turn
            # complete, clear prompts, and continue to idle for next
            # turn. RunFailedEvent must be published BEFORE
            # _turn_complete_event so the event converter can emit
            # TurnCompleteUpdate(stop_reason="cancelled").
            cancelled_event = RunFailedEvent(
                run_id=self.run_id,
                session_id=self.session_id,
                exception=RuntimeError("Run cancelled"),
            )
            if not self._comm_channel.publishes_to_event_bus:
                await event_bus.publish(self.session_id, cancelled_event)
            await self._comm_channel.publish(cancelled_event)
            # Capture cancelled state BEFORE setting _turn_complete_event.
            # handle_prompt() checks run_handle.cancelled after waking
            # from _turn_complete_event.wait(). But the loop may reset
            # cancelled=False before handle_prompt() gets scheduled.
            # _turn_was_cancelled preserves the state for observation.
            self._turn_was_cancelled = True
            self._turn_complete_event.set()
            # Route queued steer messages through _message_queue instead
            # of directly into current_prompts. This forces the loop
            # through idle, preserving cancelled=True for handle_prompt()
            # to observe before the next turn resets it.
            if self.run_ctx.queued_steer_messages:
                self._message_queue.extend(self.run_ctx.queued_steer_messages)
                self.run_ctx.queued_steer_messages.clear()
            # Prevent re-execution of cancelled prompt.
            # Preserve the cancelled turn's message history so the next
            # turn sees the partial conversation context.
            if not turn_failed:
                with contextlib.suppress(RuntimeError):
                    self._message_history = turn.message_history
            # Do NOT reset cancelled here -- handle_prompt() needs to
            # observe it. It will be reset at the start of the next turn.
            # Lifecycle state transition: RUNNING -> IDLE (cancel path).
            await self._transition(RunState.IDLE)
            return "continue"

        if turn_failed:
            return "break"

        with contextlib.suppress(RuntimeError):
            self._message_history = turn.message_history
        return "proceed"

    async def _drain_events(self) -> list[str | list[Any]]:
        """Post-turn snapshot, child event collection, and feedback drain.

        Transitions to IDLE, saves a turn-boundary snapshot, saves the
        turn result for idempotency, waits for background child tasks,
        collects queued steer messages, drains CommChannel feedback,
        and signals turn completion.

        Returns:
            Prompts for the next turn (steer feedback + queued messages).
        """
        turn = self._current_turn
        turn_id = self._current_turn_id
        assert turn is not None
        assert turn_id is not None
        assert self._snapshot_store is not None

        # Lifecycle state transition: RUNNING -> IDLE.
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
        # Collect queued steer messages from completed children as
        # prompts for the next turn.
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
        # Drain CommChannel feedback queue (ProtocolChannel). Feedback
        # may have been enqueued by steer/followup via deliver_feedback()
        # during the Turn. Steer feedback is prioritized as next-turn
        # prompts.
        feedback_steer: list[str | list[Any]] = []
        if self._comm_channel is not None:
            while True:
                fb = self._comm_channel.recv()
                if fb is None:
                    break
                if fb.is_steer:
                    if fb.content_blocks is not None:
                        feedback_steer.append(fb.content_blocks)
                    else:
                        feedback_steer.append(fb.content)
                elif fb.content_blocks is not None:
                    self._message_queue.append(fb.content_blocks)
                else:
                    self._message_queue.append(fb.content)
        prompts = feedback_steer + list(self._message_queue)
        self._message_queue.clear()
        # Signal that this turn has completed normally.
        self._turn_was_cancelled = False
        self._turn_complete_event.set()
        return prompts

    @logfire.instrument("orchestration.run_handle.steer")
    def steer(
        self,
        message: str | list[Any],
        *,
        message_id: str | None = None,
    ) -> str | None:
        """Inject a steer message into the active turn or wake idle handle.

        For ProtocolChannel (bidirectional CommChannel with
        ``deliver_feedback()``), routes through the CommChannel feedback
        loop. For DirectChannel (unidirectional), falls back to the
        existing ``_message_queue`` / ``active_agent_run.enqueue()``
        logic.

        When ``message`` is a ``list``, it is stored as
        ``Feedback.content_blocks`` (structured/multimodal content). For
        native agents with an active ``agent_run``, the content blocks
        are unpacked via ``agent_run.enqueue(*content_blocks,
        priority="asap")``.

        Design note: Once feedback is dequeued by ``recv()`` and
        delivered to the agent runtime (via ``_idle_loop`` →
        ``_execute_turn`` → ``agent_run``), it cannot be revoked.
        This is a deliberate design choice — post-delivery revocation
        would require deep integration with pydantic_ai's
        ``PendingMessage`` lifecycle, which is fragile and provides
        little value.

        Args:
            message: The steer message (plain text or structured content
                blocks).
            message_id: Optional message ID. Auto-generated as UUID4 if
                not provided.

        Returns:
            The ``message_id`` string on success, ``None`` if the handle
            is closing or in a non-steerable state.

        Raises:
            RuntimeError: If :meth:`close` has already been called.
        """
        if self._closed:
            msg = "Cannot steer after close()"
            raise RuntimeError(msg)

        if self._closing:
            return None

        # Construct Feedback with message_id and content_blocks.
        # When message_id is None, omit it so Feedback's default_factory
        # auto-generates a UUID4.
        fb_kwargs: dict[str, Any] = {}
        if message_id is not None:
            fb_kwargs["message_id"] = message_id
        if isinstance(message, list):
            fb = Feedback(
                content="",
                is_steer=True,
                content_blocks=message,
                **fb_kwargs,
            )
        else:
            fb = Feedback(
                content=message,
                is_steer=True,
                **fb_kwargs,
            )

        # Try CommChannel feedback path (ProtocolChannel returns True).
        if self._comm_channel is not None and self._comm_channel.deliver_feedback(fb):
            # Always set _idle_event when delivering via ProtocolChannel.
            # If the loop is running, the event is cleared when entering
            # idle, and the loop then drains CommChannel feedback. If the
            # loop is transitioning to idle (e.g., after cancel), the
            # event prevents blocking on _idle_event.wait() when the
            # feedback is already in the CommChannel queue.
            self._idle_event.set()
            return fb.message_id

        # Fallback: DirectChannel path (existing logic).
        if self._run_state == RunState.IDLE:
            if fb.content_blocks is not None:
                self._message_queue.append(fb.content_blocks)
            else:
                self._message_queue.append(fb.content)
            self._idle_event.set()
            return fb.message_id

        if self._run_state == RunState.RUNNING:
            agent_run = self.active_agent_run
            if agent_run is not None:
                if fb.content_blocks is not None:
                    agent_run.enqueue(*fb.content_blocks, priority="asap")
                else:
                    agent_run.enqueue(fb.content, priority="asap")
                return fb.message_id
            # No active agent_run — queue for next turn.
            if fb.content_blocks is not None:
                self.run_ctx.queued_steer_messages.append(fb.content_blocks)
            else:
                self.run_ctx.queued_steer_messages.append(fb.content)
            return fb.message_id

        return None

    @logfire.instrument("orchestration.run_handle.followup")
    def followup(
        self,
        message: str | list[Any],
        *,
        message_id: str | None = None,
    ) -> str | None:
        """Queue a follow-up prompt for the next turn.

        For ProtocolChannel, routes through the CommChannel feedback
        loop with ``is_steer=False``. For DirectChannel, falls back
        to the existing ``_message_queue`` logic.

        When ``message`` is a ``list``, it is stored as
        ``Feedback.content_blocks`` (structured/multimodal content).

        Args:
            message: The follow-up message (plain text or structured
                content blocks).
            message_id: Optional message ID. Auto-generated as UUID4 if
                not provided.

        Returns:
            The ``message_id`` string on success, ``None`` if the handle
            is closing or closed.
        """
        if self._closing or self._closed:
            return None

        # Construct Feedback BEFORE deliver_feedback to preserve
        # message_id generation for both ProtocolChannel and
        # DirectChannel paths (D17 BLOCKER 2 fix).
        # When message_id is None, omit it so Feedback's default_factory
        # auto-generates a UUID4.
        fb_kwargs: dict[str, Any] = {}
        if message_id is not None:
            fb_kwargs["message_id"] = message_id
        if isinstance(message, list):
            fb = Feedback(
                content="",
                is_steer=False,
                content_blocks=message,
                **fb_kwargs,
            )
        else:
            fb = Feedback(
                content=message,
                is_steer=False,
                **fb_kwargs,
            )

        # Try CommChannel feedback path (ProtocolChannel returns True).
        if self._comm_channel is not None and self._comm_channel.deliver_feedback(fb):
            # Always set _idle_event (see steer() for rationale).
            self._idle_event.set()
            return fb.message_id

        # Fallback: DirectChannel path (existing logic).
        if fb.content_blocks is not None:
            self._message_queue.append(fb.content_blocks)
        else:
            self._message_queue.append(fb.content)
        if self._run_state == RunState.IDLE:
            self._idle_event.set()
        return fb.message_id

    def revoke(self, message_id: str) -> bool:
        """Revoke a pending steer or followup message by ID.

        Delegates to ``comm_channel.revoke()`` which operates at the
        CommChannel queue layer. If the feedback is still pending in
        the channel's feedback queue, it is removed and marked as
        revoked. Once delivered to the agent runtime, revocation is
        not possible (by design).

        Args:
            message_id: The ID of the message to revoke.

        Returns:
            ``True`` if revoked or already gone (idempotent), ``False``
            if already delivered or no CommChannel configured.
        """
        if self._comm_channel is None:
            return False
        return self._comm_channel.revoke(message_id)

    async def _steer_callback_wrapper(self, session_id: str, message: str) -> str | None:
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
            The ``message_id`` string on success, ``None`` otherwise.
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
        self._run_state = RunState.RUNNING
        self.run_ctx.current_task = task

    def complete(self) -> None:
        """Transition the run to completed and trigger cleanup."""
        self._run_state = RunState.DONE
        self.outcome = RunOutcome.COMPLETED
        self._cleanup_run()

    def checkpoint(self) -> None:
        """Transition the run to checkpointed and trigger cleanup."""
        self._run_state = RunState.DONE
        self.outcome = RunOutcome.CHECKPOINTED
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
        self._run_state = RunState.DONE
        self.outcome = RunOutcome.FAILED
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
        """Cancel the run cooperatively, then force-cancel if needed.

        Sets the cancelled flag on the run context and wakes the idle
        event to unblock the turn loop. Calls the registered cancel
        function (wired in ``start()``) which schedules
        ``agent._interrupt()`` for subclass-specific cleanup.

        Also force-cancels ``run_ctx.current_task`` to inject
        ``CancelledError`` into a hung ``__aexit__`` (e.g. MCP
        streamable-http cleanup stuck behind an HTTP proxy). The
        ``CancelledError`` is caught by ``NativeTurn.execute()``'s
        ``except asyncio.CancelledError`` handler which checks
        ``run_ctx.cancelled`` and exits gracefully. The ``start()``
        ``finally`` block still runs, setting ``complete_event`` and
        releasing ``turn_lock``.
        """
        self.run_ctx.cancelled = True
        self._idle_event.set()

        if self._cancel_fn is not None:
            self._cancel_fn()

        # Force-cancel the task driving start() to break through __aexit__
        # hangs. The CancelledError will be caught by NativeTurn's except
        # handler which checks run_ctx.cancelled and exits gracefully. The
        # start() finally block will still run, setting complete_event and
        # releasing turn_lock.
        #
        # Idempotency guard: if _force_cancelling is already True, a
        # previous cancel() already called task.cancel(). Calling
        # task.cancel() again would throw a second CancelledError at
        # _idle_event.wait() in _idle_loop() — outside the inner except
        # handler — killing the start() generator. Just set the idle
        # event (done above) and return.
        if self._force_cancelling:
            return

        task = self.run_ctx.current_task
        if task is not None and not task.done():
            self._force_cancelling = True
            task.cancel()

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
