"""Stream utilities for merging async iterators."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import time
from typing import TYPE_CHECKING, Any, Literal


FileOperation = Literal["create", "write", "edit", "delete"]


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def merge_queue_into_iterator[T, V](  # noqa: PLR0915
    primary_stream: AsyncIterator[T],
    secondary_queue: asyncio.Queue[V],
) -> AsyncIterator[AsyncIterator[T | V]]:
    """Merge a primary async stream with events from a secondary queue.

    Args:
        primary_stream: The main async iterator (e.g., provider events)
        secondary_queue: Queue containing secondary events (e.g., progress events)

    Yields:
        Async iterator that yields events from both sources in real-time.
        Secondary queue is fully drained before the iterator completes.

    Example:
        ```python
        progress_queue: asyncio.Queue[ProgressEvent] = asyncio.Queue()

        async with merge_queue_into_iterator(provider_stream, progress_queue) as events:
            async for event in events:
                print(f"Got event: {event}")
        ```
    """
    # Create a queue for all merged events
    event_queue: asyncio.Queue[V | T | None] = asyncio.Queue()
    primary_done = asyncio.Event()
    primary_exception: BaseException | None = None
    # Track if we've signaled the end of streams
    end_signaled = False

    # Task to read from primary stream and put into merged queue
    async def primary_task() -> None:
        nonlocal primary_exception, end_signaled
        try:
            async for event in primary_stream:
                await event_queue.put(event)
        except asyncio.CancelledError:
            # Signal completion and unblock merged_events before re-raising
            primary_done.set()
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)
            raise
        except BaseException as e:  # noqa: BLE001
            primary_exception = e
        finally:
            primary_done.set()

    # Task to read from secondary queue and put into merged queue
    async def secondary_task() -> None:
        nonlocal end_signaled
        try:
            while not primary_done.is_set():
                try:
                    secondary_event = await asyncio.wait_for(secondary_queue.get(), timeout=0.01)
                    await event_queue.put(secondary_event)
                except TimeoutError:
                    continue
            # Drain any remaining events after primary completes
            while not secondary_queue.empty():
                try:
                    secondary_event = secondary_queue.get_nowait()
                    await event_queue.put(secondary_event)
                except asyncio.QueueEmpty:
                    break
            # Now signal end of all events (only if not already signaled)
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)
        except asyncio.CancelledError:
            # Still need to signal completion on cancel (only if not already signaled)
            if not end_signaled:
                end_signaled = True
                await event_queue.put(None)

    # Start both tasks
    primary_task_obj = asyncio.create_task(primary_task())
    secondary_task_obj = asyncio.create_task(secondary_task())

    try:
        # Create async iterator that drains the merged queue
        async def merged_events() -> AsyncIterator[V | T]:
            while True:
                event = await event_queue.get()
                if event is None:  # End of all streams
                    break
                yield event
            # Re-raise any exception from primary stream after draining
            if primary_exception is not None:
                raise primary_exception

        yield merged_events()

    finally:
        # Clean up tasks - cancel BOTH tasks
        primary_task_obj.cancel()
        secondary_task_obj.cancel()
        await asyncio.gather(primary_task_obj, secondary_task_obj, return_exceptions=True)


@dataclass
class FileChange:
    """Represents a single file change operation."""

    path: str
    """File path that was modified."""

    old_content: str | None
    """Content before change (None for new files)."""

    new_content: str | None
    """Content after change (None for deletions)."""

    operation: FileOperation
    """Type of operation: 'create', 'write', 'edit', 'delete'."""

    timestamp: float = field(default_factory=time.time)
    """Unix timestamp when the change occurred."""

    message_id: str | None = None
    """ID of the message that triggered this change (for revert-to-message)."""

    agent_name: str | None = None
    """Name of the agent that made this change."""

    def to_unified_diff(self) -> str:
        """Generate unified diff for this change.

        Returns:
            Unified diff string
        """
        from agentpool.utils.diffs import compute_unified_diff

        return compute_unified_diff(
            self.old_content or "",
            self.new_content or "",
            fromfile=f"a/{self.path}",
            tofile=f"b/{self.path}",
        )


@dataclass
class FileOpsTracker:
    r"""Tracks file operations with full content for diff/revert support.

    Stores file changes with before/after content so they can be:
    - Displayed as diffs
    - Reverted to previous state
    - Filtered by message ID

    Example:
        ```python
        tracker = FileOpsTracker()

        # Record a file edit
        tracker.record_change(
            path="src/main.py",
            old_content="def foo(): pass",
            new_content="def foo():\\n    return 42",
            operation="edit",
        )

        # Get all diffs
        for change in tracker.changes:
            print(change.to_unified_diff())

        # Revert all changes
        for path, content in tracker.get_revert_operations():
            write_file(path, content)
        ```
    """

    changes: list[FileChange] = field(default_factory=list)
    """List of all recorded file changes in order."""

    reverted_changes: list[FileChange] = field(default_factory=list)
    """Changes that were reverted and can be restored with unrevert."""

    def record_change(
        self,
        path: str,
        old_content: str | None,
        new_content: str | None,
        operation: FileOperation,
        message_id: str | None = None,
        agent_name: str | None = None,
    ) -> None:
        """Record a file change.

        Args:
            path: File path that was modified
            old_content: Content before change (None for new files)
            new_content: Content after change (None for deletions)
            operation: Type of operation ('create', 'write', 'edit', 'delete')
            message_id: Optional message ID that triggered this change
            agent_name: Optional name of the agent that made this change
        """
        change = FileChange(
            path=path,
            old_content=old_content,
            new_content=new_content,
            operation=operation,
            message_id=message_id,
            agent_name=agent_name,
        )
        self.changes.append(change)

    def get_changes_for_path(self, path: str) -> list[FileChange]:
        """Get all changes for a specific file path.

        Args:
            path: File path to filter by

        Returns:
            List of changes for the given path
        """
        return [c for c in self.changes if c.path == path]

    def get_changes_since(self, message_id: str) -> list[FileChange]:
        """Get all changes since (and including) a specific message."""
        for i, change in enumerate(self.changes):
            if change.message_id == message_id:
                return self.changes[i:]
        return []

    def get_modified_paths(self) -> set[str]:
        """Get set of all modified file paths."""
        return {c.path for c in self.changes}

    def get_current_state(self) -> dict[str, str | None]:
        """Get the current state of all modified files.

        For each file, returns the content after all changes have been applied.
        Returns None for deleted files.

        Returns:
            Dict mapping path to current content (or None if deleted)
        """
        return {change.path: change.new_content for change in self.changes}

    def get_original_state(self) -> dict[str, str | None]:
        """Get the original state of all modified files.

        For each file, returns the content before any changes were made.
        Returns None for files that were created (didn't exist).

        Returns:
            Dict mapping path to original content (or None if created)
        """
        return {change.path: change.old_content for change in reversed(self.changes)}

    def get_revert_operations(
        self, since_message_id: str | None = None
    ) -> list[tuple[str, str | None]]:
        """Get operations needed to revert changes.

        Returns list of (path, content) tuples in reverse order (newest first).
        If content is None, the file should be deleted.

        Args:
            since_message_id: If provided, only revert changes from this message onwards.
                              If None, revert all changes.

        Returns:
            List of (path, content_to_restore) tuples for revert
        """
        changes = self.get_changes_since(since_message_id) if since_message_id else self.changes
        # Build map of path -> content to restore
        # For each path, we need the old_content of the FIRST change in our subset
        # (that's what the file looked like before any of these changes)
        original_for_path = {change.path: change.old_content for change in reversed(changes)}
        return list(original_for_path.items())

    def get_combined_diff(self) -> str:
        """Get combined unified diff of all changes."""
        diffs = [diff for change in self.changes if (diff := change.to_unified_diff())]
        return "\n".join(diffs)

    def clear(self) -> None:
        """Clear all recorded changes."""
        self.changes.clear()

    def remove_changes_since(self, message_id: str) -> int:
        """Remove changes from a specific message onwards and store for unrevert.

        The removed changes are stored in `reverted_changes` so they can be
        restored later via `restore_reverted_changes()`.

        Args:
            message_id: Message ID to start removal from

        Returns:
            Number of changes removed
        """
        # Find the index of the first change with this message_id
        start_idx = next(
            (i for i, change in enumerate(self.changes) if change.message_id == message_id),
            None,
        )

        if start_idx is None:
            return 0

        # Store removed changes for potential unrevert
        self.reverted_changes = self.changes[start_idx:]
        self.changes = self.changes[:start_idx]
        return len(self.reverted_changes)

    def get_unrevert_operations(self) -> list[tuple[str, str | None]]:
        """Get operations needed to restore reverted changes.

        Returns list of (path, content) tuples. The content is the new_content
        from each reverted change (what the file should contain after unrevert).

        Returns:
            List of (path, content_to_write) tuples for unrevert
        """
        if not self.reverted_changes:
            return []

        # For each path, we want the LAST new_content in the reverted changes
        # (that's what the file looked like before the revert)
        final_content = {change.path: change.new_content for change in self.reverted_changes}
        return list(final_content.items())

    def restore_reverted_changes(self) -> int:
        """Move reverted changes back to main changes list. Returns number of changes restored."""
        if not self.reverted_changes:
            return 0

        restored_count = len(self.reverted_changes)
        self.changes.extend(self.reverted_changes)
        self.reverted_changes = []
        return restored_count

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "changes": [
                {
                    "path": c.path,
                    "operation": c.operation,
                    "timestamp": c.timestamp,
                    "message_id": c.message_id,
                    "agent_name": c.agent_name,
                    "has_old_content": c.old_content is not None,
                    "has_new_content": c.new_content is not None,
                }
                for c in self.changes
            ],
            "modified_paths": sorted(self.get_modified_paths()),
        }
