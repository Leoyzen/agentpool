"""Ephemeral run handle for agent execution lifecycle management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from pydantic_ai import AgentRun

from agentpool.agents.context import AgentRunContext


if TYPE_CHECKING:
    from collections.abc import Callable


class RunStatus(Enum):
    """Lifecycle states for an agent run."""

    pending = auto()
    running = auto()
    completed = auto()
    failed = auto()
    checkpointed = auto()


@dataclass
class RunHandle:
    """Ephemeral runtime handle for a single agent run.

    RunHandle is not serializable and exists only for the duration of a run.
    It bridges the SessionPool's run tracking with the actual asyncio.Task
    and AgentRunContext.

    Attributes:
        run_id: Unique identifier for this run.
        session_id: Session this run belongs to.
        agent_type: Type of agent running (e.g. ``"native"``, ``"claude"``).
        status: Current lifecycle state.
        run_ctx: Per-run isolated state container.
        complete_event: Set after cleanup finishes.
        _cleanup_callback: Optional callback invoked with run_id during cleanup.
        active_agent_run: Reference to PydanticAI AgentRun, set by
            RunExecutor during execution and cleared in ``finally``.
    """

    run_id: str
    session_id: str
    agent_type: str
    status: RunStatus = RunStatus.pending
    run_ctx: AgentRunContext = field(default_factory=AgentRunContext)
    complete_event: asyncio.Event = field(default_factory=asyncio.Event)
    _cleanup_callback: Callable[[str], None] | None = None
    active_agent_run: AgentRun[Any, Any] | None = None

    def start(self, task: asyncio.Task[Any] | None = None) -> None:
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
        """Transition the run to checkpointed and trigger cleanup.

        Unlike :meth:`fail`, checkpoint does **not** emit a
        :class:`RunFailedEvent` — it is a normal lifecycle transition
        that occurs when the agent's execution state has been persisted
        for later resumption (e.g. deferred tool calls).
        """
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

        Sets the cancelled flag on the run context and cancels the
        underlying task. Cleanup is deferred to the caller or task
        done-callback to avoid re-entrant deadlocks.
        """
        self.run_ctx.cancelled = True
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
        self.complete_event.set()
