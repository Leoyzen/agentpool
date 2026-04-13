"""Streaming file editing tool with async I/O, atomic writes, and progress reporting.

This module provides a file editing interface using:
- aiofiles for async non-blocking I/O
- Atomic writes via temp file + rename pattern
- Structured progress events via ToolCallProgressEvent
- Timeout controls for write operations
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, AsyncIterator, Literal
from uuid import uuid4

from agentpool.agents.events.events import (
    LocationContentItem,
    TextContentItem,
    ToolCallContentItem,
    ToolCallProgressEvent,
)
from agentpool.log import get_logger
from agentpool.utils.diffs import compute_unified_diff, count_changed_lines


if TYPE_CHECKING:
    from agentpool.agents.context import AgentContext


logger = get_logger(__name__)


@dataclass
class FileOperationProgress:
    """Structured file operation progress information.

    Attributes:
        operation: Type of file operation (read, write, diff_apply, verify)
        stage: Current stage (started, in_progress, completed, failed)
        bytes_processed: Number of bytes processed so far
        total_bytes: Total number of bytes to process
        percentage: Progress percentage (0.0 to 1.0)
        bytes_per_second: Current write throughput
        estimated_seconds_remaining: Estimated time to completion
        current_chunk: Current chunk number
        total_chunks: Total number of chunks
        file_path: Path to the file being operated on
        operation_id: Unique identifier for this operation
    """

    operation: Literal["read", "write", "diff_apply", "verify"]
    stage: Literal["started", "in_progress", "completed", "failed"]
    bytes_processed: int
    total_bytes: int
    percentage: float
    bytes_per_second: float
    estimated_seconds_remaining: float
    current_chunk: int
    total_chunks: int
    file_path: str
    operation_id: str


class StreamingFileEditor:
    """Async file editor with streaming I/O and progress reporting.

    This class provides file editing capabilities with:
    - Non-blocking async I/O via aiofiles
    - Atomic writes (temp file + rename)
    - Real-time progress events
    - Configurable chunk sizes for large files
    - Timeout protection

    Example:
        ```python
        editor = StreamingFileEditor(chunk_size=64*1024)
        async for event in editor.edit_file(
            "/path/to/file.txt",
            old_string="old content",
            new_string="new content",
            tool_call_id="tc_123"
        ):
            print(f"Progress: {event.progress}%")
        ```
    """

    DEFAULT_CHUNK_SIZE = 64 * 1024  # 64KB
    DEFAULT_TIMEOUT = 30.0  # 30 seconds

    def __init__(
        self,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        timeout: float = DEFAULT_TIMEOUT,
        use_atomic_write: bool = True,
    ):
        """Initialize the streaming file editor.

        Args:
            chunk_size: Size of chunks for streaming I/O (default 64KB)
            timeout: Timeout for write operations in seconds (default 30s)
            use_atomic_write: Whether to use atomic write via temp file + rename
        """
        self.chunk_size = chunk_size
        self.timeout = timeout
        self.use_atomic_write = use_atomic_write

    async def edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        tool_call_id: str,
        replace_all: bool = False,
        context: AgentContext | None = None,
    ) -> AsyncIterator[ToolCallProgressEvent]:
        """Perform async string replacement with progress reporting.

        Args:
            file_path: Path to the file to modify
            old_string: Text to replace
            new_string: Text to replace with
            tool_call_id: Unique identifier for this tool call
            replace_all: Whether to replace all occurrences
            context: Agent execution context

        Yields:
            ToolCallProgressEvent with progress updates
        """
        operation_id = str(uuid4())[:8]
        path = Path(file_path)

        if not path.is_absolute():
            path = Path.cwd() / path

        # Yield start event
        yield ToolCallProgressEvent(
            tool_call_id=tool_call_id,
            status="in_progress",
            title=f"Reading: {path.name}",
            items=[LocationContentItem(path=str(path), line=0)],
        )

        try:
            # Read file content with timeout
            async with asyncio.timeout(self.timeout):
                content = await self._read_file_async(path)

            if old_string == new_string:
                yield ToolCallProgressEvent(
                    tool_call_id=tool_call_id,
                    status="completed",
                    title=f"No changes needed: {path.name}",
                    items=[
                        LocationContentItem(path=str(path), line=0),
                        TextContentItem(text="old_string and new_string are identical"),
                    ],
                )
                return

            # Handle empty file case
            if old_string == "" and content == "":
                new_content = new_string
            else:
                # Use sublime_search for sophisticated replacement
                from sublime_search import replace_content

                result = replace_content(content, old_string, new_string, replace_all)
                new_content = result.content

            # Generate diff
            diff_text = compute_unified_diff(
                content, new_content, fromfile=str(path), tofile=str(path)
            )
            lines_changed = count_changed_lines(diff_text)

            # Yield diff preview event
            yield ToolCallProgressEvent.file_edit(
                tool_call_id=tool_call_id,
                path=str(path),
                old_text=content if len(content) < 5000 else content[:5000] + "...",
                new_text=new_content if len(new_content) < 5000 else new_content[:5000] + "...",
                status="in_progress",
            )

            # Write file with progress reporting
            async for event in self._write_file_with_progress(
                path, new_content, tool_call_id, operation_id
            ):
                yield event

            # Yield completion event
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="completed",
                title=f"✓ Edited {path.name} ({lines_changed} lines changed)",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"Changed {lines_changed} lines"),
                ],
            )

        except TimeoutError:
            logger.error("File operation timed out", path=str(path), timeout=self.timeout)
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"Timeout: {path.name}",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"Operation timed out after {self.timeout}s"),
                ],
            )
            raise RuntimeError(f"File operation timed out after {self.timeout}s") from None
        except FileNotFoundError:
            logger.error("File not found", path=str(path))
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"File not found: {path.name}",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"File not found: {path}"),
                ],
            )
            raise
        except Exception as e:
            logger.error("File operation failed", path=str(path), error=str(e))
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"Failed: {path.name}",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"Error: {e}"),
                ],
            )
            raise

    async def write_file(
        self,
        file_path: str,
        content: str,
        tool_call_id: str,
    ) -> AsyncIterator[ToolCallProgressEvent]:
        """Write content to file with progress reporting.

        Args:
            file_path: Path to write to
            content: Content to write
            tool_call_id: Unique identifier for this tool call

        Yields:
            ToolCallProgressEvent with progress updates
        """
        path = Path(file_path)

        if not path.is_absolute():
            path = Path.cwd() / path

        operation_id = str(uuid4())[:8]

        # Yield start event
        yield ToolCallProgressEvent(
            tool_call_id=tool_call_id,
            status="in_progress",
            title=f"Writing: {path.name}",
            items=[LocationContentItem(path=str(path), line=0)],
        )

        try:
            async with asyncio.timeout(self.timeout):
                async for event in self._write_file_with_progress(
                    path, content, tool_call_id, operation_id
                ):
                    yield event

            # Yield completion event
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="completed",
                title=f"✓ Written {path.name}",
                items=[LocationContentItem(path=str(path), line=0)],
            )

        except TimeoutError:
            logger.error("Write operation timed out", path=str(path), timeout=self.timeout)
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"Timeout: {path.name}",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"Write timed out after {self.timeout}s"),
                ],
            )
            raise RuntimeError(f"Write timed out after {self.timeout}s") from None
        except Exception as e:
            logger.error("Write operation failed", path=str(path), error=str(e))
            yield ToolCallProgressEvent(
                tool_call_id=tool_call_id,
                status="failed",
                title=f"Failed: {path.name}",
                items=[
                    LocationContentItem(path=str(path), line=0),
                    TextContentItem(text=f"Error: {e}"),
                ],
            )
            raise

    async def _read_file_async(self, path: Path) -> str:
        """Read file content asynchronously.

        Args:
            path: Path to the file

        Returns:
            File content as string
        """
        import aiofiles

        async with aiofiles.open(path, mode="r", encoding="utf-8") as f:
            return await f.read()

    async def _write_file_with_progress(
        self,
        path: Path,
        content: str,
        tool_call_id: str,
        operation_id: str,
    ) -> AsyncIterator[ToolCallProgressEvent]:
        """Write file with streaming progress events.

        Args:
            path: Path to write to
            content: Content to write
            tool_call_id: Tool call identifier
            operation_id: Operation identifier

        Yields:
            ToolCallProgressEvent with progress updates
        """
        import aiofiles
        import time

        content_bytes = content.encode("utf-8")
        total_size = len(content_bytes)
        total_chunks = (total_size + self.chunk_size - 1) // self.chunk_size

        start_time = time.monotonic()
        bytes_written = 0

        if self.use_atomic_write:
            # Atomic write: write to temp file then rename
            temp_path = path.parent / f".{path.name}.tmp.{operation_id}"

            try:
                f = await aiofiles.open(temp_path, mode="wb")
                try:
                    for chunk_num in range(total_chunks):
                        start_idx = chunk_num * self.chunk_size
                        end_idx = min(start_idx + self.chunk_size, total_size)
                        chunk = content_bytes[start_idx:end_idx]

                        await f.write(chunk)
                        bytes_written += len(chunk)

                        # Calculate progress metrics
                        elapsed = time.monotonic() - start_time
                        bytes_per_second = bytes_written / elapsed if elapsed > 0 else 0
                        percentage = bytes_written / total_size if total_size > 0 else 1.0
                        remaining_bytes = total_size - bytes_written
                        estimated_seconds_remaining = (
                            remaining_bytes / bytes_per_second if bytes_per_second > 0 else 0
                        )

                        # Yield progress event every few chunks or at start/end
                        if chunk_num % 4 == 0 or chunk_num == total_chunks - 1:
                            progress_items: list[ToolCallContentItem] = [
                                LocationContentItem(path=str(path), line=0),
                                TextContentItem(
                                    text=(
                                        f"Writing: {bytes_written}/{total_size} bytes "
                                        f"({percentage * 100:.1f}%) "
                                        f"@ {bytes_per_second / 1024:.1f} KB/s"
                                    )
                                ),
                            ]

                            yield ToolCallProgressEvent(
                                tool_call_id=tool_call_id,
                                status="in_progress",
                                title=f"Writing {path.name}: {percentage * 100:.0f}%",
                                items=progress_items,
                                progress=chunk_num + 1,
                                total=total_chunks,
                            )
                finally:
                    await f.close()

                # Atomic rename (run in thread to avoid blocking)
                import os

                await asyncio.to_thread(os.rename, str(temp_path), str(path))

            except Exception:
                # Cleanup temp file on failure
                try:
                    await asyncio.to_thread(temp_path.unlink, missing_ok=True)
                except Exception:
                    pass
                raise
        else:
            # Direct write without atomic guarantee
            f = await aiofiles.open(path, mode="wb")
            try:
                for chunk_num in range(total_chunks):
                    start_idx = chunk_num * self.chunk_size
                    end_idx = min(start_idx + self.chunk_size, total_size)
                    chunk = content_bytes[start_idx:end_idx]

                    await f.write(chunk)
                    bytes_written += len(chunk)

                    if chunk_num % 4 == 0 or chunk_num == total_chunks - 1:
                        percentage = bytes_written / total_size if total_size > 0 else 1.0

                        yield ToolCallProgressEvent(
                            tool_call_id=tool_call_id,
                            status="in_progress",
                            title=f"Writing {path.name}: {percentage * 100:.0f}%",
                            items=[
                                LocationContentItem(path=str(path), line=0),
                                TextContentItem(text=f"Written {bytes_written}/{total_size} bytes"),
                            ],
                            progress=chunk_num + 1,
                            total=total_chunks,
                        )
            finally:
                await f.close()


class StreamingWriteTool:
    """Tool wrapper for streaming file write operations.

    This tool provides async file writing with progress reporting,
    atomic writes, and timeout protection.
    """

    def __init__(
        self,
        chunk_size: int = StreamingFileEditor.DEFAULT_CHUNK_SIZE,
        timeout: float = StreamingFileEditor.DEFAULT_TIMEOUT,
        use_atomic_write: bool = True,
    ):
        """Initialize the streaming write tool.

        Args:
            chunk_size: Size of chunks for streaming I/O
            timeout: Timeout for write operations in seconds
            use_atomic_write: Whether to use atomic writes
        """
        self.editor = StreamingFileEditor(
            chunk_size=chunk_size,
            timeout=timeout,
            use_atomic_write=use_atomic_write,
        )

    async def write_file(
        self,
        file_path: str,
        content: str,
        context: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Write content to file asynchronously.

        Note: This is the synchronous-style interface that returns a dict.
        For streaming progress, use the StreamingFileEditor directly.

        Args:
            file_path: Path to write to
            content: Content to write
            context: Agent context (for tool_call_id)

        Returns:
            Dict with operation results
        """
        # Get tool_call_id from context if available
        tool_call_id = (
            getattr(context, "tool_call_id", str(uuid4())[:8]) if context else str(uuid4())[:8]
        )

        try:
            # Collect all progress events
            events = []
            async for event in self.editor.write_file(file_path, content, tool_call_id):
                events.append(event)

            return {
                "success": True,
                "file_path": file_path,
                "message": f"Successfully wrote {len(content)} bytes to {file_path}",
                "bytes_written": len(content.encode("utf-8")),
            }

        except Exception as e:
            return {
                "success": False,
                "file_path": file_path,
                "error": str(e),
                "message": f"Failed to write {file_path}: {e}",
            }

    async def edit_file(
        self,
        file_path: str,
        old_string: str,
        new_string: str,
        replace_all: bool = False,
        context: AgentContext | None = None,
    ) -> dict[str, Any]:
        """Edit file by replacing string content.

        Note: This is the synchronous-style interface that returns a dict.
        For streaming progress, use the StreamingFileEditor directly.

        Args:
            file_path: Path to the file
            old_string: Text to replace
            new_string: Text to replace with
            replace_all: Whether to replace all occurrences
            context: Agent context

        Returns:
            Dict with operation results including diff
        """
        tool_call_id = (
            getattr(context, "tool_call_id", str(uuid4())[:8]) if context else str(uuid4())[:8]
        )

        try:
            events = []
            async for event in self.editor.edit_file(
                file_path, old_string, new_string, tool_call_id, replace_all, context
            ):
                events.append(event)

            # Find the diff from events
            diff_text = ""
            for event in events:
                if hasattr(event, "items") and event.items:
                    for item in event.items:
                        if hasattr(item, "old_text") and hasattr(item, "new_text"):
                            # This is a DiffContentItem
                            from agentpool.utils.diffs import compute_unified_diff

                            diff_text = compute_unified_diff(
                                item.old_text or "",
                                item.new_text,
                                fromfile=file_path,
                                tofile=file_path,
                            )
                            break

            return {
                "success": True,
                "file_path": file_path,
                "diff": diff_text,
                "message": f"Successfully edited {file_path}",
            }

        except Exception as e:
            return {
                "success": False,
                "file_path": file_path,
                "error": str(e),
                "message": f"Failed to edit {file_path}: {e}",
            }


