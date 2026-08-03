"""Notification batcher with anyio structured concurrency.

Debounces and batches background-task completion notifications before
delivering them to the lead agent.  Uses ``anyio.CancelScope`` and
``anyio.create_task_group`` for structured concurrency, with
``anyio.fail_after`` for delivery timeout protection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING

import anyio


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from agentpool.capabilities.background_task.types import (
        BackgroundTask,
        TaskStatus,
    )


logger = logging.getLogger(__name__)

# Priority ordering: lower number = earlier in output
_STATUS_PRIORITY: dict[TaskStatus, int] = {
    "error": 0,
    "completed": 1,
    "cancelled": 2,
    "timed_out": 3,
    "pending": 4,
    "running": 5,
    "cancelling": 6,
}


def _format_duration(started_at: datetime | None, completed_at: datetime | None) -> str:
    """Format the duration between two datetimes as a human-readable string.

    Args:
        started_at: The start datetime
        completed_at: The end datetime

    Returns:
        A string like "2m 30s", "45s", or "" if inputs are None
    """
    if started_at is None or completed_at is None:
        return ""
    delta = completed_at - started_at
    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return ""
    minutes, seconds = divmod(total_seconds, 60)
    if minutes > 0:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


class NotificationBatcher:
    """Debounces and batches background-task completion notifications.

    Collects tasks per ``parent_session_id`` and delivers a single batched
    notification after a configurable debounce window.  Uses anyio
    structured concurrency for lifecycle management.

    ``submit()`` is **synchronous** — it only queues the task and resets
    the debounce timer.  The actual flush happens asynchronously when the
    timer fires.
    """

    def __init__(
        self,
        deliver_callback: Callable[[str, list[BackgroundTask], str], Awaitable[None]],
        debounce_ms: float = 500,
        pending_count_callback: Callable[[], int] | None = None,
        deliver_timeout: float = 5.0,
    ) -> None:
        self._debounce_ms = debounce_ms
        self.deliver_callback = deliver_callback
        self._pending_count_callback = pending_count_callback or (lambda: 0)
        self._deliver_timeout = deliver_timeout

        # anyio primitives — created in start() (requires async context)
        self._cancel_scope: anyio.CancelScope | None = None
        self._tg: anyio.abc.TaskGroup | None = None

        # Per-session pending tasks
        self._pending: dict[str, list[BackgroundTask]] = {}
        # Per-session debounce timer handles
        self._timers: dict[str, asyncio.TimerHandle] = {}
        # Global set of delivered task IDs (UUID-based, no collision)
        self._delivered: set[str] = set()
        self._started: bool = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Enter the CancelScope and create the TaskGroup.

        Must be called before or automatically on first ``submit()``.
        Safe to call multiple times — only enters once.
        """
        if self._started:
            return
        self._started = True
        self._cancel_scope = anyio.CancelScope()
        self._cancel_scope.__enter__()
        self._tg = anyio.create_task_group()
        await self._tg.__aenter__()

    # ------------------------------------------------------------------
    # Submission (SYNC)
    # ------------------------------------------------------------------

    def submit(self, task: BackgroundTask) -> None:
        """Submit a completed task for batched notification.

        Synchronous — only queues the task and resets the debounce timer.
        Auto-starts the batcher if ``start()`` hasn't been called yet.

        Args:
            task: The background task in a terminal state

        Raises:
            ValueError: If ``task.parent_session_id`` is None or empty
        """
        if not task.parent_session_id:
            msg = "parent_session_id must be a non-empty string"
            raise ValueError(msg)

        # Dedup: skip if already delivered
        if task.id in self._delivered:
            return

        session_id = task.parent_session_id

        # Dedup: skip if already pending for this session
        pending_list = self._pending.get(session_id, [])
        if any(t.id == task.id for t in pending_list):
            return

        # Append to pending list
        if session_id not in self._pending:
            self._pending[session_id] = []
        self._pending[session_id].append(task)

        # Cancel previous timer for this session (debounce reset)
        prev_timer = self._timers.pop(session_id, None)
        if prev_timer is not None:
            prev_timer.cancel()

        # Schedule new flush after debounce
        loop = asyncio.get_event_loop()
        delay = self._debounce_ms / 1000.0
        handle = loop.call_later(delay, self._schedule_flush, session_id)
        self._timers[session_id] = handle

        # Auto-start if not started
        if not self._started:
            asyncio.ensure_future(self.start())  # noqa: RUF006

    # ------------------------------------------------------------------
    # Flush scheduling (SYNC — NOT async)
    # ------------------------------------------------------------------

    def _schedule_flush(self, session_id: str) -> None:
        """Schedule an async flush for the given session.

        Called by ``loop.call_later`` — must be synchronous to avoid
        RuntimeWarning about un-awaited coroutines.
        """
        # Remove the timer handle since it has fired
        self._timers.pop(session_id, None)
        if self._tg is not None:
            self._tg.start_soon(self._flush, session_id)

    # ------------------------------------------------------------------
    # Flush (ASYNC)
    # ------------------------------------------------------------------

    async def _flush(self, session_id: str) -> None:
        """Pop pending tasks atomically, format, and deliver.

        If ``deliver_callback`` hangs, ``anyio.fail_after`` raises
        ``TimeoutError`` which is caught and logged — the batcher
        continues operating.
        """
        # Atomic pop — no lost or double notifications
        tasks = self._pending.pop(session_id, [])
        if not tasks:
            return

        # Sort by status priority before formatting and delivery
        sorted_tasks = sorted(tasks, key=lambda t: _STATUS_PRIORITY.get(t.status, 99))

        # Format the batch notice
        notice = self._format_batch(sorted_tasks)

        # Deliver with timeout protection
        try:
            with anyio.fail_after(self._deliver_timeout):
                await self.deliver_callback(session_id, sorted_tasks, notice)
        except TimeoutError:
            logger.warning(
                "deliver_callback timed out after %ss for session %s (%d tasks)",
                self._deliver_timeout,
                session_id,
                len(sorted_tasks),
            )
        except asyncio.CancelledError:
            logger.debug("deliver_callback cancelled for session %s", session_id)
            raise

        # Mark tasks as delivered
        for task in sorted_tasks:
            self._delivered.add(task.id)

        # Clear _delivered when no more pending tasks
        pending_count = self._pending_count_callback()
        if pending_count == 0:
            self._delivered.clear()

    # ------------------------------------------------------------------
    # Batch formatting
    # ------------------------------------------------------------------

    def _format_batch(self, tasks: list[BackgroundTask]) -> str:
        """Format a batch of tasks into a system-reminder notice.

        Orders tasks by status priority: error > completed > cancelled/timed_out.
        Includes "N tasks still in progress" hint when pending > 0,
        or "[ALL BACKGROUND TASKS COMPLETE]" summary when pending == 0.

        Args:
            tasks: List of tasks in terminal states (already sorted)

        Returns:
            Formatted notice string wrapped in <system-reminder> tags
        """
        # Tasks are already sorted by caller, but ensure sort here too
        sorted_tasks = sorted(tasks, key=lambda t: _STATUS_PRIORITY.get(t.status, 99))

        pending_count = self._pending_count_callback()
        all_complete = pending_count == 0

        # Determine header based on content
        has_error = any(t.status in ("error", "timed_out") for t in sorted_tasks)
        single_task = len(sorted_tasks) == 1

        if single_task and has_error:
            header = "[BACKGROUND TASK ERROR]"
        elif single_task and not has_error:
            header = "[BACKGROUND TASK RESULT READY]"
        elif has_error:
            header = "[BACKGROUND TASK ERROR]"
        elif all_complete:
            header = "[ALL BACKGROUND TASKS COMPLETE]"
        else:
            header = "[BACKGROUND TASK RESULT READY]"

        lines: list[str] = []
        lines.append("<system-reminder>")
        lines.append(header)
        lines.append("")

        # Numbered summary
        for i, task in enumerate(sorted_tasks, 1):
            duration = _format_duration(task.started_at, task.completed_at)
            duration_str = f" ({duration})" if duration else ""

            if task.status == "completed":
                lines.append(f"{i}. **{task.description}** — completed{duration_str}")
                lines.append(f"   ID: `{task.id}`")
            elif task.status in ("error", "timed_out"):
                error_brief = (task.error or "unknown error")[:80]
                lines.append(f"{i}. **{task.description}** — {task.status}{duration_str}")
                lines.append(f"   Error: {error_brief}")
                lines.append(f"   ID: `{task.id}`")
            else:  # cancelled
                lines.append(f"{i}. **{task.description}** — {task.status}{duration_str}")
                lines.append(f"   ID: `{task.id}`")

        lines.append("")

        if all_complete:
            lines.append("All background tasks have completed.")
            lines.append(
                "Use `background_output(task_id=...)` to retrieve any results you need.",
            )
        else:
            lines.append(f"{pending_count} tasks still in progress.")

        lines.append("</system-reminder>")

        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Flush remaining pending tasks, cancel timers, and tear down.

        Cancels all ``asyncio.TimerHandle`` objects, flushes any remaining
        pending tasks, then cancels the ``CancelScope`` and exits the
        ``TaskGroup``.
        """
        # Cancel all pending timers
        for handle in self._timers.values():
            handle.cancel()
        self._timers.clear()

        # Flush remaining pending tasks
        for session_id in list(self._pending.keys()):
            tasks = self._pending.pop(session_id, [])
            if not tasks:
                continue

            sorted_tasks = sorted(tasks, key=lambda t: _STATUS_PRIORITY.get(t.status, 99))
            notice = self._format_batch(sorted_tasks)
            try:
                with anyio.fail_after(self._deliver_timeout):
                    await self.deliver_callback(session_id, sorted_tasks, notice)
            except TimeoutError:
                logger.warning(
                    "deliver_callback timed out during shutdown for session %s",
                    session_id,
                )
            except asyncio.CancelledError:
                logger.debug(
                    "deliver_callback cancelled during shutdown for session %s", session_id
                )
                raise

            for task in sorted_tasks:
                self._delivered.add(task.id)

        # Clear _delivered
        if self._pending_count_callback() == 0:
            self._delivered.clear()

        # Cancel the CancelScope
        if self._cancel_scope is not None:
            self._cancel_scope.cancel()

        # Exit TaskGroup
        if self._tg is not None:
            with contextlib.suppress(Exception):
                await self._tg.__aexit__(None, None, None)
            self._tg = None

        self._started = False