# Convenience functions for direct use
async def streaming_edit_file(
    file_path: str,
    old_string: str,
    new_string: str,
    tool_call_id: str,
    replace_all: bool = False,
    chunk_size: int = StreamingFileEditor.DEFAULT_CHUNK_SIZE,
    timeout: float = StreamingFileEditor.DEFAULT_TIMEOUT,
) -> AsyncIterator[ToolCallProgressEvent]:
    """Convenience function for streaming file editing.

    Args:
        file_path: Path to the file
        old_string: Text to replace
        new_string: Text to replace with
        tool_call_id: Unique identifier for this operation
        replace_all: Whether to replace all occurrences
        chunk_size: Size of chunks for streaming I/O
        timeout: Timeout for the operation

    Yields:
        ToolCallProgressEvent with progress updates
    """
    editor = StreamingFileEditor(chunk_size=chunk_size, timeout=timeout)
    async for event in editor.edit_file(
        file_path, old_string, new_string, tool_call_id, replace_all
    ):
        yield event


async def streaming_write_file(
    file_path: str,
    content: str,
    tool_call_id: str,
    chunk_size: int = StreamingFileEditor.DEFAULT_CHUNK_SIZE,
    timeout: float = StreamingFileEditor.DEFAULT_TIMEOUT,
) -> AsyncIterator[ToolCallProgressEvent]:
    """Convenience function for streaming file writing.

    Args:
        file_path: Path to write to
        content: Content to write
        tool_call_id: Unique identifier for this operation
        chunk_size: Size of chunks for streaming I/O
        timeout: Timeout for the operation

    Yields:
        ToolCallProgressEvent with progress updates
    """
    editor = StreamingFileEditor(chunk_size=chunk_size, timeout=timeout)
    async for event in editor.write_file(file_path, content, tool_call_id):
        yield event
